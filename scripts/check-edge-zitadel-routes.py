#!/usr/bin/env python3
"""check-edge-zitadel-routes.py: the edge sends only listed routes to Zitadel, and no email or username change.

Two checks on the rendered Envoy edge config.

1. Exposure. Every route whose cluster is Zitadel core (zitadel_upstream) or
   the Zitadel login (zitadel_login) must be listed in
   helm/gibson/edge-zitadel-routes.yaml, with a reason. An unlisted route
   fails, and so does a listed route that no render produces.

2. Self-rename. One email address belongs to one account in the whole install
   (ADR-0093 decision 1). A user who changes their own email or username
   frees the old one for a second account, and Zitadel allows that through
   the auth API and the v2 UserService. The guard plays each request that
   changes an email or a username against the rendered routes, the way Envoy
   picks a route (first match wins), in every virtual host. None may reach
   Zitadel. It also plays the requests the console and the platform send,
   and each must still reach Zitadel on the app host. Every route config that
   sends a Zitadel API path to Zitadel must normalize the path and merge
   slashes, so a /../ or // detour cannot skip the 403 routes.

  check-edge-zitadel-routes.py             exit 1 on a finding, 0 when clean
  check-edge-zitadel-routes.py --selftest  prove each kind of finding fails
  check-edge-zitadel-routes.py --print     print the Zitadel routes the render produces
  check-edge-zitadel-routes.py --live      play the same requests against a running edge (kind, k3d)

--live reads these environment variables:
  EDGE_URL        the app origin, for example https://app.gibson.local:30443 (required)
  EDGE_RESOLVE    optional curl --resolve value, for example app.gibson.local:30443:127.0.0.1
  EDGE_INSECURE   set to 1 to skip TLS verification (the kind edge has a local CA)
  ZITADEL_TOKEN   optional access token of a tenant user. With it, each blocked
                  request carries the token, and the user's email and username
                  must read the same before and after.
Each blocked request must get the edge's own 403 (gRPC: grpc-status 7), and
each console request must get an answer from Zitadel, not from the edge. The
Zitadel console page (/ui/console/) must load.
"""
import copy
import json
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "helm", "gibson", "edge-zitadel-routes.yaml")
NAMESPACE = "gibson"
ZITADEL_CLUSTERS = ("zitadel_upstream", "zitadel_login")
APP_VHOST = ("public_routes", "app")
# Paths under these prefixes are Zitadel API calls. A route config that sends
# one of them to Zitadel must normalize paths.
API_FAMILIES = ("/auth/v1/", "/v2/", "/v2beta/", "/zitadel.")

# (method, path, extra headers). Each changes the caller's email or username,
# or can do so for the caller's own user ID. None may reach Zitadel.
MUST_BLOCK = [
    ("PUT", "/auth/v1/users/me/username", {}),
    ("PUT", "/auth/v1/users/me/email", {}),
    ("POST", "/auth/v1/users/me/email", {}),
    ("PUT", "/auth/v1/users/me/email/", {}),
    ("POST", "/zitadel.auth.v1.AuthService/UpdateMyUserName", {}),
    ("POST", "/zitadel.auth.v1.AuthService/SetMyEmail", {}),
    ("POST", "/zitadel.user.v2.UserService/SetEmail", {}),
    ("POST", "/zitadel.user.v2.UserService/UpdateHumanUser", {}),
    ("POST", "/zitadel.user.v2.UserService/UpdateUser", {}),
    ("POST", "/zitadel.user.v2beta.UserService/SetEmail", {}),
    ("POST", "/zitadel.user.v2beta.UserService/UpdateHumanUser", {}),
    ("POST", "/zitadel.resources.user.v3alpha.ZITADELUsers/PatchUser", {}),
    ("POST", "/zitadel.resources.user.v3alpha.ZITADELUsers/SetContactEmail", {}),
    ("POST", "/zitadel.resources.user.v3alpha.ZITADELUsers/AddUsername", {}),
    ("POST", "/zitadel.resources.user.v3alpha.ZITADELUsers/RemoveUsername", {}),
    ("POST", "/v2/users/312345678901234567/email", {}),
    ("PUT", "/v2/users/human/312345678901234567", {}),
    ("PATCH", "/v2/users/312345678901234567", {}),
    ("PATCH", "/v2/users/312345678901234567/", {}),
    ("patch", "/v2/users/312345678901234567", {}),
    ("POST", "/v2beta/users/312345678901234567/email", {}),
    ("PUT", "/v2beta/users/312345678901234567", {}),
    # Zitadel's router matches the decoded path.
    ("POST", "/zitadel.user.v2.UserService/Set%45mail", {}),
    ("POST", "/zitadel.user.v2.UserService%2FSetEmail", {}),
    ("PUT", "/auth/v1/users/me/e%6Dail", {}),
    # grpc-gateway applies X-HTTP-Method-Override to a form POST.
    ("POST", "/v2/users/312345678901234567", {"x-http-method-override": "PATCH"}),
]

