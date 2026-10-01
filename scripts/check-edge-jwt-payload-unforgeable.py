#!/usr/bin/env python3
"""check-edge-jwt-payload-unforgeable.py — a client can never supply x-jwt-payload.

ext_authz reads a person's identity from the x-jwt-payload header and from
nothing else. Only Envoy's jwt_authn filter may write that header, after it
verifies a Zitadel JWT. A client that sends its own x-jwt-payload must lose it
before ext_authz runs.

Envoy does this for us, but only under conditions the config must keep. In
Envoy v1.34 (source/extensions/filters/http/jwt_authn/verifier.cc) every
verifier calls Extractor::sanitizeHeaders(), which removes each provider's
forward_payload_header, before it checks a token:

  - provider_name / provider_and_audiences: removes THAT provider's header only;
  - allow_missing / allow_missing_or_failed: removes every provider's header;
  - an empty requirement (AllowAllVerifier): removes NOTHING;
  - a path no rule matches: jwt_authn does nothing at all.

So, on every listener whose filter chain runs ext_authz:

  1. jwt_authn runs before ext_authz;
  2. every provider forwards its payload to x-jwt-payload (a provider-only
     verifier strips only its own header, so all must name the trusted one);
  3. every rule, and every requirement nested in requires_any/requires_all,
     is non-empty, and no requirement_map or filter_state_rules exists;
  4. the last rule matches prefix "/", so every path meets a verifier;
  5. no route or virtual host overrides jwt_authn, unless the same scope also
     disables ext_authz and every route in it answers locally (direct_response
     or redirect). Then the client's header reaches neither ext_authz nor an
     upstream; the edge's 421 "misdirected" host is this case.

The render is the baseline profile with the installer inputs, the same render
every other offline guard judges.

  check-edge-jwt-payload-unforgeable.py             exit 1 on a finding, 0 when clean
  check-edge-jwt-payload-unforgeable.py --selftest  prove each kind of finding fails and a clean config passes
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMESPACE = "gibson"
TRUSTED_HEADER = "x-jwt-payload"
JWT = "envoy.filters.http.jwt_authn"
EXT_AUTHZ = "envoy.filters.http.ext_authz"


def repack() -> None:
    """Rebuild the first-party sub-chart tarballs from source, as scripts/golden.sh does.

    The umbrella renders packaged tarballs, and .charts.stamp tracks dependency
    sources, not templates: without this, an edit to an Envoy file renders the
    stale tarball and the guard judges the old config.
    """
    charts = os.path.join(ROOT, "helm", "gibson", "charts")
    for name in os.listdir(charts) if os.path.isdir(charts) else []:
        if name.startswith(("gibson-workloads-", "gibson-operators-", "gibson-crds-", "gibson-common-")) \
                and name.endswith(".tgz"):
            os.remove(os.path.join(charts, name))
    helm_dir = os.path.join(ROOT, "helm")
    for chart in os.listdir(helm_dir):
        stamp = os.path.join(helm_dir, chart, ".charts.stamp")
        if os.path.exists(stamp):
            os.remove(stamp)
    subprocess.run(["make", "-C", ROOT, "chart-deps"], check=True, capture_output=True, text=True)


def render() -> list[dict]:
    repack()
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


def connection_managers(cfg: dict):
    """Yield (label, typed_config) for every HTTP connection manager."""
    for listener in cfg.get("static_resources", {}).get("listeners", []):
        for chain in listener.get("filter_chains", []):
            for f in chain.get("filters", []):
                if f.get("name") == "envoy.filters.network.http_connection_manager":
                    tc = f.get("typed_config") or {}
                    yield f"{listener.get('name', '?')}/{tc.get('stat_prefix', '?')}", tc


def requirement_findings(req, where: str) -> list[str]:
    """An empty requirement is Envoy's AllowAllVerifier, which strips nothing."""
    if not isinstance(req, dict) or not req:
        return [f"{where}: empty requirement (allow-all: x-jwt-payload is not stripped)"]
    bad = []
    for key in ("requires_any", "requires_all"):
        if key in req:
            inner = (req[key] or {}).get("requirements") or []
            if not inner:
                bad.append(f"{where}: {key} with no requirements")
            for i, r in enumerate(inner):
                bad += requirement_findings(r, f"{where}.{key}[{i}]")
    return bad


