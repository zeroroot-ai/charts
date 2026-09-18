#!/usr/bin/env python3
"""check-exec-rbac-scoped.py — a Role that grants pods/exec is bounded.

The postgres-exec ServiceAccount stands between upgrades (it is a pre-install
hook the wave -6 Jobs need), so anyone who can create a Pod in the namespace
can wear it. What it may then reach is the chart's decision, and this guard
keeps that decision from drifting back:

  - every `secrets` rule in a Role that also grants `pods/exec` names its
    Secrets (resourceNames) and grants `get` only, never `list`;
  - no object in that file carries helm.sh/resource-policy: keep, so an
    uninstall removes the identity instead of leaving a standing pods/exec
    grant behind.

  check-exec-rbac-scoped.py             exit 1 on a violation, 0 when clean
  check-exec-rbac-scoped.py --selftest  prove an unscoped fixture fails and the tree passes
"""
import os
import re
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "helm", "gibson", "templates", "jobs", "platform-primitives-exec-rbac.yaml")
GOTMPL = re.compile(r"\{\{.*?\}\}", re.S)


def violations(path: str) -> list[str]:
    text = GOTMPL.sub("", open(path, encoding="utf-8").read())
    out = []
    docs = [d for d in yaml.safe_load_all(text) if d]
    for d in docs:
        meta = d.get("metadata", {})
        name = f"{d.get('kind')}/{meta.get('name')}"
        if (meta.get("annotations") or {}).get("helm.sh/resource-policy") == "keep":
            out.append(f"{name}: carries helm.sh/resource-policy: keep; a hook identity must not outlive an uninstall")
        if d.get("kind") != "Role":
            continue
        rules = d.get("rules") or []
        grants_exec = any("pods/exec" in (r.get("resources") or []) for r in rules)
        if not grants_exec:
            continue
        for r in rules:
            if "secrets" not in (r.get("resources") or []):
                continue
            if not r.get("resourceNames"):
                out.append(f"{name}: a secrets rule beside pods/exec has no resourceNames")
            extra = sorted(set(r.get("verbs") or []) - {"get"})
            if extra:
                out.append(f"{name}: a secrets rule beside pods/exec grants {extra}; only get is allowed")
    return out


FIXTURE_UNSCOPED = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: postgres-exec
  annotations:
    helm.sh/resource-policy: keep
rules:
- apiGroups: ['']
  resources: [pods/exec]
  verbs: [create]
- apiGroups: ['']
  resources: [secrets]
  verbs: [get, list]
"""

FIXTURE_SCOPED = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: postgres-exec
rules:
- apiGroups: ['']
  resources: [pods/exec]
  verbs: [create]
- apiGroups: ['']
  resources: [secrets]
  resourceNames: [a-credentials]
  verbs: [get]
"""


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        bad = os.path.join(d, "bad.yaml")
        open(bad, "w").write(FIXTURE_UNSCOPED)
        v = violations(bad)
        want = ("resource-policy", "resourceNames", "['list']")
        if not all(any(w in x for x in v) for w in want):
            print(f"SELFTEST FAIL: the unscoped fixture must fail on all three counts, got {v}")
            return 1
        good = os.path.join(d, "good.yaml")
        open(good, "w").write(FIXTURE_SCOPED)
        if violations(good):
            print(f"SELFTEST FAIL: the scoped fixture must pass, got {violations(good)}")
            return 1
    live = violations(TARGET)
    if live:
        print("SELFTEST FAIL: the shipped template violates the rule:\n  " + "\n  ".join(live))
        return 1
    print("OK: an unscoped pods/exec Role fails, and the shipped one is scoped")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    v = violations(TARGET)
    if v:
        print("❌ postgres-exec RBAC is not bounded:\n  " + "\n  ".join(v))
        return 1
    print("✓ postgres-exec RBAC: secrets are named, get only, nothing is kept past uninstall")
    return 0


if __name__ == "__main__":
    sys.exit(main())