# Requests the console and the platform send. Each must reach Zitadel core
# on the app host.
MUST_ROUTE = [
    ("GET", "/auth/v1/users/me", {}),
    ("GET", "/auth/v1/users/me/email", {}),
    ("POST", "/auth/v1/policies/passwords/complexity", {}),
    ("POST", "/zitadel.auth.v1.AuthService/GetMyUser", {}),
    ("POST", "/zitadel.user.v2.UserService/ListUsers", {}),
    ("POST", "/zitadel.user.v2.UserService/SetPassword", {}),
    ("POST", "/zitadel.user.v2.UserService/AddHumanUser", {}),
    ("POST", "/zitadel.management.v1.ManagementService/ListOrgMembers", {}),
    ("POST", "/zitadel.admin.v1.AdminService/GetLoginPolicy", {}),
    ("POST", "/v2/users/human", {}),
    ("POST", "/v2/users/new", {}),
    ("GET", "/v2/users/312345678901234567", {}),
    ("DELETE", "/v2/users/312345678901234567", {}),
    ("POST", "/v2/users/312345678901234567/password", {}),
    ("POST", "/management/v1/users/_search", {}),
    ("GET", "/admin/v1/policies/login", {}),
    ("GET", "/oauth/v2/keys", {}),
    ("GET", "/ui/console/", {}),
    ("GET", "/ui/console/assets/environment.json", {}),
]


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
            if any(l.get("name") == "edge_tls" for l in cfg.get("static_resources", {}).get("listeners", [])):
                out.append((d["metadata"]["name"], cfg))
    return out


def connection_managers(cfg: dict):
    """Yield the typed_config of every HTTP connection manager with an inline route config."""
    for listener in cfg.get("static_resources", {}).get("listeners", []):
        for chain in listener.get("filter_chains", []):
            for f in chain.get("filters", []):
                tc = f.get("typed_config") or {}
                if tc.get("route_config") is not None:
                    yield tc


# ------------------------------------------------------------- the matcher

class Blind(Exception):
    """A route uses a match field this guard does not model."""


def string_matches(sm: dict, value: str | None) -> bool:
    if value is None:
        return False
    if "exact" in sm:
        return value == sm["exact"]
    if "prefix" in sm:
        return value.startswith(sm["prefix"])
    if "suffix" in sm:
        return value.endswith(sm["suffix"])
    if "safe_regex" in sm:
        return re.fullmatch(sm["safe_regex"]["regex"], value) is not None
    raise Blind(f"string_match {sorted(sm)}")


def header_matches(h: dict, headers: dict) -> bool:
    value = headers.get(h["name"].lower())
    if "present_match" in h:
        got = (value is not None) == bool(h["present_match"])
    elif "string_match" in h:
        got = string_matches(h["string_match"], value)
    elif "exact_match" in h:
        got = value == h["exact_match"]
    else:
        raise Blind(f"header match {sorted(h)}")
    return got != bool(h.get("invert_match"))


def route_matches(m: dict, method: str, path: str, headers: dict) -> bool:
    known = {"prefix", "path", "safe_regex", "headers", "path_separated_prefix"}
    extra = set(m) - known
    if extra:
        raise Blind(f"route match {sorted(extra)}")
    if "prefix" in m:
        ok = path.startswith(m["prefix"])
    elif "path" in m:
        ok = path == m["path"]
    elif "path_separated_prefix" in m:
        p = m["path_separated_prefix"]
        ok = path == p or path.startswith(p + "/")
    elif "safe_regex" in m:
        ok = re.fullmatch(m["safe_regex"]["regex"], path) is not None
    else:
        raise Blind("route match with no path matcher")
    if not ok:
        return False
    hdrs = {":method": method, **{k.lower(): v for k, v in headers.items()}}
    return all(header_matches(h, hdrs) for h in m.get("headers") or [])


def outcome(vhost: dict, method: str, path: str, headers: dict) -> str:
    """What Envoy does with the request in this virtual host: a cluster name, "direct:<status>" or "none"."""
    for r in vhost.get("routes") or []:
        if route_matches(r.get("match") or {}, method, path, headers):
            if "direct_response" in r:
                return f"direct:{r['direct_response'].get('status')}"
            if "redirect" in r:
                return "redirect"
            return (r.get("route") or {}).get("cluster", "?")
    return "none"


