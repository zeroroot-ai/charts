#!/usr/bin/env python3
"""check-edge-config-identical.py — the Envoy edge renders identically on every substrate.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, ADR-0011: one
edge, one substrate-independent configuration). The Envoy ConfigMaps the
umbrella renders must be byte-identical across the baseline profile and
every substrate overlay (eks, gke, aks, guest). A substrate that needs a
different edge is a second edge, and ADR-0011 forbids that.

  check-edge-config-identical.py             exit 1 on a divergence, 0 when identical
  check-edge-config-identical.py --selftest  prove a divergent overlay fails
"""
import hashlib
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = ["helm/gibson/values-baseline.yaml", "helm/testdata/render-inputs/gibson.yaml"]
OVERLAYS = ["values-eks.yaml", "values-gke.yaml", "values-aks.yaml", "values-guest.yaml"]


def edge_digests(extra: list[str]) -> dict[str, str]:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson"]
    for v in BASE + extra:
        args += ["-f", v]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return digests([d for d in yaml.safe_load_all(out) if d])


def digests(docs: list[dict]) -> dict[str, str]:
    out = {}
    for d in docs:
        if d.get("kind") == "ConfigMap" and "envoy" in d["metadata"]["name"]:
            out[d["metadata"]["name"]] = hashlib.sha256(yaml.dump(d.get("data") or {}, sort_keys=True).encode()).hexdigest()
    return out


def judge(base: dict[str, str], others: dict[str, dict[str, str]]) -> list[str]:
    out = []
    if not base:
        return ["the baseline renders no Envoy ConfigMap; the guard has nothing to judge"]
    for name, dig in others.items():
        for cm, h in base.items():
            if cm not in dig:
                out.append(f"{name}: does not render {cm}")
            elif dig[cm] != h:
                out.append(f"{name}: {cm} differs from the baseline (one edge, ADR-0011)")
        for cm in dig:
            if cm not in base:
                out.append(f"{name}: renders {cm}, which the baseline does not")
    return out


def selftest() -> int:
    base = {"gibson-envoy": "aaa"}
    if judge(base, {"same": {"gibson-envoy": "aaa"}}):
        print("SELFTEST FAIL: an identical overlay must pass")
        return 1
    got = judge(base, {"diverged": {"gibson-envoy": "bbb"}, "missing": {}})
    if len(got) != 2:
        print(f"SELFTEST FAIL: a divergent and a missing ConfigMap must both fail, got {got}")
        return 1
    if not judge({}, {}):
        print("SELFTEST FAIL: no Envoy ConfigMap must fail")
        return 1
    live = judge(edge_digests([]), {o: edge_digests([f"helm/gibson/{o}"]) for o in OVERLAYS})
    if live:
        print("SELFTEST FAIL: the edge diverges across substrates:\n  " + "\n  ".join(live))
        return 1
    print("OK: a divergent overlay and a missing ConfigMap fail; the edge is identical on every substrate")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(edge_digests([]), {o: edge_digests([f"helm/gibson/{o}"]) for o in OVERLAYS})
    if got:
        print("❌ the Envoy edge is not one configuration:\n  " + "\n  ".join(got))
        return 1
    print("✓ edge-config-identical: the Envoy ConfigMaps are identical on baseline, eks, gke, aks and guest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
