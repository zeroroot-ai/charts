#!/usr/bin/env python3
"""check-zitadel-claimed-host.py — no claimed Zitadel host carries a port.

ADR-0092: an in-cluster Zitadel client CONNECTS to the Zitadel Service and
CLAIMS the public host. Zitadel selects its instance from the claimed host and
stamps it into the issuer, so a port in a claimed host becomes a port in the
issuer. That put ":443" into the issuer on staging (charts#162).

This guard renders the baseline and every shipped values file, and reads the
five places a rendered manifest can claim a host:

  1. an env var named ZITADEL_EXTERNAL_DOMAIN
  2. the login app's CUSTOM_REQUEST_HEADERS (Host and X-Zitadel-*-Host)
  3. the authority of an Envoy JWKS uri (.../oauth/v2/keys): Envoy sends the
     uri authority as the Host header
  4. a curl call that forges `-H "Host: ..."`. No caller may do this at all:
     the claimed host goes in the instance header, from the chart helper.

  5. PlatformBootstrap.spec.zitadel.externalDomain. The platform-operator
     claims it in the instance header and refuses a port since gibson#223.

  check-zitadel-claimed-host.py             exit 1 on a finding
  check-zitadel-claimed-host.py --selftest  prove each of the four shapes fails
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = "helm/gibson/values-baseline.yaml"

JWKS_URI = re.compile(r'uri:\s*"?(https?://([^/"\s]+)/oauth/v2/keys)"?')
REQUEST_HEADERS = re.compile(r'CUSTOM_REQUEST_HEADERS="?([^"\n]*)"?')
CLAIM_HEADER = re.compile(r'^(host|x-zitadel-[a-z]+-host)$', re.I)
CURL_HOST = re.compile(r'''-H\s+["']Host:''')
HAS_PORT = re.compile(r':\d+$')


def helm_template(extra: list[str]) -> str:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", BASELINE,
            "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"] + extra
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def strings(node):
    """Every string in a parsed manifest, at any depth."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)


def envs(node):
    """Every {name, value} env entry in a parsed manifest."""
    if isinstance(node, dict):
        if isinstance(node.get("name"), str) and isinstance(node.get("value"), str):
            yield node["name"], node["value"]
        for v in node.values():
            yield from envs(v)
    elif isinstance(node, list):
        for v in node:
            yield from envs(v)


def judge(rendered: str) -> tuple[list[str], dict[str, int]]:
    """Findings, and how many of each surface the render held."""
    out: list[str] = []
    seen = {"env": 0, "headers": 0, "jwks": 0, "bootstrap": 0}
    for doc in yaml.safe_load_all(rendered):
        if not doc:
            continue
        where = f"{doc.get('kind')}/{(doc.get('metadata') or {}).get('name')}"
        if doc.get("kind") == "PlatformBootstrap":
            seen["bootstrap"] += 1
            claimed = str((((doc.get("spec") or {}).get("zitadel") or {}).get("externalDomain")) or "")
            if not claimed or HAS_PORT.search(claimed) or "://" in claimed:
                out.append(f"{where}: spec.zitadel.externalDomain is {claimed!r}; it must be a bare host with no scheme and no port")
        for name, value in envs(doc):
            if name == "ZITADEL_EXTERNAL_DOMAIN":
                seen["env"] += 1
                if HAS_PORT.search(value):
                    out.append(f"{where}: ZITADEL_EXTERNAL_DOMAIN={value} carries a port")
        for text in strings(doc):
            for m in REQUEST_HEADERS.finditer(text):
                seen["headers"] += 1
                for pair in m.group(1).split(","):
                    key, _, val = pair.partition(":")
                    if CLAIM_HEADER.match(key.strip()) and HAS_PORT.search(val.strip()):
                        out.append(f"{where}: CUSTOM_REQUEST_HEADERS claims {key.strip()}:{val.strip()}, which carries a port")
            for m in JWKS_URI.finditer(text):
                seen["jwks"] += 1
                if HAS_PORT.search(m.group(2)):
                    out.append(f"{where}: the JWKS uri {m.group(1)} sends Host {m.group(2)}, which carries a port")
            if CURL_HOST.search(text):
                out.append(f"{where}: a curl call forges -H \"Host:\". Claim the host in the "
                           f"instance header from gibson.zitadel.env")
    return out, seen


def profiles() -> list[str]:
    found = sorted(glob.glob(os.path.join(ROOT, "helm/gibson/values-*.yaml")))
    return [os.path.relpath(f, ROOT) for f in found if os.path.relpath(f, ROOT) != BASELINE]


FIXTURE = """
kind: Deployment
metadata: {name: fixture}
spec:
  template:
    spec:
      containers:
        - name: c
          env:
            - {name: ZITADEL_EXTERNAL_DOMAIN, value: "%(env)s"}
---
kind: PlatformBootstrap
metadata: {name: fixture-pb}
spec:
  zitadel:
    externalDomain: "%(pb)s"
---
kind: ConfigMap
metadata: {name: fixture-cm}
data:
  login: |
    CUSTOM_REQUEST_HEADERS="Host:%(hdr)s,X-Zitadel-Public-Host:app.example.com"
  envoy: |
    uri: "http://%(jwks)s/oauth/v2/keys"
  script: |
    curl -sS %(curl)s http://gibson-zitadel:8080/management/v1/users
"""
CLEAN = {"env": "app.example.com", "hdr": "app.example.com", "jwks": "app.example.com",
         "pb": "app.example.com",
         "curl": '-H "x-zitadel-instance-host: app.example.com"'}


def selftest() -> int:
    found, seen = judge(FIXTURE % CLEAN)
    if found or min(seen.values()) < 1:
        print(f"selftest: the clean fixture must pass and show every surface: {found} {seen}", file=sys.stderr)
        return 1
    bad = {
        "env": "app.example.com:443",
        "hdr": "app.example.com:30443",
        "jwks": "app.example.com:443",
        "curl": '-H "Host: app.example.com"',
        "pb": "app.example.com:30443",
    }
    for key, value in bad.items():
        found, _ = judge(FIXTURE % {**CLEAN, key: value})
        if len(found) != 1:
            print(f"selftest: a bad {key} ({value}) must give one finding, gave {found}", file=sys.stderr)
            return 1
    print("check-zitadel-claimed-host selftest PASSED (a port in the env var, the login "
          "header, the JWKS authority and the PlatformBootstrap field each fail, and "
          "so does a forged curl Host)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    profs = profiles()
    if not profs:
        print("FAIL: no helm/gibson/values-*.yaml beside the baseline: nothing to read", file=sys.stderr)
        return 1
    problems: list[str] = []
    for label, extra in [(BASELINE, [])] + [(p, ["-f", p]) for p in profs]:
        found, seen = judge(helm_template(extra))
        problems += [f"{label}: {f}" for f in found]
        # A render with none of a surface means the check stopped reading it.
        for surface, n in seen.items():
            if n == 0:
                problems.append(f"{label}: the render holds no {surface} surface, so this check read nothing there")
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-zitadel-claimed-host PASSED (baseline and {len(profs)} shipped profiles: no claimed host carries a port)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