def route_key(rc: dict, vhost: dict, match: dict) -> str:
    for kind in ("prefix", "path", "path_separated_prefix"):
        if kind in match:
            return f"{rc.get('name')}/{vhost.get('name')} {kind}:{match[kind]}"
    if "safe_regex" in match:
        return f"{rc.get('name')}/{vhost.get('name')} safe_regex:{match['safe_regex']['regex']}"
    return f"{rc.get('name')}/{vhost.get('name')} {sorted(match)}"


# -------------------------------------------------------------------- audit

def audit(docs: list[dict], allow: dict) -> tuple[list[str], set[str]]:
    configs = envoy_configs(docs)
    if not configs:
        return ["no Envoy edge config (a listener named edge_tls) in the render: this guard is blind"], set()
    bad, seen = [], set()
    app_found = False
    listed = allow.get("routes") or {}
    for cm, cfg in configs:
        for hcm in connection_managers(cfg):
            rc = hcm["route_config"]
            sends_api = False
            for vh in rc.get("virtual_hosts") or []:
                for r in vh.get("routes") or []:
                    cluster = (r.get("route") or {}).get("cluster")
                    if cluster not in ZITADEL_CLUSTERS:
                        continue
                    key = route_key(rc, vh, r.get("match") or {})
                    seen.add(key)
                    if key not in listed:
                        bad.append(f"{cm}: unlisted route to {cluster}: {key!r}")
                try:
                    for method, path, headers in MUST_BLOCK:
                        got = outcome(vh, method, path, headers)
                        if got == "zitadel_upstream":
                            bad.append(f"{cm}: {rc.get('name')}/{vh.get('name')}: {method} {path}"
                                       f"{' ' + str(headers) if headers else ''} reaches Zitadel; it must get a 403")
                    for fam in API_FAMILIES:
                        if outcome(vh, "POST", fam + "x", {}) == "zitadel_upstream":
                            sends_api = True
                    if (rc.get("name"), vh.get("name")) == APP_VHOST:
                        app_found = True
                        for method, path, headers in MUST_ROUTE:
                            got = outcome(vh, method, path, headers)
                            if got != "zitadel_upstream":
                                bad.append(f"{cm}: {rc.get('name')}/{vh.get('name')}: {method} {path} must reach "
                                           f"Zitadel for the console and the platform, got {got}")
                except Blind as e:
                    bad.append(f"{cm}: {rc.get('name')}/{vh.get('name')}: a route uses {e}, which this guard "
                               "does not model; teach the matcher before you use it")
            if sends_api:
                for field in ("normalize_path", "merge_slashes"):
                    if hcm.get(field) is not True:
                        bad.append(f"{cm}: route config {rc.get('name')!r} sends Zitadel API paths to Zitadel "
                                   f"without {field}: true")
    if not app_found:
        bad.append(f"no virtual host {APP_VHOST[1]!r} in route config {APP_VHOST[0]!r}: this guard is blind")
    for key in sorted(listed):
        if key not in seen:
            bad.append(f"stale entry in {os.path.relpath(ALLOWLIST, ROOT)} (no render produces it): {key!r}")
    return bad, seen


def load_allowlist(path: str = ALLOWLIST) -> dict:
    data = yaml.safe_load(open(path)) or {}
    for k, v in (data.get("routes") or {}).items():
        if not isinstance(v, dict) or not str(v.get("why") or "").strip():
            raise SystemExit(f"{path}: route {k!r} must say why it is public (why:)")
    return data


# ---------------------------------------------------------------- self-test

def _edit(docs: list[dict], fn) -> list[dict]:
    """A copy of the render with fn applied to the app virtual host's HCM."""
    out = copy.deepcopy(docs)
    for d in out:
        if d.get("kind") != "ConfigMap":
            continue
        for k, value in (d.get("data") or {}).items():
            if not isinstance(value, str) or "static_resources" not in value:
                continue
            cfg = yaml.safe_load(value)
            hit = False
            for hcm in connection_managers(cfg):
                if hcm["route_config"].get("name") == APP_VHOST[0]:
                    fn(hcm)
                    hit = True
            if hit:
                d["data"][k] = yaml.safe_dump(cfg)
    return out


def _app(hcm: dict) -> dict:
    return next(v for v in hcm["route_config"]["virtual_hosts"] if v.get("name") == APP_VHOST[1])


