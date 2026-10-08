#!/usr/bin/env python3
"""check-openbao-tls.py: OpenBao serves only TLS, and each client verifies it (ADR-0027).

The check reads each golden render that holds the OpenBao ConfigMap, and one
more render with the vault ClusterIssuer on (the kind shape, which the goldens
do not hold). It fails on:

  1. a listener of openbao.hcl with tls_disable, or with no tls_cert_file,
  2. an OpenBao Service port whose targetPort is not a port of the OpenBao
     container,
  3. a plaintext OpenBao address anywhere in the render (an env value, a
     script, a store, a generator, an issuer, a CR), in the values files
     of the charts, or in an operator script under scripts/ (charts#554):
     http:// with "openbao" in the host, or http:// on 8200,
  4. a ClusterSecretStore, a VaultDynamicSecret or a vault ClusterIssuer that
     dials OpenBao and names no CA,
  5. a container that dials OpenBao over https and does not trust the CA:
     it must mount a volume of the listener Secret (<release>-openbao-tls),
     and the last SSL_CERT_DIR, or CURL_CA_BUNDLE, or BAO_CACERT, must name
     that mount. A container that sets one env name twice fails too: the
     kubelet keeps the last one.

  check-openbao-tls.py             exit 1 on a finding
  check-openbao-tls.py --selftest  prove each finding fails
"""
import glob
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
VALUES = [os.path.join("helm", "gibson", "values*.yaml"), os.path.join("helm", "*", "values.yaml"),
          os.path.join("helm", "testdata", "render-inputs", "*.yaml")]
PLAIN = re.compile(r"http://(?:[^\s\"'/]*openbao[^\s\"']*|[^\s\"'/]*:8200[^\s\"']*)")
TLS = re.compile(r"https://(?:[^\s\"'/]*openbao[^\s\"'/]*|127\.0\.0\.1):8200")
# The operator scripts run against a live install through kubectl exec. They
# are not part of the render, so the render scan cannot see them.
SCRIPTS = [os.path.join("scripts", "**", "*.sh")]
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}
TRUST_ENV = ("SSL_CERT_DIR", "CURL_CA_BUNDLE", "BAO_CACERT")


def pod_spec(d: dict) -> dict:
    spec = d.get("spec") or {}
    if d.get("kind") == "Pod":
        return spec
    if d.get("kind") == "CronJob":
        spec = (spec.get("jobTemplate") or {}).get("spec") or {}
    return (spec.get("template") or {}).get("spec") or {}


def ca_mounts(ps: dict, c: dict) -> list[str]:
    """The mount paths of this container that hold the listener Secret."""
    vols = {v.get("name"): ((v.get("secret") or {}).get("secretName") or "") for v in ps.get("volumes") or []}
    return [m["mountPath"] for m in c.get("volumeMounts") or []
            if vols.get(m.get("name"), "").endswith("-openbao-tls")]


def trusts(c: dict, paths: list[str]) -> tuple[bool, list[str]]:
    env = [e for e in c.get("env") or [] if "value" in e]
    names = [e["name"] for e in c.get("env") or []]
    dup = sorted({n for n in names if names.count(n) > 1})
    last = {e["name"]: str(e["value"]) for e in env}
    ok = False
    for n in TRUST_ENV:
        v = last.get(n)
        if v and any(p and any(part == p or part.startswith(p + "/") for part in v.split(":")) for p in paths):
            ok = True
    return ok, dup


