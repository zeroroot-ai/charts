#!/usr/bin/env python3
"""check-extauthz-transport.py — no unwaived plaintext ext-authz trust fetch.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1187).
ext-authz fetches the material every authorization decision trusts: the
OIDC discovery document and JWKS behind EXT_AUTHZ_ZITADEL_ISSUER, the
capability-grant keys and the authz registry from the daemon, the FGA
store. A plaintext URL there lets anyone on the path substitute a key set,
so every URL-shaped value in the ext-authz Deployment's environment must be
https. Listen addresses (`:9001`) carry no scheme and spiffe:// values are
identities the peer presents; neither is a fetch.

  check-extauthz-transport.py             exit 1 on a plaintext trust URL, 0 when clean
  check-extauthz-transport.py --selftest  prove an http:// keys URL fails and https passes
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEME = re.compile(r"^([a-z][a-z0-9+.-]*)://", re.I)


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def judge(docs: list[dict]) -> list[str]:
    out = []
    seen = False
    for d in docs:
        if d.get("kind") != "Deployment" or "ext-authz" not in d["metadata"]["name"]:
            continue
        seen = True
        for c in (d["spec"]["template"]["spec"].get("containers") or []):
            for e in c.get("env") or []:
                if not e["name"].startswith("EXT_AUTHZ_"):
                    continue
                v = e.get("value") or ""
                m = SCHEME.match(v)
                # spiffe:// values are identities the peer must present, not
                # addresses ext-authz fetches from.
                if m and m.group(1).lower() not in ("https", "spiffe"):
                    out.append(f"{d['metadata']['name']}: {e['name']}={v} is not https")
    if not seen:
        out.append("no ext-authz Deployment in the render; the guard has nothing to judge")
    return out


FIXTURE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: gibson-ext-authz}
spec:
  template:
    spec:
      containers:
        - name: ext-authz
          env:
            - {name: EXT_AUTHZ_GRPC_ADDR, value: ":9001"}
            - {name: EXT_AUTHZ_ZITADEL_ISSUER, value: "https://app.example.test"}
            - {name: EXT_AUTHZ_DAEMON_SVID, value: "spiffe://zeroroot.ai/platform/daemon"}
            - {name: EXT_AUTHZ_CGJWT_KEYS_URL, value: "http://gibson:8086/capabilitygrant/v1/keys"}
"""


def selftest() -> int:
    got = judge([d for d in yaml.safe_load_all(FIXTURE) if d])
    if len(got) != 1 or "EXT_AUTHZ_CGJWT_KEYS_URL" not in got[0]:
        print(f"SELFTEST FAIL: want exactly the http keys URL flagged, got {got}")
        return 1
    if judge([]) == []:
        print("SELFTEST FAIL: an empty render must fail, not pass silently")
        return 1
    live = judge(render())
    if live:
        print("SELFTEST FAIL: the render has a plaintext trust fetch:\n  " + "\n  ".join(live))
        return 1
    print("OK: an http keys URL fails, https and listen addresses pass, an empty render fails, the render is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(render())
    if got:
        print("❌ ext-authz trust fetch over plaintext:\n  " + "\n  ".join(got))
        return 1
    print("✓ extauthz-transport: every URL ext-authz trusts is https")
    return 0


if __name__ == "__main__":
    sys.exit(main())
