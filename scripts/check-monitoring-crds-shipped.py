#!/usr/bin/env python3
"""check-monitoring-crds-shipped.py: each monitoring.coreos.com kind that the umbrella renders has its CRD in gibson-crds.

The monitoring templates render when the cluster serves the API version
monitoring.coreos.com/v1. gibson-crds ships a trimmed set of the Prometheus
operator CRDs (helm/gibson-crds/values.yaml). A template for a kind outside
that set renders on a cluster that has the group, and the Argo sync then fails
with `PodMonitor.monitoring.coreos.com "" not found` (the Envoy PodMonitor,
2026-10-09, every kind exit test). This guard renders the umbrella with the
API version on, renders gibson-crds, and fails when a rendered kind of the
group has no CRD.

  check-monitoring-crds-shipped.py             exit 1 on a finding, 0 when clean
  check-monitoring-crds-shipped.py --selftest  prove a missing CRD fails
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUP = "monitoring.coreos.com"


def helm(*args: str) -> list[dict]:
    out = subprocess.run(["helm", "template", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def judge(umbrella: list[dict], crds: list[dict]) -> list[str]:
    shipped = {d["spec"]["names"]["kind"] for d in crds
               if d.get("kind") == "CustomResourceDefinition" and d["spec"].get("group") == GROUP}
    used = {d["kind"] for d in umbrella if str(d.get("apiVersion", "")).startswith(GROUP + "/")}
    out = [f"the umbrella renders a {k} and gibson-crds ships no {k} CRD" for k in sorted(used - shipped)]
    if not used:
        out.append(f"the umbrella renders no {GROUP} object; the API version is missing from the template call")
    return out


def render() -> tuple[list[dict], list[dict]]:
    umbrella = helm("gibson", "helm/gibson", "-f", "helm/gibson/values-baseline.yaml",
                    "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson",
                    "--api-versions", f"{GROUP}/v1")
    return umbrella, helm("gibson-crds", "helm/gibson-crds")


def selftest() -> int:
    umbrella, crds = render()
    got = judge(umbrella, crds)
    if got:
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(got))
        return 1
    # THE FIXTURE THIS EXISTS FOR: the PodMonitor CRD is not shipped.
    trimmed = [d for d in copy.deepcopy(crds)
               if not (d.get("kind") == "CustomResourceDefinition" and d["spec"]["names"]["kind"] == "PodMonitor")]
    got = judge(umbrella, trimmed)
    if got != ["the umbrella renders a PodMonitor and gibson-crds ships no PodMonitor CRD"]:
        print(f"SELFTEST FAIL: a missing PodMonitor CRD must fail once, got {got}")
        return 1
    print("selftest ok: a missing CRD fails")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    bad = judge(*render())
    for b in bad:
        print(b)
    print("monitoring CRDs shipped: ok" if not bad else "")
    sys.exit(1 if bad else 0)
