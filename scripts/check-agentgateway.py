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
  2. listener   the Gateway names an address or a listener hostname, a
                listener is not plain HTTP, the parametersRef does not name
                the rendered AgentgatewayParameters, or those parameters do
                not set the proxy Service to ClusterIP. Any of these makes a
                public listener.
  3. image      the proxy image of the AgentgatewayParameters is not on
                ghcr.io/zeroroot-ai/mirror/, has no sha256 digest, or is not
                on airgap/images.txt.
  4. strip      no policy on the listener `model` removes each x-gibson-*
                header that a rendered object names, with inheritance
                Override.
  5. formats    no backend.ai.routes on the listener `model` names the four
                paths /v1/messages, /v1/responses, /v1/embeddings and
                /v1/models.
  6. trace      no frontend policy on the Gateway sets maxBufferSize and
                adds the trace fields request.body, response.body and
                llm.cost.

  check-agentgateway.py             exit 1 on a finding, 0 when clean
  check-agentgateway.py --selftest  prove each rule fails on a broken fixture
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "airgap", "images.txt")
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
GW_GROUP = "gateway.networking.k8s.io"
FORMAT_PATHS = {"/v1/messages", "/v1/responses", "/v1/embeddings", "/v1/models"}
TRACE_FIELDS = {"request.body", "response.body", "llm.cost"}
MODEL_EXPR = "json(request.body).model"
# A whole header name. A name that ends in "-" is a prefix match of the Edge,
# not a header, and is skipped.
HEADER = re.compile(r"\bx-gibson-[a-z0-9-]+")
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


def header_names(docs: list[dict]) -> set[str]:
    """Each x-gibson-* header that a rendered object names, outside the strip policy."""
    names = set()
    for d in docs:
        if d.get("kind") == "AgentgatewayPolicy":
            continue
        names |= {h for h in HEADER.findall(yaml.safe_dump(d, width=1 << 20)) if not h.endswith("-")}
    return names


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
                and ref.get("kind", "Service") == "Service"
                and "grpc" in ea and ea.get("failureMode") == "FailClosed"
                and any(MODEL_EXPR in str(v) for v in ((ea.get("grpc") or {}).get("requestMetadata") or {}).values())):
            ok = True
    if not ok:
        bad.append(f"ext-authz: no PreRouting, Override, FailClosed gRPC extAuth policy on the whole Gateway {name} "
                   f"that calls the ext-authz Service on its grpc port and sends {MODEL_EXPR}")

    # 2. No public listener.
    if spec.get("addresses"):
        bad.append(f"listener: Gateway {name} names addresses {spec['addresses']}")
    for li in spec.get("listeners") or []:
        if li.get("hostname"):
            bad.append(f"listener: {li.get('name')} names the hostname {li['hostname']}")
        if li.get("protocol") != "HTTP":
            bad.append(f"listener: {li.get('name')} has protocol {li.get('protocol')}, want HTTP inside the cluster")
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
    want = header_names(docs)
    strip = [p for p in pols if targets(p, name, "model")
             and ((p["spec"].get("strategy") or {}).get("inheritance")) == "Override"
             and (((p["spec"].get("traffic") or {}).get("headerModifiers") or {}).get("request") or {}).get("remove")]
    removed = {h.lower() for p in strip for h in p["spec"]["traffic"]["headerModifiers"]["request"]["remove"]}
    if not strip:
        bad.append("strip: no Override policy on the listener model removes request headers")
    elif want - removed:
        bad.append(f"strip: the model listener keeps {sorted(want - removed)}; add them to gateway.stripRequestHeaders")

    # 5. The format map.
    routes: dict = {}
    for p in pols:
        if targets(p, name, "model"):
            routes.update(((p["spec"].get("backend") or {}).get("ai") or {}).get("routes") or {})
    if FORMAT_PATHS - set(routes):
        bad.append(f"formats: the model listener has no format for {sorted(FORMAT_PATHS - set(routes))}")

    # 6. The trace policy.
    ok = False
    for p in pols:
        fe = (p.get("spec") or {}).get("frontend") or {}
        added = {a.get("name") for a in ((fe.get("tracing") or {}).get("attributes") or {}).get("add") or []}
        if targets(p, name, None) and (fe.get("http") or {}).get("maxBufferSize") and TRACE_FIELDS <= added:
            ok = True
    if not ok:
        bad.append(f"trace: no frontend policy on Gateway {name} sets maxBufferSize and adds {sorted(TRACE_FIELDS)}")
    return bad


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
        "a strip policy a route can replace": lambda ds: pol(ds, "-model-strip")["spec"].pop("strategy"),
        "a format map with no /v1/models": lambda ds: pol(ds, "-model-formats")["spec"]["backend"]["ai"][
            "routes"].pop("/v1/models"),
        "a trace with no cost": lambda ds: pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"].update(
            add=[a for a in pol(ds, "-trace")["spec"]["frontend"]["tracing"]["attributes"]["add"]
                 if a["name"] != "llm.cost"]),
        "a trace with the default buffer": lambda ds: pol(ds, "-trace")["spec"]["frontend"].pop("http"),
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
    print("✓ agentgateway: ext-authz before routing, no public listener, a pinned mirror proxy image, "
          "header strip, format map and trace fields on each profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
