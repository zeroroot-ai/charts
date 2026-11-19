#!/usr/bin/env python3
"""check-edge-a2a-inbound.py: the Edge route of inbound A2A keeps its shape (epic gibson#1367).

An outside A2A client calls a published Gibson agent through the Edge. The path
is client, Edge, daemon, and the call does not cross the agentgateway. The
Edge forwards /a2a/inbound/ to the inbound listener of the daemon. ext_authz
checks the caller and writes x-gibson-tenant and x-gibson-subject, and the
daemon trusts those two headers. Each rule below is a way to lose that
contract with a render that still looks correct.

The check renders each shipped profile and fails when:

  1. route      the api virtual host has no route for /a2a/inbound/, the
                route is not the first one that matches an inbound path, it
                does not go to the cluster gibson_daemon_a2a_inbound, it
                rewrites the path, or it caps a streamed reply (timeout must
                be 0s), or it never reaps a dead HTTP/1.1 peer (idle_timeout
                must be finite and at least InboundWriteMax of the daemon,
                600s).
  2. ext_authz  the route or its virtual host changes ext_authz, or the
                connection manager of the route has no ext_authz filter that
                fails closed.
  3. headers    the route, its virtual host or its route config removes or
                adds x-gibson-tenant or x-gibson-subject. The router applies
                request_headers_to_remove after ext_authz, so a removal there
                deletes the values that ext_authz wrote, and the daemon
                refuses every call (the NOTE on api_routes in envoy.yaml).
                ext_authz overwrites a copy that the caller sent.
  4. jwt        the first jwt_authn rule that matches an inbound path does
                not allow a request with no Zitadel JWT. The caller has a
                Capability Grant JWT and no Zitadel JWT.
  5. quota      the route has no api_authenticated quota keyed on the two
                headers that ext_authz writes for this class.
  6. rewrite    another route of the Edge config rewrites a path to
                /a2a/inbound/, or sends a request to the inbound cluster.
  7. cluster    the cluster gibson_daemon_a2a_inbound does not dial the
                daemon Service on the inbound port, or does not present the
                Edge SVID and pin the daemon SVID the way gibson_daemon_grpc
                does.
  8. daemon     the daemon has no inbound container port and Service port,
                or one of the three variables is wrong:
                GIBSON_A2A_INBOUND_ADDR is ":<port>",
                GIBSON_A2A_INBOUND_EDGE_SVID is the Edge SVID that ext-authz
                pins (EXT_AUTHZ_ENVOY_SVID), and GIBSON_A2A_INBOUND_PUBLIC_URL
                is the api origin of gibson-domains plus /a2a/inbound.
  9. network    the Edge does not reach the inbound port of the daemon, or
                another pod of the namespace does (no gibson.daemonPortDeny
                rule). A probe pod, the dashboard backend, stands for each
                other pod.

  check-edge-a2a-inbound.py             exit 1 on a finding, 0 when clean
  check-edge-a2a-inbound.py --selftest  prove each rule fails on a broken fixture

The render reads the packaged sub-charts. Run make chart-deps after an edit
to a sub-chart. make check repacks once before its gates.
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import cilium_policy as cp  # noqa: E402

PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
PREFIX = "/a2a/inbound/"
CLUSTER = "gibson_daemon_a2a_inbound"
PORT_NAME = "a2a-inbound"
# The two inbound paths of one agent (gibson agentcall.InboundPathAgent).
SAMPLES = ("/a2a/inbound/recon", "/a2a/inbound/recon/.well-known/agent-card.json")
# The headers that the inbound class of ext-authz writes on allow, with
# OVERWRITE_IF_EXISTS_OR_ADD (gibson extauthz/server/inbound_a2a.go).
WRITTEN = {"x-gibson-tenant", "x-gibson-subject"}
EXT_AUTHZ = "envoy.filters.http.ext_authz"
JWT = "envoy.filters.http.jwt_authn"
REWRITES = ("prefix_rewrite", "regex_rewrite", "path_rewrite_policy")
# InboundWriteMax of the daemon (gibson a2aserver/inbound.go): the longest wait
# for one write to the caller. The route idle timeout must not be shorter.
MIN_IDLE_SECONDS = 600


def seconds(v) -> float | None:
    """An Envoy duration as seconds. None for 0s (no timeout) or a bad value."""
    m = re.fullmatch(r"([0-9.]+)s", str(v or ""))
    return float(m.group(1)) or None if m else None


ENV = ("GIBSON_A2A_INBOUND_ADDR", "GIBSON_A2A_INBOUND_EDGE_SVID", "GIBSON_A2A_INBOUND_PUBLIC_URL")


def render(profile: tuple[str, ...]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/testdata/render-inputs/gibson.yaml",
            "--namespace", "gibson"]
    for f in profile:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def configmap(docs: list[dict], name: str) -> dict:
    return next((d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == name), {})


def edge_config(docs: list[dict]) -> dict:
    return yaml.safe_load((configmap(docs, "gibson-envoy").get("data") or {}).get("envoy.yaml") or "{}") or {}


def daemon(docs: list[dict], kind: str) -> dict:
    return next((d for d in docs if d.get("kind") == kind
                 and ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == "daemon"),
                {})


def container_env(workload: dict, container: str | None = None) -> dict:
    out: dict = {}
    for c in (((workload.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []:
        if container and c.get("name") != container:
            continue
        for e in c.get("env") or []:
            if "value" in e:
                out[e["name"]] = e["value"]
    return out


def matches(match: dict, path: str) -> bool:
    if "prefix" in match:
        return path.startswith(match["prefix"])
    if "path" in match:
        return path == match["path"]
    if "path_separated_prefix" in match:
        p = match["path_separated_prefix"]
        return path == p or path.startswith(p + "/")
    if "safe_regex" in match:
        return re.fullmatch((match["safe_regex"] or {}).get("regex", ""), path) is not None
    return False


def managers(edge: dict) -> list[dict]:
    return [n for n in walk(edge) if isinstance(n, dict) and isinstance(n.get("route_config"), dict)
            and "http_filters" in n]


def judge_edge(edge: dict, svc: str, port, daemon_san: str) -> list[str]:
    bad: list[str] = []
    hcm = next((m for m in managers(edge) if m["route_config"].get("name") == "api_routes"), None)
    if hcm is None:
        return ["route: the Edge config has no route config api_routes"]
    rc = hcm["route_config"]
    vh = next((v for v in rc.get("virtual_hosts") or [] if v.get("name") == "api"), None)
    if vh is None:
        return ["route: api_routes has no virtual host api"]
    routes = vh.get("routes") or []
    ours = [r for r in routes if (r.get("match") or {}).get("prefix") == PREFIX]

    # 1. The route.
    if not ours:
        bad.append(f"route: the api virtual host has no route for {PREFIX}")
    for path in SAMPLES:
        first = next((r for r in routes if matches(r.get("match") or {}, path)), None)
        if ours and first is not ours[0]:
            bad.append(f"route: {path} meets the route {first.get('match') if first else None} first, "
                       f"not the inbound route")
    for r in ours:
        rt = r.get("route") or {}
        if rt.get("cluster") != CLUSTER:
            bad.append(f"route: {PREFIX} goes to {rt.get('cluster') or r.get('direct_response') and 'a direct response'}, "
                       f"want {CLUSTER}")
        for k in REWRITES:
            if k in rt:
                bad.append(f"route: {PREFIX} rewrites the path ({k}); the daemon reads the agent from the path as sent")
        idle = seconds(rt.get("idle_timeout"))
        if rt.get("timeout") != "0s" or idle is None or idle < MIN_IDLE_SECONDS:
            bad.append(f"route: {PREFIX} has timeout {rt.get('timeout')} and idle_timeout "
                       f"{rt.get('idle_timeout')}; want timeout 0s (no cap on a stream) and a finite "
                       f"idle_timeout of at least {MIN_IDLE_SECONDS}s (InboundWriteMax of the daemon), "
                       f"so a dead HTTP/1.1 peer is reaped")

        # 2. ext_authz.
        for scope, node in (("route", r), ("virtual host", vh)):
            if EXT_AUTHZ in (node.get("typed_per_filter_config") or {}):
                bad.append(f"ext_authz: the {scope} of {PREFIX} changes ext_authz; the Edge keeps it on there")

        # 3. The headers that ext_authz writes.
        for scope, node in (("route", r), ("virtual host", vh), ("route config", rc)):
            removed = {h.lower() for h in node.get("request_headers_to_remove") or []}
            added = {((h.get("header") or {}).get("key") or "").lower() for h in node.get("request_headers_to_add") or []}
            if WRITTEN & removed:
                bad.append(f"headers: the {scope} of {PREFIX} removes {sorted(WRITTEN & removed)}, which ext_authz "
                           f"writes; the router removes them after ext_authz")
            if WRITTEN & added:
                bad.append(f"headers: the {scope} of {PREFIX} sets {sorted(WRITTEN & added)}, which only ext_authz may write")

        # 5. The quota.
        ok = False
        for rl in rt.get("rate_limits") or []:
            acts = rl.get("actions") or []
            keys = {(a.get("generic_key") or {}).get("descriptor_value") for a in acts}
            hdrs = {(a["request_headers"].get("descriptor_key"), a["request_headers"].get("header_name"))
                    for a in acts if "request_headers" in a}
            if "api_authenticated" in keys and {("tenant", "x-gibson-tenant"), ("subject", "x-gibson-subject")} <= hdrs:
                ok = True
        if not ok:
            bad.append(f"quota: {PREFIX} has no api_authenticated quota keyed on x-gibson-tenant and x-gibson-subject")

    names = [f.get("name") for f in hcm.get("http_filters") or []]
    authz = next((f for f in hcm.get("http_filters") or [] if f.get("name") == EXT_AUTHZ), None)
    if authz is None or names.index(EXT_AUTHZ) > names.index("envoy.filters.http.router"):
        bad.append("ext_authz: the api connection manager has no ext_authz filter before the router")
    elif authz.get("disabled"):
        bad.append("ext_authz: the api ext_authz filter entry is disabled")
    elif (authz.get("typed_config") or {}).get("failure_mode_allow") is not False:
        bad.append("ext_authz: the api ext_authz filter does not fail closed")

    # 4. jwt_authn.
    jwt = next((f for f in hcm.get("http_filters") or [] if f.get("name") == JWT), None)
    rules = ((jwt or {}).get("typed_config") or {}).get("rules") or []
    for path in SAMPLES:
        rule = next((ru for ru in rules if matches(ru.get("match") or {}, path)), None)
        if rule is None or not any(isinstance(n, dict) and "allow_missing_or_failed" in n
                                   for n in walk(rule.get("requires") or {})):
            bad.append(f"jwt: the first jwt_authn rule for {path} requires a Zitadel JWT; the caller has none")

    for ru in rules:
        pfx = (ru.get("match") or {}).get("prefix")
        loose = any(isinstance(n, dict) and ("allow_missing_or_failed" in n or "allow_missing" in n)
                    for n in walk(ru.get("requires") or {}))
        if loose and pfx is not None and pfx != PREFIX and PREFIX.startswith(pfx):
            bad.append(f"jwt: the jwt_authn rule for {pfx!r} lets a request with no Zitadel JWT through, and it "
                       f"covers more than {PREFIX}")

    # 6. No other route reaches the inbound path or cluster.
    for m in managers(edge):
        for v in m["route_config"].get("virtual_hosts") or []:
            for r in v.get("routes") or []:
                if r in ours:
                    continue
                rt = r.get("route") or {}
                label = r.get("match")
                if any(isinstance(n, dict) and n.get("cluster") == CLUSTER for n in walk(rt)):
                    bad.append(f"rewrite: the route {label} sends a request to {CLUSTER}")
                target = str(rt.get("prefix_rewrite") or "")
                target += " " + str(((rt.get("regex_rewrite") or {}).get("substitution")) or "")
                if PREFIX.rstrip("/") in target or PREFIX.rstrip("/") in str(rt.get("path_rewrite_policy") or ""):
                    bad.append(f"rewrite: the route {label} rewrites a path to {PREFIX}")

    # 7. The cluster.
    clusters = {c.get("name"): c for c in ((edge.get("static_resources") or {}).get("clusters") or [])}
    c = clusters.get(CLUSTER)
    if c is None:
        bad.append(f"cluster: the Edge config has no cluster {CLUSTER}")
    else:
        addrs = [n["socket_address"] for n in walk(c.get("load_assignment") or {})
                 if isinstance(n, dict) and "socket_address" in n]
        if not addrs or any(a.get("address") != svc or str(a.get("port_value")) != str(port) for a in addrs):
            bad.append(f"cluster: {CLUSTER} does not dial {svc}:{port}")
        tls = ((c.get("transport_socket") or {}).get("typed_config") or {})
        ctx = tls.get("common_tls_context") or {}
        sds = [s.get("name") for s in ctx.get("tls_certificate_sds_secret_configs") or []]
        sans = [((m.get("matcher") or {}).get("exact")) for m in
                (((ctx.get("combined_validation_context") or {}).get("default_validation_context") or {})
                 .get("match_typed_subject_alt_names") or []) if m.get("san_type") == "URI"]
        if "default" not in sds or sans != [daemon_san]:
            bad.append(f"cluster: {CLUSTER} does not present the Edge SVID and pin only {daemon_san}")
    return bad


def daemon_san(edge: dict) -> str:
    for c in ((edge.get("static_resources") or {}).get("clusters") or []):
        if c.get("name") == "gibson_daemon_grpc":
            for n in walk(c):
                if isinstance(n, dict) and n.get("san_type") == "URI":
                    return (n.get("matcher") or {}).get("exact", "")
    return ""


def judge(docs: list[dict]) -> list[str]:
    bad: list[str] = []
    sts, svc = daemon(docs, "StatefulSet"), daemon(docs, "Service")
    sport = next((p for p in (svc.get("spec") or {}).get("ports") or [] if p.get("name") == PORT_NAME), None)
    cports = [p for c in (((sts.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []
              for p in c.get("ports") or [] if p.get("name") == PORT_NAME]
    port = sport.get("port") if sport else None

    # 8. The daemon.
    if sport is None or sport.get("targetPort") != PORT_NAME:
        bad.append(f"daemon: the daemon Service has no port {PORT_NAME} that targets the container port")
    if len(cports) != 1 or cports[0].get("containerPort") != port:
        bad.append(f"daemon: the daemon container has no port {PORT_NAME} equal to the Service port {port}")
    env = container_env(sts, "gibson")
    for name in ENV:
        if name not in env:
            bad.append(f"daemon: the daemon has no {name}; the daemon does not start without it")
    authz = next((d for d in docs if d.get("kind") == "Deployment"
                  and ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == "ext-authz"),
                 {})
    edge_svid = container_env(authz).get("EXT_AUTHZ_ENVOY_SVID")
    if "GIBSON_A2A_INBOUND_ADDR" in env and env["GIBSON_A2A_INBOUND_ADDR"] != f":{port}":
        bad.append(f"daemon: GIBSON_A2A_INBOUND_ADDR is {env['GIBSON_A2A_INBOUND_ADDR']!r}, want ':{port}'")
    if "GIBSON_A2A_INBOUND_EDGE_SVID" in env and (not edge_svid or env["GIBSON_A2A_INBOUND_EDGE_SVID"] != edge_svid):
        bad.append(f"daemon: GIBSON_A2A_INBOUND_EDGE_SVID is {env['GIBSON_A2A_INBOUND_EDGE_SVID']!r}, "
                   f"want the Edge SVID of ext-authz {edge_svid!r}")
    origin = (configmap(docs, "gibson-domains").get("data") or {}).get("apiOrigin")
    want = f"{origin}{PREFIX.rstrip('/')}"
    if "GIBSON_A2A_INBOUND_PUBLIC_URL" in env and (not origin or env["GIBSON_A2A_INBOUND_PUBLIC_URL"] != want):
        bad.append(f"daemon: GIBSON_A2A_INBOUND_PUBLIC_URL is {env['GIBSON_A2A_INBOUND_PUBLIC_URL']!r}, want {want!r}")

    edge = edge_config(docs)
    bad += judge_edge(edge, (svc.get("metadata") or {}).get("name"), port, daemon_san(edge))

    # 9. The network rule. The Edge reaches the port, and a probe pod (the
    # dashboard backend) stands for each other pod of the namespace.
    dst = cp.endpoint(*pod_labels(docs, "daemon"))
    rules = cp.rules(docs, dst[cp.NS_KEY])
    edge_pod = cp.endpoint(*pod_labels(docs, "envoy"))
    other = cp.endpoint(*pod_labels(docs, "dashboard"))
    if not cp.reaches(rules, edge_pod, dst, port):
        bad.append(f"network: the Edge does not reach the daemon port {port}")
    if not cp.denied(rules, other, dst, port):
        bad.append(f"network: a pod of the namespace other than the Edge reaches the daemon port {port}; "
                   f"add a gibson.daemonPortDeny rule")
    return bad


def pod_labels(docs: list[dict], component: str) -> tuple[str, dict]:
    """(namespace, pod labels) of the rendered workload of a component."""
    for d in docs:
        if d.get("kind") in ("Deployment", "StatefulSet") and \
                ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == component:
            return ((d.get("metadata") or {}).get("namespace") or "",
                    (((d.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {})
    return "", {}


def edge_mutate(docs: list[dict], change) -> None:
    """Apply change to the Edge config, in place."""
    cm = configmap(docs, "gibson-envoy")
    edge = yaml.safe_load(cm["data"]["envoy.yaml"])
    change(edge)
    cm["data"]["envoy.yaml"] = yaml.safe_dump(edge)


def api(edge: dict) -> tuple[dict, dict, dict]:
    hcm = next(m for m in managers(edge) if m["route_config"].get("name") == "api_routes")
    vh = next(v for v in hcm["route_config"]["virtual_hosts"] if v["name"] == "api")
    return hcm, vh, next(r for r in vh["routes"] if (r.get("match") or {}).get("prefix") == PREFIX)


def route(change):
    return lambda ds: edge_mutate(ds, lambda e: change(api(e)[2]))


def set_env(ds: list[dict], name: str, value) -> None:
    for c in daemon(ds, "StatefulSet")["spec"]["template"]["spec"]["containers"]:
        if c["name"] == "gibson":
            if value is None:
                c["env"] = [e for e in c["env"] if e["name"] != name]
            else:
                next(e for e in c["env"] if e["name"] == name)["value"] = value


def drop_deny(ds: list[dict]) -> None:
    ds.remove(next(d for d in ds if d.get("kind") == "CiliumNetworkPolicy"
                   and d["metadata"]["name"] == "gibson-daemon-a2a-inbound"))


def deny_edge(ds: list[dict]) -> None:
    next(d for d in ds if d.get("kind") == "CiliumNetworkPolicy"
         and d["metadata"]["name"] == "gibson-daemon-a2a-inbound")["spec"]["ingressDeny"][0].update(fromEndpoints=[{}])


def other_rewrite(e: dict) -> None:
    vh = api(e)[1]
    vh["routes"].insert(0, {"match": {"prefix": "/agents/"},
                            "route": {"cluster": "gibson_daemon_grpc", "prefix_rewrite": PREFIX}})


def shadow(e: dict) -> None:
    api(e)[1]["routes"].insert(0, {"match": {"prefix": "/a2a/"}, "direct_response": {"status": 404}})


def drop_jwt_rule(e: dict) -> None:
    jwt = next(f for f in api(e)[0]["http_filters"] if f["name"] == JWT)
    jwt["typed_config"]["rules"] = [r for r in jwt["typed_config"]["rules"]
                                    if (r.get("match") or {}).get("prefix") != PREFIX]


def loose_rule(e: dict) -> None:
    jwt = next(f for f in api(e)[0]["http_filters"] if f["name"] == JWT)
    jwt["typed_config"]["rules"].insert(0, {"match": {"prefix": "/a2a/"},
                                            "requires": {"allow_missing_or_failed": {}}})


def wrong_port(e: dict) -> None:
    c = next(c for c in e["static_resources"]["clusters"] if c["name"] == CLUSTER)
    next(n for n in walk(c) if isinstance(n, dict) and "socket_address" in n)["socket_address"]["port_value"] = 8443


FIXTURES = {
    "no inbound route": lambda ds: edge_mutate(ds, lambda e: api(e)[1].update(
        routes=[r for r in api(e)[1]["routes"] if (r.get("match") or {}).get("prefix") != PREFIX])),
    "an inbound route with ext_authz off": route(lambda r: r.update(typed_per_filter_config={EXT_AUTHZ: {
        "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute",
        "disabled": True}})),
    "an api virtual host with ext_authz off": lambda ds: edge_mutate(ds, lambda e: api(e)[1].update(
        typed_per_filter_config={EXT_AUTHZ: {"disabled": True}})),
    "an inbound route that strips x-gibson-tenant": route(lambda r: r.update(
        request_headers_to_remove=r.get("request_headers_to_remove", []) + ["x-gibson-tenant"])),
    "a route config that strips x-gibson-subject": lambda ds: edge_mutate(ds, lambda e: api(e)[0]["route_config"]
                                                                          ["request_headers_to_remove"].append(
                                                                              "x-gibson-subject")),
    "an inbound route that sets x-gibson-tenant": route(lambda r: r.update(request_headers_to_add=[
        {"header": {"key": "x-gibson-tenant", "value": "t"}}])),
    "an inbound route that rewrites the path": route(lambda r: r["route"].update(prefix_rewrite="/")),
    "an inbound route that caps a stream": route(lambda r: r["route"].update(idle_timeout="300s")),
    "an inbound quota keyed on the identity headers": route(lambda r: r["route"]["rate_limits"][0].update(actions=[
        {"generic_key": {"descriptor_key": "route", "descriptor_value": "api_authenticated"}},
        {"request_headers": {"header_name": "x-gibson-identity-tenant", "descriptor_key": "tenant"}},
        {"request_headers": {"header_name": "x-gibson-identity-subject", "descriptor_key": "subject"}}])),
    "an earlier route that shadows the inbound route": lambda ds: edge_mutate(ds, shadow),
    "no jwt_authn rule for the inbound route": lambda ds: edge_mutate(ds, drop_jwt_rule),
    "another route that rewrites to the inbound path": lambda ds: edge_mutate(ds, other_rewrite),
    "a cluster on the wrong port": lambda ds: edge_mutate(ds, wrong_port),
    "no GIBSON_A2A_INBOUND_ADDR": lambda ds: set_env(ds, "GIBSON_A2A_INBOUND_ADDR", None),
    "an Edge SVID that is not the Edge": lambda ds: set_env(ds, "GIBSON_A2A_INBOUND_EDGE_SVID",
                                                            container_env(daemon(ds, "StatefulSet"), "gibson")
                                                            ["GIBSON_A2A_GATEWAY_SVID"]),
    "a public URL with a literal domain": lambda ds: set_env(ds, "GIBSON_A2A_INBOUND_PUBLIC_URL",
                                                             "https://api.example.com/a2a/inbound"),
    "no deny rule on the inbound port": drop_deny,
    "a deny rule that refuses the Edge too": deny_edge,
    "a jwt_authn rule that loosens all of /a2a/": lambda ds: edge_mutate(ds, loose_rule),
    "an ext_authz filter entry that is disabled": lambda ds: edge_mutate(ds, lambda e: next(
        f for f in api(e)[0]["http_filters"] if f["name"] == EXT_AUTHZ).update(disabled=True)),
}

# The rule that each fixture must hit: the tag that starts its finding.
FIXTURE_RULE = {
    "no inbound route": "route",
    "an inbound route with ext_authz off": "ext_authz",
    "an api virtual host with ext_authz off": "ext_authz",
    "an inbound route that strips x-gibson-tenant": "headers",
    "a route config that strips x-gibson-subject": "headers",
    "an inbound route that sets x-gibson-tenant": "headers",
    "an inbound route that rewrites the path": "route",
    "an inbound route that caps a stream": "route",
    "an inbound quota keyed on the identity headers": "quota",
    "an earlier route that shadows the inbound route": "route",
    "no jwt_authn rule for the inbound route": "jwt",
    "another route that rewrites to the inbound path": "rewrite",
    "a cluster on the wrong port": "cluster",
    "no GIBSON_A2A_INBOUND_ADDR": "daemon",
    "an Edge SVID that is not the Edge": "daemon",
    "a public URL with a literal domain": "daemon",
    "no deny rule on the inbound port": "network",
    "a deny rule that refuses the Edge too": "network",
    "a jwt_authn rule that loosens all of /a2a/": "jwt",
    "an ext_authz filter entry that is disabled": "ext_authz",
}


def selftest() -> int:
    docs = render(PROFILES[0])
    base = judge(docs)
    if base:
        print("❌ selftest: the baseline render has findings:\n  " + "\n  ".join(base))
        return 1
    failed = []
    if set(FIXTURES) != set(FIXTURE_RULE):
        print(f"❌ selftest: fixtures with no rule tag: {sorted(set(FIXTURES) ^ set(FIXTURE_RULE))}")
        return 1
    for name, mutate in FIXTURES.items():
        ds = copy.deepcopy(docs)
        mutate(ds)
        got = judge(ds)
        if not any(b.startswith(FIXTURE_RULE[name] + ":") for b in got):
            failed.append(f"{name} (want a {FIXTURE_RULE[name]} finding, got {got})")
    if failed:
        print("❌ selftest: these fixtures pass the guard:\n  " + "\n  ".join(failed))
        return 1
    print(f"✓ selftest: each of {len(FIXTURES)} broken fixtures fails the A2A inbound guard on its own rule")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    bad = []
    for profile in PROFILES:
        bad += [f"{'+'.join(profile)}: {b}" for b in judge(render(profile))]
    if bad:
        print("❌ the inbound A2A route of the Edge lost its contract (epic gibson#1367):\n  " + "\n  ".join(bad))
        return 1
    print("✓ edge-a2a-inbound: the route, ext_authz, the trusted headers, the JWT rule, the quota, the cluster, "
          "the daemon variables and the network rule on each profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
