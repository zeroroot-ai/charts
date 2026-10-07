#!/usr/bin/env python3
"""check-edge-misdirected-authority.py: every browser-facing edge chain answers a coalesced request with 421.

Every public hostname on the edge (api., app., docs., the apex) shares one
certificate and one address. A browser therefore reuses the HTTP/2 connection
it opened to one of them for a request to another (RFC 9113 section 9.1.1,
"connection coalescing"). Envoy picks the filter chain by the connection's
SNI, not by the request's :authority, so the request lands on a chain that
has no virtual host for it. Without a rule for that case the chain answers
404, or 401 from jwt_authn on the api chain, and the browser shows an error
page. Measured 2026-09-29 on staging: the GitLab connector's OAuth redirect to
api.<domain>/connectors/oauth/callback rode the browser's app.<domain>
connection and got the public chain's 404.

The rule (RFC 9113 section 9.1.2): the chain answers 421 Misdirected Request,
and the browser retries on a new connection with the right SNI. This guard
checks the rendered edge config for that rule:

1. Every TLS chain on the edge listener that browsers can reach (a chain with
   no source-address restriction) has exactly one virtual host for "*", and
   that virtual host does one thing: a direct 421 for every path. It never
   routes and never redirects, so it can never become an unauthenticated
   route (deploy#1204).
2. On a chain that carries jwt_authn or ext_authz, that virtual host turns
   both off. A coalesced request carries no token for the host it names, and
   a 401 would end the browser's retry before it starts.
3. No chain is restricted by source address. The auth-free in-cluster chain
   that was (deploy#1204) is deleted (charts#163, ADR-0092), and a chain
   selected by source address is how it would come back.

  check-edge-misdirected-authority.py             exit 1 on a finding, 0 when clean
  check-edge-misdirected-authority.py --selftest  prove each kind of finding fails
  check-edge-misdirected-authority.py --live      send coalesced requests to a running edge

--live reads these environment variables:
  EDGE_URL       the app origin, for example https://app.gibson.local:30443 (required)
  API_HOST       the api host name, for example api.gibson.local (required)
  EDGE_RESOLVE   optional curl --resolve values, comma separated,
                 for example app.gibson.local:30443:127.0.0.1,api.gibson.local:30443:127.0.0.1
  EDGE_INSECURE  set to 1 to skip TLS verification (the kind edge has a local CA)
A request sent on a connection with the app host's SNI and the api host's
:authority must get 421, and the reverse must get 421. A request whose SNI and
:authority agree must not get 421.
"""

from __future__ import annotations

import copy
import os
import subprocess
import sys
from urllib.parse import urlsplit

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMESPACE = "gibson"
LISTENER = "edge_tls"
STATUS = 421
JWT_AUTHN = "envoy.filters.http.jwt_authn"
EXT_AUTHZ = "envoy.filters.http.ext_authz"
JWT_PER_ROUTE = "type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.PerRouteConfig"
EXT_AUTHZ_PER_ROUTE = "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute"


# ------------------------------------------------------------------ render

def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def envoy_configs(docs: list[dict]) -> list[tuple[str, dict]]:
    """(ConfigMap name, parsed config) for every Envoy bootstrap that has the edge listener."""
    out = []
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        for value in (d.get("data") or {}).values():
            if not isinstance(value, str) or "static_resources" not in value:
                continue
            cfg = yaml.safe_load(value)
            if any(l.get("name") == LISTENER for l in cfg.get("static_resources", {}).get("listeners", [])):
                out.append((d["metadata"]["name"], cfg))
    return out


def edge_chains(cfg: dict):
    """Yield (chain index, filter_chain_match, HCM typed_config) for every chain on the edge listener."""
    for listener in cfg.get("static_resources", {}).get("listeners", []):
        if listener.get("name") != LISTENER:
            continue
        for i, chain in enumerate(listener.get("filter_chains", [])):
            for f in chain.get("filters", []):
                tc = f.get("typed_config") or {}
                if tc.get("route_config") is not None:
                    yield i, chain.get("filter_chain_match") or {}, tc


def filter_names(hcm: dict) -> set[str]:
    return {f.get("name") for f in hcm.get("http_filters") or []}


def wildcard_vhosts(hcm: dict) -> list[dict]:
    return [v for v in hcm["route_config"].get("virtual_hosts") or [] if "*" in (v.get("domains") or [])]


# ------------------------------------------------------------------- audit

