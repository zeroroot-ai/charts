#!/usr/bin/env python3
"""check-openbao-tls.py: OpenBao serves only TLS, and each client dials it over TLS (ADR-0027).

The check reads each golden render that holds the OpenBao ConfigMap and fails on:

  1. a listener of openbao.hcl with tls_disable, or with no tls_cert_file,
  2. an http:// OpenBao address anywhere in the render (an env value, a
     script, a store, an issuer, a CR), and in the values files of the
     umbrella,
  3. a ClusterSecretStore or a vault ClusterIssuer that dials OpenBao and
     names no CA (caProvider, caBundleSecretRef),
  4. a container that names the OpenBao address in its env or script and
     mounts no OpenBao CA, so it could not verify the listener.

  check-openbao-tls.py             exit 1 on a finding
  check-openbao-tls.py --selftest  prove each finding fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
VALUES = [os.path.join("helm", "gibson", "values*.yaml"), os.path.join("helm", "*", "values.yaml"),
          os.path.join("helm", "testdata", "render-inputs", "*.yaml")]
PLAIN = re.compile(r"http://[^\s\"'/]*openbao[^\s\"']*")
TLS = re.compile(r"https://[^\s\"'/]*openbao[^\s\"']*|https://127\.0\.0\.1:8200")
CA_VOLUMES = {"openbao-ca", "tls"}
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob"}


def pod_spec(d: dict) -> dict:
    spec = d.get("spec") or {}
    if d.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def judge(docs: list, text: str) -> list[str]:
    bad = []
    hcl = [d for d in docs if d.get("kind") == "ConfigMap" and "openbao.hcl" in (d.get("data") or {})]
    for d in hcl:
        listener = d["data"]["openbao.hcl"]
        if re.search(r"tls_disable\s*=", listener):
            bad.append(f"ConfigMap {d['metadata']['name']}: the listener sets tls_disable")
        if "tls_cert_file" not in listener:
            bad.append(f"ConfigMap {d['metadata']['name']}: the listener names no tls_cert_file")
    for m in sorted(set(PLAIN.findall(text))):
        bad.append(f"an http:// OpenBao address: {m}")
    for d in docs:
        kind, spec = d.get("kind"), d.get("spec") or {}
        if kind == "ClusterSecretStore":
            v = (spec.get("provider") or {}).get("vault") or {}
            if "openbao" in str(v.get("server", "")) and not v.get("caProvider") and not v.get("caBundle"):
                bad.append(f"ClusterSecretStore {d['metadata']['name']}: dials OpenBao and names no CA")
        if kind == "ClusterIssuer":
            v = spec.get("vault") or {}
            if "openbao" in str(v.get("server", "")) and not v.get("caBundleSecretRef") and not v.get("caBundle"):
                bad.append(f"ClusterIssuer {d['metadata']['name']}: dials OpenBao and names no CA")
        if kind not in POD_KINDS:
            continue
        ps = pod_spec(d)
        for c in (ps.get("initContainers") or []) + (ps.get("containers") or []):
            body = yaml.safe_dump({"env": c.get("env"), "command": c.get("command"), "args": c.get("args")})
            if not TLS.search(body):
                continue
            mounts = {m.get("name") for m in c.get("volumeMounts") or []}
            if not mounts & CA_VOLUMES:
                bad.append(f"{kind} {d['metadata']['name']} container {c.get('name')}: dials OpenBao and mounts no CA")
    return bad


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        text = open(f).read()
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        if not any(d.get("kind") == "ConfigMap" and "openbao.hcl" in (d.get("data") or {}) for d in docs):
            continue
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(docs, text)]
    for pattern in VALUES:
        for f in sorted(glob.glob(os.path.join(root, pattern))):
            for m in sorted(set(PLAIN.findall(open(f).read()))):
                bad.append(f"{os.path.relpath(f, root)}: an http:// OpenBao address: {m}")
    if not seen:
        bad.append("no golden render holds the OpenBao ConfigMap: this check is blind")
    return bad, seen


def selftest() -> int:
    hcl_ok = 'listener "tcp" {\n  tls_cert_file = "/x"\n}\n'
    cm = lambda hcl: {"kind": "ConfigMap", "metadata": {"name": "bao"}, "data": {"openbao.hcl": hcl}}

    def job(addr, mounts=("openbao-ca",)):
        return {"kind": "Job", "metadata": {"name": "j"}, "spec": {"template": {"spec": {"containers": [{
            "name": "c", "env": [{"name": "BAO_ADDR", "value": addr}],
            "volumeMounts": [{"name": m, "mountPath": "/m"} for m in mounts]}]}}}}

    store = lambda ca: {"kind": "ClusterSecretStore", "metadata": {"name": "s"}, "spec": {"provider": {"vault": dict(
        {"server": "https://gibson-openbao.gibson.svc:8200"}, **({"caProvider": {"name": "x"}} if ca else {}))}}}
    good = [cm(hcl_ok), job("https://gibson-openbao.gibson.svc:8200"), store(True)]
    if judge(good, yaml.safe_dump_all(good)):
        print(f"SELFTEST FAIL: a TLS listener and TLS clients must pass, got {judge(good, yaml.safe_dump_all(good))}")
        return 1
    failing = (
        ("a listener with tls_disable", [cm(hcl_ok.replace("}", "tls_disable = true\n}"))]),
        ("a listener with no certificate", [cm('listener "tcp" {\n}\n')]),
        ("an http:// OpenBao address", [cm(hcl_ok), job("http://gibson-openbao:8200", mounts=("openbao-ca",))]),
        ("a store with no CA", [cm(hcl_ok), store(False)]),
        ("a client with no CA mount", [cm(hcl_ok), job("https://gibson-openbao.gibson.svc:8200", mounts=("tmp",))]),
    )
    for what, docs in failing:
        got = judge(docs, yaml.safe_dump_all(docs))
        if len(got) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {got}")
            return 1
    print("  ✓ selftest: a plaintext listener, a listener with no certificate, an http:// address, a store with no "
          "CA and a client with no CA fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("OpenBao is not TLS only, or a client does not dial it over TLS (ADR-0027):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ openbao-tls: OpenBao serves only TLS and each client verifies it, in {seen} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
