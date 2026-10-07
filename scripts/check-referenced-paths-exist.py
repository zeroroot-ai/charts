#!/usr/bin/env python3
"""check-referenced-paths-exist.py — no comment names a file that is not there.

A comment that points at a guard is how a reader checks a claim. When the file
it names does not exist, the claim cannot be checked and nothing says so — the
pointer reads as coverage.

Measured on main before this guard: 41 sites named one of 18 `*.bats` or
`*.py` files under a `tests/` tree that does not exist in this repository and
never has — the suites were not carried across the 2026-09-04 split (ADR-0086)
and the comments were. Eight more named a runbook under `docs/runbooks/` that
does not exist. Eleven named a real file that lives in `zeroroot-ai/hosted`
without saying so, which sends the reader looking here.

The same class has been found three times by hand this quarter. This is the
search, run every build:

    extract every repo-relative path a source file names, and stat it.

THE RULE

A token that looks like a path into this repository — one of the top-level
directories, a slash, and a known file extension — must either resolve, or name
the repository it belongs to on the same line. "zeroroot-ai/hosted
scripts/recreate-kind.sh" is a reference a reader can follow.
"scripts/recreate-kind.sh" is a reference that is simply wrong here.

A path that is correct and will never resolve here — a file a guard only reads
once something has been recorded in it, or one a selftest writes into a
temporary tree — is declared below with its reason. The declaration is checked
in BOTH directions: an entry whose file now exists FAILS, so the exception
cannot outlive the thing it excused.

  check-referenced-paths-exist.py             exit 1 on a path that is not there
  check-referenced-paths-exist.py --selftest  prove the fixtures fail and the tree is clean
  check-referenced-paths-exist.py --list      print every unresolved reference, one per line
"""
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Directories this repository has at its root. A token starting with any other
# segment is not a path into this tree.
TOPS = ("helm", "scripts", "docs", "gitops", "bootstrap", "tests", ".github")
EXTS = ("bats", "py", "sh", "md", "yaml", "yml", "tpl", "txt", "json", "go")

REFERENCE = re.compile(
    r'(?<![A-Za-z0-9_./-])((?:' + "|".join(re.escape(t) for t in TOPS) + r')/'
    r'[A-Za-z0-9_./*-]*\.(?:' + "|".join(EXTS) + r'))'
)
# A line that names the owning repository is a reference a reader can follow.
OTHER_REPO = re.compile(r'zeroroot-ai/[a-z0-9.-]+')

# Where to look: the files GIT TRACKS, and nothing else. CI checks sibling
# repositories out INSIDE this one — `org-dot-github/` is zeroroot-ai/.github —
# and their comments are correct in their own tree. Walking the working
# directory scanned 38 of them and failed the build on paths that resolve
# perfectly well where they live.
#
# Rendered goldens are output: they carry a copy of every template comment and
# would double every finding. docs/adr-index.md is a byte copy of the file in
# gibson (charts#365): its paths name files of gibson and of the docs repo, and
# an edit here would break the copy.
SKIP_DIRS = ("helm/testdata/golden", "docs/adr-index.md")
SCAN_EXTS = (".yaml", ".yml", ".py", ".sh", ".txt", ".tpl", ".md", ".bats", ".json")

# Paths that are correct and will never resolve here, each with the reason.
# Two kinds: a file a guard reads only once something has been recorded in it,
# and a file a guard WRITES into a temporary tree for its own selftest.
NOT_IN_THIS_TREE = {
    "scripts/.extauthz-transport-waivers.txt":
        "check-extauthz-transport.py reads it only when a waiver has been recorded",
    "scripts/.secret-plumbing-allowlist.txt":
        "check-secret-plumbing.py reads it only when a runtime producer has been recorded",
    "scripts/.values-consumed-exemptions.txt":
        "check-values-consumed.py reads it only when an exemption has been recorded",
    "scripts/.workload-rbac-allowlist.txt":
        "check-workload-rbac.py reads it only when a grant has been recorded",
    ".github/workflows/fixture.yml":
        "check-workflows.sh writes it into a temporary git tree and runs actionlint "
        "there, so the path is relative to that tree and never to this repository",
}