def audit_vhost(where: str, vh: dict, filters: set[str]) -> list[str]:
    bad = []
    if vh.get("domains") != ["*"]:
        bad.append(f"{where}: the misdirected virtual host must match exactly ['*'], got {vh.get('domains')!r}")
    routes = vh.get("routes") or []
    if len(routes) != 1:
        bad.append(f"{where}: the misdirected virtual host must have exactly one route, got {len(routes)}")
    for r in routes:
        if r.get("match") != {"prefix": "/"}:
            bad.append(f"{where}: the misdirected route must match every path (prefix '/'), got {r.get('match')!r}")
        if "route" in r or "redirect" in r:
            bad.append(f"{where}: the misdirected route must not route or redirect; a '*' virtual host that "
                       "reaches an upstream is an unauthenticated route (deploy#1204)")
        dr = r.get("direct_response") or {}
        if dr.get("status") != STATUS:
            bad.append(f"{where}: the misdirected route must answer {STATUS}, got {dr.get('status')!r}")
    per = vh.get("typed_per_filter_config") or {}
    for name, typ in ((JWT_AUTHN, JWT_PER_ROUTE), (EXT_AUTHZ, EXT_AUTHZ_PER_ROUTE)):
        if name not in filters:
            continue
        cfg = per.get(name) or {}
        if cfg.get("@type") != typ or cfg.get("disabled") is not True:
            bad.append(f"{where}: the chain runs {name}, so the misdirected virtual host must disable it "
                       f"({typ} with disabled: true), or a coalesced request gets 401 instead of {STATUS}")
    return bad


def audit(docs: list[dict]) -> list[str]:
    configs = envoy_configs(docs)
    if not configs:
        return [f"no Envoy edge config (a listener named {LISTENER}) in the render: this guard is blind"]
    bad = []
    public_chains = 0
    for cm, cfg in configs:
        for i, match, hcm in edge_chains(cfg):
            rc = hcm["route_config"].get("name")
            where = f"{cm}: {LISTENER} chain {i} ({rc})"
            wild = wildcard_vhosts(hcm)
            if match.get("source_prefix_ranges"):
                bad.append(f"{where}: a source-restricted chain came back; the in-cluster chain is deleted "
                           f"(charts#163, ADR-0092)")
                continue
            public_chains += 1
            if len(wild) != 1:
                bad.append(f"{where}: a browser-facing chain must carry exactly one '*' virtual host that answers "
                           f"{STATUS} to a coalesced request, got {len(wild)}")
                continue
            bad.extend(audit_vhost(where, wild[0], filter_names(hcm)))
    if public_chains < 2:
        bad.append(f"fewer than two browser-facing chains on {LISTENER} ({public_chains}): this guard is blind")
    return bad


# ---------------------------------------------------------------- self-test

def _edit(docs: list[dict], fn) -> list[dict]:
    """A copy of the render with fn applied to every edge chain (index, match, hcm)."""
    out = copy.deepcopy(docs)
    for d in out:
        if d.get("kind") != "ConfigMap":
            continue
        for k, value in (d.get("data") or {}).items():
            if not isinstance(value, str) or "static_resources" not in value:
                continue
            cfg = yaml.safe_load(value)
            hit = False
            for i, match, hcm in edge_chains(cfg):
                if fn(i, match, hcm):
                    hit = True
            if hit:
                d["data"][k] = yaml.safe_dump(cfg)
    return out


def _on_chain(route_config: str, fn):
    def apply(i, match, hcm):
        if hcm["route_config"].get("name") != route_config:
            return False
        fn(hcm)
        return True
    return apply


def _wild(hcm: dict) -> dict:
    return wildcard_vhosts(hcm)[0]


def _drop_vhost(hcm):
    hcm["route_config"]["virtual_hosts"] = [v for v in hcm["route_config"]["virtual_hosts"] if "*" not in v["domains"]]


def _wrong_status(hcm):
    _wild(hcm)["routes"][0]["direct_response"]["status"] = 404


def _routes_upstream(hcm):
    r = _wild(hcm)["routes"][0]
    del r["direct_response"]
    r["route"] = {"cluster": "gibson_dashboard"}


def _narrow_match(hcm):
    _wild(hcm)["routes"][0]["match"] = {"prefix": "/connectors/"}


def _extra_route(hcm):
    _wild(hcm)["routes"].append({"match": {"prefix": "/x"}, "direct_response": {"status": STATUS}})


def _keep_jwt(hcm):
    del _wild(hcm)["typed_per_filter_config"][JWT_AUTHN]


def _keep_ext_authz(hcm):
    del _wild(hcm)["typed_per_filter_config"][EXT_AUTHZ]


def _restrict_source(i, match, hcm):
    if i != 0:
        return False
    match["source_prefix_ranges"] = [{"address_prefix": "10.0.0.0", "prefix_len": 8}]
    return True


