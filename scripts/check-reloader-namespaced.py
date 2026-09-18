#!/usr/bin/env python3
"""check-reloader-namespaced.py — Reloader reads Secrets in its own namespace only.

Reloader scopes its Secret and ConfigMap informers to KUBERNETES_NAMESPACE;
unset, it watches every namespace and was handed a ClusterRole over every
Secret in the cluster to do so. This guard keeps the two halves together:
the Deployment sets the variable, and no cluster-scoped grant on secrets
exists for the reloader ServiceAccount.

  check-reloader-namespaced.py             exit 1 on a violation, 0 when clean
  check-reloader-namespaced.py --selftest  prove the cluster-scoped fixture fails and the tree passes
"""
import os
import re
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "helm", "gibson-operators", "templates", "reloader", "reloader.yaml")
GOTMPL = re.compile(r"\{\{.*?\}\}", re.S)


def violations(path: str) -> list[str]:
    text = GOTMPL.sub("", open(path, encoding="utf-8").read())
    out = []
    docs = [d for d in yaml.safe_load_all(text) if d]
    for d in docs:
        kind = d.get("kind")
        name = (d.get("metadata") or {}).get("name")
        if kind == "ClusterRole":
            for r in d.get("rules") or []:
                if "secrets" in (r.get("resources") or []):
                    out.append(f"ClusterRole/{name} grants secrets cluster-wide")
        if kind == "ClusterRoleBinding":
            out.append(f"ClusterRoleBinding/{name}: the reloader needs no cluster-scoped binding")
        if kind == "Deployment":
            envs = []
            for c in ((d.get("spec") or {}).get("template", {}).get("spec", {}).get("containers") or []):
                envs += [e.get("name") for e in (c.get("env") or [])]
            if "KUBERNETES_NAMESPACE" not in envs:
                out.append(f"Deployment/{name} does not set KUBERNETES_NAMESPACE, so Reloader watches every namespace")
    return out


BAD = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: reloader}
rules:
- apiGroups: ['']
  resources: [secrets, configmaps]
  verbs: [list, get, watch]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: reloader}
spec:
  template:
    spec:
      containers:
      - name: reloader
        args: ["--reload-strategy=annotations"]
"""


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.yaml")
        open(p, "w").write(BAD)
        v = violations(p)
        if not (any("cluster-wide" in x for x in v) and any("KUBERNETES_NAMESPACE" in x for x in v)):
            print(f"SELFTEST FAIL: the cluster-scoped fixture must fail on both counts, got {v}")
            return 1
    live = violations(TARGET)
    if live:
        print("SELFTEST FAIL: the shipped template violates the rule:\n  " + "\n  ".join(live))
        return 1
    print("OK: a cluster-scoped reloader fails, and the shipped one is namespaced")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    v = violations(TARGET)
    if v:
        print("❌ reloader is not namespaced:\n  " + "\n  ".join(v))
        return 1
    print("✓ reloader: KUBERNETES_NAMESPACE set, secrets read through a Role in its namespace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
