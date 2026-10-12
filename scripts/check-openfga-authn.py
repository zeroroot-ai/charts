#!/usr/bin/env python3
"""check-openfga-authn.py: OpenFGA refuses a call with no key, and each client sends the key (charts#637).

The check reads the committed golden renders (helm/testdata/golden/), which
`make golden` keeps equal to the chart. In each render with an OpenFGA
Deployment it fails when:

  - the OpenFGA container has no OPENFGA_AUTHN_METHOD=preshared,
  - its OPENFGA_AUTHN_PRESHARED_KEYS is a literal value, or reads another
    Secret than the key Secret (a key in a value is a key in a repository),
  - no ExternalSecret writes the key Secret from the OpenBao key,
  - an OpenFGA client container does not get the key: GIBSON_FGA_KEY_DIR,
    and a mount of the key Secret at that directory. A client is a container
    with an FGA address in its env (EXT_AUTHZ_FGA_ADDR, FGA_URL), with the FGA
    store id (GIBSON_AUTHZ_FGA_STORE_ID), or whose script calls the OpenFGA
    store API (/stores) of the OpenFGA Service, or the platform-operator,
    which reads the OpenFGA address from its PlatformBootstrap,
  - a render has fewer clients than MIN_CLIENTS: the check would be blind.

It fails as blind when no golden file holds an OpenFGA Deployment.

  check-openfga-authn.py             exit 1 on a finding
  check-openfga-authn.py --selftest  prove each finding fails
"""
import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
KEY_SECRET = "gibson-openfga-preshared-key"
KEY_ENV = "GIBSON_FGA_KEY_DIR"
ADDRESS_ENVS = {"EXT_AUTHZ_FGA_ADDR", "FGA_URL", "GIBSON_AUTHZ_FGA_STORE_ID"}
# The daemon, ext-authz, the tenant-operator, the platform-operator and
# fga-init render in each umbrella profile.
MIN_CLIENTS = 5
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}