def judge(docs: list, text: str) -> list[str]:
    bad = []
    for d in docs:
        if d.get("kind") == "ConfigMap" and "openbao.hcl" in (d.get("data") or {}):
            listener = d["data"]["openbao.hcl"]
            if re.search(r"tls_disable\s*=", listener):
                bad.append(f"ConfigMap {d['metadata']['name']}: the listener sets tls_disable")
            if "tls_cert_file" not in listener:
                bad.append(f"ConfigMap {d['metadata']['name']}: the listener names no tls_cert_file")
    bao_ports = set()
    for d in docs:
        if d.get("kind") in POD_KINDS and (d.get("metadata") or {}).get("name", "").endswith("-openbao"):
            for c in pod_spec(d).get("containers") or []:
                if c.get("name") == "openbao":
                    for p in c.get("ports") or []:
                        bao_ports |= {p.get("name"), p.get("containerPort")}
    for d in docs:
        if d.get("kind") == "Service" and d["metadata"]["name"].endswith("-openbao") and bao_ports:
            for p in (d.get("spec") or {}).get("ports") or []:
                if p.get("targetPort", p.get("port")) not in bao_ports:
                    bad.append(f"Service {d['metadata']['name']}: port {p.get('name')} targets "
                               f"{p.get('targetPort')}, which the OpenBao container does not serve")
    for m in sorted(set(PLAIN.findall(text))):
        bad.append(f"a plaintext OpenBao address: {m}")
    for d in docs:
        kind, spec = d.get("kind"), d.get("spec") or {}
        if kind in ("ClusterSecretStore", "SecretStore", "VaultDynamicSecret"):
            v = (spec.get("provider") or {})
            v = v.get("vault") or v
            if "openbao" in str(v.get("server", "")) and not v.get("caProvider") and not v.get("caBundle"):
                bad.append(f"{kind} {d['metadata']['name']}: dials OpenBao and names no CA")
        if kind in ("ClusterIssuer", "Issuer"):
            v = spec.get("vault") or {}
            if "openbao" in str(v.get("server", "")) and not v.get("caBundleSecretRef") and not v.get("caBundle"):
                bad.append(f"{kind} {d['metadata']['name']}: dials OpenBao and names no CA")
        if kind not in POD_KINDS:
            continue
        ps = pod_spec(d)
        for c in (ps.get("initContainers") or []) + (ps.get("containers") or []):
            body = yaml.safe_dump({"env": c.get("env"), "command": c.get("command"), "args": c.get("args")})
            ok, dup = trusts(c, ca_mounts(ps, c))
            where = f"{kind} {d['metadata']['name']} container {c.get('name')}"
            if dup:
                bad.append(f"{where}: sets {dup} more than once, and the kubelet keeps only the last")
            if TLS.search(body) and not ok:
                bad.append(f"{where}: dials OpenBao and does not trust the CA of the listener")
    return bad


def script_findings(name: str, text: str) -> list[str]:
    """Each plaintext OpenBao address in one operator script."""
    return [f"{name}: a plaintext OpenBao address: {m}" for m in sorted(set(PLAIN.findall(text)))]


def render_with_vault_issuer(root: str) -> tuple[list, str]:
    r = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
         "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml",
         "--set", "gibson-workloads.certManager.issuers.vault.enabled=true",
         "--set", "gibson-workloads.certManager.issuers.vault.path=pki_int/sign/gibson",
         "--set", "gibson-workloads.certManager.issuers.vault.auth.appRole.roleId=example-role-id"],
        cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"the render with the vault issuer failed: {r.stderr.strip()[-400:]}")
    return [d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)], r.stdout


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        text = open(f).read()
        docs = [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]
        if not any(d.get("kind") == "ConfigMap" and "openbao.hcl" in (d.get("data") or {}) for d in docs):
            continue
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(docs, text)]
    docs, text = render_with_vault_issuer(root)
    if not any(d.get("kind") == "ClusterIssuer" and (d.get("spec") or {}).get("vault") for d in docs):
        bad.append("the render with the vault issuer holds no vault ClusterIssuer: this check is blind to it")
    bad += [f"vault-issuer render: {x}" for x in judge(docs, text)]
    for pattern in VALUES:
        for f in sorted(glob.glob(os.path.join(root, pattern))):
            for m in sorted(set(PLAIN.findall(open(f).read()))):
                bad.append(f"{os.path.relpath(f, root)}: a plaintext OpenBao address: {m}")
    scripts = sorted({f for pattern in SCRIPTS for f in glob.glob(os.path.join(root, pattern), recursive=True)})
    if not scripts:
        bad.append("no operator script under scripts/: this check is blind to them")
    for f in scripts:
        bad += script_findings(os.path.relpath(f, root), open(f).read())
    if not seen:
        bad.append("no golden render holds the OpenBao ConfigMap: this check is blind")
    return bad, seen


