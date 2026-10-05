#!/usr/bin/env python3
"""check-edge-grpc-routes.py: each gRPC route of the edge names a service that the daemon serves.

The edge routes a gRPC request by its path, `/<package>.<Service>/<Method>`.
A route for a service that no proto file defines matches no request. Its
rate limit and its auth rule then apply to nothing, and the real traffic
takes the catch-all route (charts#356, ADR-0094).

The guard renders the chart and reads each route match and each jwt_authn
rule match in each Envoy config. A match that names a service
(`prefix: /pkg.Service/`) or a method (`path: /pkg.Service/Method`) must be
in helm/contracts/gibson-grpc-methods.txt, the methods of the generated authz
registry of gibson at the pinned tag. A prefix that names no full service
(`/gibson.`) is a catch-all and passes.

  check-edge-grpc-routes.py             exit 1 on a finding, 0 when clean
  check-edge-grpc-routes.py --selftest  prove that a route for an unknown service fails
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, "helm", "contracts", "gibson-grpc-methods.txt")
NAMESPACE = "gibson"
# `/pkg.sub.v1.Service/` or `/pkg.sub.v1.Service/Method`: a dotted package, then a service name.
GRPC = re.compile(r"^/((?:[a-z][a-z0-9_]*\.)+[A-Z][A-Za-z0-9_]*)(?:/([A-Za-z0-9_]*))?$")
# The packages of the daemon. A gRPC path of a different owner is not in the registry.
OURS = "gibson."


def load_contract(path: str = CONTRACT) -> tuple[set[str], set[str]]:
    methods = {line.strip() for line in open(path) if line.startswith("/")}
    return {m.rsplit("/", 1)[0].lstrip("/") for m in methods}, methods


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def envoy_configs(docs: list[dict]) -> list[tuple[str, dict]]:
    out = []
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        for value in (d.get("data") or {}).values():
            if isinstance(value, str) and "static_resources" in value:
                out.append((d["metadata"]["name"], yaml.safe_load(value)))
    return out


def matches(node, where: str = ""):
    """Yield (where, kind, value) for each `match: {prefix|path: ...}` in the config."""
    if isinstance(node, dict):
        m = node.get("match")
        if isinstance(m, dict):
            for kind in ("prefix", "path", "path_separated_prefix"):
                if isinstance(m.get(kind), str):
                    yield where, kind, m[kind]
        for k, v in node.items():
            yield from matches(v, node.get("name") if isinstance(node.get("name"), str) and k in ("routes", "rules", "virtual_hosts") else where)
    elif isinstance(node, list):
        for v in node:
            yield from matches(v, where)


def audit(configs: list[tuple[str, dict]], services: set[str], methods: set[str]) -> tuple[list[str], int]:
    bad, seen = [], 0
    for cm, cfg in configs:
        for where, kind, value in matches(cfg):
            m = GRPC.match(value)
            if not m or not m.group(1).startswith(OURS):
                continue
            seen += 1
            service, method = m.group(1), m.group(2)
            if service not in services:
                bad.append(f"{cm}: {where or 'route'}: {kind} {value!r} names the service {service}, "
                           "and the daemon serves no such service")
            elif method and kind == "path" and value not in methods:
                bad.append(f"{cm}: {where or 'route'}: {kind} {value!r} names a method that the daemon does not serve")
    if not seen:
        bad.append("no gRPC route in the rendered Envoy config: this guard is blind")
    return bad, seen


FIXTURE = """
static_resources:
  listeners:
    - filter_chains:
        - filters:
            - typed_config:
                route_config:
                  virtual_hosts:
                    - name: api
                      routes:
                        - match: { prefix: "/gibson.daemon.v1.DaemonService/" }
                          route: { cluster: daemon }
                        - match: { path: "/gibson.daemon.v1.DaemonService/Ping" }
                          route: { cluster: daemon }
                        - match: { prefix: "/gibson." }
                          route: { cluster: daemon }
                        - match: { prefix: "/zitadel.user.v2.UserService/" }
                          route: { cluster: other }
                        - match: { prefix: "/" }
                          route: { cluster: web }
"""


def selftest() -> int:
    services, methods = {"gibson.daemon.v1.DaemonService"}, {"/gibson.daemon.v1.DaemonService/Ping"}
    base = yaml.safe_load(FIXTURE)
    routes = base["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"][
        "route_config"]["virtual_hosts"][0]["routes"]
    got, seen = audit([("fixture", base)], services, methods)
    if got or seen != 2:
        print(f"SELFTEST FAIL: the clean fixture must pass with 2 gRPC routes, got {seen}: {got}")
        return 1
    for what, route in {
        "a prefix for a service in the wrong package": {"match": {"prefix": "/gibson.user.v1.UserService/"}},
        "a prefix for a service that does not exist": {"match": {"prefix": "/gibson.tenant.v1.TenantAdminService/"}},
        "a path for a method that does not exist": {"match": {"path": "/gibson.daemon.v1.DaemonService/Gone"}},
        "a path for a method of an unknown service": {"match": {"path": "/gibson.budget.v1.BudgetService/Get"}},
    }.items():
        routes.insert(0, route)
        got, _ = audit([("fixture", base)], services, methods)
        routes.pop(0)
        if len(got) != 1:
            print(f"SELFTEST FAIL: {what}: want one finding, got {got}")
            return 1
    if not audit([("fixture", {"static_resources": {}})], services, methods)[0]:
        print("SELFTEST FAIL: a config with no gRPC route must fail as blind")
        return 1
    print("  ✓ selftest: 4 routes for an unknown service or method fail, a blind config fails, the clean config passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    services, methods = load_contract()
    if len(methods) < 100:
        print(f"::error::{os.path.relpath(CONTRACT, ROOT)} holds {len(methods)} method(s): the contract is empty or broken",
              file=sys.stderr)
        return 1
    bad, seen = audit(envoy_configs(render()), services, methods)
    if bad:
        print("an edge route names a gRPC service or method that the daemon does not serve (charts#356):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ edge-grpc-routes: {seen} gRPC route and auth matches, each names a service of the authz registry "
          f"({len(services)} services, {len(methods)} methods)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
