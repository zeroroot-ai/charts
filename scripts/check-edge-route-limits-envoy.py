#!/usr/bin/env python3
"""check-edge-route-limits-envoy.py: the pinned Envoy accepts and enforces the keyed route limiters.

The three routes with no Zitadel JWT (/a2a/inbound/, /gateway/model/,
/gateway/agent/) carry a local_ratelimit keyed on the client address
(masked_remote_address, a wildcard descriptor, max_dynamic_descriptors). The
descriptor fields of local_ratelimit are easy to get wrong, and a config that
Envoy refuses stops the Edge at start. A render guard reads YAML. It cannot say
what Envoy does with it. This check runs the pinned Envoy image of the chart.

It renders the umbrella with one trusted egress address, takes the rendered
typed_per_filter_config of each of the three routes as it is, and puts them
into a small bootstrap: one listener on 127.0.0.1 with use_remote_address, the
local_ratelimit filter and the router, and one route for each prefix that
answers 200 directly. Then:

  1. validate  `envoy --mode validate` accepts the bootstrap.
  2. behavior  Envoy runs on the host network. Each client binds its own
               loopback source address:
                 - client A (127.0.0.2) sends ten times its rate on each route
                   and gets 429 for some requests;
                 - client B (127.0.0.4) then gets 200 on each route, because
                   its bucket is its own;
                 - client T (the trusted address) sends more than the bucket
                   of one address on /gateway/model/ and gets no 429.

  check-edge-route-limits-envoy.py             run both steps
  check-edge-route-limits-envoy.py --selftest  prove a broken config fails
                                               validation and a keyless
                                               bucket fails the behavior step

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
import tempfile
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXES = ("/a2a/inbound/", "/gateway/model/", "/gateway/agent/")
LRL = "envoy.filters.http.local_ratelimit"
TRUSTED = "127.0.0.3"
CLIENT_A, CLIENT_B = "127.0.0.2", "127.0.0.4"


def render() -> tuple[dict, str, dict]:
    """(the per-route limiter of each prefix, the Envoy image, the rate values)."""
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
            "--set", f"gibson-workloads.envoy.rateLimit.gatewayForward.trustedSourceAddresses={{{TRUSTED}/32}}"]
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
    values = yaml.safe_load(open(os.path.join(ROOT, "helm", "gibson-workloads", "values.yaml")))
    return limiters, image, values["envoy"]["rateLimit"]


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


def write(cfg: dict, d: str) -> str:
    path = os.path.join(d, "envoy.yaml")
    with open(path, "w") as f:
        yaml.safe_dump(cfg, f)
    os.chmod(d, 0o755)
    os.chmod(path, 0o644)
    return path


def validate(cfg: dict, image: str) -> tuple[bool, str]:
    with tempfile.TemporaryDirectory() as d:
        write(cfg, d)
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{d}:/cfg:ro", image,
                            "--mode", "validate", "-c", "/cfg/envoy.yaml"], capture_output=True, text=True)
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
    with tempfile.TemporaryDirectory() as d:
        write(cfg, d)
        cid = subprocess.run(["docker", "run", "-d", "--rm", "--network", "host", "-v", f"{d}:/cfg:ro", image,
                              "-c", "/cfg/envoy.yaml", "--log-level", "warn"],
                             capture_output=True, text=True, check=True).stdout.strip()
        try:
            for _ in range(100):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                    break
                except OSError:
                    time.sleep(0.1)
            per = {"/a2a/inbound/": rates["a2aInbound"]["perAddress"],
                   "/gateway/model/": rates["gatewayForward"]["perAddress"],
                   "/gateway/agent/": rates["gatewayForward"]["perAddress"]}
            for path in PREFIXES:
                flood = 10 * int(per[path]["maxTokens"])
                a = send(CLIENT_A, port, path, flood)
                b = send(CLIENT_B, port, path, 5)
                print(f"  {path}: client A sent {flood}: {json.dumps(a)}; client B sent 5: {json.dumps(b)}")
                if not a.get(429):
                    bad.append(f"behavior: {path}: client A sent {flood} and got no 429")
                if b.get(200) != 5:
                    bad.append(f"behavior: {path}: client B got {b} after client A flooded, want 5 x 200")
            many = 2 * int(rates["gatewayForward"]["perAddress"]["maxTokens"])
            t = send(TRUSTED, port, "/gateway/model/", many)
            print(f"  /gateway/model/: trusted {TRUSTED} sent {many}: {json.dumps(t)}")
            if t.get(429):
                bad.append(f"behavior: the trusted address got 429 below the bucket of a trusted address")
        finally:
            subprocess.run(["docker", "rm", "-f", cid], capture_output=True)
    return bad


def run(limiters: dict, image: str, rates: dict) -> list[str]:
    missing = [p for p in PREFIXES if not limiters.get(p)]
    if missing:
        return [f"render: no local_ratelimit on {missing}"]
    cfg = bootstrap(limiters, free_port())
    ok, last = validate(cfg, image)
    print(f"  envoy --mode validate ({image.split('@')[0]}): {'OK' if ok else 'REFUSED'}: {last}")
    if not ok:
        return [f"validate: the pinned Envoy refuses the route limiters: {last}"]
    return behave(cfg, image, rates)


def selftest() -> int:
    limiters, image, rates = render()
    broken = copy.deepcopy(limiters)
    broken["/a2a/inbound/"]["descriptors"][0]["token_bucket"]["fill_interval"] = "0.01s"
    ok, _ = validate(bootstrap(broken, free_port()), image)
    if ok:
        print("SELFTEST FAIL: a descriptor with a 10 ms fill interval passed validation")
        return 2
    keyless = copy.deepcopy(limiters)
    for p in PREFIXES:
        keyless[p].pop("rate_limits", None)
        keyless[p].pop("descriptors", None)
        keyless[p].pop("max_dynamic_descriptors", None)
        keyless[p]["token_bucket"] = {"max_tokens": 5, "tokens_per_fill": 1, "fill_interval": "60s"}
    if not behave(bootstrap(keyless, free_port()), image, rates):
        print("SELFTEST FAIL: a keyless route bucket passed the behavior step")
        return 2
    print("✓ selftest: a bad fill interval fails validation, and a keyless bucket fails the behavior step")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    bad = run(*render())
    if bad:
        print("❌ the keyed route limiters of the Edge do not hold:\n  " + "\n  ".join(bad))
        return 1
    print("✓ edge-route-limits-envoy: the pinned Envoy accepts the keyed route limiters, a flooding client "
          "gets 429 while another client gets 200, and a trusted egress address gets its large bucket")
    return 0


if __name__ == "__main__":
    sys.exit(main())
