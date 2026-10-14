#!/usr/bin/env python3
"""check-queue-lanes.py: every PR check of this repo also runs in the merge queue.

gibson#807 condition 7. charts merges through a merge queue. A required check
that never starts on merge_group stalls the queue. A check that starts but
skips in the queue reads as a pass, so the commit that lands is not checked.

The rule, for each workflow under .github/workflows:

  - A workflow that triggers on pull_request also triggers on merge_group.
  - No job in it is limited to the pull_request lane by its `if:`.

  check-queue-lanes.py              check .github/workflows
  check-queue-lanes.py --selftest   prove each rule can fail
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def triggers(doc: dict) -> set[str]:
    # PyYAML reads the bare key `on` as the boolean True.
    on = doc.get("on", doc.get(True)) or {}
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return set(on)
    return set(on)


def problems(path: Path) -> list[str]:
    doc = yaml.safe_load(path.read_text()) or {}
    on = triggers(doc)
    if "pull_request" not in on:
        return []
    out = []
    if "merge_group" not in on:
        out.append(f"{path.name}: triggers on pull_request but not on merge_group, so the queue waits on its checks")
    for name, job in (doc.get("jobs") or {}).items():
        cond = str((job or {}).get("if") or "")
        if "github.event_name == 'pull_request'" in cond and "merge_group" not in cond:
            out.append(f"{path.name}: job `{name}` runs on pull_request only, so the queue skips it")
    return out


def check(root: Path) -> int:
    files = sorted((root / ".github" / "workflows").glob("*.yml"))
    if not files:
        print("::error::check-queue-lanes: found no workflow to check")
        return 1
    found = [p for f in files for p in problems(f)]
    for p in found:
        print(f"::error::{p}")
    if found:
        return 1
    n = sum(1 for f in files if "pull_request" in triggers(yaml.safe_load(f.read_text()) or {}))
    print(f"  ✓ queue-lanes: {n} pull_request workflow(s) also run every job in the merge queue")
    return 0


GOOD = """
on:
  pull_request:
  merge_group:
jobs:
  gate: {runs-on: ubuntu-latest}
"""


def selftest() -> int:
    cases = [
        ("a workflow on both events", GOOD, 0),
        ("MUTATION no merge_group trigger", GOOD.replace("  merge_group:\n", ""), 1),
        ("MUTATION a job on pull_request only", GOOD.replace(
            "gate: {runs-on: ubuntu-latest}", "gate: {if: \"github.event_name == 'pull_request'\", runs-on: ubuntu-latest}"), 1),
        ("a push-only workflow is out of scope", "on:\n  push:\njobs:\n  x: {runs-on: ubuntu-latest}\n", 0),
    ]
    failed = 0
    with tempfile.TemporaryDirectory() as d:
        wf = Path(d) / ".github" / "workflows"
        wf.mkdir(parents=True)
        for desc, text, want in cases:
            (wf / "x.yml").write_text(text)
            got = 1 if problems(wf / "x.yml") else 0
            failed += got != want
            print(f"  {'ok  ' if got == want else 'FAIL'}  {desc} -> {'fail' if got else 'pass'}")
        (wf / "x.yml").unlink()
        if check(Path(d)) != 1:
            print("  FAIL  an empty workflow directory must fail")
            failed += 1
    print(f"check-queue-lanes --selftest: {len(cases) + 1 - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(check(ROOT))
