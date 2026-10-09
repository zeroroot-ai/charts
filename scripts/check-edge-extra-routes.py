#!/usr/bin/env python3
"""check-edge-extra-routes.py: each extra edge route states its authentication mode (charts#375).

envoy.extraRoutes is the one way a component outside the core chart gets an
edge route (ADR-0060, ADR-0074). The edge adds no authentication to such a
route, so each entry must state who authenticates the request:
signed-request, own-sign-in or query-token. There is no default.

The check renders the umbrella:

  1. with no entry: the browser chain holds no extra route and no extra
     cluster, and no billing object renders;
  2. with one complete entry: exactly one route on the browser chain and one
     cluster, with the stated rate-limit descriptor, and an entry of each
     other mode (own-sign-in, query-token) renders;
  3. with an entry that states no auth mode, an unknown mode, or no
     rate-limit class: the render fails.

  check-edge-extra-routes.py   exit 1 on a finding
"""
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOOD = {"path": "/api/billing/webhook", "rewrite": "/webhook", "service": "gibson-billing-webhook",
        "port": 8090, "rateLimitClass": "catch-all", "auth": "signed-request"}


def render(routes):
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml"]
    if routes is not None:
        args += ["--set-json", "gibson-workloads.envoy.extraRoutes=" + json.dumps(routes)]
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def envoy_config(out: str) -> str:
    for d in yaml.safe_load_all(out):
        if d and d.get("kind") == "ConfigMap" and "envoy" in d["metadata"]["name"]:
            for v in (d.get("data") or {}).values():
                if "static_resources" in v:
                    return v
    return ""


def main() -> int:
    bad = []
    rc, out, err = render(None)
    if rc != 0:
        print(f"the baseline does not render: {err[-400:]}", file=sys.stderr)
        return 1
    cfg = envoy_config(out)
    if not cfg:
        bad.append("no Envoy configuration in the render: this check is blind")
    if "extra_" in cfg or "gibson_billing_webhook" in cfg:
        bad.append("the baseline Envoy configuration holds an extra or billing cluster with no entry")
    kinds = [(d.get("kind"), d["metadata"]["name"]) for d in yaml.safe_load_all(out) if d]
    if any("billing" in n or "stripe" in n for _, n in kinds):
        bad.append(f"the baseline renders a billing object: {[k for k in kinds if 'billing' in k[1] or 'stripe' in k[1]]}")
    rc, out, err = render([GOOD])
    cfg = envoy_config(out) if rc == 0 else ""
    if rc != 0:
        bad.append(f"a complete entry does not render: {err[-300:]}")
    else:
        if cfg.count("- name: extra_gibson_billing_webhook_8090") != 1:
            bad.append("a complete entry must render exactly one cluster")
        if cfg.count('path: "/api/billing/webhook"') != 1:
            bad.append("a complete entry must render exactly one route on the browser chain")
    for mode in ("own-sign-in", "query-token"):
        rc, _, err = render([dict(GOOD, auth=mode)])
        if rc != 0:
            bad.append(f"an entry with the auth mode {mode} does not render: {err[-300:]}")
    for what, entry in (("no auth mode", {k: v for k, v in GOOD.items() if k != "auth"}),
                        ("an unknown auth mode", dict(GOOD, auth="none")),
                        ("no rate-limit class", {k: v for k, v in GOOD.items() if k != "rateLimitClass"})):
        rc, _, err = render([entry])
        if rc == 0:
            bad.append(f"an entry with {what} rendered; it must fail")
    if bad:
        print("envoy.extraRoutes is not held to its typed shape (charts#375):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print("  ✓ edge-extra-routes: no extra route or billing object by default; one entry renders one route and one "
          "cluster; no auth mode, an unknown mode and no rate-limit class each fail the render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
