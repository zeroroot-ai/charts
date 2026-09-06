#!/usr/bin/env python3
"""bump-image-digest.py — re-pin a first-party image across the charts (deploy#1391).

WHY THIS EXISTS

ADR-0004:30 asserts "the umbrella chart re-versions on every first-party
service bump — handled by an automated fan-out (existing pattern), not by
hand." No fan-out existed: the criterion was tracked as deploy#806, closed
without being built, and re-filed with evidence as deploy#1391. Until now a
first-party image bump meant hand-editing a digest in a values file, and on
2026-08-12 alone that was done six times by hand (www, docs-site twice,
gibson + ext-authz together, and five setec images at once).

WHAT IT DOES

Given an image and a new reference, rewrite every pin of that image in the
chart values files. It is deliberately a TEXT rewrite, not a YAML round-trip:
these files are heavily commented, several CI guards are line-anchored
(scripts/.secret-plumbing-allowlist.txt pins <path>:<line>:<sha>), and a
reformatting writer would shift every one of them. Only the pin line changes.

PIN SHAPES IT UNDERSTANDS

  tag: "<ref>@sha256:<digest>"      # most first-party pins
  tag: "<ref>"                      # paired with a sibling
  digest: "sha256:<digest>"         #   digest: key (docs-site)

Both forms resolve to "repository@digest" at render time; which one a chart
uses is a local style choice, so this handles either without changing it.

WHAT IT DELIBERATELY SKIPS

  helm/gibson-workloads/values-kind.yaml — kind/dev tracks moving :main tags
  on purpose and is exempt from digest-pin-check. Re-pinning it would fight
  the dev loop. Named explicitly rather than pattern-matched so a new overlay
  cannot become exempt by accident.

  ghcr.io/zeroroot-ai/mirror/* — third-party mirrors, not first-party builds.

USAGE

  # Event-driven: the publisher knows exactly what it published.
  bump-image-digest.py --image dashboard --ref latest --digest sha256:abc…

  # Report what is pinned today (no writes).
  bump-image-digest.py --list

  # Prove the rewriter still works.
  bump-image-digest.py --selftest

Exit codes: 0 changed (or clean for --list/--selftest), 1 error, 2 no pin found.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every values file that carries an immutable first-party pin. A new chart
# must be added here; the --list output is the audit surface that makes an
# omission visible.
PIN_FILES = [
    "helm/gibson/values.yaml",
    "helm/gibson-operators/values.yaml",
    "helm/gibson-workloads/values.yaml",
    # values-aws-prod.yaml and the saas-overlay values are NOT here: they are
    # the operator's own estate and live in the hosted repo. They carried no
    # first-party pin of their own, inheriting them from the base values above,
    # so nothing is unpinned by their absence. If the hosted repo ever adds one,
    # it maintains its own list — an unlisted file is how a pin goes stale.
]

# See "WHAT IT DELIBERATELY SKIPS" above.
EXEMPT_FILES = {"helm/gibson-workloads/values-kind.yaml"}

REGISTRY = "ghcr.io/zeroroot-ai"
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")

# Keys that may sit alongside `repository:` inside an image mapping. Scanning
# stops at anything else, so the walk cannot wander into the next block.
IMAGE_SIBLINGS = {"tag", "digest", "pullPolicy", "repository"}


class Pin:
    """One `repository:`-rooted image mapping in one file."""

    def __init__(self, path: str, image: str, repo_line: int, tag_line: int | None,
                 digest_line: int | None):
        self.path = path
        self.image = image
        self.repo_line = repo_line
        self.tag_line = tag_line
        self.digest_line = digest_line


def find_pins(lines: list[str], path: str) -> list[Pin]:
    """Every first-party image mapping in a file, block-scanned.

    A line window does not work here: `repository:` and its `tag:` are
    separated by up to ~50 lines of rationale in gibson-workloads/values.yaml.
    The block is instead the run of sibling keys at the same indent, skipping
    comments and blank lines, stopping at the first key that is not an image
    sibling or that dedents.
    """
    pins: list[Pin] = []
    for i, line in enumerate(lines):
        m = re.match(r'(\s*)repository:\s*"?(%s/[\w./-]+)"?\s*$' % re.escape(REGISTRY), line)
        if not m:
            continue
        repo = m.group(2)
        if repo.startswith(f"{REGISTRY}/mirror/"):
            continue
        base = len(m.group(1))
        tag_line = digest_line = None
        for j in range(i + 1, len(lines)):
            nxt = lines[j]
            if not nxt.strip() or nxt.lstrip().startswith("#"):
                continue
            key_m = re.match(r"(\s*)([\w.-]+):", nxt)
            if not key_m:
                # A list item or continuation — end of this mapping.
                break
            ind, key = len(key_m.group(1)), key_m.group(2)
            if ind < base or key not in IMAGE_SIBLINGS:
                break
            if ind > base:
                continue
            if key == "repository":
                break
            if key == "tag" and tag_line is None:
                tag_line = j
            elif key == "digest" and digest_line is None:
                digest_line = j
        pins.append(Pin(path, repo[len(REGISTRY) + 1:], i, tag_line, digest_line))
    return pins


def rewrite_pin(lines: list[str], pin: Pin, ref: str | None, digest: str) -> bool:
    """Apply the new reference to one pin. Returns whether anything changed.

    Preserves the file's existing shape: a `tag: "<ref>@<digest>"` pin stays
    combined, a separate `digest:` key stays separate. Trailing comments on the
    pin line survive, because several of them carry the provenance note that
    explains why that specific build is pinned.
    """
    changed = False

    # Which shape is this pin ACTUALLY using? Several blocks carry BOTH a
    # combined `tag: "<ref>@sha256:…"` and an empty `digest: ""` sibling (the
    # digest key documents an option the chart supports; the template ignores
    # it when empty). Writing into that empty key would silently migrate the
    # pin to the other shape — rendering the same image, but rewriting two
    # lines instead of one and quietly changing the file's declared style
    # across the repo. So the separate-key branch is taken only when the
    # digest key is genuinely in use.
    tag_is_combined = False
    if pin.tag_line is not None:
        tm = re.search(r'tag:\s*"([^"]*)"', lines[pin.tag_line])
        tag_is_combined = bool(tm and "@" in tm.group(1))
    digest_in_use = False
    if pin.digest_line is not None:
        dm = re.search(r'digest:\s*"([^"]*)"', lines[pin.digest_line])
        digest_in_use = bool(dm and dm.group(1).strip())

    if digest_in_use or (pin.digest_line is not None and not tag_is_combined):
        # Separate-key shape: digest holds the sha, tag holds provenance.
        old = lines[pin.digest_line]
        new = re.sub(r'(digest:\s*")[^"]*(")', lambda mm: mm.group(1) + digest + mm.group(2), old)
        if new != old:
            lines[pin.digest_line] = new
            changed = True
        if ref and pin.tag_line is not None:
            old_t = lines[pin.tag_line]
            new_t = re.sub(r'(tag:\s*")[^"]*(")', lambda mm: mm.group(1) + ref + mm.group(2), old_t)
            if new_t != old_t:
                lines[pin.tag_line] = new_t
                changed = True
        return changed

    if pin.tag_line is None:
        return False

    old = lines[pin.tag_line]
    m = re.search(r'tag:\s*"([^"]*)"', old)
    if not m:
        return False
    current = m.group(1)
    if "@" in current:
        keep_ref = ref if ref else current.split("@", 1)[0]
        replacement = f"{keep_ref}@{digest}"
    elif ref:
        # Bare tag with no digest anywhere: pinning it would silently change
        # the chart's contract (mutable -> immutable). Refuse rather than guess.
        return False
    else:
        return False
    if replacement != current:
        lines[pin.tag_line] = old.replace(f'"{current}"', f'"{replacement}"')
        changed = True
    return changed


class ResolveError(RuntimeError):
    """A first-party image reference could not be resolved to a digest."""


def resolve_digest(image: str, ref: str) -> str:
    """Ask GHCR what digest `ghcr.io/zeroroot-ai/<image>:<ref>` points at.

    Registry v2 over urllib rather than a CLI: the publish job already has
    ghcr credentials for helm and cosign but no docker/crane login, and adding
    a third-party setup action to the release path is a worse trade than 30
    lines of stdlib. Works locally with the same token.

    Returns the manifest digest — for a multi-arch image that is the index
    digest, which is what a values pin must carry (pinning one architecture's
    manifest would break the other).
    """
    token_env = os.environ.get("GHCR_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if not token_env:
        raise ResolveError("no GHCR_TOKEN/GITHUB_TOKEN in the environment to authenticate with")
    repo = f"zeroroot-ai/{image}"

    basic = base64.b64encode(f"x:{token_env}".encode()).decode()
    tok_req = urllib.request.Request(
        f"https://ghcr.io/token?service=ghcr.io&scope=repository:{repo}:pull",
        headers={"Authorization": f"Basic {basic}"},
    )
    try:
        with urllib.request.urlopen(tok_req, timeout=30) as r:
            bearer = json.load(r).get("token")
    except urllib.error.URLError as e:
        raise ResolveError(f"token request for {repo} failed: {e}") from e
    if not bearer:
        raise ResolveError(f"registry returned no pull token for {repo}")

    man_req = urllib.request.Request(
        f"https://ghcr.io/v2/{repo}/manifests/{ref}",
        headers={
            "Authorization": f"Bearer {bearer}",
            # Index types first: a multi-arch image must resolve to its index.
            "Accept": ", ".join([
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ]),
        },
        method="HEAD",
    )
    try:
        with urllib.request.urlopen(man_req, timeout=30) as r:
            digest = r.headers.get("Docker-Content-Digest") or r.headers.get("docker-content-digest")
    except urllib.error.HTTPError as e:
        raise ResolveError(f"{repo}:{ref} -> HTTP {e.code} (does that tag exist?)") from e
    except urllib.error.URLError as e:
        raise ResolveError(f"{repo}:{ref} -> {e}") from e
    if not digest or not DIGEST_RE.fullmatch(digest):
        raise ResolveError(f"{repo}:{ref} returned no usable Docker-Content-Digest")
    return digest


def cmd_resolve_all(require: bool) -> int:
    """Re-resolve every first-party pin against the registry (package time).

    ADR-0004 wants a chart version to be an exact deployable. The pins are
    committed by hand (now also by the fan-out, deploy#1391), and several name
    a MUTABLE ref — `main@sha256:…`, `latest@sha256:…`. Committed digest and
    registry truth can therefore diverge between the last bump and the
    release. Running this immediately before `helm package` closes that
    window: what ships is what the ref means at package time.

    --require makes an unresolvable first-party image fail the publish rather
    than silently shipping the stale committed digest.
    """
    drift, failed = [], []
    for path in PIN_FILES:
        lines = load(path)
        if lines is None:
            continue
        dirty = False
        for pin in find_pins(lines, path):
            ref = current_ref(lines, pin)
            if ref is None:
                # A pin with no resolvable ref (bare digest-only) is left
                # alone: there is no tag to ask the registry about.
                continue
            try:
                digest = resolve_digest(pin.image, ref)
            except ResolveError as e:
                failed.append(f"{pin.image}:{ref} — {e}")
                continue
            if rewrite_pin(lines, pin, None, digest):
                drift.append(f"{pin.image} {ref} -> {digest[:19]}… ({path})")
                dirty = True
        if dirty:
            (ROOT / path).write_text("".join(lines), encoding="utf-8")

    for d in drift:
        print(f"[resolve-all] re-pinned {d}")
    for f in failed:
        print(f"[resolve-all] UNRESOLVED {f}", file=sys.stderr)
    if failed and require:
        print("[resolve-all] refusing to package: a first-party image could not be "
              "resolved to a digest, so the chart would not be an exact deployable "
              "(ADR-0004).", file=sys.stderr)
        return 1
    if not drift and not failed:
        print("[resolve-all] every first-party pin already matches the registry")
    return 0


def current_ref(lines: list[str], pin: Pin) -> str | None:
    """The tag/ref a pin names, independent of which shape it uses."""
    if pin.tag_line is None:
        return None
    m = re.search(r'tag:\s*"([^"]*)"', lines[pin.tag_line])
    if not m:
        return None
    val = m.group(1)
    return val.split("@", 1)[0] if "@" in val else (val or None)


def load(path: str) -> list[str] | None:
    p = ROOT / path
    if not p.is_file():
        return None
    return p.read_text(encoding="utf-8").splitlines(keepends=True)


def cmd_list() -> int:
    print(f"{'image':26} {'file':44} pin")
    for path in PIN_FILES:
        lines = load(path)
        if lines is None:
            print(f"  MISSING {path}", file=sys.stderr)
            return 1
        for pin in find_pins(lines, path):
            src = pin.tag_line if pin.tag_line is not None else pin.repo_line
            val = lines[src].strip()
            if pin.digest_line is not None:
                val += "  +  " + lines[pin.digest_line].strip()
            print(f"{pin.image:26} {path:44} {val[:88]}")
    return 0


def cmd_bump(image: str, ref: str | None, digest: str) -> int:
    if not DIGEST_RE.fullmatch(digest):
        print(f"not a sha256 digest: {digest!r}", file=sys.stderr)
        return 1
    touched, found = [], False
    for path in PIN_FILES:
        lines = load(path)
        if lines is None:
            continue
        pins = [p for p in find_pins(lines, path) if p.image == image]
        if not pins:
            continue
        found = True
        if any(rewrite_pin(lines, p, ref, digest) for p in pins):
            (ROOT / path).write_text("".join(lines), encoding="utf-8")
            touched.append(path)
    if not found:
        print(f"no pin for {REGISTRY}/{image} in any chart values file "
              f"(is it first-party, and is its chart listed in PIN_FILES?)", file=sys.stderr)
        return 2
    for t in touched:
        print(f"[bump-image-digest] re-pinned {image} in {t}")
    if not touched:
        print(f"[bump-image-digest] {image} already at {digest} — nothing to do")
    return 0


SELFTEST_FIXTURE = '''\
image:
  # A long rationale block, the way gibson-workloads/values.yaml carries one,
  # so the scanner cannot rely on a line window.
  #
  # More prose. And more.
  repository: ghcr.io/zeroroot-ai/gibson
  # provenance note that must survive
  tag: "sha-old@sha256:''' + "0" * 64 + '''"  # trailing note
  pullPolicy: IfNotPresent
docs:
  image:
    repository: ghcr.io/zeroroot-ai/docs-site
    tag: "oldcommit"
    digest: "sha256:''' + "1" * 64 + '''"
mirrored:
  image:
    repository: ghcr.io/zeroroot-ai/mirror/kubectl
    tag: "1.31.4"
'''


def selftest() -> int:
    failures = []

    def check(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    lines = SELFTEST_FIXTURE.splitlines(keepends=True)
    pins = find_pins(lines, "fixture")
    names = sorted(p.image for p in pins)
    check(names == ["docs-site", "gibson"],
          f"expected the two first-party pins and no mirror, got {names}")

    gib = next(p for p in pins if p.image == "gibson")
    check(gib.tag_line is not None,
          "combined tag not found across the comment block — the scanner regressed "
          "to a line window, which is exactly what fails on the real gibson pin")

    new = "sha256:" + "a" * 64
    check(rewrite_pin(lines, gib, "sha-new", new), "combined-shape rewrite reported no change")
    tagline = lines[gib.tag_line]
    check(f'"sha-new@{new}"' in tagline, f"combined pin not rewritten: {tagline!r}")
    check("# trailing note" in tagline, "trailing provenance comment was destroyed")

    doc = next(p for p in pins if p.image == "docs-site")
    check(rewrite_pin(lines, doc, "newcommit", new), "separate-shape rewrite reported no change")
    check(f'digest: "{new}"' in lines[doc.digest_line], "separate digest not rewritten")
    check('tag: "newcommit"' in lines[doc.tag_line], "provenance tag not rewritten")

    # Idempotence: a second application must be a no-op.
    check(not rewrite_pin(lines, doc, "newcommit", new), "rewrite is not idempotent")

    # A bare mutable tag must never be silently pinned.
    bare = find_pins(['  repository: ghcr.io/zeroroot-ai/gibson\n', '  tag: "main"\n'], "f")[0]
    check(not rewrite_pin(['  repository: ghcr.io/zeroroot-ai/gibson\n', '  tag: "main"\n'],
                          bare, "main", new),
          "a bare mutable tag was rewritten into a digest pin — that silently "
          "changes the chart's contract")

    # The real tree must parse, or the workflow would open empty PRs forever.
    # Asserted over the union rather than per file: an overlay legitimately
    # carries no pin of its own (values-aws-prod.yaml inherits them), but the
    # scanner finding NOTHING anywhere means it has stopped working.
    total = 0
    for path in PIN_FILES:
        got = load(path)
        check(got is not None, f"{path} listed in PIN_FILES but missing")
        if got:
            total += len(find_pins(got, path))
    check(total >= 10, f"only {total} first-party pins found across the charts — "
                       "the scanner has regressed (there were 18 when this landed)")

    # And the images this repo actually ships must all be reachable by name,
    # so a fan-out event for any of them can find its pin.
    seen = set()
    for path in PIN_FILES:
        got = load(path)
        if got:
            seen.update(p.image for p in find_pins(got, path))
    # www is NOT here: it is an off-cluster surface since ADR-0009 (deploy#1622
    # removed its chart and its pin), so no fan-out event exists for it. It sat
    # in this list for a week and failed the 0.118.0 chart publish.
    for required in ("gibson", "ext-authz", "dashboard", "docs-site", "setec"):
        check(required in seen, f"{required} has no discoverable pin; a fan-out "
                                f"event for it would exit 2")

    for f in failures:
        print(f"[selftest] FAIL {f}", file=sys.stderr)
    if failures:
        return 1
    print("[bump-image-digest] selftest OK")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", help="image name without the registry, e.g. 'dashboard'")
    ap.add_argument("--ref", default=None, help="tag/provenance ref to record alongside the digest")
    ap.add_argument("--digest", help="sha256:… digest to pin")
    ap.add_argument("--list", action="store_true", help="print every first-party pin")
    ap.add_argument("--resolve-all", action="store_true",
                    help="re-resolve every first-party pin against GHCR (package time)")
    ap.add_argument("--require", action="store_true",
                    help="with --resolve-all: fail if any first-party image cannot be resolved")
    ap.add_argument("--selftest", action="store_true", help="verify the rewriter")
    args = ap.parse_args(argv)

    if args.selftest:
        return selftest()
    if args.list:
        return cmd_list()
    if args.resolve_all:
        return cmd_resolve_all(args.require)
    if not args.image or not args.digest:
        ap.error("--image and --digest are required (or use --list / --selftest)")
    return cmd_bump(args.image, args.ref, args.digest)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
