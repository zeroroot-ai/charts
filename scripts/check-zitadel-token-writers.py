#!/usr/bin/env python3
"""check-zitadel-token-writers.py — each Zitadel token Secret has one writer (charts#407, ADR-0014).

OpenBao holds the two Zitadel tokens, and an ExternalSecret with creationPolicy
Owner is the one writer of each Secret:

  iam-admin-pat  the platform-operator mints the PAT and writes it to
                 secret/gibson-zitadel-iam-admin-pat (gibson#794).
  login-client   the Zitadel setup Job mints the first PAT, and its container
                 login-client-pat-store writes it to
                 secret/gibson-zitadel-login-client-pat.

A Secret that lived only in Kubernetes did not survive a restore (2026-09-07,
blocker 3 loop), and a Secret with two writers gives two values. This guard
renders the baseline profile and fails when:

  - an ExternalSecret of the two is missing, is not Owner, or reads another key;
  - the waves let a fresh install or a restore deadlock;
  - the Zitadel config map holds FirstInstance.Org.Machine or LoginClient, so
    the upstream chart starts a kubectl container that writes a Secret;
  - a container of a Zitadel Job runs `kubectl create secret`, or a Role bound
    to the Zitadel ServiceAccount grants a verb on Secrets;
  - the setup Job does not mint the login-client PAT from the steps file, or
    its store container does not write it to OpenBao with its own token;
  - the setup pod cannot reach OpenBao on 8200;
  - the deleted escrow hook comes back.

Each check has a planted fixture in --selftest, keyed by content.

  check-zitadel-token-writers.py             render, check, exit 1 on a finding
  check-zitadel-token-writers.py --selftest  prove each fixture fails and the render passes
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import cilium_policy as cp  # noqa: E402

NS = "gibson"
ZITADEL_ENTRIES = {
    # Secret: (data key, store key, the properties its ExternalSecret reads)
    "iam-admin-pat": ("pat", "gibson-zitadel-iam-admin-pat", {"pat", "userId"}),
    "login-client": ("pat", "gibson-zitadel-login-client-pat", {"pat"}),
}
SETUP_JOB = "gibson-zitadel-setup"
SETUP_CONTAINER = "zitadel-setup"
STORE_CONTAINER = "login-client-pat-store"
STEPS_CONFIGMAP = "gibson-zitadel-first-instance"
STORE_AUTH_HOLDER = "gibson-openbao-zitadel-setup"
STORE_POLICY = "zitadel-setup"
ZITADEL_SA = "gibson-zitadel"


def render() -> list[dict]:
    chart = os.path.join(ROOT, "helm", "gibson")
    out = subprocess.run(
        ["helm", "template", "gibson", chart,
         "-f", os.path.join(chart, "values-baseline.yaml"),
         "-f", os.path.join(ROOT, "helm", "testdata", "render-inputs", "gibson.yaml"),
         "--namespace", NS],
        capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def wave(d: dict) -> int:
    w = (d["metadata"].get("annotations") or {}).get("argocd.argoproj.io/sync-wave")
    return int(w) if w is not None else 0


def es_for_target(docs: list[dict], target: str) -> dict | None:
    for d in docs:
        if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == target:
            return d
    return None


def pod_spec(d: dict) -> dict:
    return d["spec"]["template"]["spec"]


def zitadel_jobs(docs: list[dict]) -> list[dict]:
    return [d for d in docs if d.get("kind") == "Job" and "zitadel" in d["metadata"]["name"]]


def script_of(c: dict) -> str:
    return " ".join((c.get("command") or []) + (c.get("args") or []))


def first_instance_of(docs: list[dict]) -> list[tuple[str, dict]]:
    """The FirstInstance block of each Zitadel config file in a ConfigMap."""
    out = []
    for d in docs:
        if d.get("kind") != "ConfigMap" or "zitadel" not in d["metadata"]["name"]:
            continue
        for k, v in (d.get("data") or {}).items():
            if isinstance(v, str) and "FirstInstance" in v:
                cfg = yaml.safe_load(v) or {}
                out.append((f"{d['metadata']['name']}/{k}", cfg.get("FirstInstance") or {}))
    return out


def check(docs: list[dict]) -> list[str]:
    bad: list[str] = []

    # 1. One ExternalSecret writer of each token Secret, from the right key.
    for target, (data_key, store_key, props) in ZITADEL_ENTRIES.items():
        es = es_for_target(docs, target)
        if es is None:
            bad.append(f"no ExternalSecret targets the Secret {target}: nothing writes it from OpenBao, so a restore cannot bring it back")
            continue
        if es["spec"]["target"].get("creationPolicy") != "Owner":
            bad.append(f"the ExternalSecret {target} must use creationPolicy Owner: it is the one writer of the Secret (charts#407, ADR-0014)")
        got = {(x["remoteRef"]["key"], x["remoteRef"].get("property")) for x in es["spec"].get("data", [])}
        for p in props:
            if (store_key, p) not in got:
                bad.append(f"the ExternalSecret {target} does not read property {p} of {store_key}")
        tmpl = (es["spec"]["target"].get("template") or {}).get("data") or {}
        for k in {data_key} | (props - {"pat"}):
            if k not in tmpl:
                bad.append(f"the ExternalSecret {target} does not write the data key {k!r}, which its consumer reads")
    if es_for_target(docs, "iam-admin") is not None:
        bad.append("an ExternalSecret writes the Secret iam-admin: the machine key of iam-admin is deleted (ADR-0171)")

    # 2. The waves. login-client: after the setup Job writes the key, before
    # wave 0 where zitadel-login and the platform-operator wait for it.
    # iam-admin-pat: not ahead of the platform-operator that writes its key.
    setup = [d for d in docs if d.get("kind") == "Job" and d["metadata"]["name"] == SETUP_JOB]
    lc = es_for_target(docs, "login-client")
    if setup and lc is not None and not (wave(setup[0]) < wave(lc) < 0):
        bad.append(f"the ExternalSecret login-client (wave {wave(lc)}) must come after the setup Job (wave {wave(setup[0])}) and BEFORE wave 0: earlier it is Degraded on a fresh install, at 0 or later a restore deadlocks")
    pat = es_for_target(docs, "iam-admin-pat")
    ops = [d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-platform-operator")]
    if pat is not None and ops and wave(pat) < min(wave(o) for o in ops):
        bad.append(f"the ExternalSecret iam-admin-pat (wave {wave(pat)}) is ahead of the platform-operator (wave {min(wave(o) for o in ops)}) that writes its key: a fresh install never ends")

    # 3. The upstream chart starts a kubectl Secret writer for each block of
    # FirstInstance.Org in its config map.
    for name, fi in first_instance_of(docs):
        if name.startswith(STEPS_CONFIGMAP + "/"):
            continue
        org = fi.get("Org") or {}
        for block in ("Machine", "LoginClient"):
            if org.get(block):
                bad.append(f"{name} holds FirstInstance.Org.{block}: the upstream chart then starts a kubectl container that writes a Secret, a second writer (charts#407)")

    # 4. No Zitadel Job writes a Secret, and the Zitadel ServiceAccount may not.
    for j in zitadel_jobs(docs):
        for c in pod_spec(j).get("containers", []) + (pod_spec(j).get("initContainers") or []):
            s = script_of(c)
            if "kubectl" in s and "create secret" in s:
                bad.append(f"the container {c['name']} of Job {j['metadata']['name']} runs `kubectl create secret`: a Secret writer outside its ExternalSecret (charts#407)")
    roles = {(d["kind"], d["metadata"]["name"]): d for d in docs if d.get("kind") in ("Role", "ClusterRole")}
    for b in docs:
        if b.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        if not any(s.get("kind") == "ServiceAccount" and s.get("name") == ZITADEL_SA for s in b.get("subjects") or []):
            continue
        role = roles.get((b["roleRef"]["kind"], b["roleRef"]["name"]))
        for rule in (role or {}).get("rules", []):
            if "secrets" in (rule.get("resources") or []) or "*" in (rule.get("resources") or []):
                bad.append(f"{b['roleRef']['kind']} {b['roleRef']['name']} grants {rule.get('verbs')} on Secrets to the ServiceAccount {ZITADEL_SA}: the Zitadel pods write no Secret (charts#407)")

    # 5. The setup Job mints the login-client PAT from the steps file and
    # writes it straight to OpenBao.
    steps = [d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == STEPS_CONFIGMAP]
    lc_block = {}
    if steps:
        fi = (yaml.safe_load((steps[0].get("data") or {}).get("steps.yaml") or "") or {}).get("FirstInstance") or {}
        lc_block = (fi.get("Org") or {}).get("LoginClient") or {}
        if (fi.get("Org") or {}).get("Machine"):
            bad.append(f"the steps file of {STEPS_CONFIGMAP} creates FirstInstance.Org.Machine: the platform-operator creates iam-admin and mints its PAT (gibson#794)")
    if not lc_block.get("Pat"):
        bad.append(f"no steps file {STEPS_CONFIGMAP}/steps.yaml creates FirstInstance.Org.LoginClient with a Pat: nothing mints the first login-client PAT")
    if not setup:
        bad.append(f"no Job {SETUP_JOB} renders")
        return bad
    spec = pod_spec(setup[0])
    cs = {c["name"]: c for c in spec.get("containers", [])}
    sc = cs.get(SETUP_CONTAINER)
    vols = {v["name"]: v for v in spec.get("volumes", [])}
    if sc is not None:
        args = sc.get("args") or []
        mounts = {m["name"]: m["mountPath"] for m in sc.get("volumeMounts", [])}
        steps_vol = [n for n, v in vols.items() if (v.get("configMap") or {}).get("name") == STEPS_CONFIGMAP]
        steps_path = f"{mounts.get(steps_vol[0], '')}/steps.yaml" if steps_vol else None
        if not steps_path or not any(a == "--steps" and i + 1 < len(args) and args[i + 1] == steps_path for i, a in enumerate(args)):
            bad.append(f"the container {SETUP_CONTAINER} does not pass the mounted steps file of {STEPS_CONFIGMAP} with --steps: Zitadel creates no login client")
        if "/login-client" not in mounts.values():
            bad.append(f"the container {SETUP_CONTAINER} mounts nothing at /login-client: Zitadel cannot write the login-client PAT there")
    store = cs.get(STORE_CONTAINER)
    if store is None:
        bad.append(f"the Job {SETUP_JOB} has no container {STORE_CONTAINER}: the minted login-client PAT reaches no store")
    else:
        s = script_of(store)
        if f"KEY={ZITADEL_ENTRIES['login-client'][1]}" not in s or "/v1/secret/data/$KEY" not in s:
            bad.append(f"the container {STORE_CONTAINER} does not write secret/data/{ZITADEL_ENTRIES['login-client'][1]}")
        if "/login-client/pat" not in s or "/login-client" not in [m["mountPath"] for m in store.get("volumeMounts", [])]:
            bad.append(f"the container {STORE_CONTAINER} does not read /login-client/pat from the volume that Zitadel writes")
        if f"name=='{SETUP_CONTAINER}'" not in s:
            bad.append(f"the container {STORE_CONTAINER} does not wait for the container {SETUP_CONTAINER} to stop: it reads the file before Zitadel writes it")
        named = {(e["valueFrom"]["secretKeyRef"]["name"]) for e in store.get("env", []) if (e.get("valueFrom") or {}).get("secretKeyRef")}
        if named != {STORE_AUTH_HOLDER}:
            bad.append(f"the container {STORE_CONTAINER} must read exactly one Secret, the one that holds its OpenBao token ({len(named)} named)")
        if sc is not None:
            uid = lambda c: (c.get("securityContext") or {}).get("runAsUser") or (spec.get("securityContext") or {}).get("runAsUser")
            if uid(store) != uid(sc):
                bad.append(f"the container {STORE_CONTAINER} runs as user {uid(store)}, the container {SETUP_CONTAINER} as {uid(sc)}: Zitadel writes the PAT readable by its owner only")
    bao = [d for d in docs if d.get("kind") == "StatefulSet"
           and (d["spec"]["template"]["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "openbao"]
    minted = any(f"{STORE_AUTH_HOLDER}:{STORE_POLICY}" in script_of(c)
                 for b in bao for c in pod_spec(b).get("containers", []) + (pod_spec(b).get("initContainers") or []))
    if not minted:
        bad.append(f"openbao-auto-init does not mint {STORE_AUTH_HOLDER} with policy {STORE_POLICY} (JOB_ZITADEL_ENTRIES): the store container has no token")

    # 6. The network half (D76): the setup pod reaches OpenBao on 8200.
    rules = cp.rules(docs, NS)
    src = cp.endpoint(NS, setup[0]["spec"]["template"]["metadata"].get("labels") or {})
    dst = [cp.endpoint(NS, b["spec"]["template"]["metadata"].get("labels") or {}) for b in bao]
    if not dst or not cp.reaches(rules, src, dst[0], 8200):
        bad.append(f"the network policy does not let the Job {SETUP_JOB} reach OpenBao on 8200: the login-client PAT reaches no store")

    # 7. The escrow hook stays deleted.
    for d in docs:
        if d["metadata"].get("name") == "iam-admin-pat-escrow" or \
           ((d.get("spec") or {}).get("template", {}).get("metadata", {}).get("labels") or {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow":
            bad.append(f"{d['kind']} iam-admin-pat-escrow renders: the copy hook is a second writer of the token Secrets (charts#407)")
            break
    return bad


def mutate(docs: list[dict], fn) -> list[dict]:
    out = copy.deepcopy(docs)
    for d in out:
        fn(d)
    return out


def store_container(d: dict) -> dict | None:
    if d.get("kind") == "Job" and d["metadata"]["name"] == SETUP_JOB:
        for c in pod_spec(d)["containers"]:
            if c["name"] == STORE_CONTAINER:
                return c
    return None


def selftest(docs: list[dict]) -> None:
    def es_set(target, fn):
        def f(d):
            if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == target:
                fn(d)
        return f

    def drop(pred):
        out = [d for d in copy.deepcopy(docs) if not pred(d)]
        return out

    def config_block(block, value):
        def f(d):
            if d.get("kind") == "ConfigMap" and d["metadata"]["name"] != STEPS_CONFIGMAP and "zitadel" in d["metadata"]["name"]:
                for k, v in (d.get("data") or {}).items():
                    if isinstance(v, str) and "FirstInstance" in v:
                        cfg = yaml.safe_load(v)
                        cfg.setdefault("FirstInstance", {}).setdefault("Org", {})[block] = value
                        d["data"][k] = yaml.safe_dump(cfg)
        return f

    def store_edit(fn):
        def f(d):
            c = store_container(d)
            if c is not None:
                fn(c)
        return f

    def kubectl_writer(d):
        if d.get("kind") == "Job" and d["metadata"]["name"] == SETUP_JOB:
            pod_spec(d)["containers"].append({"name": "zitadel-login-client-pat", "command": ["sh", "-c"],
                                              "args": ["kubectl --namespace=gibson create secret generic login-client --from-file=pat=/login-client/pat"]})

    def secrets_role(d):
        if d.get("kind") == "Role" and d["metadata"]["name"] == "gibson-zitadel-setup":
            d["rules"].append({"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]})

    with_escrow = copy.deepcopy(docs) + [{"apiVersion": "batch/v1", "kind": "Job",
                                          "metadata": {"name": "iam-admin-pat-escrow", "namespace": NS},
                                          "spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/component": "iam-admin-pat-escrow"}},
                                                                "spec": {"containers": []}}}}]
    iam_admin_back = copy.deepcopy(docs)
    lc = es_for_target(docs, "login-client")
    if lc is not None:
        e = copy.deepcopy(lc)
        e["metadata"]["name"] = e["spec"]["target"]["name"] = "iam-admin"
        iam_admin_back.append(e)

    cases = [
        ("the ExternalSecret login-client removed", drop(lambda d: d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "login-client"), "no ExternalSecret targets the Secret login-client"),
        ("the ExternalSecret iam-admin-pat removed", drop(lambda d: d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat"), "no ExternalSecret targets the Secret iam-admin-pat"),
        ("login-client on Orphan", mutate(docs, es_set("login-client", lambda d: d["spec"]["target"].__setitem__("creationPolicy", "Orphan"))), "ExternalSecret login-client must use creationPolicy Owner"),
        ("iam-admin-pat on Orphan", mutate(docs, es_set("iam-admin-pat", lambda d: d["spec"]["target"].__setitem__("creationPolicy", "Orphan"))), "ExternalSecret iam-admin-pat must use creationPolicy Owner"),
        ("iam-admin-pat without userId", mutate(docs, es_set("iam-admin-pat", lambda d: d["spec"].__setitem__("data", [x for x in d["spec"]["data"] if x["remoteRef"].get("property") != "userId"]))), "property userId"),
        ("an ExternalSecret writes iam-admin", iam_admin_back, "writes the Secret iam-admin"),
        ("login-client at wave 1", mutate(docs, es_set("login-client", lambda d: d["metadata"].setdefault("annotations", {}).__setitem__("argocd.argoproj.io/sync-wave", "1"))), "BEFORE wave 0"),
        ("login-client at the wave of the setup Job", mutate(docs, es_set("login-client", lambda d: d["metadata"].setdefault("annotations", {}).__setitem__("argocd.argoproj.io/sync-wave", "-3"))), "BEFORE wave 0"),
        ("iam-admin-pat ahead of the operator", mutate(docs, es_set("iam-admin-pat", lambda d: d["metadata"].setdefault("annotations", {}).__setitem__("argocd.argoproj.io/sync-wave", "-1"))), "ahead of the platform-operator"),
        ("the config map creates the login client", mutate(docs, config_block("LoginClient", {"Machine": {"Username": "login-client"}, "Pat": {"ExpirationDate": "2029-01-01T00:00:00Z"}})), "FirstInstance.Org.LoginClient"),
        ("the config map creates iam-admin", mutate(docs, config_block("Machine", {"Machine": {"Username": "iam-admin"}, "MachineKey": {"Type": 1}})), "FirstInstance.Org.Machine"),
        ("a kubectl Secret writer in the setup Job", mutate(docs, kubectl_writer), "create secret"),
        ("a Secret grant for the Zitadel ServiceAccount", mutate(docs, secrets_role), "on Secrets to the ServiceAccount"),
        ("no steps file", drop(lambda d: d.get("kind") == "ConfigMap" and d["metadata"]["name"] == STEPS_CONFIGMAP), "creates FirstInstance.Org.LoginClient with a Pat"),
        ("the store container removed", mutate(docs, lambda d: pod_spec(d).__setitem__("containers", [c for c in pod_spec(d)["containers"] if c["name"] != STORE_CONTAINER]) if store_container(d) is not None else None), f"has no container {STORE_CONTAINER}"),
        ("the store container writes another key", mutate(docs, store_edit(lambda c: c.__setitem__("args", [a.replace("KEY=gibson-zitadel-login-client-pat", "KEY=gibson-zitadel-other") for a in c["args"]]))), "does not write secret/data/gibson-zitadel-login-client-pat"),
        ("the store container does not wait", mutate(docs, store_edit(lambda c: c.__setitem__("args", [a.replace("name=='zitadel-setup'", "name=='other'") for a in c["args"]]))), "does not wait for the container"),
        ("the store container reads another Secret", mutate(docs, store_edit(lambda c: c["env"].append({"name": "X", "valueFrom": {"secretKeyRef": {"name": "gibson-openbao-keys", "key": "VAULT_ADMIN_TOKEN"}}}))), "must read exactly one Secret"),
        ("the store container as another user", mutate(docs, store_edit(lambda c: c.setdefault("securityContext", {}).__setitem__("runAsUser", 65534))), "readable by its owner only"),
        ("the setup pod without the OpenBao label", mutate(docs, lambda d: d["spec"]["template"]["metadata"]["labels"].pop("gibson.zeroroot.ai/client-openbao", None) if d.get("kind") == "Job" and d["metadata"]["name"] == SETUP_JOB else None), "reach OpenBao on 8200"),
        ("the escrow hook back", with_escrow, "iam-admin-pat-escrow renders"),
    ]
    broken = []
    for name, fixture, want in cases:
        if not any(want in b for b in check(fixture)):
            broken.append(f"{name}: no finding contains {want!r}")
    if broken:
        print("✗ check-zitadel-token-writers self-test broken:", file=sys.stderr)
        for b in broken:
            print("   " + b, file=sys.stderr)
        sys.exit(2)
    print(f"✅ self-test: {len(cases)} planted fixtures each fail")


def main() -> int:
    docs = render()
    if "--selftest" in sys.argv:
        selftest(docs)
    bad = check(docs)
    if bad:
        print("✗ check-zitadel-token-writers:", file=sys.stderr)
        for b in bad:
            print("   " + b, file=sys.stderr)
        return 1
    print("✅ each Zitadel token Secret has one writer, its Owner ExternalSecret; the setup Job writes the login-client PAT straight to OpenBao and writes no Secret")
    return 0


if __name__ == "__main__":
    sys.exit(main())
