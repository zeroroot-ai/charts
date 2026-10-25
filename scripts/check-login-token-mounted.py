#!/usr/bin/env python3
"""check-login-token-mounted.py: the Zitadel login pod mounts the PAT file it reads.

The login app reads its service user token from the file that
ZITADEL_SERVICE_USER_TOKEN_FILE names in its dotenv ConfigMap. The upstream
chart mounts the login-client Secret only when FirstInstance.Org.LoginClient
is set, and the umbrella sets that block to null (charts#758). The pod then
waited for the file until its liveness probe killed it, on each rollout.

This guard renders each profile and fails when the login Deployment has no
Secret volume mounted at the directory of that file.

  check-login-token-mounted.py             exit 1 on a finding, 0 when clean
  check-login-token-mounted.py --selftest  prove that each fixture fails
"""
from __future__ import annotations

import copy
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
TOKEN = re.compile(r'^ZITADEL_SERVICE_USER_TOKEN_FILE="?([^"\n]+)"?', re.M)


def render(values: tuple[str, ...]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml"]
    for v in values:
        args += ["-f", f"helm/gibson/{v}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def login_deployment(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        labels = (d.get("metadata") or {}).get("labels") or {}
        if labels.get("app.kubernetes.io/name") == "zitadel-login":
            return d
    return None


def findings(docs: list[dict]) -> list[str]:
    dep = login_deployment(docs)
    if dep is None:
        return ["the render holds no zitadel-login Deployment: this guard is blind"]
    spec = dep["spec"]["template"]["spec"]
    secrets = {v["name"] for v in spec.get("volumes") or [] if "secret" in v}
    cms = {v["name"]: v["configMap"]["name"] for v in spec.get("volumes") or [] if "configMap" in v}
    files = []
    for d in docs:
        if d.get("kind") == "ConfigMap" and d["metadata"]["name"] in cms.values():
            for text in (d.get("data") or {}).values():
                files += TOKEN.findall(str(text))
    if not files:
        return ["the login dotenv ConfigMap names no ZITADEL_SERVICE_USER_TOKEN_FILE: this guard is blind"]
    bad = []
    for path in files:
        parent = os.path.dirname(path)
        mounted = [m for c in spec.get("containers") or [] for m in c.get("volumeMounts") or []
                   if m.get("name") in secrets and m.get("mountPath", "").rstrip("/") == parent]
        if not mounted:
            bad.append(f"the login pod reads {path}, and no Secret volume is mounted at {parent}")
    return bad


FIXTURE = """
apiVersion: v1
kind: ConfigMap
metadata: {name: login-dotenv}
data:
  .env: 'ZITADEL_SERVICE_USER_TOKEN_FILE="/login-client/pat"'
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: login, labels: {app.kubernetes.io/name: zitadel-login}}
spec:
  template:
    spec:
      containers:
        - name: login
          volumeMounts:
            - {name: env, mountPath: /.env-file/}
            - {name: login-client, mountPath: /login-client}
      volumes:
        - {name: env, configMap: {name: login-dotenv}}
        - {name: login-client, secret: {secretName: login-client}}
"""


def selftest() -> int:
    base = [d for d in yaml.safe_load_all(FIXTURE) if d]
    if findings(base):
        print(f"SELFTEST FAIL: the mounted fixture must pass: {findings(base)}")
        return 1

    def no_volume(docs):
        spec = docs[1]["spec"]["template"]["spec"]
        spec["volumes"] = spec["volumes"][:1]
        spec["containers"][0]["volumeMounts"] = spec["containers"][0]["volumeMounts"][:1]

    def other_path(docs):
        docs[1]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][1]["mountPath"] = "/elsewhere"

    def config_map_volume(docs):
        docs[1]["spec"]["template"]["spec"]["volumes"][1] = {"name": "login-client", "configMap": {"name": "x"}}

    def no_login(docs):
        docs.pop(1)

    cases = {
        "the upstream render with no login-client volume (charts#758)": no_volume,
        "the Secret mounted at a different directory": other_path,
        "a ConfigMap at the directory of the PAT": config_map_volume,
        "a render with no login Deployment": no_login,
    }
    for what, fn in cases.items():
        docs = copy.deepcopy(base)
        fn(docs)
        if not findings(docs):
            print(f"SELFTEST FAIL: {what}: the guard gave no finding")
            return 1
    print(f"  ✓ selftest: {len(cases)} fixtures fail, the mounted login pod passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for values in PROFILES:
        bad += [f"{'+'.join(values)}: {b}" for b in findings(render(values))]
    if bad:
        print("the Zitadel login pod does not mount the PAT file it reads:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ login-token-mounted: the login pod mounts its PAT file in {len(PROFILES)} profiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
