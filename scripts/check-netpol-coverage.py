#!/usr/bin/env python3
"""check-netpol-coverage.py — no workload in the release namespace without a NetworkPolicy.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1365).
The release namespace carries a default-deny NetworkPolicy, so a workload
no policy selects has no network at all, and a workload only the deny
selects is not a hardened one, it is a dead one. Every Deployment,
StatefulSet, DaemonSet, Job and CronJob the umbrella renders into the
release namespace must be selected by at least one NetworkPolicy in that
namespace, by pod labels. Sub-chart namespaces (setec-system) carry their
own policies and are the sub-chart's contract.

The render is the baseline profile with the installer inputs, the same
render every other offline guard judges.

  check-netpol-coverage.py             exit 1 on an uncovered workload, 0 when clean
  check-netpol-coverage.py --selftest  prove an unselected workload fails and a selected one passes
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
NAMESPACE = "gibson"


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_labels(d: dict) -> dict:
    tmpl = d["spec"]["jobTemplate"]["spec"]["template"] if d["kind"] == "CronJob" else d["spec"]["template"]
    return (tmpl.get("metadata") or {}).get("labels") or {}


def selects(np: dict, labels: dict) -> bool:
    sel = np["spec"].get("podSelector") or {}
    ml = sel.get("matchLabels") or {}
    for e in sel.get("matchExpressions") or []:
        k, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and labels.get(k) in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return all(labels.get(k) == v for k, v in ml.items())


def uncovered(docs: list[dict]) -> list[str]:
    ns = lambda d: (d.get("metadata") or {}).get("namespace") or NAMESPACE
    nps = [d for d in docs if d.get("kind") == "NetworkPolicy" and ns(d) == NAMESPACE]
    out = []
    for d in docs:
        if d.get("kind") not in WORKLOADS or ns(d) != NAMESPACE:
            continue
        labels = pod_labels(d)
        if not any(selects(np, labels) for np in nps):
            out.append(f"{d['kind']}/{d['metadata']['name']}")
    return out


FIXTURE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: covered, namespace: gibson}
spec:
  template:
    metadata: {labels: {app: covered}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: naked, namespace: gibson}
spec:
  template:
    metadata: {labels: {app: naked}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: elsewhere, namespace: other}
spec:
  template:
    metadata: {labels: {app: elsewhere}}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: covered, namespace: gibson}
spec:
  podSelector: {matchLabels: {app: covered}}
"""


def selftest() -> int:
    got = uncovered([d for d in yaml.safe_load_all(FIXTURE) if d])
    if got != ["Deployment/naked"]:
        print(f"SELFTEST FAIL: want exactly Deployment/naked (not the covered one, not the other namespace), got {got}")
        return 1
    live = uncovered(render())
    if live:
        print("SELFTEST FAIL: the baseline render has an uncovered workload:\n  " + "\n  ".join(live))
        return 1
    print("OK: an unselected workload fails, a selected one and another namespace pass, the render is covered")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = uncovered(render())
    if got:
        print("❌ workloads in the release namespace no NetworkPolicy selects:\n  " + "\n  ".join(got))
        return 1
    print("✓ netpol-coverage: every workload in the release namespace is selected by a NetworkPolicy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