def _sealed(scope: dict, routes: list[dict]) -> bool:
    """True when scope disables ext_authz and every route answers locally."""
    ea = (scope.get("typed_per_filter_config") or {}).get(EXT_AUTHZ) or {}
    local = all(("direct_response" in r or "redirect" in r) and "route" not in r for r in routes)
    return bool(ea.get("disabled")) and bool(routes) and local


def audit(docs: list[dict]) -> list[str]:
    configs = envoy_configs(docs)
    if not configs:
        return ["no Envoy edge config (a listener named edge_tls) in the render: this guard is blind"]
    bad = []
    for cm, cfg in configs:
        guarded = 0
        for label, tc in connection_managers(cfg):
            names = [h.get("name") for h in tc.get("http_filters") or []]
            if EXT_AUTHZ not in names:
                continue
            guarded += 1
            where = f"{cm}: {label}"
            if JWT not in names or names.index(JWT) > names.index(EXT_AUTHZ):
                bad.append(f"{where}: ext_authz runs without jwt_authn before it")
                continue
            jwt = (tc["http_filters"][names.index(JWT)].get("typed_config")) or {}
            providers = jwt.get("providers") or {}
            if not providers:
                bad.append(f"{where}: jwt_authn has no providers")
            for pname, p in providers.items():
                header = (p.get("forward_payload_header") or "").lower()
                if header != TRUSTED_HEADER:
                    bad.append(f"{where}: provider {pname} forwards its payload to {header or 'nothing'!r}, "
                               f"not {TRUSTED_HEADER!r}, so a route that requires it leaves a client's "
                               f"{TRUSTED_HEADER} in place")
            for key in ("requirement_map", "filter_state_rules"):
                if jwt.get(key):
                    bad.append(f"{where}: jwt_authn uses {key}, which this guard cannot judge")
            rules = jwt.get("rules") or []
            if not rules:
                bad.append(f"{where}: jwt_authn has no rules, so no path meets a verifier")
            for i, rule in enumerate(rules):
                bad += requirement_findings(rule.get("requires"), f"{where}: rule {i} {rule.get('match')}")
            if rules and (rules[-1].get("match") or {}) != {"prefix": "/"}:
                bad.append(f"{where}: the last jwt_authn rule is {rules[-1].get('match')}, not prefix \"/\", "
                           f"so some path meets no verifier")
            for vh in (tc.get("route_config") or {}).get("virtual_hosts") or []:
                routes = vh.get("routes") or []
                if JWT in (vh.get("typed_per_filter_config") or {}) and not _sealed(vh, routes):
                    bad.append(f"{where}: virtual host {vh.get('name')} overrides jwt_authn and can still reach "
                               f"ext_authz or an upstream")
                for r in routes:
                    if JWT in (r.get("typed_per_filter_config") or {}) and not _sealed(r, [r]):
                        bad.append(f"{where}: route {r.get('match')} in {vh.get('name')} overrides jwt_authn and "
                                   f"can still reach ext_authz or an upstream")
        if guarded == 0:
            bad.append(f"{cm}: no connection manager runs ext_authz: this guard is blind")
    return bad


def _clean() -> dict:
    return {
        "name": "envoy.filters.network.http_connection_manager",
        "typed_config": {
            "stat_prefix": "api_ingress",
            "route_config": {"name": "api_routes", "virtual_hosts": [{"name": "api", "routes": [
                {"match": {"prefix": "/"}, "route": {"cluster": "daemon"}}]}]},
            "http_filters": [
                {"name": JWT, "typed_config": {
                    "providers": {
                        "zitadel_dashboard": {"forward_payload_header": TRUSTED_HEADER},
                        "zitadel_s2s": {"forward_payload_header": TRUSTED_HEADER},
                    },
                    "rules": [
                        {"match": {"prefix": "/gibson.identity.v1.IdentityService/"}, "requires": {"requires_any": {
                            "requirements": [{"provider_name": "zitadel_dashboard"},
                                             {"allow_missing_or_failed": {}}]}}},
                        {"match": {"prefix": "/"}, "requires": {"provider_name": "zitadel_s2s"}},
                    ],
                }},
                {"name": EXT_AUTHZ, "typed_config": {}},
                {"name": "envoy.filters.http.router", "typed_config": {}},
            ],
        },
    }


