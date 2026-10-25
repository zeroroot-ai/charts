#!/usr/bin/env python3
"""check-login-token-mounted.py: the Zitadel login pod mounts the PAT file it reads.

The login app reads its service user token from the file that
ZITADEL_SERVICE_USER_TOKEN_FILE names in its dotenv ConfigMap. The upstream
chart mounts the login-client Secret only when FirstInstance.Org.LoginClient
is set, and the umbrella sets that block to null (charts#758). The pod then
waited for the file until its liveness probe killed it, on each rollout.

This guard renders each profile and fails unless three things hold:

  1. The login container mounts a Secret volume at the directory of the file.
  2. That volume is the Secret login-client.
  3. The file name is a key of that Secret: the ExternalSecret login-client
     writes the key, and the volume projects it (all keys, or an item with
     that key and path).

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
SECRET = "login-client"
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


def secret_keys(docs: list[dict], name: str) -> set[str]:
    """The keys of the Secret that the ExternalSecret with target `name` writes."""
    for d in docs:
        if d.get("kind") != "ExternalSecret":
            continue
        target = (d.get("spec") or {}).get("target") or {}
        if (target.get("name") or d["metadata"]["name"]) != name:
            continue
        template = (target.get("template") or {}).get("data")
        if template:
            return set(template)
        return {x.get("secretKey") for x in (d["spec"].get("data") or [])}
    return set()


def projected(volume: dict, key: str, filename: str, keys: set[str]) -> bool:
    """The Secret volume puts `key` at `filename` in its mount directory."""
    items = volume["secret"].get("items")
    if items:
        return any(i.get("key") == key and i.get("path") == filename for i in items) and key in keys
    return filename in keys


def findings(docs: list[dict]) -> list[str]:
    dep = login_deployment(docs)
    if dep is None:
        return ["the render holds no zitadel-login Deployment: this guard is blind"]
    spec = dep["spec"]["template"]["spec"]
    secret_volumes = {v["name"]: v for v in spec.get("volumes") or [] if "secret" in v}
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
        parent, filename = os.path.dirname(path), os.path.basename(path)
        mounted = [secret_volumes[m["name"]] for c in spec.get("containers") or []
                   for m in c.get("volumeMounts") or []
                   if m.get("name") in secret_volumes and m.get("mountPath", "").rstrip("/") == parent]
        if not mounted:
            bad.append(f"the login pod reads {path}, and no Secret volume is mounted at {parent}")
            continue
        for vol in mounted:
            got = vol["secret"].get("secretName")
            if got != SECRET:
                bad.append(f"the volume at {parent} is the Secret {got!r}, want {SECRET!r}")
                continue
            keys = secret_keys(docs, SECRET)
            if not projected(vol, filename, filename, keys):
                bad.append(f"the Secret {SECRET!r} puts no key {filename!r} at {path}: "
                           f"the ExternalSecret writes {sorted(k for k in keys if k)}")
    return bad


FIXTURE = """
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata: {name: login-client}
spec:
  data: [{secretKey: pat, remoteRef: {key: gibson-zitadel-login-client-pat, property: pat}}]
  target:
    name: login-client
    template: {data: {pat: "{{ .pat }}"}}
---
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
        spec = docs[2]["spec"]["template"]["spec"]
        spec["volumes"] = spec["volumes"][:1]
        spec["containers"][0]["volumeMounts"] = spec["containers"][0]["volumeMounts"][:1]

    def other_path(docs):
        docs[2]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][1]["mountPath"] = "/elsewhere"

    def config_map_volume(docs):
        docs[2]["spec"]["template"]["spec"]["volumes"][1] = {"name": "login-client", "configMap": {"name": "x"}}

    def wrong_secret(docs):
        docs[2]["spec"]["template"]["spec"]["volumes"][1]["secret"]["secretName"] = "iam-admin-pat"

    def missing_key(docs):
        docs[0]["spec"]["target"]["template"]["data"] = {"token": "{{ .pat }}"}

    def item_elsewhere(docs):
        docs[2]["spec"]["template"]["spec"]["volumes"][1]["secret"]["items"] = [{"key": "pat", "path": "token"}]

    def no_login(docs):
        docs.pop(2)

    cases = {
        "the upstream render with no login-client volume (charts#758)": no_volume,
        "the Secret mounted at a different directory": other_path,
        "a ConfigMap at the directory of the PAT": config_map_volume,
        "the volume is a different Secret": wrong_secret,
        "the Secret holds no key pat": missing_key,
        "an item puts the key pat at a different path": item_elsewhere,
        "a render with no login Deployment": no_login,
    }
    docs = copy.deepcopy(base)
    docs[2]["spec"]["template"]["spec"]["volumes"][1]["secret"]["items"] = [{"key": "pat", "path": "pat"}]
    if findings(docs):
        print(f"SELFTEST FAIL: an item that puts the key pat at pat must pass: {findings(docs)}")
        return 1
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
