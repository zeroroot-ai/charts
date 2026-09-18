#!/usr/bin/env python3
"""check-secret-plumbing.py — no dangling Secret reference in the render.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1348).
Every secretKeyRef, secretRef and secretName a rendered workload names must
be produced: by a Secret the chart renders, by an ExternalSecret's target,
by a Certificate's secretName, or by a runtime actor recorded with its
producer in helm/gibson/secret-producers.yaml. A reference nothing produces
is a CreateContainerConfigError or an empty mount at runtime, which is how a
mistyped *SecretName in values used to reach a cluster. A listed producer
whose Secret the chart does render is a stale entry and fails too, so the
list stays honest.

  check-secret-plumbing.py             exit 1 on a dangling or stale entry, 0 when clean
  check-secret-plumbing.py --selftest  prove a dangling reference and a stale entry fail
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCERS = os.path.join(ROOT, "helm", "gibson", "secret-producers.yaml")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def references(o, owner: str, into: dict) -> None:
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("secretKeyRef", "secretRef") and isinstance(v, dict) and v.get("name"):
                into.setdefault(v["name"], set()).add(owner)
            if k == "secretName" and isinstance(v, str):
                into.setdefault(v, set()).add(owner)
            references(v, owner, into)
    elif isinstance(o, list):
        for i in o:
            references(i, owner, into)


def judge(docs: list[dict], producers: dict[str, str]) -> list[str]:
    rendered: dict[str, str] = {}
    for d in docs:
        k, n = d.get("kind"), (d.get("metadata") or {}).get("name")
        if k == "Secret":
            rendered[n] = "Secret"
        elif k == "ExternalSecret":
            rendered[((d.get("spec") or {}).get("target") or {}).get("name") or n] = "ExternalSecret"
        elif k == "Certificate":
            rendered[(d.get("spec") or {}).get("secretName")] = "Certificate"
    refs: dict[str, set] = {}
    for d in docs:
        if d.get("kind") in WORKLOADS:
            references(d, f"{d['kind']}/{d['metadata']['name']}", refs)
    out = []
    for name in sorted(refs):
        if name not in rendered and name not in producers:
            out.append(f"dangling: {name} (referenced by {', '.join(sorted(refs[name]))}) — nothing renders it and no producer is recorded")
    for name in sorted(producers):
        if name in rendered:
            out.append(f"stale producer entry: {name} is rendered by a {rendered[name]}; delete it from secret-producers.yaml")
    return out


FIXTURE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: gibson}
spec:
  template:
    spec:
      containers:
        - name: c
          env:
            - name: A
              valueFrom: {secretKeyRef: {name: rendered-secret, key: k}}
            - name: B
              valueFrom: {secretKeyRef: {name: minted-at-runtime, key: k}}
            - name: C
              valueFrom: {secretKeyRef: {name: typo-secret, key: k}}
      volumes:
        - name: tls
          secret: {secretName: cert-secret}
---
apiVersion: v1
kind: Secret
metadata: {name: rendered-secret}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: cert}
spec: {secretName: cert-secret}
"""


def selftest() -> int:
    docs = [d for d in yaml.safe_load_all(FIXTURE) if d]
    got = judge(docs, {"minted-at-runtime": "an operator mints it", "rendered-secret": "stale"})
    want_dangling = any(x.startswith("dangling: typo-secret") for x in got)
    want_stale = any(x.startswith("stale producer entry: rendered-secret") for x in got)
    if not (want_dangling and want_stale and len(got) == 2):
        print(f"SELFTEST FAIL: want exactly the typo flagged and the stale entry flagged, got {got}")
        return 1
    live = judge(render(), load_producers())
    if live:
        print("SELFTEST FAIL: the baseline render has a plumbing gap:\n  " + "\n  ".join(live))
        return 1
    print("OK: a dangling reference and a stale producer entry fail; the render is fully plumbed")
    return 0


def load_producers() -> dict[str, str]:
    return (yaml.safe_load(open(PRODUCERS)) or {}).get("producers") or {}


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(render(), load_producers())
    if got:
        print("❌ secret plumbing:\n  " + "\n  ".join(got))
        return 1
    print("✓ secret-plumbing: every Secret a workload references is rendered or has a recorded producer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
