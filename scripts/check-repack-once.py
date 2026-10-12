#!/usr/bin/env python3
"""check-repack-once.py: inside make check, no gate repacks the umbrella (charts#667).

make check repacks the first-party sub-charts once, then runs every gate in
parallel on that repack, with CHART_DEPS_DONE=1 in the environment. A gate
that deletes helm/gibson/charts/gibson-{workloads,operators,crds,common}-*.tgz
or a .charts.stamp file at that point removes tarballs that other gates are
rendering: they fail with "missing in charts/ directory". Four scripts did
that, each with its own repack.

The check reads each file under scripts/ and tests/ and the Makefile. A file
whose code (comment lines removed) deletes a packaged first-party chart or a
stamp must also test CHART_DEPS_DONE in its code. scripts/render-diff.sh
repacks a separate checkout, not this tree, and is not a gate.

  check-repack-once.py             exit 1 on a file that repacks with no CHART_DEPS_DONE test
  check-repack-once.py --selftest  prove an unguarded repack fails and a guarded one passes
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOT_A_GATE = {"scripts/render-diff.sh"}
DELETES = re.compile(
    r"(rm\s+-[a-z]*f|os\.remove|\.unlink\(|unlink\s)[^\n]*"
    r"(gibson-(\{?workloads|operators|crds|common)|charts\.stamp|helm_dir|\bcharts\b.*\.tgz)"
    r"|startswith\(\(\"gibson-workloads-")
GUARD = re.compile(r"CHART_DEPS_DONE")


def code(text: str) -> str:
    """The text with comment lines removed (#-comments of shell, Python and make)."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("#", "@#")))


def judge(path: str, text: str) -> list[str]:
    body = code(text)
    if DELETES.search(body) and not GUARD.search(body):
        return [f"{path}: deletes a packaged first-party chart or a stamp, and has no CHART_DEPS_DONE test: "
                "inside make check it removes tarballs that parallel gates read"]
    return []


def files() -> list[str]:
    out = ["Makefile"]
    for top in ("scripts", "tests"):
        for dirpath, _, names in os.walk(os.path.join(ROOT, top)):
            for n in names:
                if n.endswith((".py", ".sh", ".bats")):
                    rel = os.path.relpath(os.path.join(dirpath, n), ROOT)
                    if rel not in NOT_A_GATE and not rel.startswith("tests/fixtures/"):
                        out.append(rel)
    return sorted(out)


def selftest() -> int:
    unguarded = 'rm -f "$ROOT"/helm/gibson/charts/gibson-{workloads,operators}-*.tgz\nmake chart-deps\n'
    guarded = 'if [ -z "${CHART_DEPS_DONE:-}" ]; then\n' + unguarded + 'fi\n'
    py = ('for name in os.listdir(charts):\n    if name.startswith(("gibson-workloads-", "x")):\n'
          '        os.remove(os.path.join(charts, name))\n')
    commented = '# the guard CHART_DEPS_DONE is named only in a comment\n' + unguarded
    cases = [("an unguarded shell repack", unguarded, 1), ("a guarded shell repack", guarded, 0),
             ("an unguarded Python repack", py, 1), ("a guard named only in a comment", commented, 1),
             ("a file with no repack", "echo hello\n", 0)]
    for label, text, want in cases:
        if len(judge("fixture", text)) != want:
            print(f"SELFTEST FAIL: {label} must give {want} finding(s), got {judge('fixture', text)}")
            return 1
    print("  ✓ selftest: an unguarded shell or Python repack and a guard in a comment fail; a guarded repack passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    seen, bad = 0, []
    for rel in files():
        seen += 1
        bad += judge(rel, open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read())
    if seen < 50:
        bad.append(f"only {seen} files read: this check is blind")
    if bad:
        print("a gate repacks the umbrella inside make check (charts#667):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ repack-once: {seen} files, each repack tests CHART_DEPS_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
