#!/usr/bin/env python3
"""check-gates-complete.py: every guard target of the Makefile runs in `make check`.

gibson#807 condition 7. charts#512 (4f5b1b56) rewrote the `check:` list and
dropped seam-conditions and kvm-fleet-selector without a word. Both kept
their selftests, and neither ran in CI again: a guard that never runs
cannot fail.

The rule: a Makefile target whose recipe runs a guard (`--selftest`,
`--self-test` or `bats`) is a prerequisite of `check-gates`, or is named in
NOT_GATES with the reason it runs elsewhere. Each name in `check-gates` is a
target that exists.

  check-gates-complete.py              check the Makefile
  check-gates-complete.py --selftest   prove each rule can fail
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Guard targets that run outside `make check`, each with where and why.
NOT_GATES: dict[str, str] = {}

GUARD = re.compile(r"--selftest|--self-test|\bbats\b")


def parse(text: str) -> tuple[set[str], dict[str, str]]:
    m = re.search(r"^check-gates:([^#\n]*)", text, re.M)
    gates = set(m.group(1).split()) if m else set()
    recipes: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        t = re.match(r"^([A-Za-z0-9_.-]+):(?!=)", line)
        if t and not line.startswith("\t"):
            cur = t.group(1)
            recipes.setdefault(cur, [])
        elif line.startswith("\t") and cur:
            recipes[cur].append(line)
    return gates, {k: "\n".join(v) for k, v in recipes.items()}


def problems(text: str) -> list[str]:
    gates, recipes = parse(text)
    if not gates:
        return ["no `check-gates:` target: the list of gates is gone"]
    out = [f"`{t}` runs a guard, and `check-gates` does not run it: a guard that never runs cannot fail"
           for t, body in sorted(recipes.items())
           if GUARD.search(body) and t not in gates and t not in NOT_GATES and t != "check-gates"]
    out += [f"`check-gates` names `{g}`, and no such target exists" for g in sorted(gates - set(recipes))]
    out += [f"NOT_GATES names `{t}`, and no such target exists" for t in sorted(set(NOT_GATES) - set(recipes))]
    return out


def selftest() -> int:
    good = "check-gates: a b\na:\n\t@x --selftest\nb:\n\t@bats b.bats\nc:\n\t@echo hi\n"
    cases = [
        ("every guard target is a gate", good, 0),
        ("MUTATION a --selftest target dropped from check-gates", good.replace("check-gates: a b", "check-gates: b"), 1),
        ("MUTATION a bats target dropped from check-gates", good.replace("check-gates: a b", "check-gates: a"), 1),
        ("MUTATION check-gates names a missing target", good.replace("check-gates: a b", "check-gates: a b z"), 1),
        ("MUTATION no check-gates target", good.replace("check-gates: a b\n", ""), 1),
    ]
    failed = 0
    for desc, text, want in cases:
        got = 1 if problems(text) else 0
        failed += got != want
        print(f"  {'ok  ' if got == want else 'FAIL'}  {desc} -> {'fail' if got else 'pass'}")
    print(f"check-gates-complete --selftest: {len(cases) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    found = problems((ROOT / "Makefile").read_text())
    for p in found:
        print(f"::error::{p}")
    if found:
        sys.exit(1)
    gates, _ = parse((ROOT / "Makefile").read_text())
    print(f"  ✓ gates-complete: each guard target is one of the {len(gates)} gates of make check")
