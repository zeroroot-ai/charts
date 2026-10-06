#!/usr/bin/env python3
"""check-extauthz-redis.py: ext-authz has Redis in each profile (ADR-0045, charts#397).

ext-authz refuses to start without Redis, and it keeps the replay state of
component tokens there. A profile that renders the ext-authz pod without the
Redis address, without the Redis password, or without the init container that
waits for Redis would install a pod that cannot start or crash-loops.

The check reads the committed golden renders (helm/testdata/golden/), which
`make golden` keeps equal to the chart, and fails on each rendered ext-authz
Deployment where:

  - the ext-authz container has no non-empty EXT_AUTHZ_REDIS_URL value,
  - the ext-authz container has no REDIS_PASSWORD from a secretKeyRef, or
  - the pod has no wait-for-redis init container.

It fails as blind when no golden file holds an ext-authz Deployment.

  check-extauthz-redis.py             exit 1 on a finding
  check-extauthz-redis.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")


def extauthz(docs):
    for d in docs:
        if (isinstance(d, dict) and d.get("kind") == "Deployment"
                and ((d.get("metadata") or {}).get("name") or "").endswith("-ext-authz")):
            yield d


def judge(d: dict) -> list[str]:
    spec = d["spec"]["template"]["spec"]
    name = d["metadata"]["name"]
    out = []
    main = next((c for c in spec.get("containers") or [] if c.get("name") == "ext-authz"), None)
    if main is None:
        return [f"{name}: no ext-authz container"]
    env = {e.get("name"): e for e in main.get("env") or []}
    if not (env.get("EXT_AUTHZ_REDIS_URL") or {}).get("value"):
        out.append(f"{name}: the ext-authz container has no EXT_AUTHZ_REDIS_URL value")
    if not ((env.get("REDIS_PASSWORD") or {}).get("valueFrom") or {}).get("secretKeyRef"):
        out.append(f"{name}: the ext-authz container has no REDIS_PASSWORD from a Secret")
    if not any(c.get("name") == "wait-for-redis" for c in spec.get("initContainers") or []):
        out.append(f"{name}: the pod has no wait-for-redis init container")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        for d in extauthz(yaml.safe_load_all(open(f))):
            seen += 1
            bad += [f"{os.path.basename(f)}: {x}" for x in judge(d)]
    if not seen:
        bad.append("no golden render holds an ext-authz Deployment: this check is blind")
    return bad, seen


def selftest() -> int:
    def dep(env, init=True):
        return {"kind": "Deployment", "metadata": {"name": "g-ext-authz"},
                "spec": {"template": {"spec": {
                    "initContainers": [{"name": "wait-for-redis"}] if init else [],
                    "containers": [{"name": "ext-authz", "env": env}]}}}}
    url = {"name": "EXT_AUTHZ_REDIS_URL", "value": "redis://:${REDIS_PASSWORD}@r:6379"}
    pw = {"name": "REDIS_PASSWORD", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}}
    if judge(dep([url, pw])):
        print("SELFTEST FAIL: a complete ext-authz pod must pass")
        return 1
    for what, d in (("no URL", dep([pw])), ("an empty URL", dep([dict(url, value=""), pw])),
                    ("no password", dep([url])), ("a literal password", dep([url, {"name": "REDIS_PASSWORD", "value": "x"}])),
                    ("no wait", dep([url, pw], init=False))):
        if len(judge(d)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(d)}")
            return 1
    print("  ✓ selftest: no URL, an empty URL, no password, a literal password and no Redis wait each fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("ext-authz has no Redis in a profile (ADR-0045, charts#397):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ extauthz-redis: {seen} rendered ext-authz pods each have the Redis address, the password and the Redis wait")
    return 0


if __name__ == "__main__":
    sys.exit(main())