def sources() -> list[pathlib.Path]:
    tracked = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
    out = []
    for rel in tracked.split("\0"):
        if not rel or any(rel == d or rel.startswith(d + "/") for d in SKIP_DIRS):
            continue
        p = ROOT / rel
        if p.suffix in SCAN_EXTS and p.is_file():
            out.append(p)
    # A scan that found nothing is not a pass. If `git ls-files` ever returns
    # empty — a detached worktree, a shallow checkout, a different cwd — this
    # guard would report every file clean while reading none of them.
    if len(out) < 100:
        raise SystemExit(f"check-referenced-paths-exist: git ls-files returned {len(out)} "
                         f"scannable file(s) under {ROOT}, which is too few to be the chart "
                         f"tree. Refusing to report a pass over nothing.")
    return out


def unresolved(text: str, label: str) -> list[tuple[str, str]]:
    """[(reference, "<file>:<line>")] for every path that is not there."""
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if OTHER_REPO.search(line):
            continue
        for m in REFERENCE.finditer(line):
            ref = m.group(1).rstrip(".,:;)")
            if "*" in ref:          # a glob is a shape, not a file
                continue
            if ref in NOT_IN_THIS_TREE:
                continue
            if (ROOT / ref).exists():
                continue
            out.append((ref, f"{label}:{n}"))
    return out


def check() -> list[str]:
    bad = []
    for p in sources():
        rel = str(p.relative_to(ROOT))
        if rel == f"scripts/{os.path.basename(__file__)}":
            continue
        for ref, where in unresolved(p.read_text(errors="replace"), rel):
            bad.append(f"{where}: names {ref}, which is not in this repository")
    for ref, why in sorted(NOT_IN_THIS_TREE.items()):
        if (ROOT / ref).exists():
            bad.append(f"NOT_IN_THIS_TREE names {ref}, which now exists. Delete the entry: "
                       f"it was there because {why}")
    return bad


DEAD = 'tests/ratelimit-service.bats asserts the ordering.'
LIVE = 'scripts/check-velero-volume-excludes.sh keeps this list equal.'
QUALIFIED = 'zeroroot-ai/hosted scripts/recreate-kind.sh stage 2 installs it.'
DECLARED = 'reads scripts/.values-consumed-exemptions.txt when one is recorded.'
GLOB = 'helm/**/charts/*.tgz is gitignored build output.'


def selftest() -> int:
    for name, text, want in (
        ("a dead reference", DEAD, 1),
        ("a live reference", LIVE, 0),
        ("a reference that names its repository", QUALIFIED, 0),
        ("a declared path that is not in this tree", DECLARED, 0),
        ("a glob", GLOB, 0),
    ):
        got = unresolved(text, "fixture")
        if len(got) != want:
            print(f"SELFTEST FAIL: {name} must yield {want}, got {len(got)}: {got}")
            return 1
    # CI checks sibling repositories out inside this one. An untracked file
    # must not be scanned, however dead the paths it names: they are correct
    # where that file actually lives.
    planted = ROOT / "org-dot-github" / "scripts" / "selftest-planted.sh"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_text("# names scripts/check-a-file-that-is-not-here.sh\n")
    try:
        scanned = sources()
        if planted in scanned:
            print("SELFTEST FAIL: an untracked file inside the tree was scanned; a sibling "
                  "checkout would fail the build on paths that resolve in its own repository")
            return 1
    finally:
        planted.unlink()
        for d in (planted.parent, planted.parent.parent):
            try:
                d.rmdir()
            except OSError:
                pass

    live = check()
    if live:
        print(f"SELFTEST FAIL: {len(live)} unresolved reference(s) in the tree:\n  " + "\n  ".join(live))
        return 1
    print(f"OK: a dead reference fails; a live one, one that names its repository, a "
          f"declared path, a glob and an untracked sibling checkout all pass; "
          f"{len(sources())} tracked file(s) clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = check()
    if "--list" in sys.argv:
        for b in bad:
            print(b)
        return 0
    if bad:
        print(f"❌ {len(bad)} reference(s) name a file that is not in this repository:\n  "
              + "\n  ".join(bad))
        print("  name the owning repository (zeroroot-ai/hosted scripts/x.sh), point at the "
              "file that does exist, or drop the claim.")
        return 1
    print(f"✓ referenced-paths-exist: every repo-relative path named in {len(sources())} "
          f"file(s) resolves")
    return 0


if __name__ == "__main__":
    sys.exit(main())