def selftest() -> int:
    hcl_ok = 'listener "tcp" {\n  tls_cert_file = "/x"\n}\n'
    cm = lambda hcl: {"kind": "ConfigMap", "metadata": {"name": "bao"}, "data": {"openbao.hcl": hcl}}
    ca_vol = {"name": "openbao-ca", "secret": {"secretName": "gibson-openbao-tls"}}

    def job(addr, env=None, vol=ca_vol, kind="Job"):
        c = {"name": "c", "env": [{"name": "BAO_ADDR", "value": addr}] + (env if env is not None else [
            {"name": "CURL_CA_BUNDLE", "value": "/etc/gibson/openbao-ca/ca.crt"}]),
             "volumeMounts": [{"name": vol["name"], "mountPath": "/etc/gibson/openbao-ca"}]}
        ps = {"containers": [c], "volumes": [vol]}
        return {"kind": kind, "metadata": {"name": "j"},
                "spec": ps if kind == "Pod" else {"template": {"spec": ps}}}

    def bao(port_name="https"):
        return [{"kind": "StatefulSet", "metadata": {"name": "gibson-openbao"}, "spec": {"template": {"spec": {
            "containers": [{"name": "openbao", "ports": [{"name": "https", "containerPort": 8200}]}]}}}},
                {"kind": "Service", "metadata": {"name": "gibson-openbao"},
                 "spec": {"ports": [{"name": "https", "port": 8200, "targetPort": port_name}]}}]

    store = lambda ca: {"kind": "ClusterSecretStore", "metadata": {"name": "s"}, "spec": {"provider": {"vault": dict(
        {"server": "https://gibson-openbao.gibson.svc:8200"}, **({"caProvider": {"name": "x"}} if ca else {}))}}}
    issuer = lambda ca: {"kind": "ClusterIssuer", "metadata": {"name": "i"}, "spec": {"vault": dict(
        {"server": "https://gibson-openbao.gibson.svc:8200"}, **({"caBundleSecretRef": {"name": "x"}} if ca else {}))}}
    good = [cm(hcl_ok), *bao(), job("https://gibson-openbao.gibson.svc:8200"), store(True), issuer(True),
            job("https://gibson-openbao.gibson.svc:8200", env=[
                {"name": "SSL_CERT_DIR", "value": "/etc/ssl/certs:/etc/gibson/openbao-ca"}])]
    if judge(good, yaml.safe_dump_all(good)):
        print(f"SELFTEST FAIL: a TLS listener and TLS clients must pass, got {judge(good, yaml.safe_dump_all(good))}")
        return 1
    addr = "https://gibson-openbao.gibson.svc:8200"
    failing = (
        ("a listener with tls_disable", [cm(hcl_ok.replace("}", "tls_disable = true\n}"))]),
        ("a listener with no certificate", [cm('listener "tcp" {\n}\n')]),
        ("a Service that targets a port the container lacks", [cm(hcl_ok), *bao(port_name="http")]),
        ("an http:// OpenBao address", [cm(hcl_ok), job("http://gibson-openbao:8200")]),
        ("an http:// address on 8200", [cm(hcl_ok), job("http://127.0.0.1:8200")]),
        ("a store with no CA", [cm(hcl_ok), store(False)]),
        ("an issuer with no CA", [cm(hcl_ok), issuer(False)]),
        ("a client with no CA volume", [cm(hcl_ok), job(addr, vol={"name": "tls", "secret": {"secretName": "other"}})]),
        ("a client whose last SSL_CERT_DIR drops the CA", [cm(hcl_ok), job(addr, env=[
            {"name": "SSL_CERT_DIR", "value": "/etc/ssl/certs:/etc/gibson/openbao-ca"},
            {"name": "SSL_CERT_DIR", "value": "/etc/ssl/certs:/etc/ssl/fga-ca"}])]),
        ("a test Pod with no CA", [cm(hcl_ok), job(addr, env=[], kind="Pod")]),
    )
    for what, docs in failing:
        got = judge(docs, yaml.safe_dump_all(docs))
        want = 2 if "last SSL_CERT_DIR" in what else 1
        if len(got) != want:
            print(f"SELFTEST FAIL: {what} must give {want} finding(s), got {got}")
            return 1
    # charts#554: an operator script that calls OpenBao over plain HTTP.
    if not script_findings("set.sh", 'curl -sS "http://127.0.0.1:8200/v1/secret/data/$K"\n'):
        print("SELFTEST FAIL: an operator script with http://127.0.0.1:8200 must fail")
        return 1
    if script_findings("set.sh", 'curl -sS "https://127.0.0.1:8200/v1/secret/data/$K"\n'):
        print("SELFTEST FAIL: an operator script with https://127.0.0.1:8200 must pass")
        return 1
    print("  ✓ selftest: a plaintext listener, a listener with no certificate, a Service on a missing port, an "
          "http:// address, a store or issuer with no CA, a client with no CA, a duplicate SSL_CERT_DIR, a test "
          "Pod and an operator script on http:// fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("OpenBao is not TLS only, or a client does not verify it (ADR-0027):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ openbao-tls: OpenBao serves only TLS and each client verifies it, in {seen} renders and the "
          "vault-issuer render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