def _is_block(r: dict, needle: str) -> bool:
    m = r.get("match") or {}
    return "direct_response" in r and needle in str(m.get("safe_regex") or m.get("path"))


def _drop(needle: str):
    def fn(hcm):
        vh = _app(hcm)
        vh["routes"] = [r for r in vh["routes"] if not _is_block(r, needle)]
    return fn


def _move_blocks_last(hcm):
    vh = _app(hcm)
    blocks = [r for r in vh["routes"] if "direct_response" in r]
    rest = [r for r in vh["routes"] if r not in blocks]
    vh["routes"] = rest + blocks


def _add_route(prefix: str, cluster: str):
    def fn(hcm):
        _app(hcm)["routes"].insert(0, {"match": {"prefix": prefix}, "route": {"cluster": cluster}})
    return fn


def _block_all_v2(hcm):
    _app(hcm)["routes"].insert(0, {"match": {"prefix": "/v2/"}, "direct_response": {"status": 403}})


def _no_normalize(hcm):
    hcm.pop("normalize_path", None)


def _unknown_match(hcm):
    _app(hcm)["routes"].insert(0, {"match": {"prefix": "/", "query_parameters": [{"name": "x"}]},
                                   "direct_response": {"status": 403}})


FIXTURES_THAT_MUST_FAIL = {
    "an unlisted route sends /system/v1/ to Zitadel": _add_route("/system/v1/", "zitadel_upstream"),
    "an unlisted route sends /debug/ to the Zitadel login": _add_route("/debug/", "zitadel_login"),
    "the 403 route for the v2 gRPC SetEmail is missing": _drop("/zitadel.user.v2.UserService/SetEmail"),
    "the 403 route for the auth gRPC UpdateMyUserName is missing": _drop("AuthService/UpdateMyUserName"),
    "the auth REST 403 route is missing": _drop("users/me/"),
    "the v2 email 403 route is missing": _drop("/email/?$"),
    "the percent-sign 403 route is missing": _drop("%"),
    "the method-override 403 route is missing": _drop("^/(?:auth/v1/|v2/|v2beta/).*$"),
    "the 403 routes come after the Zitadel routes": _move_blocks_last,
    "a 403 on all of /v2/ also blocks signup and the console": _block_all_v2,
    "the public route config does not normalize paths": _no_normalize,
    "a route uses a match field the guard does not model": _unknown_match,
}


def selftest() -> int:
    allow = load_allowlist()
    docs = render()
    got, _ = audit(docs, allow)
    if got:
        print("SELFTEST FAIL: the render and the allowlist disagree:\n  " + "\n  ".join(got))
        return 1
    for what, fn in FIXTURES_THAT_MUST_FAIL.items():
        got, _ = audit(_edit(docs, fn), allow)
        if not got:
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    stale = {"routes": {**allow["routes"], "public_routes/app prefix:/gone/": {"why": "fixture"}}}
    if not any(x.startswith("stale entry") for x in audit(docs, stale)[0]):
        print("SELFTEST FAIL: a stale allowlist entry must fail")
        return 1
    if not audit([], allow)[0]:
        print("SELFTEST FAIL: a render with no edge config must fail as blind")
        return 1
    print(f"  ✓ selftest: {len(FIXTURES_THAT_MUST_FAIL)} broken edges fail, a stale entry fails, "
          "a blind render fails, the render passes")
    return 0


# ------------------------------------------------------------------- live

EDGE_BODY = "forbidden"


# A real rename for the calls that change the caller's own user, so a broken
# edge shows up as a changed email or username, not only as a status code.
RENAME_BODIES = {
    "/auth/v1/users/me/email": {"email": "renamed-by-edge-probe@example.invalid"},
    "/zitadel.auth.v1.AuthService/SetMyEmail": {"email": "renamed-by-edge-probe@example.invalid"},
    "/auth/v1/users/me/username": {"userName": "renamed-by-edge-probe"},
    "/zitadel.auth.v1.AuthService/UpdateMyUserName": {"userName": "renamed-by-edge-probe"},
}


