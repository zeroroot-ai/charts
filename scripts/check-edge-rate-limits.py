#!/usr/bin/env python3
"""check-edge-rate-limits.py — a limiter that renders fine and enforces nothing.

THREE WAYS THE EDGE'S RATE LIMITING IS WRONG WITHOUT LOOKING WRONG

1. `filter_enabled` and `filter_enforced` BOTH DEFAULT TO 0% WHEN OMITTED.
   A `local_ratelimit` that omits them renders as a fully configured filter,
   reports as configured in Envoy's config dump, and enforces nothing. This is
   the sharpest "renders fine when wrong" case on the edge, because the thing
   that is missing is the thing that makes it a limiter.

2. A DESCRIPTOR UNDER AN HCM THAT DOES NOT TRUST THE PEER KEYS EVERY CLIENT TO
   ONE BUCKET. `masked_remote_address` takes the address Envoy believes the
   client has. Without `use_remote_address: true` that comes from a
   client-supplied `x-forwarded-for`, which the NLB does not set because it is
   L4. One attacker then shares a bucket with everyone, or evades it entirely
   by varying the header.

3. A QUOTA ABOVE ITS FUSE NEVER APPLIES. Each route carries a `local_ratelimit`
   fuse and may also send a descriptor to the shared quota service. The fuse is
   per Envoy replica and trips first; the quota is exact across replicas and is
   the number anybody reasons about. A quota above the fuse is a number in a
   config file that no request ever reaches.

All three hold today. None of them was asserted: the chart's own comments
claimed a bats suite that has never existed in this repository (charts#322).

  check-edge-rate-limits.py             exit 1 on any of the three
  check-edge-rate-limits.py --selftest  prove each one fails on a fixture
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Two different filters share the words "local ratelimit", and only one of them
# has the fields this guard is about. The HTTP filter gates per request and
# carries filter_enabled / filter_enforced. The NETWORK filter gates per
# connection, carries a token bucket and nothing else, and always enforces —
# there is no percentage to omit, so it cannot have this defect.
HTTP_LOCAL_RL = "envoy.extensions.filters.http.local_ratelimit"
NETWORK_LOCAL_RL = "envoy.extensions.filters.network.local_ratelimit"
FULL = {"numerator": 100, "denominator": "HUNDRED"}


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml",
         "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def configs(docs: list[dict]) -> tuple[dict, dict]:
    """(the rendered Envoy bootstrap, the quota service's descriptor table)."""
    envoy = quota = None
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        data = d.get("data") or {}
        name = (d.get("metadata") or {}).get("name", "")
        if name.endswith("-envoy") and "envoy.yaml" in data:
            envoy = yaml.safe_load(data["envoy.yaml"])
        if name.endswith("-ratelimit-config") and "config.yaml" in data:
            quota = yaml.safe_load(data["config.yaml"])
    return envoy, quota


def seconds(interval) -> float:
    """Envoy durations are '1s', '0.5s', '60s' — or a {seconds, nanos} message."""
    if isinstance(interval, dict):
        return float(interval.get("seconds", 0)) + float(interval.get("nanos", 0)) / 1e9
    m = re.fullmatch(r"([0-9.]+)s", str(interval or ""))
    return float(m.group(1)) if m else 0.0


UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def rate_per_second(bucket: dict) -> float:
    """The SUSTAINED rate of a token bucket, not its burst.

    tokens_per_fill is what refills each interval; max_tokens alone is the
    burst the bucket can hold. A bucket that refills 4000 per second sustains
    4000/s however large max_tokens is.
    """
    per_fill = bucket.get("tokens_per_fill", bucket.get("max_tokens"))
    interval = seconds(bucket.get("fill_interval"))
    return float(per_fill) / interval if per_fill and interval else 0.0


def walk(node, path=()):
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from walk(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, path + (i,))


def is_local_rl(cfg: dict) -> bool:
    """The HTTP local rate limit, the one with a percentage to get wrong."""
    return isinstance(cfg, dict) and HTTP_LOCAL_RL in str(cfg.get("@type", ""))


def is_network_rl(cfg: dict) -> bool:
    return isinstance(cfg, dict) and NETWORK_LOCAL_RL in str(cfg.get("@type", ""))


def hcms(envoy: dict) -> list[dict]:
    return [n for _, n in walk(envoy)
            if isinstance(n.get("@type"), str) and "HttpConnectionManager" in n["@type"]]


def routes_with_actions(node):
    """Every route that sends descriptors, with its own per-filter limiter."""
    for _, n in walk(node):
        if not isinstance(n.get("route"), dict):
            continue
        if not n["route"].get("rate_limits"):
            continue
        tpf = n.get("typed_per_filter_config") or {}
        own = next((v for v in tpf.values() if is_local_rl(v)), None)
        yield n, own


def descriptor_value(route: dict) -> str | None:
    for group in route["route"].get("rate_limits") or []:
        for action in group.get("actions") or []:
            gk = action.get("generic_key")
            if gk and gk.get("descriptor_key") == "route":
                return gk.get("descriptor_value")
    return None


def quota_rates(quota: dict) -> dict[str, float]:
    """route descriptor value -> the tightest quota under it, per second."""
    out: dict[str, float] = {}

    def deepest(entry) -> list[dict]:
        inner = entry.get("descriptors") or []
        if not inner:
            return [entry]
        return [d for i in inner for d in deepest(i)]

    for top in (quota or {}).get("descriptors") or []:
        if top.get("key") != "route" or not top.get("value"):
            continue
        rates = []
        for leaf in deepest(top):
            rl = leaf.get("rate_limit")
            if rl and rl.get("requests_per_unit") and rl.get("unit"):
                per = UNIT_SECONDS.get(str(rl["unit"]).lower())
                if per:
                    rates.append(float(rl["requests_per_unit"]) / per)
        if rates:
            out[top["value"]] = max(rates)
    return out


def judge(envoy: dict, quota: dict) -> list[str]:
    bad: list[str] = []
    if envoy is None:
        return ["no rendered Envoy ConfigMap in the release"]

    # 1. every local_ratelimit enforces
    seen = 0
    for path, node in walk(envoy):
        if not is_local_rl(node):
            continue
        seen += 1
        where = "/".join(str(p) for p in path[-4:])
        for field in ("filter_enabled", "filter_enforced"):
            got = (node.get(field) or {}).get("default_value")
            if got != FULL:
                bad.append(f"local_ratelimit at {where}: {field} is {got}, want "
                           f"{FULL} — it defaults to 0% when omitted, so the filter "
                           f"renders as configured and enforces nothing")
    if seen == 0:
        bad.append("the render contains no HTTP local_ratelimit at all, so the edge has no fuse")

    # the connection-level fuse has one knob, and an absent bucket is no fuse
    for path, node in walk(envoy):
        if is_network_rl(node) and not rate_per_second(node.get("token_bucket") or {}):
            where = "/".join(str(p) for p in path[-4:])
            bad.append(f"network local_ratelimit at {where} has no usable token_bucket, "
                       f"so the connection-level fuse admits everything")

    # 2. a descriptor needs an HCM that trusts the peer
    for hcm in hcms(envoy):
        trusts = bool(hcm.get("use_remote_address"))
        for _, node in walk(hcm):
            if is_local_rl(node) and node.get("descriptors") and not trusts:
                bad.append(f"HCM {hcm.get('stat_prefix')!r}: a local_ratelimit carries "
                           f"descriptors while use_remote_address is unset, so the address "
                           f"comes from a client-supplied x-forwarded-for and every client "
                           f"shares one bucket")

    # 3. no quota above the fuse that trips first
    rates = quota_rates(quota)
    for hcm in hcms(envoy):
        inherited = next((rate_per_second(f["typed_config"]["token_bucket"])
                          for f in hcm.get("http_filters") or []
                          if is_local_rl(f.get("typed_config") or {})
                          and (f["typed_config"].get("token_bucket"))), None)
        for route, own in routes_with_actions(hcm):
            value = descriptor_value(route)
            if value is None or value not in rates:
                continue
            fuse = rate_per_second(own["token_bucket"]) if own and own.get("token_bucket") else inherited
            if not fuse:
                continue
            if rates[value] >= fuse:
                match = (route.get("match") or {})
                bad.append(f"route {match.get('prefix') or match.get('path')!r} sends descriptor "
                           f"route={value}: its quota is {rates[value]:.3g}/s and the "
                           f"local_ratelimit fuse in front of it is {fuse:.3g}/s, so the fuse "
                           f"trips first and the quota never applies")
    return bad


# --------------------------------------------------------------- fixtures ---
def _rl(enabled=FULL, enforced=FULL, bucket=None, descriptors=None):
    out = {"@type": "type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit",
           "stat_prefix": "fixture"}
    if enabled is not None:
        out["filter_enabled"] = {"default_value": enabled}
    if enforced is not None:
        out["filter_enforced"] = {"default_value": enforced}
    if bucket:
        out["token_bucket"] = bucket
    if descriptors:
        out["descriptors"] = descriptors
    return out


def _envoy(*, hcm_bucket=None, route_rl=None, trusts=True, hcm_rl=None):
    route = {"match": {"prefix": "/x"},
             "route": {"rate_limits": [{"actions": [
                 {"generic_key": {"descriptor_key": "route", "descriptor_value": "x"}}]}]}}
    if route_rl:
        route["typed_per_filter_config"] = {"envoy.filters.http.local_ratelimit": route_rl}
    hcm = {"@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
           "stat_prefix": "fixture_ingress",
           "route_config": {"virtual_hosts": [{"name": "v", "routes": [route]}]},
           "http_filters": []}
    if trusts:
        hcm["use_remote_address"] = True
    filt = hcm_rl if hcm_rl is not None else _rl(bucket=hcm_bucket or {"max_tokens": 100, "tokens_per_fill": 100, "fill_interval": "1s"})
    hcm["http_filters"].append({"name": "envoy.filters.http.local_ratelimit", "typed_config": filt})
    return {"static_resources": {"listeners": [{"filter_chains": [{"filters": [{"typed_config": hcm}]}]}]}}


def _with_network(envoy, bucket):
    chain = envoy["static_resources"]["listeners"][0]["filter_chains"][0]
    chain["filters"].insert(0, {"name": "envoy.filters.network.local_ratelimit",
                                "typed_config": dict({"@type":
                                    "type.googleapis.com/" + NETWORK_LOCAL_RL + ".v3.LocalRateLimit",
                                    "stat_prefix": "conn"}, **({"token_bucket": bucket} if bucket else {}))})
    return envoy


def _quota(per_unit, unit="second"):
    return {"descriptors": [{"key": "route", "value": "x",
                             "descriptors": [{"key": "masked_remote_address",
                                              "rate_limit": {"requests_per_unit": per_unit, "unit": unit}}]}]}


def selftest() -> int:
    cases = [
        ("a clean edge", _envoy(), _quota(1), 0),
        ("filter_enabled omitted", _envoy(hcm_rl=_rl(enabled=None, bucket={"max_tokens": 100, "tokens_per_fill": 100, "fill_interval": "1s"})), _quota(1), 1),
        ("filter_enforced at 0%", _envoy(hcm_rl=_rl(enforced={"numerator": 0, "denominator": "HUNDRED"}, bucket={"max_tokens": 100, "tokens_per_fill": 100, "fill_interval": "1s"})), _quota(1), 1),
        ("descriptors without use_remote_address",
         _envoy(trusts=False, hcm_rl=_rl(bucket={"max_tokens": 100, "tokens_per_fill": 100, "fill_interval": "1s"},
                                         descriptors=[{"entries": [{"key": "masked_remote_address"}]}])), _quota(1), 1),
        ("a quota at the fuse", _envoy(hcm_bucket={"max_tokens": 10, "tokens_per_fill": 10, "fill_interval": "1s"}), _quota(10), 1),
        ("a quota above the fuse", _envoy(hcm_bucket={"max_tokens": 10, "tokens_per_fill": 10, "fill_interval": "1s"}), _quota(600, "minute"), 1),
        ("a route fuse overriding a wide HCM fuse",
         _envoy(hcm_bucket={"max_tokens": 4000, "tokens_per_fill": 4000, "fill_interval": "1s"},
                route_rl=_rl(bucket={"max_tokens": 2, "tokens_per_fill": 2, "fill_interval": "1s"})), _quota(5), 1),
        ("units converted, not compared raw", _envoy(hcm_bucket={"max_tokens": 20, "tokens_per_fill": 20, "fill_interval": "1s"}), _quota(600, "minute"), 0),
        # the NETWORK filter has no percentage, so it must not be judged for one
        ("a network local_ratelimit is not an HTTP one", _with_network(_envoy(), {"max_tokens": 50, "fill_interval": "1s"}), _quota(1), 0),
        ("a network local_ratelimit with no bucket", _with_network(_envoy(), {}), _quota(1), 1),
    ]
    for name, envoy, quota, want in cases:
        got = judge(envoy, quota)
        if len(got) != want:
            print(f"SELFTEST FAIL: {name} must yield {want} finding(s), got {len(got)}: {got}")
            return 1
    live = judge(*configs(render()))
    if live:
        print("SELFTEST FAIL: the live render is not clean:\n  " + "\n  ".join(live))
        return 1
    print("OK: an omitted filter_enabled, a 0% filter_enforced, a descriptor under an "
          "untrusted peer, a quota at or above its fuse, a route fuse tighter than the quota "
          "and a bucketless connection fuse all fail; a clean edge, a minute-to-second "
          "conversion and a connection-level filter all pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = judge(*configs(render()))
    if bad:
        print("❌ the edge's rate limiting does not hold:\n  " + "\n  ".join(bad))
        return 1
    print("✓ edge-rate-limits: every local_ratelimit enforces at 100%, no descriptor keys off "
          "an untrusted peer address, and every quota sits below the fuse in front of it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
