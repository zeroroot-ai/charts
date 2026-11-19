#!/usr/bin/env python3
"""check-namespace-wave.py: a namespace the chart creates syncs before each object in it.

WHY THIS EXISTS
Argo CD applies the objects of a sync by phase, then by sync wave. The
umbrella creates some namespaces itself (the agentgateway namespace, the
setec namespaces, the SPIRE node namespace). An object in one of them with a
lower wave than the Namespace, or in an earlier phase, reaches the API server
before its namespace exists. The review of charts#818 found the network
policy of the proxy at wave -20 and the gateway Namespace at wave -10.

Argo orders the objects of one wave by kind, and its sort moves a Namespace
ahead of the first object that names it (gitops-engine pkg/sync/sync_tasks.go,
adjustDeps). So an object at the SAME wave as its Namespace is safe. The
chart does not lean on the move across waves: a lower wave fails here.

WHAT IT CHECKS, on every rendered golden (helm/testdata/golden/*.yaml)
  For each Namespace that a render holds, each object of that namespace:
    1. has a sync wave at or above the wave of the Namespace, and
    2. is not a PreSync hook (argocd.argoproj.io/hook: PreSync, or a Helm
       pre-install / pre-upgrade hook), because a hook runs in an earlier
       phase than the Namespace.

USAGE
  scripts/check-namespace-wave.py             check the goldens
  scripts/check-namespace-wave.py --selftest  prove a late namespace fails
Exit: 0 pass, 1 a namespace syncs too late, 2 self-test broke
"""
from __future__ import annotations

import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden")
WAVE = "argocd.argoproj.io/sync-wave"
EARLY_HOOKS = {"PreSync"}
EARLY_HELM_HOOKS = {"pre-install", "pre-upgrade"}


def wave(d: dict) -> int:
    return int(((d.get("metadata") or {}).get("annotations") or {}).get(WAVE, "0"))


def early_hook(d: dict) -> bool:
    ann = (d.get("metadata") or {}).get("annotations") or {}
    argo = {h.strip() for h in str(ann.get("argocd.argoproj.io/hook", "")).split(",") if h.strip()}
    helm = {h.strip() for h in str(ann.get("helm.sh/hook", "")).split(",") if h.strip()}
    return bool(argo & EARLY_HOOKS or (not argo and helm & EARLY_HELM_HOOKS))


def judge(docs: list[dict]) -> list[str]:
    spaces = {d["metadata"]["name"]: wave(d) for d in docs if d.get("kind") == "Namespace"}
    bad = []
    for d in docs:
        md = d.get("metadata") or {}
        ns = md.get("namespace")
        if d.get("kind") == "Namespace" or ns not in spaces:
            continue
        name = f"{d.get('kind')}/{md.get('name')} in {ns}"
        if wave(d) < spaces[ns]:
            bad.append(f"{name}: wave {wave(d)} is below the wave {spaces[ns]} of its Namespace")
        if early_hook(d):
            bad.append(f"{name}: a PreSync hook runs before the Namespace that the chart creates")
    return bad


def load(path: str) -> list[dict]:
    with open(path) as f:
        return [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]


def selftest() -> int:
    path = next(p for p in sorted(glob.glob(os.path.join(GOLDEN, "values-baseline*.yaml"))))
    docs = load(path)
    if judge(docs):
        print("selftest: the baseline golden already fails:\n  " + "\n  ".join(judge(docs)))
        return 2
    ns = next(d for d in docs if d.get("kind") == "Namespace" and any(
        (m.get("metadata") or {}).get("namespace") == d["metadata"]["name"] for m in docs))
    name = ns["metadata"]["name"]
    member = next(d for d in docs if (d.get("metadata") or {}).get("namespace") == name)

    def lower(ds):
        m = next(d for d in ds if d is not None and d.get("metadata", {}).get("name") == member["metadata"]["name"]
                 and d.get("kind") == member.get("kind") and d["metadata"].get("namespace") == name)
        m["metadata"].setdefault("annotations", {})[WAVE] = str(wave(ns) - 1)

    def hook(ds):
        m = next(d for d in ds if d.get("metadata", {}).get("name") == member["metadata"]["name"]
                 and d.get("kind") == member.get("kind") and d["metadata"].get("namespace") == name)
        m["metadata"].setdefault("annotations", {})["argocd.argoproj.io/hook"] = "PreSync"

    def raise_ns(ds):
        n = next(d for d in ds if d.get("kind") == "Namespace" and d["metadata"]["name"] == name)
        n["metadata"].setdefault("annotations", {})[WAVE] = str(max(wave(d) for d in ds
                                                                 if d.get("metadata", {}).get("namespace") == name) + 1)

    failed = []
    for label, mutate in (("an object below the wave of its Namespace", lower),
                          ("a PreSync hook in a namespace that the chart creates", hook),
                          ("a Namespace above the wave of its objects", raise_ns)):
        ds = copy.deepcopy(docs)
        mutate(ds)
        if not judge(ds):
            failed.append(label)
    if failed:
        print("selftest: these fixtures pass:\n  " + "\n  ".join(failed))
        return 2
    print(f"✓ selftest: 3 fixtures fail on {os.path.basename(path)} (namespace {name})")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    bad, seen = [], set()
    for path in sorted(glob.glob(os.path.join(GOLDEN, "*.yaml"))):
        docs = load(path)
        seen |= {d["metadata"]["name"] for d in docs if d.get("kind") == "Namespace"}
        bad += [f"{os.path.basename(path)}: {b}" for b in judge(docs)]
    if bad:
        print("❌ an object syncs before the Namespace that the chart creates for it:\n  " + "\n  ".join(bad))
        return 1
    print(f"✓ namespace-wave: each object of {sorted(seen)} syncs at or after its Namespace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
