#!/usr/bin/env python3
"""check-edge-route-limits-envoy.py: the pinned Envoy accepts and enforces the keyed route limiters.

The three routes with no Zitadel JWT (/a2a/inbound/, /gateway/model/,
/gateway/agent/) carry a local_ratelimit keyed on the client address
(masked_remote_address, a wildcard descriptor, max_dynamic_descriptors). The
descriptor fields of local_ratelimit are easy to get wrong, and a config that
Envoy refuses stops the Edge at start. A render guard reads YAML. It cannot say
what Envoy does with it. This check runs the pinned Envoy image of the chart.

It renders the umbrella, takes the rendered typed_per_filter_config of each of
the three routes as it is, and puts them into a small bootstrap: one listener
on 127.0.0.1 with use_remote_address, the local_ratelimit filter and the
router, and one route for each prefix that answers 200 directly. Envoy gets
the bootstrap with --config-yaml, so no file is written. Then:

  1. validate  `envoy --mode validate` accepts the bootstrap.
  2. behavior  Envoy runs on the host network. Each client binds its own
               loopback source address:
                 - client A (127.0.0.2) floods /a2a/inbound/ and
                   /gateway/model/ and gets 429 for some requests;
                 - client B (127.0.0.4) then gets 200 on both, because its
                   bucket is its own;
                 - on /gateway/agent/, several addresses each stay inside
                   their own bucket, together they pass the route fuse, and
                   some of them get 429.

  check-edge-route-limits-envoy.py             run both steps
  check-edge-route-limits-envoy.py --selftest  prove a broken config fails
                                               validation, a keyless bucket
                                               fails the isolation proof and
                                               a missing fuse fails the fuse
                                               proof

Needs docker and a login to ghcr.io for the mirror image (CI has both).
"""
from __future__ import annotations

import copy
import http.client
import json
import os
import socket
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXES = ("/a2a/inbound/", "/gateway/model/", "/gateway/agent/")
LRL = "envoy.filters.http.local_ratelimit"
CLIENT_A, CLIENT_B = "127.0.0.2", "127.0.0.4"
# The addresses of the fuse proof: each stays inside its own bucket.
FUSE_CLIENTS = tuple(f"127.0.0.{i}" for i in range(10, 16))


# Small numbers for the behavior step. A Python client sends a few thousand
# requests each second, below the refill of the real fuse (4000 each second), so
# the behavior step renders the same templates with these numbers. The
# validation step uses the shipped values.
SMALL = {"a2aInbound": {"maxTokens": 60, "tokensPerFill": 1, "perAddress": {"maxTokens": 20, "tokensPerFill": 1}},
         "gatewayForward": {"maxTokens": 60, "tokensPerFill": 1, "perAddress": {"maxTokens": 20, "tokensPerFill": 1}}}


def render(small: bool = False) -> tuple[dict, str, dict]:
    """(the per-route limiter of each prefix, the Envoy image, the rate values)."""
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml"]
    if small:
        args += ["--set-json", "gibson-workloads.envoy.rateLimit.a2aInbound=" + json.dumps(SMALL["a2aInbound"]),
                 "--set-json", "gibson-workloads.envoy.rateLimit.gatewayForward=" + json.dumps(SMALL["gatewayForward"])]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    docs = [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]
    cm = next(d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "gibson-envoy")
    edge = yaml.safe_load(cm["data"]["envoy.yaml"])
    limiters = {}
    stack = [edge]
    while stack:
        n = stack.pop()
        if isinstance(n, dict):
            if (n.get("match") or {}).get("prefix") in PREFIXES and "route" in n:
                limiters[n["match"]["prefix"]] = (n.get("typed_per_filter_config") or {}).get(LRL)
            stack.extend(n.values())
        elif isinstance(n, list):
            stack.extend(n)
    dep = next(d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"] == "gibson-envoy")
    image = next(c["image"] for c in dep["spec"]["template"]["spec"]["containers"] if c["name"] == "envoy")
    values = yaml.safe_load(open(os.path.join(ROOT, "helm", "gibson-workloads", "values.yaml")))["envoy"]["rateLimit"]
    if small:
        for k, v in SMALL.items():
            values[k] = {**values[k], **{x: y for x, y in v.items() if x != "perAddress"},
                         "perAddress": {**values[k]["perAddress"], **v["perAddress"]}}
    return limiters, image, values


def bootstrap(limiters: dict, port: int) -> dict:
    routes = [{"match": {"prefix": p}, "direct_response": {"status": 200, "body": {"inline_string": "ok"}},
               "typed_per_filter_config": {LRL: limiters[p]}} for p in PREFIXES]
    hcm = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "check", "codec_type": "AUTO", "use_remote_address": True, "xff_num_trusted_hops": 0,
        "route_config": {"name": "check", "virtual_hosts": [{"name": "check", "domains": ["*"], "routes": routes}]},
        "http_filters": [
            {"name": LRL, "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit",
                "stat_prefix": "check_filter"}},
            {"name": "envoy.filters.http.router", "typed_config": {
                "@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"}},
        ],
    }
    return {"static_resources": {"listeners": [{
        "name": "check", "address": {"socket_address": {"address": "127.0.0.1", "port_value": port}},
        "filter_chains": [{"filters": [{"name": "envoy.filters.network.http_connection_manager",
                                        "typed_config": hcm}]}]}]}}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def validate(cfg: dict, image: str) -> tuple[bool, str]:
    r = subprocess.run(["docker", "run", "--rm", image, "--mode", "validate", "--config-yaml", json.dumps(cfg)],
                       capture_output=True, text=True)
    lines = (r.stdout + r.stderr).strip().splitlines()
    verdict = next((ln for ln in reversed(lines) if "OK" in ln or "error" in ln.lower()), lines[-1] if lines else "")
    return r.returncode == 0, verdict