def _misdirected(ext_authz_off: bool) -> dict:
    per = {JWT: {"disabled": True}}
    if ext_authz_off:
        per[EXT_AUTHZ] = {"disabled": True}
    return {"name": "misdirected", "domains": ["*"], "typed_per_filter_config": per,
            "routes": [{"match": {"prefix": "/"}, "direct_response": {"status": 421}}]}


def _docs(hcm: dict) -> list[dict]:
    cfg = {"static_resources": {"listeners": [{"name": "edge_tls", "filter_chains": [{"filters": [hcm]}]}]}}
    body = "# http_connection_manager\n" + yaml.safe_dump(cfg)
    return [{"kind": "ConfigMap", "metadata": {"name": "gibson-envoy"}, "data": {"envoy.yaml": body}}]


def selftest() -> int:
    def broken(mutate):
        h = copy.deepcopy(_clean())
        mutate(h["typed_config"])
        return h

    def jwt(tc):
        return tc["http_filters"][0]["typed_config"]

    cases = {
        "jwt_authn after ext_authz": lambda tc: tc["http_filters"].reverse(),
        "no jwt_authn": lambda tc: tc["http_filters"].pop(0),
        "a provider forwards elsewhere": lambda tc: jwt(tc)["providers"]["zitadel_s2s"].update(
            forward_payload_header="x-other"),
        "a provider forwards nothing": lambda tc: jwt(tc)["providers"]["zitadel_s2s"].pop("forward_payload_header"),
        "an empty rule requirement": lambda tc: jwt(tc)["rules"][0].update(requires={}),
        "a rule with no requirement": lambda tc: jwt(tc)["rules"][0].pop("requires"),
        "an empty nested requirement": lambda tc: jwt(tc)["rules"][0]["requires"]["requires_any"][
            "requirements"].append({}),
        "no catch-all rule": lambda tc: jwt(tc)["rules"].pop(),
        "a requirement_map": lambda tc: jwt(tc).update(requirement_map={"x": {"provider_name": "zitadel_s2s"}}),
        "a per-route jwt_authn override": lambda tc: tc["route_config"]["virtual_hosts"][0]["routes"][0].update(
            typed_per_filter_config={JWT: {"disabled": True}}),
        "a per-vhost jwt_authn override": lambda tc: tc["route_config"]["virtual_hosts"][0].update(
            typed_per_filter_config={JWT: {"disabled": True}}),
        "a jwt_authn override with ext_authz off that still forwards upstream":
            lambda tc: tc["route_config"]["virtual_hosts"][0].update(
                typed_per_filter_config={JWT: {"disabled": True}, EXT_AUTHZ: {"disabled": True}}),
        "a jwt_authn override on a local-answer host that keeps ext_authz":
            lambda tc: tc["route_config"]["virtual_hosts"].append(_misdirected(ext_authz_off=False)),
    }
    for label, mutate in cases.items():
        if not audit(_docs(broken(mutate))):
            print(f"SELFTEST FAIL: {label} was not reported", file=sys.stderr)
            return 1
    if audit(_docs(_clean())):
        print(f"SELFTEST FAIL: the clean config was reported: {audit(_docs(_clean()))}", file=sys.stderr)
        return 1
    sealed = broken(lambda tc: tc["route_config"]["virtual_hosts"].append(_misdirected(ext_authz_off=True)))
    if audit(_docs(sealed)):
        print(f"SELFTEST FAIL: a sealed 421 host was reported: {audit(_docs(sealed))}", file=sys.stderr)
        return 1
    if not audit([]):
        print("SELFTEST FAIL: a render with no edge config was not reported as blind", file=sys.stderr)
        return 1
    print(f"  ✓ selftest: {len(cases)} broken edges fail, the clean edge and a sealed 421 host pass, "
          f"a blind render fails")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = audit(render())
    if bad:
        print(f"a client could supply {TRUSTED_HEADER}, which ext_authz trusts as the caller's identity:",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        print("\nSee the docstring of scripts/check-edge-jwt-payload-unforgeable.py for the five rules.",
              file=sys.stderr)
        return 1
    print(f"  ✓ edge-jwt-payload-unforgeable: every ext_authz listener strips a client's {TRUSTED_HEADER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