def selftest(docs: list[dict]) -> int:
    api = [rc for _, cfg in envoy_configs(docs) for _, m, h in edge_chains(cfg)
           if JWT_AUTHN in filter_names(h) and not m.get("source_prefix_ranges")
           for rc in [h["route_config"]["name"]]]
    if not api:
        print("SELFTEST FAIL: no browser-facing chain with jwt_authn in the render", file=sys.stderr)
        return 1
    api_rc = api[0]
    public = [h["route_config"]["name"] for _, cfg in envoy_configs(docs) for _, m, h in edge_chains(cfg)
              if JWT_AUTHN not in filter_names(h) and not m.get("source_prefix_ranges")]
    if not public:
        print("SELFTEST FAIL: no browser-facing chain without jwt_authn in the render", file=sys.stderr)
        return 1
    public_rc = public[0]
    cases = [
        ("api chain without the misdirected virtual host", _on_chain(api_rc, _drop_vhost), "exactly one '*'"),
        ("public chain without the misdirected virtual host", _on_chain(public_rc, _drop_vhost), "exactly one '*'"),
        ("misdirected route answers 404", _on_chain(api_rc, _wrong_status), f"must answer {STATUS}"),
        ("misdirected route reaches an upstream", _on_chain(public_rc, _routes_upstream), "must not route"),
        ("misdirected route matches one prefix only", _on_chain(api_rc, _narrow_match), "every path"),
        ("misdirected virtual host carries a second route", _on_chain(public_rc, _extra_route), "exactly one route"),
        ("api chain keeps jwt_authn on the misdirected virtual host", _on_chain(api_rc, _keep_jwt), JWT_AUTHN),
        ("api chain keeps ext_authz on the misdirected virtual host", _on_chain(api_rc, _keep_ext_authz), EXT_AUTHZ),
        ("a chain selected by source address comes back", _restrict_source, "source-restricted"),
    ]
    rc = 0
    clean = audit(docs)
    if clean:
        print("SELFTEST FAIL: the render itself has findings:\n  " + "\n  ".join(clean), file=sys.stderr)
        rc = 1
    for name, fn, needle in cases:
        findings = audit(_edit(docs, fn))
        if any(needle in f for f in findings):
            print(f"  ok   {name}")
        else:
            print(f"  FAIL {name}: expected a finding containing {needle!r}, got {findings}", file=sys.stderr)
            rc = 1
    print("SELFTEST " + ("PASS" if rc == 0 else "FAIL"))
    return rc


# --------------------------------------------------------------------- live

def curl(url: str, authority: str) -> int:
    args = ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}", "--max-time", "20",
            "-H", f"Host: {authority}", url]
    for r in filter(None, os.environ.get("EDGE_RESOLVE", "").split(",")):
        args += ["--resolve", r.strip()]
    if os.environ.get("EDGE_INSECURE") == "1":
        args.append("-k")
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"PREFLIGHT: curl {url} (authority {authority}): {out.stderr.strip()} (this is NOT a pass)")
    return int(out.stdout.strip())


def live() -> int:
    edge = os.environ.get("EDGE_URL") or sys.exit("EDGE_URL is required for --live")
    api_host = os.environ.get("API_HOST") or sys.exit("API_HOST is required for --live")
    u = urlsplit(edge)
    app_host = u.hostname
    port = f":{u.port}" if u.port else ""
    api_url = f"{u.scheme}://{api_host}{port}"
    path = "/connectors/oauth/callback?code=x&state=y"
    checks = [
        ("SNI app, authority api", f"{edge}{path}", api_host, STATUS, True),
        ("SNI api, authority app", f"{api_url}/", app_host, STATUS, True),
        ("CONTROL SNI api, authority api", f"{api_url}{path}", api_host, STATUS, False),
        ("CONTROL SNI app, authority app", f"{edge}/", app_host, STATUS, False),
    ]
    rc = 0
    for name, url, authority, status, want in checks:
        got = curl(url, authority)
        ok = (got == status) == want
        print(f"  {'ok  ' if ok else 'FAIL'} {name}: {got}" + ("" if ok else f" (want {'' if want else 'not '}{status})"))
        if not ok:
            rc = 1
    print("LIVE " + ("PASS" if rc == 0 else "FAIL"))
    return rc


def main() -> int:
    if "--live" in sys.argv:
        return live()
    docs = render()
    if "--selftest" in sys.argv:
        return selftest(docs)
    bad = audit(docs)
    if bad:
        print("edge-misdirected-authority: FAIL\n  " + "\n  ".join(bad))
        return 1
    print("edge-misdirected-authority: OK (every browser-facing edge chain answers a coalesced request with 421)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
