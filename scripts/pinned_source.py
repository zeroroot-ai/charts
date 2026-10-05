#!/usr/bin/env python3
"""pinned_source.py — the source tree of a service AT THE TAG the chart pins.

A vendored contract under helm/contracts/ describes the code inside an image.
The chart pins that image by release tag, so the contract is true or false
against that tag and against nothing else. The first nightly compared each
contract with the service's `main`. `main` moves with every merge, so the
check went red on the day after each sync and stayed red: it measured the
distance between a release and `main`, which is never zero and is not a defect.

So both generators read through here. `pins()` takes each tag from
airgap/images.txt, the rendered image list. `tree_at()` extracts that tag from
the sibling clone with `git archive`, so the result does not depend on what
the clone has checked out.

  pinned_source.py --selftest    prove the pin parser and the header check
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "airgap" / "images.txt"
CONTRACTS = ROOT / "helm" / "contracts"

PIN = re.compile(r"^ghcr\.io/zeroroot-ai/([a-z0-9-]+):(v\d+\.\d+\.\d+)(?:@sha256:[0-9a-f]{64})?\s*$")
SOURCE = re.compile(r"^# Source: zeroroot-ai/([a-z0-9-]+) @ (\S+)$", re.M)


def parse_pins(text: str) -> dict[str, str]:
    """image name -> release tag, for every first-party image pinned by tag."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = PIN.match(line.strip())
        if m:
            out[m.group(1)] = m.group(2)
    return out


def pins() -> dict[str, str]:
    return parse_pins(IMAGES.read_text())


def pinned_tag(repo: str) -> str:
    """The tag of the image named after the repo. Every image a repo builds
    ships from one release, so the repo's own image names the release."""
    tag = pins().get(repo)
    if not tag:
        raise SystemExit(f"::error::airgap/images.txt pins no ghcr.io/zeroroot-ai/{repo}:vX.Y.Z, "
                         f"so no tag says which {repo} source the contract describes")
    return tag


def sibling(repo: str) -> Path | None:
    for cand in (ROOT.parent / repo, ROOT.parent.parent / "opensource" / repo,
                 ROOT.parent.parent / "enterprise" / "platform" / repo,
                 ROOT.parent / "enterprise" / "platform" / repo,
                 ROOT.parent / "opensource" / repo):
        if (cand / ".git").exists():
            return cand
    return None


def tree_at(repo: str, tag: str) -> tempfile.TemporaryDirectory:
    """A temporary directory holding `repo` at `tag`. The caller keeps the
    handle alive for as long as it reads the tree."""
    clone = sibling(repo)
    if clone is None:
        raise SystemExit(f"::error::no checkout of zeroroot-ai/{repo} beside this one")
    # A monorepo-style release names its tag after the component (docs-site-v0.6.13).
    for ref in (f"refs/tags/{tag}", f"refs/tags/{repo}-{tag}"):
        ok = subprocess.run(["git", "-C", str(clone), "rev-parse", "-q", "--verify", ref + "^{commit}"],
                            capture_output=True, text=True)
        if ok.returncode == 0:
            break
    else:
        raise SystemExit(f"::error::{clone} has no tag {tag} or {repo}-{tag}. "
                         f"Run `git -C {clone} fetch origin --tags`.")
    tmp = tempfile.TemporaryDirectory(prefix=f"{repo}-{tag}-")
    archive = subprocess.Popen(["git", "-C", str(clone), "archive", ref], stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", tmp.name], stdin=archive.stdout, check=True)
    if archive.wait() != 0:
        raise SystemExit(f"::error::git archive {ref} failed in {clone}")
    return tmp


def header_problems(contract_text: str, name: str, pinned: dict[str, str]) -> list[str]:
    """A contract must name the tag it was read at, and that tag must be the pin."""
    m = SOURCE.search(contract_text)
    if not m:
        return [f"{name} names no source tag (`# Source: zeroroot-ai/<repo> @ <tag>`)"]
    repo, tag = m.group(1), m.group(2)
    want = pinned.get(repo)
    if want is None:
        return [f"{name} describes zeroroot-ai/{repo}, and the chart pins no image of that name"]
    if tag != want:
        return [f"{name} was read at {repo} {tag}, and the chart pins {want}: "
                f"run `make env-contract-sync config-contract-sync` in the PR that moves the pin"]
    return []


def check_headers() -> int:
    pinned = pins()
    files = sorted(CONTRACTS.glob("*.txt"))
    if not files:
        print("::error::helm/contracts holds no contract: the check found nothing to read")
        return 1
    problems = [p for f in files for p in header_problems(f.read_text(), f.name, pinned)]
    for p in problems:
        print("::error::" + p)
    if problems:
        return 1
    print(f"  ✓ {len(files)} vendored contract(s) each name the tag the chart pins")
    return 0


def selftest() -> int:
    pinned = parse_pins(
        "# a comment\n"
        "ghcr.io/zeroroot-ai/gibson:v0.150.1@sha256:" + "a" * 64 + "\n"
        "ghcr.io/zeroroot-ai/gibson-bootstrap-runner:sha-2f91a7b@sha256:" + "b" * 64 + "\n"
        "ghcr.io/zeroroot-ai/gibson-executor@sha256:" + "c" * 64 + "\n"
        "ghcr.io/zitadel/zitadel:v4.19.4\n"
    )
    if pinned != {"gibson": "v0.150.1"}:
        print(f"SELFTEST FAIL: parse_pins read {pinned}; only a first-party release tag is a pin")
        return 1
    cases = [
        ("# Source: zeroroot-ai/gibson @ v0.150.1\nA\n", 0, "a contract at the pinned tag"),
        ("# Source: zeroroot-ai/gibson @ v0.149.0\nA\n", 1, "a contract read at an older tag"),
        ("# Source: zeroroot-ai/gibson\nA\n", 1, "a contract that names no tag"),
        ("# Source: zeroroot-ai/billing @ v1.0.0\nA\n", 1, "a contract for an image the chart does not pin"),
    ]
    for text, want, what in cases:
        got = len(header_problems(text, "fixture.txt", pinned))
        if got != want:
            print(f"SELFTEST FAIL: {what} gave {got} problem(s), want {want}")
            return 1
    print("pinned_source: selftest OK — only release tags are pins, and a contract "
          "at another tag, with no tag, or for an unpinned image fails")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--check-headers" in sys.argv:
        sys.exit(check_headers())
    if "--refs" in sys.argv:
        # repo=tag lines for a workflow, one per repo named on the command line
        for repo in sys.argv[sys.argv.index("--refs") + 1:]:
            print(f"{repo}={pinned_tag(repo)}")
        sys.exit(0)
    print(__doc__)
    sys.exit(2)
