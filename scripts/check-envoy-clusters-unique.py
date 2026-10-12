#!/usr/bin/env python3
"""check-envoy-clusters-unique.py: each Envoy config of the chart names each cluster once (charts#655).

Envoy refuses a config that declares one cluster name twice, and the pod
crash-loops:

    cluster manager: duplicate cluster 'extra_gibson_billing_webhook_8090'

`helm template` and kubeconform do not read the config text inside the
ConfigMap, so no render check saw it. The hosted overlays of staging and
drill name four envoy.extraRoutes to one Service and port, and the chart
emitted one cluster for each route.

The check reads each ConfigMap whose data holds an Envoy config
(static_resources) and fails on a cluster name that appears twice. It reads:

  - every golden render (helm/testdata/golden/), and
  - a render of the baseline with tests/fixtures/envoy-clusters/
    routes-share-a-backend.yaml, the route shape of the hosted overlays.

  check-envoy-clusters-unique.py             exit 1 on a duplicate cluster
  check-envoy-clusters-unique.py --selftest  prove a duplicate fails and a clean config passes
"""
import collections
import glob
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join("tests", "fixtures", "envoy-clusters", "routes-share-a-backend.yaml")


def envoy_configs(docs):
    """(ConfigMap name, data key, parsed config) of each Envoy config."""
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") != "ConfigMap":
            continue
        for key, text in (d.get("data") or {}).items():
            if isinstance(text, str) and "static_resources" in text:
                yield d["metadata"]["name"], key, yaml.safe_load(text)


def duplicates(cfg: dict) -> list[str]:
    names = [c.get("name") for c in ((cfg.get("static_resources") or {}).get("clusters") or [])]
    return sorted(n for n, k in collections.Counter(names).items() if k > 1)


def judge(source: str, docs) -> tuple[list[str], int]:
    bad, seen = [], 0
    for cm, key, cfg in envoy_configs(docs):
        seen += 1
        bad += [f"{source}: ConfigMap {cm} {key}: cluster {n!r} is declared more than once" for n in duplicates(cfg)]
    return bad, seen


def fixture_render() -> str:
    return subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "-f", FIXTURE],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout


def selftest() -> int:
    def cm(names):
        cfg = {"static_resources": {"clusters": [{"name": n} for n in names]}}
        return [{"kind": "ConfigMap", "metadata": {"name": "e"}, "data": {"envoy.yaml": yaml.safe_dump(cfg)}}]
    bad, seen = judge("fixture", cm(["a", "extra_x_8090", "extra_x_8090"]))
    if seen != 1 or len(bad) != 1 or "extra_x_8090" not in bad[0]:
        print(f"SELFTEST FAIL: a cluster declared twice must give one finding, got {bad}")
        return 1
    bad, _ = judge("fixture", cm(["a", "extra_x_8090"]))
    if bad:
        print(f"SELFTEST FAIL: unique clusters must pass, got {bad}")
        return 1
    print("  ✓ selftest: a cluster declared twice fails, unique clusters pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(ROOT, "helm", "testdata", "golden", "*.yaml"))):
        b, s = judge(os.path.basename(f), yaml.safe_load_all(open(f)))
        bad, seen = bad + b, seen + s
    b, s = judge(FIXTURE, yaml.safe_load_all(fixture_render()))
    if s == 0:
        bad.append(f"{FIXTURE}: the render holds no Envoy config: this check is blind")
    bad, seen = bad + b, seen + s
    if seen == 0:
        bad.append("no render holds an Envoy config: this check is blind")
    if bad:
        print("Envoy refuses a config that declares a cluster twice (charts#655):", file=sys.stderr)
        for x in bad:
            print(f"  {x}", file=sys.stderr)
        return 1
    print(f"  ✓ envoy-clusters-unique: {seen} Envoy configs, each cluster declared once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
