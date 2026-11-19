#!/usr/bin/env python3
"""check-agentgateway.py: the one agentgateway Gateway keeps its platform policies (charts#815).

The umbrella renders one Gateway for the Model gateway and the Agent gateway
(gibson#1366). The tenant-operator adds the routes of each tenant later, so
each rule below must hold on the Gateway itself, where no route can undo it.
The check renders each shipped profile and fails when:

  1. ext-authz  no AgentgatewayPolicy targets the whole Gateway (no
                sectionName) with spec.traffic.phase PreRouting,
                strategy.inheritance Override and spec.traffic.extAuth that
                calls the rendered ext-authz Service on its grpc port, over
                the gRPC protocol, with failureMode FailClosed and the
                expression json(request.body).model in grpc.requestMetadata.
                The Gateway must have the two listeners model and agent, and
                no listener policy may replace the ext-authz of either one.
  2. listener   the Gateway names an address or a listener hostname, a
                listener is not HTTPS with mode Terminate and a certificate
                Secret that a rendered cert-manager Certificate writes (a
                caller token must not cross the pod network in clear text),
                the parametersRef does not name
                the rendered AgentgatewayParameters, or those parameters do
                not set the proxy Service to ClusterIP. Any of these makes a
                public listener.
  3. image      the proxy image of the AgentgatewayParameters is not on
                ghcr.io/zeroroot-ai/mirror/, has no sha256 digest, or is not
                on airgap/images.txt.
  4. strip      no policy on the listener `model` removes each x-gibson-*
                header that a rendered object names and each header of
                MUST_STRIP (the route claim and the Capability Grant), with
                inheritance Override.
  5. formats    no backend.ai.routes on the listener `model` names the four
                paths /v1/messages, /v1/responses, /v1/embeddings and
                /v1/models.
  6. trace      no frontend policy on the Gateway sets maxBufferSize and
                adds the trace fields that the daemon reads, each with
                its expression (TRACE_FIELDS), or the ext-authz policy does
                not copy the trusted headers and the route agent into the
                request metadata that those fields read.
  7. namespace  the Gateway is in the release namespace, or in a namespace
                that the render does not create, or a policy on the Gateway
                is in another namespace (a targetRef names an object of the
                namespace of the policy, so it would attach to nothing), or a
                reference across namespaces (a listener certificate, the
                ext-authz or the trace collector Service) has no
                ReferenceGrant in the namespace of its target. The gateway
                namespace keeps the provider credential Secrets apart from
                the Secrets of the platform.
  8. edge       a sandbox reaches the gateway only through the Edge
                (charts#815). For /gateway/model/ and /gateway/agent/ the
                Edge config of the render must hold a route to the cluster
                agentgateway_<listener> with the prefix removed, timeout 0s
                and idle_timeout 0s (no cap on a streamed reply), no
                per-route ext_authz switch-off, the removal of the four
                trusted headers and never of x-capability-grant or the
                route claim, a JWT rule that lets the Capability Grant
                through, and a cluster that verifies the component CA and
                the proxy name on the listener port of the Gateway.
  9. a2a        the agent listener has no HTTPRoute with path prefix / and
                no header match to the daemon Service on its A2A port, that
                port has no appProtocol agentgateway.dev/a2a, the daemon
                Service has no backend TLS policy (SPIFFE, the daemon SVID)
                on that port, the HTTPRoute has no ReferenceGrant, or the
                agent listener removes a header that the daemon reads (the
                trusted headers and the route claim) or keeps one it must
                not get (the grant and the Edge quota headers).
 10. egress     a Cilium rule that selects the proxy pods lets them reach
                an address of DENY_V4 or DENY_V6 (each range that the
                netguard of the daemon blocks, the cloud metadata address
                and both NAT64 prefixes among them), uses an entity or a host name in an
                allow rule (the world entity includes the metadata address,
                and a name can resolve to a private address), or names a
                pod with no namespace, no pod label or no port. No rule
                that selects the proxy denies the host, remote-node and
                kube-apiserver entities and each range of DENY_V4 and
                DENY_V6 (gibson contract round 8: the proxy resolves a
                tenant URL again at each call, and the CNI is the only
                control at connect time).
 11. ports      a pod of the release namespace other than the one peer
                reaches the A2A port of the daemon (peer: the proxy, from
                the gateway namespace) or its trace port (peer: the trace
                collector), or the peer does not reach it. The platform
                rule lets each platform pod reach each daemon port, so only
                an ingressDeny rule (gibson.daemonPortDeny) closes the port.
                A probe pod (the dashboard backend) stands for each other
                pod.
 12. rbac       a binding gives the controller its deployer role (the writes
                of Deployments, Services, ServiceAccounts, Secrets and the
                rest of the proxy) outside the gateway namespace: a
                ClusterRoleBinding, or a RoleBinding in another namespace,
                or no RoleBinding in the gateway namespace.
 13. collector  the trace collector queue is not limited in bytes, or its
                byte limit x 2 + 512 MiB passes its PVC (a compaction writes
                a second copy), or the receiver takes less than two buffers
                (a request body and a response body, maxBufferSize each), or
                the proxy may send more than one span in one OTLP export
                (OTEL_BSP_MAX_EXPORT_BATCH_SIZE is not 1 in the
                AgentgatewayParameters): a batch of large spans passes the
                receive limit and the collector refuses all of it.
 14. ingress    a pod of a sandbox namespace reaches a gateway listener
                directly, or the daemon, the dashboard backend or the Edge
                does not. A sandbox reaches the gateway only through the
                Edge (gibson daemon/model_gateway.go).
 15. grants     a ReferenceGrant into the release namespace lets a kind of
                the gateway namespace name an object that it does not use:
                the Gateway names only the listener certificate Secret, an
                AgentgatewayPolicy only the ext-authz and trace collector
                Services, an HTTPRoute only the daemon Service.
 16. limits     the controller container has no CPU or memory limit. The
                controller derives GOMAXPROCS and GOMEMLIMIT from them.
 17. default    the default proxy image of the controller
                (AGW_PROXY_IMAGE_*) is not the proxy image of the
                parameters, pinned by the same digest.
 18. listeners  GIBSON_MODEL_GATEWAY_LISTENER or GIBSON_AGENT_GATEWAY_LISTENER
                of the tenant-operator (the sectionName of each tenant
                route) does not name the Gateway listener that the chart
                uses for that role: the listener of the model strip policy,
                and the listener of the agent route to the daemon.

  check-agentgateway.py             exit 1 on a finding, 0 when clean
  check-agentgateway.py --selftest  prove each rule fails on a broken fixture
"""
from __future__ import annotations

import copy
import ipaddress
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import cilium_policy as cp  # noqa: E402

LIST = os.path.join(ROOT, "airgap", "images.txt")
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
GW_GROUP = "gateway.networking.k8s.io"
FORMAT_PATHS = {"/v1/messages", "/v1/responses", "/v1/embeddings", "/v1/models"}
LISTENERS = {"model", "agent"}
TRUSTED = ("tenant", "subject", "mission", "task")
# The request metadata that the ext-authz policy copies after the check, with
# the header that it reads. The trusted headers that ext-authz set, and the
# route agent of an A2A call (gibson contract round 8). The route of an
# outside agent removes x-gibson-route-agent before the trace snapshot, so
# the trace reads the copy.
META_COPY = {**{f: f'request.headers["x-gibson-{f}"]' for f in TRUSTED},
             "route_agent": 'request.headers["x-gibson-route-agent"]'}