def curl(method: str, path: str, headers: dict, token: str | None) -> tuple[int, dict, str]:
    """(HTTP status, response headers, body) for one request through the live edge."""
    url = os.environ["EDGE_URL"].rstrip("/") + path
    # HTTP/1.1: over HTTP/2, Envoy answers a direct response before it reads
    # the request body and then resets the stream, which curl may report as
    # a framing error instead of the 403.
    args = ["curl", "-sS", "--http1.1", "--path-as-is", "-X", method, "-D", "-", "-o", "-", "--max-time", "20"]
    if os.environ.get("EDGE_RESOLVE"):
        args += ["--resolve", os.environ["EDGE_RESOLVE"]]
    if os.environ.get("EDGE_INSECURE") == "1":
        args += ["-k"]
    hdrs = dict(headers)
    if path.startswith("/zitadel."):
        hdrs.setdefault("content-type", "application/json")
        hdrs.setdefault("connect-protocol-version", "1")
    elif method not in ("GET", "HEAD", "DELETE"):
        hdrs.setdefault("content-type", "application/json")
    if token:
        hdrs["authorization"] = f"Bearer {token}"
    for k, v in hdrs.items():
        args += ["-H", f"{k}: {v}"]
    if method not in ("GET", "HEAD", "DELETE"):
        args += ["--data", json.dumps(RENAME_BODIES.get(path, {}))]
    out = subprocess.run(args + [url], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"PREFLIGHT: curl {method} {url}: {out.stderr.strip()} (this is NOT a pass)")
    # text=True turns CRLF into LF.
    head, _, body = out.stdout.partition("\n\n")
    lines = head.split("\n")
    status = int(lines[0].split()[1])
    rh = {}
    for line in lines[1:]:
        k, _, v = line.partition(":")
        rh[k.strip().lower()] = v.strip()
    return status, rh, body


def edge_refused(status: int, rh: dict, body: str) -> bool:
    return (status == 403 and body.strip() == EDGE_BODY) or rh.get("grpc-status") == "7"


def whoami(token: str) -> tuple[str, str]:
    status, _, body = curl("GET", "/auth/v1/users/me", {}, token)
    if status != 200:
        raise SystemExit(f"PREFLIGHT: GET /auth/v1/users/me with ZITADEL_TOKEN answered {status}; "
                         "the token must be a tenant user's access token (this is NOT a pass)")
    user = json.loads(body).get("user") or {}
    return user.get("userName", ""), ((user.get("human") or {}).get("email") or {}).get("email", "")


def live() -> int:
    if not os.environ.get("EDGE_URL"):
        raise SystemExit("PREFLIGHT: set EDGE_URL to the app origin, for example https://app.gibson.local:30443")
    token = os.environ.get("ZITADEL_TOKEN") or None
    bad = []
    before = whoami(token) if token else None
    for method, path, headers in MUST_BLOCK:
        if method != method.upper():
            continue  # Envoy's HTTP/1.1 codec rejects a lowercase method; the render check covers it
        status, rh, body = curl(method, path, headers, token)
        if edge_refused(status, rh, body):
            print(f"  refused  {method} {path}")
        else:
            bad.append(f"{method} {path}: got {status} {body.strip()[:80]!r}, want the edge's 403")
    for method, path, headers in MUST_ROUTE:
        status, rh, body = curl(method, path, headers, None)
        # Zitadel answers an unauthenticated call with 401 (or 200 for a
        # public endpoint). The edge's 403, the dashboard's 404 or a 5xx
        # means the call never reached Zitadel.
        if edge_refused(status, rh, body) or status == 404 or status >= 500:
            bad.append(f"{method} {path}: got {status}, want an answer from Zitadel")
        else:
            print(f"  routed   {method} {path} ({status})")
    status, _, body = curl("GET", "/ui/console/", {}, None)
    if status != 200 or "<cnsl-root" not in body:
        bad.append(f"GET /ui/console/: got {status}, want the Zitadel console page")
    else:
        print("  routed   GET /ui/console/ (the console loads)")
    if token:
        after = whoami(token)
        if after != before:
            bad.append(f"the tenant user's username and email changed: {before} -> {after}")
        else:
            print(f"  unchanged username and email for the tenant user: {before}")
    if bad:
        print("FAIL: the live edge does not match the rendered contract:\n  " + "\n  ".join(bad))
        return 1
    print(f"OK: {len(MUST_BLOCK)} email and username changes refused at the edge, "
          f"{len(MUST_ROUTE)} console and platform calls reach Zitadel, the console loads")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    if "--live" in sys.argv:
        return live()
    allow = load_allowlist()
    bad, seen = audit(render(), allow)
    if "--print" in sys.argv:
        for k in sorted(seen):
            print(k)
        return 0
    if bad:
        print("the Envoy edge exposes Zitadel beyond helm/gibson/edge-zitadel-routes.yaml, or lets a user "
              "change their own email or username:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ edge-zitadel-routes: {len(seen)} Zitadel routes, every one listed; "
          f"{len(MUST_BLOCK)} email and username changes get a 403, {len(MUST_ROUTE)} console calls route")
    return 0


if __name__ == "__main__":
    sys.exit(main())