def pod_spec(d):
    kind = d.get("kind")
    if kind == "Pod":
        return d.get("spec") or {}
    if kind == "CronJob":
        return ((((d.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    return (((d.get("spec") or {}).get("template") or {}).get("spec")) or {}


def containers(d):
    spec = pod_spec(d)
    return (spec.get("initContainers") or []) + (spec.get("containers") or [])


def is_openfga_server(d) -> bool:
    return d.get("kind") == "Deployment" and any(
        e.get("name") == "OPENFGA_DATASTORE_ENGINE" for c in containers(d) for e in c.get("env") or [])


def env_map(c):
    return {e.get("name"): e for e in c.get("env") or []}


def is_client(d, c) -> bool:
    env = env_map(c)
    if ADDRESS_ENVS & set(env):
        return True
    script = " ".join(str(x) for x in (c.get("command") or []) + (c.get("args") or []))
    if "gibson-openfga" in script and "/stores" in script:
        return True
    return d.get("kind") == "Deployment" and "platform-operator" in d["metadata"].get("name", "") \
        and c.get("name") in {"manager", "platform-operator"}


def judge_server(name, d) -> list[str]:
    out = []
    for c in containers(d):
        env = env_map(c)
        if "OPENFGA_DATASTORE_ENGINE" not in env:
            continue
        method = (env.get("OPENFGA_AUTHN_METHOD") or {}).get("value")
        if method != "preshared":
            out.append(f"{name}: OpenFGA runs with OPENFGA_AUTHN_METHOD={method!r}, not preshared")
        keys = env.get("OPENFGA_AUTHN_PRESHARED_KEYS") or {}
        ref = ((keys.get("valueFrom") or {}).get("secretKeyRef")) or {}
        if "value" in keys:
            out.append(f"{name}: OPENFGA_AUTHN_PRESHARED_KEYS is a literal value")
        elif ref.get("name") != KEY_SECRET or ref.get("key") != "keys":
            out.append(f"{name}: OPENFGA_AUTHN_PRESHARED_KEYS reads {ref or 'nothing'}, not {KEY_SECRET}/keys")
    return out


def judge_client(name, d, c) -> list[str]:
    env = env_map(c)
    keydir = (env.get(KEY_ENV) or {}).get("value")
    if not keydir:
        return [f"{name}/{c.get('name')}: an OpenFGA client with no {KEY_ENV}; OpenFGA refuses its calls"]
    vols = {v.get("name"): v for v in pod_spec(d).get("volumes") or []}
    for m in c.get("volumeMounts") or []:
        if m.get("mountPath", "").rstrip("/") != keydir.rstrip("/"):
            continue
        secret = (vols.get(m.get("name")) or {}).get("secret") or {}
        if secret.get("secretName") == KEY_SECRET:
            return []
    return [f"{name}/{c.get('name')}: {KEY_ENV}={keydir} is not a mount of the Secret {KEY_SECRET}"]


def judge(docs) -> tuple[list[str], int, bool]:
    """Findings, the client count and whether the render holds OpenFGA."""
    docs = [d for d in docs if isinstance(d, dict) and d.get("kind")]
    servers = [d for d in docs if is_openfga_server(d)]
    if not servers:
        return [], 0, False
    out = []
    for d in servers:
        out += judge_server(d["metadata"]["name"], d)
    if not any(d.get("kind") == "ExternalSecret"
               and (((d.get("spec") or {}).get("target") or {}).get("name")) == KEY_SECRET
               and any(((x.get("remoteRef") or {}).get("key")) == KEY_SECRET for x in (d.get("spec") or {}).get("data") or [])
               for d in docs):
        out.append(f"no ExternalSecret writes {KEY_SECRET} from the OpenBao key {KEY_SECRET}")
    clients = 0
    for d in docs:
        if d.get("kind") not in WORKLOADS or is_openfga_server(d):
            continue
        for c in containers(d):
            if is_client(d, c):
                clients += 1
                out += judge_client(f"{d['kind']}/{d['metadata']['name']}", d, c)
    if clients < MIN_CLIENTS:
        out.append(f"only {clients} OpenFGA clients found, want at least {MIN_CLIENTS}: this check is blind")
    return out, clients, True


def audit(root):
    bad, renders, clients = [], 0, 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        found, n, has = judge(list(yaml.safe_load_all(open(f))))
        if has:
            renders += 1
            clients += n
            bad += [f"{os.path.basename(f)}: {x}" for x in found]
    if not renders:
        bad.append("no golden render holds an OpenFGA Deployment: this check is blind")
    return bad, renders, clients


def fixture():
    """A render that passes: the server, the ExternalSecret and five clients."""
    def workload(kind, name, cname, env, args=None):
        keyvol = {"name": "openfga-key", "secret": {"secretName": KEY_SECRET}}
        keymount = {"name": "openfga-key", "mountPath": "/etc/gibson/openfga-key"}
        keyenv = {"name": KEY_ENV, "value": "/etc/gibson/openfga-key"}
        c = {"name": cname, "env": [keyenv] + env, "volumeMounts": [keymount]}
        if args:
            c["args"] = args
        return {"kind": kind, "metadata": {"name": name},
                "spec": {"template": {"spec": {"containers": [c], "volumes": [keyvol]}}}}

    server = {"kind": "Deployment", "metadata": {"name": "gibson-openfga"}, "spec": {"template": {"spec": {"containers": [{
        "name": "openfga", "env": [
            {"name": "OPENFGA_DATASTORE_ENGINE", "value": "postgres"},
            {"name": "OPENFGA_AUTHN_METHOD", "value": "preshared"},
            {"name": "OPENFGA_AUTHN_PRESHARED_KEYS",
             "valueFrom": {"secretKeyRef": {"name": KEY_SECRET, "key": "keys"}}}]}]}}}}
    es = {"kind": "ExternalSecret", "metadata": {"name": KEY_SECRET},
          "spec": {"target": {"name": KEY_SECRET}, "data": [{"remoteRef": {"key": KEY_SECRET, "property": "key"}}]}}
    return [server, es,
            workload("StatefulSet", "gibson", "gibson", [{"name": "GIBSON_AUTHZ_FGA_STORE_ID", "value": "s"}]),
            workload("Deployment", "gibson-ext-authz", "ext-authz", [{"name": "EXT_AUTHZ_FGA_ADDR", "value": "https://x"}]),
            workload("Deployment", "gibson-tenant-operator", "manager", [{"name": "FGA_URL", "value": "https://x"}]),
            workload("Deployment", "gibson-platform-operator", "manager", []),
            workload("Job", "gibson-fga-init", "fga-init", [], ["FGA=https://gibson-openfga:8080; fcurl ${FGA}/stores"])]


def selftest() -> int:
    good = fixture()
    found, n, _ = judge(good)
    if found or n != 5:
        print(f"SELFTEST FAIL: the good fixture must pass with 5 clients, got {found}, {n}")
        return 1

    def server_env(docs, name):
        return next(e for e in docs[0]["spec"]["template"]["spec"]["containers"][0]["env"] if e["name"] == name)

    cases = []
    d = copy.deepcopy(good); server_env(d, "OPENFGA_AUTHN_METHOD")["value"] = "none"
    cases.append(("an OpenFGA with no authentication", d))
    d = copy.deepcopy(good); env = server_env(d, "OPENFGA_AUTHN_PRESHARED_KEYS"); env.pop("valueFrom"); env["value"] = "k"
    cases.append(("a literal key list", d))
    d = copy.deepcopy(good); server_env(d, "OPENFGA_AUTHN_PRESHARED_KEYS")["valueFrom"]["secretKeyRef"]["name"] = "other"
    cases.append(("a key list from another Secret", d))
    d = copy.deepcopy(good); del d[1]
    cases.append(("no ExternalSecret of the key", d))
    for i, what in ((2, "the daemon"), (3, "ext-authz"), (6, "fga-init")):
        d = copy.deepcopy(good); c = d[i]["spec"]["template"]["spec"]["containers"][0]
        c["env"] = [e for e in c["env"] if e["name"] != KEY_ENV]
        cases.append((f"{what} with no key directory", d))
    d = copy.deepcopy(good); d[4]["spec"]["template"]["spec"]["volumes"][0]["secret"]["secretName"] = "other"
    cases.append(("the tenant-operator with the key directory from another Secret", d))
    d = copy.deepcopy(good); d[5]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"] = []
    cases.append(("the platform-operator with no key mount", d))
    for what, docs in cases:
        found, _, _ = judge(docs)
        if len(found) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {found}")
            return 1
    d = copy.deepcopy(good)[:4]
    found, _, _ = judge(d)
    if not any("blind" in f for f in found):
        print(f"SELFTEST FAIL: a render with two clients must be blind, got {found}")
        return 1
    print(f"  ✓ selftest: {len(cases)} broken fixtures each give one finding, a short render is blind, the good fixture passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, renders, clients = audit(ROOT)
    if bad:
        print("OpenFGA runs without its preshared key, or a client does not send it:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ openfga-authn: OpenFGA requires the key in {renders} renders, and {clients} client containers send it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
