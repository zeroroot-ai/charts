#!/usr/bin/env python3
"""Vendored-chart-dependency freshness guard (deploy#1600).

`helm/**/charts/*.tgz` is gitignored build output that `helm template` reads
SILENTLY. Stale, missing or packaged-from-another-branch tarballs produce a
wrong render with no error, and every assertion made against that render is
wrong too — green in CI, wrong on a workstation (deploy#1001, deploy#1465).

The primary fix is structural and lives in the Makefile: `helm/<chart>/.charts.stamp`
is a real target whose prerequisites are the chart's dependency sources (see
`scripts/chart-dep-srcs.sh`), and every render-consuming target depends on it.
Nobody has to remember `make chart-deps`.

This guard covers the ONE path make cannot reach: the `helm/<chart>/tests/*.bats`
suites, which developers and CI invoke as `bats …` directly, never through a
make target. Each such directory carries a `setup_suite.bash` that calls this
guard before the first render, so a stale tarball fails the suite with the
command to run instead of quietly producing a wrong verdict.

It is the same stamp either way: the Makefile recipe writes it with --write
after packaging, and the digest inside it is computed from the same file list
make used as prerequisites, so the mtime rule and the content check cannot
disagree about what "the sources" are.

Usage:
    python3 scripts/check-chart-deps-fresh.py                  # verify all
    python3 scripts/check-chart-deps-fresh.py gibson-workloads # verify one
    python3 scripts/check-chart-deps-fresh.py --write gibson   # record (make)
    python3 scripts/check-chart-deps-fresh.py --selftest

Exit codes: 0 fresh, 1 stale/missing, 2 the guard itself is broken.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SRCS_SCRIPT = REPO_ROOT / "scripts" / "chart-dep-srcs.sh"
STAMP_NAME = ".charts.stamp"

# Charts whose charts/ directory is vendored build output, i.e. the ones that
# declare at least one dependency. --selftest re-derives this set from the
# working tree and fails if it has drifted, so a NEW dependency on a chart that
# has none today cannot slip past without a stamp rule in the Makefile.
CHARTS = ("gibson-operators", "gibson-workloads", "gibson-crds", "gibson", "gibson-velero")

STAMP_HEADER = (
    "# chart-deps stamp — written by `make chart-deps`; gitignored build output.\n"
    "# Records the digest of the sources helm/<chart>/charts/*.tgz was packaged\n"
    "# from. Do not edit by hand: see scripts/check-chart-deps-fresh.py.\n"
)


class GuardBroken(Exception):
    """The guard cannot see what it claims to check."""


# ---------------------------------------------------------------------------
# Source closure + digest
# ---------------------------------------------------------------------------


def source_files(chart: str, root: pathlib.Path) -> list[str]:
    """The dependency-source closure of a chart, via the shared shell helper.

    Shelling out is the point: the Makefile computes its prerequisite list from
    the SAME script, so there is exactly one definition of "the sources".
    """
    chart_dir = root / "helm" / chart
    res = subprocess.run(
        [str(SRCS_SCRIPT), os.path.relpath(chart_dir, root)],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if res.returncode != 0:
        raise GuardBroken(
            f"chart-dep-srcs.sh failed for {chart}:\n{res.stderr.strip()}"
        )
    files = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not files:
        raise GuardBroken(
            f"chart-dep-srcs.sh listed NO sources for {chart} — an empty "
            "prerequisite list would make the stamp permanently 'fresh'"
        )
    return files


def sources_digest(chart: str, root: pathlib.Path) -> str:
    """sha256 over (path, content-hash) of the whole closure, order-stable."""
    h = hashlib.sha256()
    for rel in sorted(source_files(chart, root)):
        p = root / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(hashlib.sha256(p.read_bytes()).hexdigest().encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Declared dependencies -> expected tarballs
# ---------------------------------------------------------------------------

_DEP_NAME = re.compile(r"^\s*-\s+name:\s*[\"']?([A-Za-z0-9_.-]+)")


def declared_deps(chart_dir: pathlib.Path) -> list[str]:
    """Dependency names from a Chart.yaml, without needing PyYAML.

    Line-scanned rather than parsed: `dependencies:` blocks in this repo are
    interleaved with column-0 comment blocks explaining each entry, and the
    guard must keep working in jobs that do not install PyYAML.
    """
    text = (chart_dir / "Chart.yaml").read_text(encoding="utf-8")
    names: list[str] = []
    inside = False
    for line in text.splitlines():
        if not inside:
            if re.match(r"^dependencies:\s*$", line):
                inside = True
            continue
        if re.match(r"^[A-Za-z0-9_.-]+\s*:", line):  # next top-level key
            break
        m = _DEP_NAME.match(line)
        if m:
            names.append(m.group(1))
    return names


def missing_tarballs(chart_dir: pathlib.Path) -> list[str]:
    """Declared dependencies with no packaged tarball in charts/."""
    charts = chart_dir / "charts"
    return [
        dep
        for dep in declared_deps(chart_dir)
        if not list(charts.glob(f"{dep}-*.tgz"))
    ]


# ---------------------------------------------------------------------------
# Stamp read / write / verify
# ---------------------------------------------------------------------------


def write_stamp(chart: str, root: pathlib.Path) -> None:
    chart_dir = root / "helm" / chart
    missing = missing_tarballs(chart_dir)
    if missing:
        raise GuardBroken(
            f"{chart}: `helm dependency update` left no tarball for "
            f"{', '.join(missing)} — refusing to stamp a build that did not happen"
        )
    (chart_dir / STAMP_NAME).write_text(
        f"{STAMP_HEADER}chart: {chart}\nsources-sha256: {sources_digest(chart, root)}\n",
        encoding="utf-8",
    )


def stamp_digest(chart_dir: pathlib.Path) -> str | None:
    stamp = chart_dir / STAMP_NAME
    if not stamp.is_file():
        return None
    for line in stamp.read_text(encoding="utf-8").splitlines():
        if line.startswith("sources-sha256:"):
            return line.split(":", 1)[1].strip()
    return None


def verify(chart: str, root: pathlib.Path) -> str | None:
    """-> None when fresh, else a one-line reason."""
    chart_dir = root / "helm" / chart
    if not (chart_dir / "Chart.yaml").is_file():
        raise GuardBroken(f"{chart_dir}/Chart.yaml not found")

    recorded = stamp_digest(chart_dir)
    if recorded is None:
        return f"{chart}: no {STAMP_NAME} — charts/*.tgz was never built here"

    missing = missing_tarballs(chart_dir)
    if missing:
        return (
            f"{chart}: stamped, but charts/ has no tarball for "
            f"{', '.join(missing)} — the vendored dependency is gone"
        )

    actual = sources_digest(chart, root)
    if actual != recorded:
        return (
            f"{chart}: subchart sources changed since charts/*.tgz was packaged "
            f"(stamped {recorded[:12]}, now {actual[:12]})"
        )
    return None


# ---------------------------------------------------------------------------
# Selftest
# ---------------------------------------------------------------------------

_MIN_CHART = "apiVersion: v2\nname: {name}\nversion: 0.1.0\n"


def _synthetic_repo(tmp: pathlib.Path) -> None:
    """A two-chart tree: `parent` with a file:// dependency on `lib`."""
    (tmp / "scripts").mkdir(parents=True)
    shutil.copy2(SRCS_SCRIPT, tmp / "scripts" / SRCS_SCRIPT.name)

    lib = tmp / "helm" / "lib"
    (lib / "templates").mkdir(parents=True)
    (lib / "Chart.yaml").write_text(_MIN_CHART.format(name="lib"), encoding="utf-8")
    (lib / "templates" / "_helpers.tpl").write_text(
        '{{- define "lib.name" -}}lib{{- end -}}\n', encoding="utf-8"
    )

    parent = tmp / "helm" / "parent"
    (parent / "templates").mkdir(parents=True)
    (parent / "Chart.yaml").write_text(
        _MIN_CHART.format(name="parent")
        + 'dependencies:\n  - name: lib\n    version: "0.1.0"\n'
        '    repository: "file://../lib"\n',
        encoding="utf-8",
    )
    (parent / "charts").mkdir()
    (parent / "charts" / "lib-0.1.0.tgz").write_bytes(b"packaged")


