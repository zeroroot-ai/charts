#!/usr/bin/env python3
"""check-servicemonitor-tls.py — a ServiceMonitor verifies the certificate it scrapes.

`insecureSkipVerify: true` on a ServiceMonitor endpoint turns a TLS-only
metrics port into a channel Prometheus will speak to any responder on. The
daemon's metrics Secret carries the issuing CA in ca.crt, so every https
endpoint names a CA Secret and a serverName instead. This guard renders the
umbrella and fails on any endpoint that skips verification, or on any https
endpoint with no CA.

  check-servicemonitor-tls.py             exit 1 on a violation, 0 when clean
  check-servicemonitor-tls.py --selftest  prove insecureSkipVerify fails
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson", "--api-versions", "monitoring.coreos.com/v1"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def judge(docs: list[dict]) -> list[str]:
    out = []
    seen = 0
    for d in docs:
        if d.get("kind") != "ServiceMonitor":
            continue
        seen += 1
        name = d["metadata"]["name"]
        for ep in d.get("spec", {}).get("endpoints", []):
            tls = ep.get("tlsConfig") or {}
            if tls.get("insecureSkipVerify"):
                out.append(f"ServiceMonitor {name} endpoint {ep.get('port')}: insecureSkipVerify is set")
            if ep.get("scheme") == "https" and not (tls.get("ca") or tls.get("caFile")):
                out.append(f"ServiceMonitor {name} endpoint {ep.get('port')}: https with no CA")
    if seen == 0:
        out.append("no ServiceMonitor in the render; the monitoring API version is missing from the template call")
    return out


def selftest() -> int:
    docs = render()
    if judge(docs):
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(judge(docs)))
        return 1
    sms = [d for d in docs if d.get("kind") == "ServiceMonitor"]
    # THE FIXTURE THIS EXISTS FOR: the skip flag returns.
    bad = copy.deepcopy(sms)
    bad[0]["spec"]["endpoints"][0].setdefault("tlsConfig", {})["insecureSkipVerify"] = True
    got = judge(bad)
    if len(got) != 1 or "insecureSkipVerify" not in got[0]:
        print(f"SELFTEST FAIL: insecureSkipVerify must fail once, got {got}")
        return 1
    # An https endpoint that forgets its CA fails too.
    noca = copy.deepcopy(sms)
    for d in noca:
        for ep in d["spec"]["endpoints"]:
            if ep.get("scheme") == "https":
                ep["tlsConfig"] = {"serverName": "x"}
    got = judge(noca)
    if not got or not all("no CA" in g for g in got):
        print(f"SELFTEST FAIL: an https endpoint with no CA must fail, got {got}")
        return 1
    print(f"OK: insecureSkipVerify fails, https without a CA fails, {len(sms)} ServiceMonitors in the baseline pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(render())
    if got:
        print("❌ a ServiceMonitor scrapes without verifying the certificate:\n  " + "\n  ".join(got))
        return 1
    print("✓ servicemonitor-tls: every https scrape names its CA and none skips verification")
    return 0


if __name__ == "__main__":
    sys.exit(main())