def send(src: str, port: int, path: str, n: int) -> dict[int, int]:
    counts: dict[int, int] = {}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5, source_address=(src, 0))
    for _ in range(n):
        conn.request("GET", path)
        r = conn.getresponse()
        r.read()
        counts[r.status] = counts.get(r.status, 0) + 1
    conn.close()
    return counts


def behave(cfg: dict, image: str, rates: dict) -> list[str]:
    bad: list[str] = []
    port = free_port()
    cfg = copy.deepcopy(cfg)
    cfg["static_resources"]["listeners"][0]["address"]["socket_address"]["port_value"] = port
    cid = subprocess.run(["docker", "run", "-d", "--rm", "--network", "host", image,
                          "--config-yaml", json.dumps(cfg), "--log-level", "warn"],
                         capture_output=True, text=True, check=True).stdout.strip()
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        # Isolation: a flooding client does not deny another one.
        per = {"/a2a/inbound/": rates["a2aInbound"]["perAddress"],
               "/gateway/model/": rates["gatewayForward"]["perAddress"]}
        for path, bucket in per.items():
            flood = 3 * int(bucket["maxTokens"])
            a = send(CLIENT_A, port, path, flood)
            b = send(CLIENT_B, port, path, 5)
            print(f"  {path}: client A sent {flood}: {json.dumps(a)}; client B sent 5: {json.dumps(b)}")
            if not a.get(429):
                bad.append(f"isolation: {path}: client A sent {flood} and got no 429")
            if b.get(200) != 5:
                bad.append(f"isolation: {path}: client B got {b} after client A flooded, want 5 x 200")
        # Fuse: addresses that each stay inside their own bucket pass the route
        # fuse together, and the route answers 429.
        gf = rates["gatewayForward"]
        each = int(gf["perAddress"]["maxTokens"]) - int(gf["perAddress"]["tokensPerFill"])
        total = {}
        for src in FUSE_CLIENTS:
            for code, n in send(src, port, "/gateway/agent/", each).items():
                total[code] = total.get(code, 0) + n
        print(f"  /gateway/agent/: {len(FUSE_CLIENTS)} addresses sent {each} each, "
              f"{len(FUSE_CLIENTS) * each} in all against a fuse of {gf['maxTokens']}: {json.dumps(total)}")
        if not total.get(429):
            bad.append(f"fuse: {len(FUSE_CLIENTS)} addresses inside their own buckets passed the route fuse of "
                       f"{gf['maxTokens']} with no 429")
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return bad


def run() -> list[str]:
    limiters, image, _ = render()
    missing = [p for p in PREFIXES if not limiters.get(p)]
    if missing:
        return [f"render: no local_ratelimit on {missing}"]
    ok, last = validate(bootstrap(limiters, free_port()), image)
    print(f"  envoy --mode validate ({image.split('@')[0]}, shipped values): {'OK' if ok else 'REFUSED'}: {last}")
    if not ok:
        return [f"validate: the pinned Envoy refuses the route limiters: {last}"]
    small, image, rates = render(small=True)
    print("  behavior, the same templates with small numbers: one address 20 burst, route fuse 60 burst, "
          "refill 1 each second")
    return behave(bootstrap(small, free_port()), image, rates)


def selftest() -> int:
    limiters, image, _ = render()
    broken = copy.deepcopy(limiters)
    broken["/a2a/inbound/"]["descriptors"][0]["token_bucket"]["fill_interval"] = "0.01s"
    ok, _ = validate(bootstrap(broken, free_port()), image)
    if ok:
        print("SELFTEST FAIL: a descriptor with a 10 ms fill interval passed validation")
        return 2
    limiters, image, rates = render(small=True)
    keyless = copy.deepcopy(limiters)
    for p in PREFIXES:
        keyless[p].pop("rate_limits", None)
        keyless[p].pop("descriptors", None)
        keyless[p].pop("max_dynamic_descriptors", None)
        keyless[p]["token_bucket"] = {"max_tokens": 5, "tokens_per_fill": 1, "fill_interval": "60s"}
    if not any(b.startswith("isolation:") for b in behave(bootstrap(keyless, free_port()), image, rates)):
        print("SELFTEST FAIL: a keyless route bucket passed the isolation proof")
        return 2
    nofuse = copy.deepcopy(limiters)
    nofuse["/gateway/agent/"]["always_consume_default_token_bucket"] = False
    if not any(b.startswith("fuse:") for b in behave(bootstrap(nofuse, free_port()), image, rates)):
        print("SELFTEST FAIL: a gateway route with no fuse passed the fuse proof")
        return 2
    print("✓ selftest: a bad fill interval fails validation, a keyless bucket fails the isolation proof, "
          "and a gateway route with no fuse fails the fuse proof")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    bad = run()
    if bad:
        print("❌ the keyed route limiters of the Edge do not hold:\n  " + "\n  ".join(bad))
        return 1
    print("✓ edge-route-limits-envoy: the pinned Envoy accepts the keyed route limiters, a flooding client "
          "gets 429 while another client gets 200, and many addresses together meet the route fuse")
    return 0


if __name__ == "__main__":
    sys.exit(main())
