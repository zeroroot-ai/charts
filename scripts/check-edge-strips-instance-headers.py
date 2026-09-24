#!/usr/bin/env python3
"""check-edge-strips-instance-headers.py — the edge never forwards a client's instance-selection headers.

Zitadel selects its instance from x-zitadel-instance-host, X-Forwarded-Host
or Forwarded, as well as from Host. Under ADR-0092 every in-cluster identity
client dials Zitadel by Service name and states the instance in
x-zitadel-instance-host, so Zitadel trusts that header. A client on the
internet must therefore never be able to supply it, or the generic forwarded
headers that do the same job.

Every route configuration in the rendered Envoy config removes the four
headers, with no exceptions and no allowlist: the rule is the same for every
listener, so a new listener or route table cannot slip past it.

The render is the baseline profile with the installer inputs, the same render
every other offline guard judges.

  check-edge-strips-instance-headers.py             exit 1 on a route config that forwards them, 0 when clean
  check-edge-strips-instance-headers.py --selftest  prove a missing or partial strip fails and a full one passes
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMESPACE = "gibson"
REQUIRED = ("x-zitadel-instance-host", "x-zitadel-public-host", "x-forwarded-host", "forwarded")


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def envoy_configs(docs: list[dict]) -> list[tuple[str, dict]]:
    """(ConfigMap name, parsed config) for every Envoy bootstrap the render ships as the edge."""
    out = []
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        name = d["metadata"]["name"]
        for value in (d.get("data") or {}).values():
            if "static_resources" not in value or "http_connection_manager" not in value:
                continue
            cfg = yaml.safe_load(value)
            if any(l.get("name") == "edge_tls" for l in cfg.get("static_resources", {}).get("listeners", [])):
                out.append((name, cfg))
    return out


def route_configs(cfg: dict):
    """Yield (listener, route config) for every inline route config."""
    for listener in cfg.get("static_resources", {}).get("listeners", []):
        for chain in listener.get("filter_chains", []):
            for f in chain.get("filters", []):
                rc = (f.get("typed_config") or {}).get("route_config")
                if rc is not None:
                    yield listener.get("name", "?"), rc


def audit(docs: list[dict]) -> list[str]:
    configs = envoy_configs(docs)
    if not configs:
        return ["no Envoy edge config (a listener named edge_tls) in the render: this guard is blind"]
    bad = []
    for cm, cfg in configs:
        seen = 0
        for listener, rc in route_configs(cfg):
            seen += 1
            removed = {h.lower() for h in rc.get("request_headers_to_remove") or []}
            missing = [h for h in REQUIRED if h not in removed]
            if missing:
                bad.append(f"{cm}: listener {listener}, route config {rc.get('name')!r} forwards {missing}")
        if seen == 0:
            bad.append(f"{cm}: no inline route config found: this guard is blind")
    return bad


def _fixture(remove: list[str] | None) -> list[dict]:
    rc = {"name": "public_routes", "virtual_hosts": [{"name": "app", "domains": ["*"], "routes": []}]}
    if remove is not None:
        rc["request_headers_to_remove"] = remove
    cfg = {"static_resources": {"listeners": [{
        "name": "edge_tls",
        "filter_chains": [{"filters": [{
            "name": "envoy.filters.network.http_connection_manager",
            "typed_config": {"route_config": rc},
        }]}],
    }]}}
    body = "# http_connection_manager\n" + yaml.safe_dump(cfg)
    return [{"kind": "ConfigMap", "metadata": {"name": "gibson-envoy"}, "data": {"envoy.yaml": body}}]


def selftest() -> int:
    if not audit(_fixture(None)):
        print("SELFTEST FAIL: a route config with no strip was not reported", file=sys.stderr)
        return 1
    if not audit(_fixture(["x-zitadel-instance-host", "x-forwarded-host"])):
        print("SELFTEST FAIL: a partial strip was not reported", file=sys.stderr)
        return 1
    if audit(_fixture([h.upper() for h in REQUIRED])):
        print("SELFTEST FAIL: a full strip (any case) was reported", file=sys.stderr)
        return 1
    if not audit([]):
        print("SELFTEST FAIL: a render with no edge config was not reported as blind", file=sys.stderr)
        return 1
    print("  ✓ selftest: a missing or partial strip fails, a full strip passes, a blind render fails")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = audit(render())
    if bad:
        print("the Envoy edge forwards instance-selection headers from clients:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        print("\nAdd request_headers_to_remove for " + ", ".join(REQUIRED), file=sys.stderr)
        print("to that route config in helm/gibson-workloads/files/envoy/ (ADR-0092).", file=sys.stderr)
        return 1
    print("  ✓ edge-strips-instance-headers: every route config removes the four headers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
