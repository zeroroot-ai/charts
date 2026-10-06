#!/usr/bin/env python3
"""check-connector-proxy-caller.py: each connector proxy admits only the daemon.

The connector-operator writes the caller check of each connector proxy from
three env values, and refuses to start without them:

  CONNECTOR_PROXY_OIDC_ISSUER  the SPIRE OIDC issuer (https://)
  CONNECTOR_PROXY_JWKS_URL     the JWKS of that issuer (https://)
  GIBSON_DAEMON_SPIFFE_ID      the one caller the proxy admits (spiffe://)

The check reads each golden render and fails when the connector-operator
Deployment lacks one of them, or when one has the wrong scheme. It fails as
blind when no render holds the Deployment.

  check-connector-proxy-caller.py             exit 1 on a finding
  check-connector-proxy-caller.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
WANT = {"CONNECTOR_PROXY_OIDC_ISSUER": "https://", "CONNECTOR_PROXY_JWKS_URL": "https://",
        "GIBSON_DAEMON_SPIFFE_ID": "spiffe://"}


def judge(dep: dict) -> list[str]:
    env = {e.get("name"): str(e.get("value") or "") for c in dep["spec"]["template"]["spec"]["containers"]
           for e in c.get("env") or []}
    out = []
    for name, scheme in WANT.items():
        if not env.get(name):
            out.append(f"the connector-operator has no {name}")
        elif not env[name].startswith(scheme):
            out.append(f"the connector-operator {name} is {env[name]!r}, want a {scheme} value")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "values-*.yaml"))):
        for d in yaml.safe_load_all(open(f)):
            if (isinstance(d, dict) and d.get("kind") == "Deployment"
                    and ((d.get("metadata") or {}).get("name") or "").endswith("connector-operator")):
                seen += 1
                bad += [f"{os.path.basename(f)}: {x}" for x in judge(d)]
    if not seen:
        bad.append("no golden render holds the connector-operator Deployment: this check is blind")
    return bad, seen


def selftest() -> int:
    def dep(**env):
        return {"spec": {"template": {"spec": {"containers": [
            {"name": "manager", "env": [{"name": k, "value": v} for k, v in env.items()]}]}}}}
    good = {"CONNECTOR_PROXY_OIDC_ISSUER": "https://oidc", "CONNECTOR_PROXY_JWKS_URL": "https://oidc/keys",
            "GIBSON_DAEMON_SPIFFE_ID": "spiffe://td/platform/daemon"}
    if judge(dep(**good)):
        print(f"SELFTEST FAIL: a complete env must pass, got {judge(dep(**good))}")
        return 1
    for name in WANT:
        less = {k: v for k, v in good.items() if k != name}
        if len(judge(dep(**less))) != 1:
            print(f"SELFTEST FAIL: no {name} must give one finding")
            return 1
    if len(judge(dep(**dict(good, CONNECTOR_PROXY_JWKS_URL="http://oidc/keys")))) != 1:
        print("SELFTEST FAIL: an http JWKS must give one finding")
        return 1
    print("  ✓ selftest: each missing value and an http JWKS fail; the complete env passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("a connector proxy cannot check its caller:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ connector-proxy-caller: {seen} renders give the connector-operator the issuer, the JWKS and the daemon ID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