def _case(label: str, want_stale: bool, fn) -> bool:
    """Run one selftest case; -> True on failure."""
    with tempfile.TemporaryDirectory() as td:
        root = pathlib.Path(td)
        _synthetic_repo(root)
        write_stamp("parent", root)
        fn(root)
        reason = verify("parent", root)
        got_stale = reason is not None
    ok = got_stale == want_stale
    print(f"  selftest: {'ok' if ok else 'SELFTEST FAIL'}: {label}")
    if not ok:
        print(f"    wanted stale={want_stale}, got {reason!r}")
    return not ok


def selftest() -> int:
    failed = False

    failed |= _case("untouched tree is fresh", False, lambda root: None)

    failed |= _case(
        "edited dependency template is stale",
        True,
        lambda root: (root / "helm" / "lib" / "templates" / "_helpers.tpl").write_text(
            '{{- define "lib.name" -}}CHANGED{{- end -}}\n', encoding="utf-8"
        ),
    )
    failed |= _case(
        "new file in the dependency is stale",
        True,
        lambda root: (root / "helm" / "lib" / "templates" / "extra.yaml").write_text(
            "kind: ConfigMap\n", encoding="utf-8"
        ),
    )
    failed |= _case(
        "changed dependency list is stale",
        True,
        lambda root: (root / "helm" / "parent" / "Chart.yaml").write_text(
            (root / "helm" / "parent" / "Chart.yaml").read_text(encoding="utf-8")
            + '  - name: other\n    version: "0.1.0"\n'
            '    repository: "file://../lib"\n',
            encoding="utf-8",
        ),
    )
    failed |= _case(
        "deleted tarball is stale",
        True,
        lambda root: (root / "helm" / "parent" / "charts" / "lib-0.1.0.tgz").unlink(),
    )
    failed |= _case(
        "missing stamp is stale",
        True,
        lambda root: (root / "helm" / "parent" / STAMP_NAME).unlink(),
    )
    failed |= _case(
        "edit to the parent's OWN templates is not stale",
        False,
        lambda root: (root / "helm" / "parent" / "templates" / "cm.yaml").write_text(
            "kind: ConfigMap\n", encoding="utf-8"
        ),
    )

    # Reach assertion: the declared CHARTS set must still equal the set of
    # first-party charts that actually declare a dependency. A new dependency
    # on a chart with no stamp rule would otherwise go unguarded.
    # `*/charts/` holds resolved dependency tarballs, and `*/charts/`
    # is unpacked build output — neither is a first-party chart.
    discovered = set()
    for chart_yaml in (REPO_ROOT / "helm").rglob("Chart.yaml"):
        rel = chart_yaml.parent.relative_to(REPO_ROOT / "helm")
        if rel.parts and "charts" in rel.parts:
            continue
        if declared_deps(chart_yaml.parent):
            discovered.add(str(rel))
    if discovered != set(CHARTS):
        print(
            f"  selftest: SELFTEST FAIL: CHARTS drifted — declared {sorted(CHARTS)}, "
            f"working tree has {sorted(discovered)}. Add a .charts.stamp rule in the "
            "Makefile and list the chart in CHARTS."
        )
        failed = True
    else:
        print("  selftest: ok: CHARTS matches the charts that declare dependencies")

    if failed:
        print("check-chart-deps-fresh: SELFTEST FAILED", file=sys.stderr)
        return 2
    print("check-chart-deps-fresh: selftest passed (guard can fail).")
    return 0


# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("charts", nargs="*", default=None, help=f"subset of {CHARTS}")
    ap.add_argument("--write", metavar="CHART", help="record the stamp after packaging")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    try:
        if args.write:
            write_stamp(args.write, REPO_ROOT)
            return 0

        wanted = args.charts or list(CHARTS)
        unknown = [c for c in wanted if c not in CHARTS]
        if unknown:
            print(f"ERROR: unknown chart(s): {', '.join(unknown)}", file=sys.stderr)
            return 2

        reasons = [r for c in wanted if (r := verify(c, REPO_ROOT))]
    except GuardBroken as exc:
        print(f"ERROR: check-chart-deps-fresh is broken: {exc}", file=sys.stderr)
        return 2

    if reasons:
        print(
            "\nERROR: vendored chart dependencies are STALE — any render made now\n"
            "       reads helm/**/charts/*.tgz built from different sources, and\n"
            "       every assertion against it would be wrong:\n",
            file=sys.stderr,
        )
        for r in reasons:
            print(f"  - {r}", file=sys.stderr)
        print("\n  Fix:  make chart-deps\n", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