# The trace attributes that the daemon reads, by name, with their expression.
TRACE_FIELDS = {
    "gibson.request_body": "string(request.body)",
    "gibson.response_body": "string(response.body)",
    **{f"gibson.{f}": f"metadata.{f}" for f in META_COPY},
}
# The addresses that the proxy must never reach (gibson contract round 8):
# each range that the netguard of the daemon blocks (gibson
# internal/infra/netguard/netguard.go), with the whole of both NAT64 prefixes.
# The cloud metadata address 169.254.169.254 is in 169.254.0.0/16. This copy
# is apart from the template on purpose, so a range removed there fails here.
DENY_V4 = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
           "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15", "224.0.0.0/4", "240.0.0.0/4")
DENY_V6 = ("::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "64:ff9b:1::/48", "fc00::/7", "fe80::/10",
           "ff00::/8")
DENY_ENTITIES = {"host", "remote-node", "kube-apiserver"}
MODEL_EXPR = "json(request.body).model"
# A whole header name. A name that ends in "-" is a prefix match of the Edge,
# not a header, and is skipped.
HEADER = re.compile(r"\bx-gibson-[a-z0-9-]+")
# The headers that the provider must never get: the route claim that each
# route matches on, the Capability Grant, the four trusted headers that
# ext-authz sets (gibson internal/server/extauthz/modelcall/modelcall.go),
# and the two Edge quota headers that the gateway forward class sets
# (gibson contract round 7, gibson#1388).
MUST_STRIP = {"x-gibson-route-tenant", "x-gibson-route-provider", "x-capability-grant",
              "x-gibson-tenant", "x-gibson-subject", "x-gibson-mission", "x-gibson-task",
              "x-gibson-identity-tenant", "x-gibson-identity-subject"}
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def render(profile: tuple[str, ...]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/testdata/render-inputs/gibson.yaml",
            "--namespace", "gibson"]
    for f in profile:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def of_kind(docs: list[dict], kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def targets(pol: dict, gw: str, section: str | None) -> bool:
    refs = (pol.get("spec") or {}).get("targetRefs") or []
    return any(r.get("group") == GW_GROUP and r.get("kind") == "Gateway" and r.get("name") == gw
               and r.get("sectionName") == section for r in refs)


def ns(d: dict) -> str:
    return (d.get("metadata") or {}).get("namespace") or ""


def granted(docs: list[dict], from_group: str, from_kind: str, from_ns: str,
            to_kind: str, to_name: str, to_ns: str) -> bool:
    """A ReferenceGrant in to_ns lets from_kind objects of from_ns name this object."""
    for g in of_kind(docs, "ReferenceGrant"):
        if ns(g) != to_ns:
            continue
        spec = g.get("spec") or {}
        f_ok = any(f.get("group") == from_group and f.get("kind") == from_kind and f.get("namespace") == from_ns
                   for f in spec.get("from") or [])
        t_ok = any(t.get("kind") == to_kind and t.get("name", to_name) == to_name for t in spec.get("to") or [])
        if f_ok and t_ok:
            return True
    return False


def header_names(docs: list[dict]) -> set[str]:
    """Each x-gibson-* header that a rendered object names, outside the strip policy."""
    names = set()
    for d in docs:
        if d.get("kind") == "AgentgatewayPolicy":
            continue
        names |= {h for h in HEADER.findall(yaml.safe_dump(d, width=1 << 20)) if not h.endswith("-")}
    return names


# The agent listener: the daemon reads these, so they pass, and it must not
# get these, so they go (gibson contract round 7).
A2A_KEEP = {"x-gibson-tenant", "x-gibson-subject", "x-gibson-mission", "x-gibson-task",
            "x-gibson-route-tenant", "x-gibson-route-agent", "x-gibson-route-bank"}
A2A_STRIP = {"x-capability-grant", "x-gibson-identity-tenant", "x-gibson-identity-subject",
             "x-gibson-identity-issuer", "x-gibson-identity-issued-at", "x-gibson-identity-credential-type"}
# gibson gatewayheaders.Removed() at the pinned release (v0.165.0): the model
# listener removes exactly these. A copy apart from values.yaml, so a header
# dropped there fails here.
GATEWAY_REMOVED = {"x-gibson-tenant", "x-gibson-subject", "x-gibson-mission", "x-gibson-task",
                   "x-gibson-identity-subject", "x-gibson-identity-issuer", "x-gibson-identity-credential-type",
                   "x-gibson-identity-tenant", "x-gibson-identity-issued-at",
                   "x-gibson-route-tenant", "x-gibson-route-provider", "x-gibson-route-agent", "x-gibson-route-bank",
                   "x-capability-grant"}
A2A_PROTOCOL = "agentgateway.dev/a2a"
EDGE_PREFIXES = {"model": "/gateway/model/", "agent": "/gateway/agent/"}
TRUSTED_HEADERS = {"x-gibson-tenant", "x-gibson-subject", "x-gibson-mission", "x-gibson-task"}
PASS_HEADERS = {"x-capability-grant", "x-gibson-route-tenant", "x-gibson-route-provider"}


def walk(node):
    yield node
    if isinstance(node, dict):
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def edge_config(docs: list[dict]) -> dict:
    cm = next((d for d in of_kind(docs, "ConfigMap") if d["metadata"]["name"] == "gibson-envoy"), None)
    return yaml.safe_load(((cm or {}).get("data") or {}).get("envoy.yaml") or "{}") or {}


def judge_edge(edge: dict, gw: dict) -> list[str]:
    bad: list[str] = []
    ports = {li.get("name"): li.get("port") for li in (gw.get("spec") or {}).get("listeners") or []}
    host = f"{gw['metadata']['name']}.{ns(gw)}.svc"
    route_configs = [n for n in walk(edge) if isinstance(n, dict) and "virtual_hosts" in n]
    jwt_rules = [n for n in walk(edge) if isinstance(n, dict) and "requires" in n and "match" in n]
    clusters = {c.get("name"): c for c in ((edge.get("static_resources") or {}).get("clusters") or [])}
    for listener, prefix in EDGE_PREFIXES.items():
        found = []
        for rc in route_configs:
            for vh in rc.get("virtual_hosts") or []:
                for r in vh.get("routes") or []:
                    if (r.get("match") or {}).get("prefix") == prefix:
                        found.append((rc, r))
        if not found:
            bad.append(f"edge: no route for {prefix}")
            continue
        for rc, r in found:
            rt = r.get("route") or {}
            want = f"agentgateway_{listener}"
            if rt.get("cluster") != want or rt.get("prefix_rewrite") != "/":
                bad.append(f"edge: {prefix} goes to {rt.get('cluster')} with prefix_rewrite "
                           f"{rt.get('prefix_rewrite')!r}, want {want} and '/'")
            if rt.get("timeout") != "0s" or rt.get("idle_timeout") != "0s":
                bad.append(f"edge: {prefix} caps a streamed reply (timeout {rt.get('timeout')}, "
                           f"idle_timeout {rt.get('idle_timeout')}), want 0s and 0s")
            off = ((r.get("typed_per_filter_config") or {}).get("envoy.filters.http.ext_authz") or {})
            if off.get("disabled") or off:
                bad.append(f"edge: {prefix} changes ext_authz on its route; the Edge keeps ext_authz there")
            removed = {h.lower() for h in (r.get("request_headers_to_remove") or [])}
            removed_all = removed | {h.lower() for h in (rc.get("request_headers_to_remove") or [])}
            if TRUSTED_HEADERS - removed:
                bad.append(f"edge: {prefix} lets the caller send {sorted(TRUSTED_HEADERS - removed)}")
            if PASS_HEADERS & removed_all:
                bad.append(f"edge: {prefix} strips {sorted(PASS_HEADERS & removed_all)}, which the gateway needs")
        if not any((j.get("match") or {}).get("prefix") == prefix and "allow_missing_or_failed" in (j.get("requires") or {})
                   for j in jwt_rules):
            bad.append(f"edge: no JWT rule lets the Capability Grant through on {prefix}")
        c = clusters.get(f"agentgateway_{listener}") or {}
        tls = ((c.get("transport_socket") or {}).get("typed_config") or {})
        vc = ((tls.get("common_tls_context") or {}).get("validation_context") or {})
        sans = [((m.get("matcher") or {}).get("exact")) for m in vc.get("match_typed_subject_alt_names") or []]
        if not (vc.get("trusted_ca") or {}).get("filename") or host not in sans or tls.get("sni") != host:
            bad.append(f"edge: cluster agentgateway_{listener} does not verify the component CA and the name {host}")
        addrs = [n for n in walk(c.get("load_assignment") or {}) if isinstance(n, dict) and "socket_address" in n]
        if not addrs or any(a["socket_address"].get("address") != host
                            or a["socket_address"].get("port_value") != ports.get(listener) for a in addrs):
            bad.append(f"edge: cluster agentgateway_{listener} does not dial {host}:{ports.get(listener)}")
    return bad


def judge_a2a(docs: list[dict], gw: dict, pols: list[dict]) -> list[str]:
    bad: list[str] = []
    name, gw_ns = gw["metadata"]["name"], ns(gw)
    routes = [r for r in of_kind(docs, "HTTPRoute") if ns(r) == gw_ns and any(
        p.get("name") == name and p.get("sectionName") == "agent" for p in (r.get("spec") or {}).get("parentRefs") or [])]
    target = None
    for r in routes:
        for rule in (r.get("spec") or {}).get("rules") or []:
            for m in rule.get("matches") or []:
                if (m.get("path") or {}).get("type") == "PathPrefix" and (m.get("path") or {}).get("value") == "/" \
                        and not m.get("headers"):
                    refs = rule.get("backendRefs") or []
                    if len(refs) == 1:
                        target = (r, refs[0])
    if not target:
        return ["a2a: the agent listener has no route with path prefix / and no header match to one backend"]
    route, ref = target
    svc = next((s for s in of_kind(docs, "Service") if s["metadata"]["name"] == ref.get("name")
                and ns(s) == ref.get("namespace", gw_ns)), None)
    port = next((p for p in ((svc or {}).get("spec") or {}).get("ports") or [] if p.get("port") == ref.get("port")), None)
    if not svc or not port or (svc["metadata"].get("labels") or {}).get("app.kubernetes.io/component") != "daemon":
        return [f"a2a: the agent route goes to {ref.get('namespace')}/{ref.get('name')}:{ref.get('port')}, "
                "not to a port of the daemon Service"]
    if port.get("appProtocol") != A2A_PROTOCOL:
        bad.append(f"a2a: the daemon port {port.get('name')} has no appProtocol {A2A_PROTOCOL}")
    if ns(svc) != gw_ns and not granted(docs, GW_GROUP, "HTTPRoute", gw_ns, "Service", svc["metadata"]["name"], ns(svc)):
        bad.append(f"a2a: the agent route names Service {ns(svc)}/{svc['metadata']['name']} with no ReferenceGrant")
    tls_ok = False
    for p in pols:
        if ns(p) != ns(svc):
            continue
        for t in (p.get("spec") or {}).get("targetRefs") or []:
            if t.get("kind") == "Service" and t.get("name") == svc["metadata"]["name"] \
                    and t.get("sectionName") in (None, str(port.get("port"))):
                tls = ((p.get("spec") or {}).get("backend") or {}).get("tls") or {}
                if tls.get("certificateSource") == "SPIFFE" and any(
                        str(v).endswith("/platform/daemon") for v in tls.get("verifySubjectAltNames") or []):
                    tls_ok = True
    if not tls_ok:
        bad.append("a2a: no backend TLS policy (SPIFFE, the daemon SVID) on the A2A port of the daemon Service")
    removed = set()
    strip_ok = False
    for p in pols:
        if targets(p, name, "agent") or targets(p, name, None):
            r = ((((p.get("spec") or {}).get("traffic") or {}).get("headerModifiers") or {}).get("request") or {})
            got = {h.lower() for h in r.get("remove") or []}
            removed |= got
            if targets(p, name, "agent") and got and ((p["spec"].get("strategy") or {}).get("inheritance")) == "Override":
                strip_ok = True
    if A2A_KEEP & removed:
        bad.append(f"a2a: the agent listener removes {sorted(A2A_KEEP & removed)}, which the daemon reads")
    if not strip_ok or A2A_STRIP - removed:
        bad.append(f"a2a: the agent listener keeps {sorted(A2A_STRIP - removed)} or has no Override removal policy")
    return bad


def cidrs_of(peer: dict) -> list[tuple[str, list[str]]]:
    """(cidr, except) for each CIDR of an egress or egressDeny item."""
    out = [(c, []) for c in peer.get("toCIDR") or []]
    out += [(c.get("cidr"), list(c.get("except") or [])) for c in peer.get("toCIDRSet") or []]
    return out


def judge_egress(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    labels = ((gw.get("spec") or {}).get("infrastructure") or {}).get("labels") or {}
    proxy = cp.endpoint(ns(gw), labels)
    mine = [(p, r, rns) for p, r, rns in cp.rules(docs, ns(gw)) if cp.rule_selects(r, rns, proxy)]
    if not mine:
        return ["egress: no Cilium rule selects the proxy pods"]
    deny_nets = [ipaddress.ip_network(c) for c in DENY_V4 + DENY_V6]
    denied_entities: set[str] = set()
    denied_nets: list = []
    for p, r, _ in mine:
        pname = f"{p['kind']}/{p['metadata']['name']}"
        for e in r.get("egress") or []:
            if e.get("toEntities"):
                bad.append(f"egress: {pname} lets the proxy reach the entities {e['toEntities']}")
            if e.get("toFQDNs"):
                bad.append(f"egress: {pname} lets the proxy reach host names, which can resolve to a private address")
            for sel in e.get("toEndpoints") or []:
                ml = {cp.norm(k): v for k, v in (sel.get("matchLabels") or {}).items()}
                if (sel.get("matchExpressions") or cp.NS_KEY not in ml or len(ml) < 2 or not e.get("toPorts")):
                    bad.append(f"egress: {pname} lets the proxy reach pods by {sel} with ports "
                               f"{e.get('toPorts')}; name a namespace, a pod label and a port")
            for c, exc in cidrs_of(e):
                net = ipaddress.ip_network(c, strict=False)
                excs = [ipaddress.ip_network(x, strict=False) for x in exc]
                for d in deny_nets:
                    if d.version == net.version and d.overlaps(net) and not any(
                            x.version == d.version and d.subnet_of(x) for x in excs):
                        bad.append(f"egress: {pname} lets the proxy reach {d} through {c}")
        for e in r.get("egressDeny") or []:
            denied_entities |= set(e.get("toEntities") or [])
            denied_nets += [ipaddress.ip_network(c, strict=False) for c, _ in cidrs_of(e)]
    if DENY_ENTITIES - denied_entities:
        bad.append(f"egress: no rule of the proxy denies the entities {sorted(DENY_ENTITIES - denied_entities)}")
    for d in deny_nets:
        if not any(x.version == d.version and d.subnet_of(x) for x in denied_nets):
            bad.append(f"egress: no rule of the proxy denies {d}")
    return bad


def pod_labels(docs: list[dict], component: str) -> tuple[str, dict]:
    """(namespace, pod labels) of the rendered workload of a component."""
    for d in docs:
        if d.get("kind") in ("Deployment", "StatefulSet") and \
                ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == component:
            return ns(d), (((d.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels") or {}
    return "", {}


def judge_daemon_ports(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    daemon_ns, daemon_labels = pod_labels(docs, "daemon")
    svc = next((d for d in of_kind(docs, "Service")
                if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == "daemon"), {})
    ports = {p.get("name"): p.get("port") for p in (svc.get("spec") or {}).get("ports") or []}
    proxy = cp.endpoint(ns(gw), ((gw.get("spec") or {}).get("infrastructure") or {}).get("labels") or {})
    collector = cp.endpoint(*pod_labels(docs, "trace-collector"))
    other = cp.endpoint(*pod_labels(docs, "dashboard"))
    dst = cp.endpoint(daemon_ns, daemon_labels)
    rules = cp.rules(docs, daemon_ns)
    for name, peer, label in (("a2a", proxy, "the agentgateway proxy"), ("otlp-grpc", collector, "the trace collector")):
        port = ports.get(name)
        if port is None:
            bad.append(f"ports: the daemon Service has no port {name}")
            continue
        if not cp.reaches(rules, peer, dst, port):
            bad.append(f"ports: {label} does not reach the daemon port {name} ({port})")
        if not cp.denied(rules, other, dst, port):
            bad.append(f"ports: a pod of the release namespace other than {label} reaches the daemon port "
                       f"{name} ({port}); add a gibson.daemonPortDeny rule")
        if cp.denied(rules, peer, dst, port):
            bad.append(f"ports: a deny rule refuses {label} on the daemon port {name} ({port})")
    return bad


# What each kind of the gateway namespace may name in the release namespace.
GRANT_TARGETS = {
    "Gateway": {("Secret", "gibson-agentgateway-tls")},
    "AgentgatewayPolicy": {("Service", "gibson-ext-authz"), ("Service", "gibson-trace-collector")},
    "HTTPRoute": {("Service", "gibson-gibson-workloads")},
}


def judge_grants(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    for g in of_kind(docs, "ReferenceGrant"):
        spec = g.get("spec") or {}
        for f in spec.get("from") or []:
            if f.get("namespace") != ns(gw):
                continue
            allowed = GRANT_TARGETS.get(f.get("kind"), set())
            for t in spec.get("to") or []:
                if (t.get("kind"), t.get("name")) not in allowed:
                    bad.append(f"grants: ReferenceGrant/{g['metadata']['name']} lets {f.get('kind')} name "
                               f"{t.get('kind')}/{t.get('name') or '*'}, which it does not use")
    return bad


SANDBOX_NS_LABEL = "io.cilium.k8s.namespace.labels.setec.zeroroot.ai/sandbox-namespace"


def judge_ingress(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    ports = [li.get("port") for li in (gw.get("spec") or {}).get("listeners") or []]
    proxy = cp.endpoint(ns(gw), ((gw.get("spec") or {}).get("infrastructure") or {}).get("labels") or {})
    rules = cp.rules(docs, ns(gw))
    sandbox = {**cp.endpoint("sandbox-example", {"app": "tool"}), SANDBOX_NS_LABEL: "true"}
    for port in ports:
        # setec writes the egress side of a sandbox, so only the ingress side
        # of the proxy is in the render. It must admit no sandbox pod.
        if any(cp.rule_selects(r, rns, proxy) and cp.ingress_admits(r, rns, sandbox, port) for _, r, rns in rules):
            bad.append(f"ingress: a sandbox pod reaches the gateway listener {port}; it must go through the Edge")
        for comp in ("daemon", "dashboard", "envoy"):
            src = cp.endpoint(*pod_labels(docs, comp))
            if not cp.reaches(rules, src, proxy, port):
                bad.append(f"ingress: the {comp} pods do not reach the gateway listener {port}")
    return bad


# The share of the proxy memory limit that its in-memory span queue may hold.
SPAN_QUEUE_SHARE = 0.5


def mib(q: str) -> float:
    m = re.fullmatch(r"(\d+)(Mi|Gi)", str(q))
    return float(m.group(1)) * (1024 if m.group(2) == "Gi" else 1) if m else 0.0


def judge_collector(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    cm = next((d for d in of_kind(docs, "ConfigMap")
               if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == "trace-collector"),
              None)
    sts = next((d for d in of_kind(docs, "StatefulSet")
                if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") == "trace-collector"),
               None)
    if not cm or not sts:
        return ["collector: the render has no trace collector ConfigMap and StatefulSet"]
    col = yaml.safe_load(cm["data"]["collector.yaml"])
    q = (((col.get("exporters") or {}).get("otlp/daemon") or {}).get("sending_queue") or {})
    pvc = next((mib(((v.get("spec") or {}).get("resources") or {}).get("requests", {}).get("storage"))
                for v in (sts.get("spec") or {}).get("volumeClaimTemplates") or []
                if (v.get("metadata") or {}).get("name") == "queue"), 0.0)
    if q.get("sizer") != "bytes":
        bad.append(f"collector: the queue sizer is {q.get('sizer')!r}; one span can hold 32 MiB, so it must be bytes")
    elif int(q.get("queue_size") or 0) / 1048576 * 2 + 512 > pvc:
        bad.append(f"collector: a queue of {int(q.get('queue_size') or 0) / 1048576:.0f} MiB and its compaction copy "
                   f"do not fit the PVC of {pvc:.0f} MiB")
    pol = next((p for p in of_kind(docs, "AgentgatewayPolicy")
                if (((p.get("spec") or {}).get("frontend") or {}).get("http") or {}).get("maxBufferSize")), {})
    buf = mib(pol["spec"]["frontend"]["http"]["maxBufferSize"]) if pol else 0.0
    recv = float((((col.get("receivers") or {}).get("otlp") or {}).get("protocols") or {}).get("grpc", {})
                 .get("max_recv_msg_size_mib") or 0)
    if not buf or recv < 2 * buf:
        bad.append(f"collector: the receiver takes {recv} MiB, less than two buffers of {buf} MiB")
    pref = ((gw.get("spec") or {}).get("infrastructure") or {}).get("parametersRef") or {}
    params = next((a for a in of_kind(docs, "AgentgatewayParameters") if a["metadata"]["name"] == pref.get("name")), {})
    env = {e.get("name"): e.get("value") for e in (params.get("spec") or {}).get("env") or []}
    if env.get("OTEL_BSP_MAX_EXPORT_BATCH_SIZE") != "1":
        bad.append("collector: the proxy may send more than one span in one export "
                   "(OTEL_BSP_MAX_EXPORT_BATCH_SIZE is not 1)")
    # The in-memory span queue of the proxy: each span holds up to two buffers.
    limit = mib((((params.get("spec") or {}).get("resources") or {}).get("limits") or {}).get("memory"))
    queue = int(env.get("OTEL_BSP_MAX_QUEUE_SIZE") or 2048)
    if not limit or queue * 2 * buf > SPAN_QUEUE_SHARE * limit:
        bad.append(f"collector: the span queue of the proxy holds {queue} spans of up to {2 * buf:.0f} MiB, more "
                   f"than {SPAN_QUEUE_SHARE:.0%} of its memory limit of {limit:.0f} MiB")
    return bad


def judge_rbac(docs: list[dict], gw: dict) -> list[str]:
    bad: list[str] = []
    deployer = [b for b in docs if b.get("kind") in ("ClusterRoleBinding", "RoleBinding")
                and str(((b.get("roleRef") or {}).get("name") or "")).endswith("-deployer")
                and any(s.get("name") == "agentgateway" for s in b.get("subjects") or [])]
    if not any(b["kind"] == "RoleBinding" and ns(b) == ns(gw) for b in deployer):
        bad.append(f"rbac: no RoleBinding gives the controller its deployer role in {ns(gw)}")
    for b in deployer:
        if b["kind"] == "ClusterRoleBinding" or ns(b) != ns(gw):
            where = "in each namespace" if b["kind"] == "ClusterRoleBinding" else f"in {ns(b)}"
            bad.append(f"rbac: {b['kind']}/{b['metadata']['name']} gives the controller its deployer role {where}")
    return bad


def proxy_image(params: dict) -> str:
    img = (params.get("spec") or {}).get("image") or {}
    ref = "/".join(p for p in (img.get("registry"), img.get("repository")) if p)
    if img.get("tag"):
        ref += f":{img['tag']}"
    if img.get("digest"):
        ref += f"@{img['digest']}"
    return ref


def listed() -> set[str]:
    return {ln.strip() for ln in open(LIST) if ln.strip() and not ln.startswith("#")}


def judge(docs: list[dict], on_list: set[str]) -> list[str]:
    bad: list[str] = []
    gws = [g for g in of_kind(docs, "Gateway") if (g.get("spec") or {}).get("gatewayClassName") == "agentgateway"]
    if len(gws) != 1:
        return [f"want one Gateway of class agentgateway, got {len(gws)}"]
    gw = gws[0]
    name = gw["metadata"]["name"]
    spec = gw.get("spec") or {}
    pols = of_kind(docs, "AgentgatewayPolicy")

    # 7. The gateway namespace, and each reference out of it.
    gw_ns = ns(gw)
    release_ns = ns(next((s for s in of_kind(docs, "Service")
                          if (s["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "ext-authz"), gw))
    if gw_ns == release_ns:
        bad.append(f"namespace: Gateway {name} is in the release namespace {gw_ns}, not in a gateway namespace")
    if gw_ns not in {d["metadata"]["name"] for d in of_kind(docs, "Namespace")}:
        bad.append(f"namespace: the render creates no Namespace {gw_ns} for Gateway {name}")
    for p in pols:
        refs = (p.get("spec") or {}).get("targetRefs") or []
        if any(r.get("kind") == "Gateway" and r.get("name") == name for r in refs) and ns(p) != gw_ns:
            bad.append(f"namespace: the policy {p['metadata']['name']} targets Gateway {name} from namespace {ns(p)}")
        sp = p.get("spec") or {}
        for ref in (((sp.get("traffic") or {}).get("extAuth") or {}).get("backendRef"),
                    ((sp.get("frontend") or {}).get("tracing") or {}).get("backendRef")):
            if ref and ref.get("namespace", ns(p)) != ns(p) and not granted(
                    docs, "agentgateway.dev", "AgentgatewayPolicy", ns(p), "Service", ref.get("name"), ref["namespace"]):
                bad.append(f"namespace: the policy {p['metadata']['name']} names Service {ref['namespace']}/"
                           f"{ref.get('name')} with no ReferenceGrant there")
    for li in spec.get("listeners") or []:
        for r in ((li.get("tls") or {}).get("certificateRefs") or []):
            if r.get("namespace", gw_ns) != gw_ns and not granted(
                    docs, GW_GROUP, "Gateway", gw_ns, "Secret", r.get("name"), r["namespace"]):
                bad.append(f"namespace: listener {li.get('name')} names Secret {r['namespace']}/{r.get('name')} "
                           f"with no ReferenceGrant there")

    # 1. ext-authz on the whole Gateway, before routing, fail closed.
    svc = next((s for s in of_kind(docs, "Service")
                if (s["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "ext-authz"), None)
    grpc_port = next((p["port"] for p in ((svc or {}).get("spec") or {}).get("ports") or [] if p.get("name") == "grpc"), None)
    ok = False
    for p in pols:
        t = (p.get("spec") or {}).get("traffic") or {}
        ea = t.get("extAuth") or {}
        ref = ea.get("backendRef") or {}
        if (targets(p, name, None) and t.get("phase") == "PreRouting"
                and ((p["spec"].get("strategy") or {}).get("inheritance")) == "Override"
                and svc and ref.get("name") == svc["metadata"]["name"] and ref.get("port") == grpc_port
                and ref.get("namespace", ns(p)) == ns(svc)
                and ref.get("kind", "Service") == "Service"
                and "grpc" in ea and ea.get("failureMode") == "FailClosed"
                and any(MODEL_EXPR in str(v) for v in ((ea.get("grpc") or {}).get("requestMetadata") or {}).values())):
            ok = True
    if not ok:
        bad.append(f"ext-authz: no PreRouting, Override, FailClosed gRPC extAuth policy on the whole Gateway {name} "
                   f"that calls the ext-authz Service on its grpc port and sends {MODEL_EXPR}")
    names = {li.get("name") for li in spec.get("listeners") or []}
    if LISTENERS - names:
        bad.append(f"ext-authz: Gateway {name} has no listener {sorted(LISTENERS - names)}")
    for li in sorted(names):
        for p in pols:
            t = (p.get("spec") or {}).get("traffic") or {}
            if targets(p, name, li) and "extAuth" in t:
                bad.append(f"ext-authz: the policy {p['metadata']['name']} sets an extAuth on the listener {li}")
    # The trusted headers go into the request metadata after ext-authz ran.
    meta = {}
    for p in pols:
        t = (p.get("spec") or {}).get("traffic") or {}
        if targets(p, name, None) and t.get("phase") == "PreRouting" and "extAuth" in t:
            meta = ((t.get("transformation") or {}).get("request") or {}).get("metadata") or {}
    for f, expr in META_COPY.items():
        if meta.get(f) != expr:
            bad.append(f"trace: the ext-authz policy does not copy {expr} into the request metadata {f}")

    # 2. No public listener, and TLS on each listener.
    cert_secrets = {(c.get("spec") or {}).get("secretName") for c in of_kind(docs, "Certificate")}
    if spec.get("addresses"):
        bad.append(f"listener: Gateway {name} names addresses {spec['addresses']}")
    for li in spec.get("listeners") or []:
        if li.get("hostname"):
            bad.append(f"listener: {li.get('name')} names the hostname {li['hostname']}")
        tls = li.get("tls") or {}
        refs = tls.get("certificateRefs") or []
        if li.get("protocol") != "HTTPS" or tls.get("mode", "Terminate") != "Terminate":
            bad.append(f"listener: {li.get('name')} has protocol {li.get('protocol')}, want HTTPS with mode Terminate")
        elif not refs or any(r.get("kind", "Secret") != "Secret" or r.get("name") not in cert_secrets for r in refs):
            bad.append(f"listener: {li.get('name')} names no Secret that a rendered Certificate writes")
    pref = (spec.get("infrastructure") or {}).get("parametersRef") or {}
    params = next((a for a in of_kind(docs, "AgentgatewayParameters") if a["metadata"]["name"] == pref.get("name")), None)
    if pref.get("kind") != "AgentgatewayParameters" or params is None:
        bad.append(f"listener: Gateway {name} names no rendered AgentgatewayParameters")
    else:
        stype = ((((params.get("spec") or {}).get("service") or {}).get("spec")) or {}).get("type")
        if stype != "ClusterIP":
            bad.append(f"listener: the proxy Service type is {stype or 'the upstream LoadBalancer'}, want ClusterIP")

        # 3. The proxy image.
        img = (params.get("spec") or {}).get("image") or {}
        ref = proxy_image(params)
        if img.get("registry") != "ghcr.io" or not str(img.get("repository", "")).startswith("zeroroot-ai/mirror/"):
            bad.append(f"image: the proxy image {ref} is not on ghcr.io/zeroroot-ai/mirror/")
        if not DIGEST.match(str(img.get("digest", ""))):
            bad.append(f"image: the proxy image {ref} has no sha256 digest")
        if ref not in on_list:
            bad.append(f"image: the proxy image {ref} is not on airgap/images.txt")

    # 4. The x-gibson-* headers leave the model listener.
    want = header_names(docs) | MUST_STRIP
    strip = [p for p in pols if targets(p, name, "model")
             and ((p["spec"].get("strategy") or {}).get("inheritance")) == "Override"
             and (((p["spec"].get("traffic") or {}).get("headerModifiers") or {}).get("request") or {}).get("remove")]
    removed = {h.lower() for p in strip for h in p["spec"]["traffic"]["headerModifiers"]["request"]["remove"]}
    if not strip:
        bad.append("strip: no Override policy on the listener model removes request headers")
    elif want - removed:
        bad.append(f"strip: the model listener keeps {sorted(want - removed)}; add them to gateway.stripRequestHeaders")
    if strip and removed != GATEWAY_REMOVED:
        bad.append(f"strip: the model listener removes {sorted(removed)}, not gibson gatewayheaders.Removed(): "
                   f"missing {sorted(GATEWAY_REMOVED - removed)}, extra {sorted(removed - GATEWAY_REMOVED)}")

    # 5. The format map.
    routes: dict = {}
    for p in pols:
        if targets(p, name, "model"):
            routes.update(((p["spec"].get("backend") or {}).get("ai") or {}).get("routes") or {})
    if FORMAT_PATHS - set(routes):
        bad.append(f"formats: the model listener has no format for {sorted(FORMAT_PATHS - set(routes))}")

    # 9. The Agent gateway route to the daemon.
    bad += judge_a2a(docs, gw, pols)

    # 10. The egress of the proxy pods.
    bad += judge_egress(docs, gw)

    # 8. The Edge routes of a sandbox.
    bad += judge_edge(edge_config(docs), gw)

    # 11. The daemon ports that accept one peer.
    bad += judge_daemon_ports(docs, gw)

    # 12. The deployer role of the controller.
    bad += judge_rbac(docs, gw)

    # 13. The trace collector and the span export of the proxy.
    bad += judge_collector(docs, gw)

    # 14. Who reaches the listeners.
    bad += judge_ingress(docs, gw)

    # 15. The ReferenceGrants of the gateway namespace.
    bad += judge_grants(docs, gw)

    # 18. The listener names that the tenant-operator gives each route.
    op = next((d for d in of_kind(docs, "Deployment")
               if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/name") == "gibson-tenant-operator"),
              None)
    op_env = {}
    for c in ((((op or {}).get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []:
        op_env.update({e["name"]: e.get("value") for e in c.get("env") or [] if "value" in e})
    names = {li.get("name") for li in (gw.get("spec") or {}).get("listeners") or []}
    model_ls = {r.get("sectionName") for p in pols if p["metadata"]["name"].endswith("-model-strip")
                for r in (p.get("spec") or {}).get("targetRefs") or []}
    agent_ls = {pr.get("sectionName") for r in of_kind(docs, "HTTPRoute") if ns(r) == ns(gw)
                for pr in (r.get("spec") or {}).get("parentRefs") or [] if pr.get("name") == name}
    for var, want in (("GIBSON_MODEL_GATEWAY_LISTENER", model_ls), ("GIBSON_AGENT_GATEWAY_LISTENER", agent_ls)):
        got = op_env.get(var)
        if not got or got not in names or {got} != want:
            bad.append(f"listeners: the tenant-operator sets {var}={got!r}; the chart uses the listener "
                       f"{sorted(x for x in want if x)} of {sorted(names)}")

    # 17. The default proxy image of the controller.
    if ctrl_env := {e["name"]: e.get("value") for e in ((next((d for d in of_kind(docs, "Deployment")
                    if d["metadata"]["name"] == "agentgateway"), {}).get("spec") or {}).get("template", {})
                    .get("spec", {}).get("containers") or [{}])[0].get("env") or []}:
        default = (f"{ctrl_env.get('AGW_PROXY_IMAGE_REGISTRY')}/{ctrl_env.get('AGW_PROXY_IMAGE_REPOSITORY')}:"
                   f"{ctrl_env.get('AGW_PROXY_IMAGE_TAG')}")
        pref = ((gw.get("spec") or {}).get("infrastructure") or {}).get("parametersRef") or {}
        prm = next((a for a in of_kind(docs, "AgentgatewayParameters") if a["metadata"]["name"] == pref.get("name")), {})
        if not prm or default != proxy_image(prm):
            bad.append(f"default: the default proxy image {default} is not the pinned proxy image "
                       f"{proxy_image(prm) if prm else None}")

    # 16. The limits of the controller.
    ctrl = next((d for d in of_kind(docs, "Deployment") if d["metadata"]["name"] == "agentgateway"), None)
    lim = {} if not ctrl else (((ctrl["spec"]["template"]["spec"]["containers"] or [{}])[0].get("resources") or {})
                               .get("limits") or {})
    if not lim.get("cpu") or not lim.get("memory"):
        bad.append(f"limits: the agentgateway controller has limits {lim}; it needs cpu and memory "
                   f"(GOMAXPROCS and GOMEMLIMIT come from them)")

    # 6. The trace policy.
    ok = False
    for p in pols:
        fe = (p.get("spec") or {}).get("frontend") or {}
        added = {a.get("name"): a.get("expression")
                 for a in ((fe.get("tracing") or {}).get("attributes") or {}).get("add") or []}
        if (targets(p, name, None) and (fe.get("http") or {}).get("maxBufferSize")
                and all(added.get(k) == v for k, v in TRACE_FIELDS.items())):
            ok = True
    if not ok:
        bad.append(f"trace: no frontend policy on Gateway {name} sets maxBufferSize and adds {TRACE_FIELDS}")
    return bad


def edge_mutate(ds: list[dict], change) -> None:
    """Apply change to the /gateway/model/ route of the Edge config, in place."""
    cm = next(d for d in ds if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "gibson-envoy")
    edge = yaml.safe_load(cm["data"]["envoy.yaml"])
    route = next(n for n in walk(edge) if isinstance(n, dict) and (n.get("match") or {}).get("prefix")
                 == EDGE_PREFIXES["model"] and "route" in n)
    change(route)
    cm["data"]["envoy.yaml"] = yaml.safe_dump(edge)


def selftest() -> int:
    docs = render(PROFILES[0])
    on_list = listed()
    if judge(docs, on_list):
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(judge(docs, on_list)))
        return 1

    def pol(ds, suffix):
        return next(d for d in ds if d.get("kind") == "AgentgatewayPolicy" and d["metadata"]["name"].endswith(suffix))

    def gateway(ds):
        return next(d for d in ds if d.get("kind") == "Gateway")

    def params(ds):
        return next(d for d in ds if d.get("kind") == "AgentgatewayParameters")

    def proxy_rule(ds):
        return next(d for d in ds if d.get("kind") == "CiliumNetworkPolicy"
                    and d["metadata"].get("namespace") == "gibson-gateway")["spec"]

    def proxy_egress(ds, key):
        return [c for e in proxy_rule(ds)["egress"] for c in e.get(key) or []]

    fixtures = {
        "ext-authz on a route rule, not the Gateway": lambda ds: pol(ds, "-ext-authz")["spec"].update(
            targetRefs=[{"group": GW_GROUP, "kind": "HTTPRoute", "name": "x"}]),
        "ext-authz after routing": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"].pop("phase"),
        "ext-authz fails open": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"]["extAuth"].update(
            failureMode="FailOpen"),
        "ext-authz over HTTP": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"]["extAuth"].update(
            http={}) or pol(ds, "-ext-authz")["spec"]["traffic"]["extAuth"].pop("grpc"),
        "ext-authz with no model name": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"]["extAuth"]["grpc"].pop(
            "requestMetadata"),
        "a public LoadBalancer": lambda ds: params(ds)["spec"].pop("service"),
        "a listener hostname": lambda ds: gateway(ds)["spec"]["listeners"][0].update(hostname="api.example.com"),
        "a Gateway address": lambda ds: gateway(ds)["spec"].update(addresses=[{"value": "203.0.113.1"}]),
        "a proxy image off the mirror": lambda ds: params(ds)["spec"]["image"].update(registry="cr.agentgateway.dev",
                                                                                         repository="agentgateway"),
        "a proxy image with no digest": lambda ds: params(ds)["spec"]["image"].pop("digest"),
        "a header the strip list misses": lambda ds: pol(ds, "-model-strip")["spec"]["traffic"]["headerModifiers"][
            "request"].update(remove=["x-gibson-tenant"]),
        "a strip list that keeps the agent route claim": lambda ds: pol(ds, "-model-strip")["spec"]["traffic"][
            "headerModifiers"]["request"].update(remove=[h for h in pol(ds, "-model-strip")["spec"]["traffic"][
                "headerModifiers"]["request"]["remove"] if h != "x-gibson-route-agent"]),
        "a strip list that keeps the Capability Grant": lambda ds: pol(ds, "-model-strip")["spec"]["traffic"][
            "headerModifiers"]["request"].update(remove=[h for h in pol(ds, "-model-strip")["spec"]["traffic"][
                "headerModifiers"]["request"]["remove"] if h != "x-capability-grant"]),
        "a strip list that keeps the route provider": lambda ds: pol(ds, "-model-strip")["spec"]["traffic"][
            "headerModifiers"]["request"].update(remove=[h for h in pol(ds, "-model-strip")["spec"]["traffic"][
                "headerModifiers"]["request"]["remove"] if h != "x-gibson-route-provider"]),
        "a strip list that keeps an Edge quota header": lambda ds: pol(ds, "-model-strip")["spec"]["traffic"][
            "headerModifiers"]["request"].update(remove=[h for h in pol(ds, "-model-strip")["spec"]["traffic"][
                "headerModifiers"]["request"]["remove"] if h != "x-gibson-identity-subject"]),
        "a strip policy a route can replace": lambda ds: pol(ds, "-model-strip")["spec"].pop("strategy"),
        "a format map with no /v1/models": lambda ds: pol(ds, "-model-formats")["spec"]["backend"]["ai"][
            "routes"].pop("/v1/models"),
        "a trace with no tenant": lambda ds: pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"].update(
            add=[a for a in pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"]["add"]
                 if a["name"] != "gibson.tenant"]),
        "a trace field that reads the caller header": lambda ds: next(
            a for a in pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"]["add"]
            if a["name"] == "gibson.task").update(expression='request.headers["x-gibson-task"]'),
        "no metadata copy of the trusted headers": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"].pop(
            "transformation"),
        "a plain HTTP listener": lambda ds: gateway(ds)["spec"]["listeners"][0].update(protocol="HTTP")
            or gateway(ds)["spec"]["listeners"][0].pop("tls"),
        "a listener certificate no Certificate writes": lambda ds: gateway(ds)["spec"]["listeners"][1]["tls"][
            "certificateRefs"][0].update(name="hand-made-secret"),
        "a Gateway in the release namespace": lambda ds: [d["metadata"].update(namespace="gibson") for d in ds
                                                       if d.get("kind") in ("Gateway", "AgentgatewayPolicy")
                                                       and d["metadata"].get("namespace") == "gibson-gateway"],
        "a Gateway policy in another namespace": lambda ds: pol(ds, "-model-strip")["metadata"].update(
            namespace="gibson"),
        "no ReferenceGrant for the cross-namespace references": lambda ds: [
            ds.remove(d) for d in [d for d in ds if d.get("kind") == "ReferenceGrant"]],
        "an Edge gateway route with no ext_authz": lambda ds: edge_mutate(ds, lambda r: r.update(
            typed_per_filter_config={"envoy.filters.http.ext_authz": {
                "@type": "type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute",
                "disabled": True}})),
        "an Edge gateway route that strips the grant": lambda ds: edge_mutate(ds, lambda r: r.update(
            request_headers_to_remove=r["request_headers_to_remove"] + ["x-capability-grant"])),
        "an Edge gateway route that passes a trusted header": lambda ds: edge_mutate(ds, lambda r: r.update(
            request_headers_to_remove=[h for h in r["request_headers_to_remove"] if h != "x-gibson-task"])),
        "an Edge gateway route that caps a stream": lambda ds: edge_mutate(ds, lambda r: r["route"].update(
            idle_timeout="300s")),
        "an agent listener that removes a trusted header": lambda ds: pol(ds, "-agent-strip")["spec"]["traffic"][
            "headerModifiers"]["request"]["remove"].append("x-gibson-tenant"),
        "an agent listener that passes the grant": lambda ds: pol(ds, "-agent-strip")["spec"]["traffic"][
            "headerModifiers"]["request"].update(remove=["x-gibson-identity-tenant", "x-gibson-identity-subject"]),
        "an agent route with no TLS to the daemon": lambda ds: ds.remove(pol(ds, "-daemon-a2a-tls")),
        "an agent route with a header match": lambda ds: next(d for d in ds if d.get("kind") == "HTTPRoute"
                                                              and d["metadata"]["name"].endswith("-agents"))[
            "spec"]["rules"][0]["matches"][0].update(headers=[{"name": "x-gibson-route-agent", "value": "a"}]),
        "a daemon A2A port with no a2a appProtocol": lambda ds: [p.pop("appProtocol") for d in ds
                                                                 if d.get("kind") == "Service"
                                                                 for p in d["spec"].get("ports") or []
                                                                 if p.get("name") == "a2a"],
        "no agent listener": lambda ds: gateway(ds)["spec"].update(
            listeners=[li for li in gateway(ds)["spec"]["listeners"] if li["name"] != "agent"]),
        "an agent listener policy that replaces ext-authz": lambda ds: ds.append({
            "apiVersion": "agentgateway.dev/v1alpha1", "kind": "AgentgatewayPolicy",
            "metadata": {"name": "open-agent"},
            "spec": {"targetRefs": [{"group": GW_GROUP, "kind": "Gateway", "name": gateway(ds)["metadata"]["name"],
                                     "sectionName": "agent"}],
                     "traffic": {"extAuth": {"backendRef": {"name": "other", "port": 1}, "grpc": {},
                                             "failureMode": "FailOpen"}}}}),
        "a trace with the default buffer": lambda ds: pol(ds, "-trace")["spec"]["frontend"].pop("http"),
        "a trace with no route agent": lambda ds: pol(ds, "-trace")["spec"]["frontend"]["tracing"][
            "attributes"].update(add=[a for a in pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"][
                "add"] if a["name"] != "gibson.route_agent"]),
        "a route agent field that reads the header": lambda ds: next(
            a for a in pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"]["add"]
            if a["name"] == "gibson.route_agent").update(expression='request.headers["x-gibson-route-agent"]'),
        "no metadata copy of the route agent": lambda ds: pol(ds, "-ext-authz")["spec"]["traffic"][
            "transformation"]["request"]["metadata"].pop("route_agent"),
        "a proxy policy that allows a private range": lambda ds: [c["except"].remove("10.0.0.0/8")
                                                               for c in proxy_egress(ds, "toCIDRSet")
                                                               if c["cidr"] == "0.0.0.0/0"],
        "a proxy policy that allows the world entity": lambda ds: proxy_rule(ds)["egress"].append(
            {"toEntities": ["world"]}),
        "a proxy policy that allows the whole cluster": lambda ds: proxy_rule(ds)["egress"].append(
            {"toEntities": ["cluster"]}),
        "a proxy policy that allows each pod of a namespace": lambda ds: proxy_rule(ds)["egress"].append(
            {"toEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "models"}}],
             "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]}),
        "a proxy policy that allows a host name": lambda ds: proxy_rule(ds)["egress"].append(
            {"toFQDNs": [{"matchPattern": "*"}]}),
        "a proxy policy with no deny rule for the metadata address": lambda ds: proxy_rule(ds)["egressDeny"][1].update(
            toCIDRSet=[c for c in proxy_rule(ds)["egressDeny"][1]["toCIDRSet"] if c["cidr"] != "169.254.0.0/16"]),
        "no deny rule on the daemon A2A port": lambda ds: ds.remove(next(
            d for d in ds if d.get("kind") == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-daemon-a2a")),
        "no deny rule on the daemon trace port": lambda ds: ds.remove(next(
            d for d in ds if d.get("kind") == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-daemon-trace")),
        "a deny rule on the trace port that refuses the collector": lambda ds: next(
            d for d in ds if d.get("kind") == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-daemon-trace")[
            "spec"]["ingressDeny"][0].update(fromEndpoints=[{}]),
        "a tenant route listener that the Gateway does not name": lambda ds: [
            e.update(value="models") for d in ds if d.get("kind") == "Deployment"
            and d["metadata"].get("labels", {}).get("app.kubernetes.io/name") == "gibson-tenant-operator"
            for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env") or []
            if e["name"] == "GIBSON_MODEL_GATEWAY_LISTENER"],
        "a default proxy image with no digest": lambda ds: [
            e.update(value="v1.7.0") for e in next(
                d for d in ds if d.get("kind") == "Deployment" and d["metadata"]["name"] == "agentgateway"
            )["spec"]["template"]["spec"]["containers"][0]["env"] if e["name"] == "AGW_PROXY_IMAGE_TAG"],
        "a controller with no memory limit": lambda ds: next(
            d for d in ds if d.get("kind") == "Deployment" and d["metadata"]["name"] == "agentgateway"
        )["spec"]["template"]["spec"]["containers"][0]["resources"]["limits"].pop("memory"),
        "a route that may name the ext-authz Service": lambda ds: next(
            d for d in ds if d.get("kind") == "ReferenceGrant" and d["metadata"]["name"].endswith("-route")
        )["spec"]["to"].append({"group": "", "kind": "Service", "name": "gibson-ext-authz"}),
        "a sandbox namespace admitted to the listeners": lambda ds: proxy_rule(ds)["ingress"][0]["fromEndpoints"].append(
            {"matchLabels": {"k8s:" + SANDBOX_NS_LABEL: "true"},
             "matchExpressions": [{"key": "k8s:io.kubernetes.pod.namespace", "operator": "Exists"}]}),
        "a collector queue limited by count": lambda ds: [
            c["data"].update({"collector.yaml": c["data"]["collector.yaml"].replace("sizer: bytes", "sizer: requests")})
            for c in ds if c.get("kind") == "ConfigMap" and c["metadata"]["name"] == "gibson-trace-collector"],
        "a collector queue too large for its PVC": lambda ds: [
            c["data"].update({"collector.yaml": re.sub(r"queue_size: \d+", "queue_size: 3221225472",
                                                        c["data"]["collector.yaml"])})
            for c in ds if c.get("kind") == "ConfigMap" and c["metadata"]["name"] == "gibson-trace-collector"],
        "a proxy span queue that passes half of its memory": lambda ds: [
            e.update(value="64") for e in params(ds)["spec"]["env"] if e["name"] == "OTEL_BSP_MAX_QUEUE_SIZE"],
        "a proxy that batches spans": lambda ds: params(ds)["spec"].update(
            env=[e for e in params(ds)["spec"]["env"] if e["name"] != "OTEL_BSP_MAX_EXPORT_BATCH_SIZE"]),
        "a deployer role bound in each namespace": lambda ds: next(
            d for d in ds if d.get("kind") == "RoleBinding" and d["metadata"]["name"] == "agentgateway-write-role-gibson"
        ).update(kind="ClusterRoleBinding"),
        "a deployer role bound in the release namespace": lambda ds: next(
            d for d in ds if d.get("kind") == "RoleBinding" and d["metadata"]["name"] == "agentgateway-write-role-gibson"
        )["metadata"].update(namespace="gibson"),
        "a proxy policy that allows the NAT64 prefix": lambda ds: [c["except"].remove("64:ff9b::/96")
                                                                 for c in proxy_egress(ds, "toCIDRSet")
                                                                 if c["cidr"] == "::/0"],
        "a proxy policy with no deny rule for the NAT64 prefix": lambda ds: proxy_rule(ds)["egressDeny"][1].update(
            toCIDRSet=[c for c in proxy_rule(ds)["egressDeny"][1]["toCIDRSet"] if c["cidr"] != "64:ff9b::/96"]),
        "a proxy policy with no deny rule for the node": lambda ds: proxy_rule(ds)["egressDeny"][0].update(
            toEntities=["remote-node", "kube-apiserver"]),
    }
    failed = 0
    for desc, mutate in fixtures.items():
        ds = copy.deepcopy(docs)
        mutate(ds)
        if not judge(ds, on_list):
            print(f"SELFTEST FAIL: {desc} passed")
            failed += 1
    ref = proxy_image(params(docs))
    if not judge(docs, on_list - {ref}):
        print("SELFTEST FAIL: a proxy image missing from the air-gap list passed")
        failed += 1
    if failed:
        return 1
    print(f"OK: {len(fixtures) + 1} broken fixtures fail, the baseline render passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    on_list = listed()
    bad = []
    for p in PROFILES:
        bad += [f"{'+'.join(p)}: {b}" for b in judge(render(p), on_list)]
    if bad:
        print("❌ the agentgateway Gateway lost a platform policy (charts#815):\n  " + "\n  ".join(bad))
        return 1
    print("✓ agentgateway: ext-authz before routing on both listeners, TLS and no public listener, a pinned "
          "mirror proxy image, header strip, format map, trace fields, the Edge routes of a sandbox and the "
          "proxy egress (public addresses only) on each profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
