#!/usr/bin/env bash
# check-iam-admin-pat-escrow.sh — the Zitadel admin credentials survive a restore,
# and the IAM_OWNER PAT has one writer (charts#407).
#
# The setup Job mints iam-admin-pat once and writes only a Kubernetes Secret;
# Secrets are excluded from the backup, so after a restore the PAT was gone
# and the platform could not come back (2026-09-07, blocker 3 loop). The
# chart escrows it: a post-sync Job writes it to OpenBao and an
# ExternalSecret reads it back into `iam-admin-pat`, with creationPolicy
# Orphan because the setup Job creates that Secret first on a fresh
# bootstrap. This guard renders the baseline profile and checks all three
# halves are there and agree; it self-tests by dropping the ExternalSecret
# from a copy of the render.
#
# Usage: scripts/check-iam-admin-pat-escrow.sh
# Exit:  0 escrow present and coherent · 1 a half is missing · 2 self-test broken
set -euo pipefail
CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"; trap 'rm -f "$RENDER"' EXIT
helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-baseline.yaml" -f "$CHART_DIR/../testdata/render-inputs/gibson.yaml" --namespace gibson > "$RENDER"
python3 - "$RENDER" "$(dirname "$0")/lib" <<'PY'
import sys, yaml, copy
sys.path.insert(0, sys.argv[2])
import cilium_policy as cp
KEY = "gibson-zitadel-iam-admin-pat"
# Every Secret the Zitadel setup Job mints, and the store key each rides in.
# The IAM admin PAT is not one of them since charts#407: the platform-operator
# mints it and writes it to OpenBao, and its ExternalSecret is the one writer.
MINTED = {
    "iam-admin": ("iam-admin.json", "gibson-zitadel-iam-admin-machinekey"),
    "login-client": ("pat", "gibson-zitadel-login-client-pat"),
}
def check(docs):
    bad = []
    # The PAT: one writer, the ExternalSecret with Owner, and no mint by Zitadel.
    pat = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat"]
    if not pat:
        bad.append("no ExternalSecret targets iam-admin-pat: nothing delivers the PAT the platform-operator writes to OpenBao")
    else:
        if pat[0]["spec"]["target"].get("creationPolicy") != "Owner":
            bad.append("the iam-admin-pat ExternalSecret must use creationPolicy Owner: it is the one writer of that Secret (charts#407)")
        if KEY not in [x["remoteRef"]["key"] for x in pat[0]["spec"].get("data", [])]:
            bad.append(f"the iam-admin-pat ExternalSecret does not read {KEY}")
    for d in docs:
        if d.get("kind") == "ConfigMap" and "zitadel" in d["metadata"]["name"]:
            for v in (d.get("data") or {}).values():
                cfg = yaml.safe_load(v) if isinstance(v, str) and "FirstInstance" in v else None
                m = (((cfg or {}).get("FirstInstance") or {}).get("Org") or {}).get("Machine") or {}
                if m.get("Pat"):
                    bad.append("the Zitadel setup Job still mints iam-admin-pat (FirstInstance.Org.Machine.Pat): two writers of one Secret (charts#407)")
    es = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin"]
    for secret, (data_key, store_key) in MINTED.items():
        got = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == secret]
        if not got:
            bad.append(f"no ExternalSecret targets the Secret {secret}: a restore cannot bring it back (the Zitadel setup Job mints it once and never again)")
            continue
        g = got[0]
        if g["spec"]["target"].get("creationPolicy") != "Orphan":
            bad.append(f"the {secret} ExternalSecret must use creationPolicy Orphan: the setup Job creates that Secret first on a fresh bootstrap and Owner refuses it")
        if store_key not in [x["remoteRef"]["key"] for x in g["spec"].get("data", [])]:
            bad.append(f"the {secret} ExternalSecret does not read {store_key}")
        if data_key not in ((g["spec"]["target"].get("template") or {}).get("data") or {}):
            bad.append(f"the {secret} ExternalSecret does not write data key {data_key!r}, the key its consumer reads")
    if es:
        e = es[0]
        wave = (e["metadata"].get("annotations") or {}).get("argocd.argoproj.io/sync-wave")
        es_wave = int(wave) if wave is not None else 0
        # The order that works on BOTH paths: the setup Job mints the key,
        # the escrow copies it to the store, this reads it back, and all of
        # that before wave 0, where the platform-operator waits for it.
        setup = [d for d in docs if d.get("kind") == "Job" and d["metadata"]["name"].endswith("zitadel-setup")]
        escrow = [d for d in docs if d.get("kind") == "Job" and d["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow"]
        def wave_of(d):
            w = (d["metadata"].get("annotations") or {}).get("argocd.argoproj.io/sync-wave")
            return int(w) if w is not None else 0
        if setup and escrow:
            sw, jw = wave_of(setup[0]), wave_of(escrow[0])
            # What the escrow hook mounts must exist at its wave: the
            # VAULT_ADMIN_TOKEN Secret comes from the gibson-openbao-keys
            # ExternalSecret. Run 34258967223: that ExternalSecret at wave 0
            # and the hook at -2 left the hook in CreateContainerConfigError
            # for 20 minutes and the fresh bringup never reached wave 0.
            keys = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "gibson-openbao-keys"]
            if keys and not (wave_of(keys[0]) < jw):
                bad.append(f"the gibson-openbao-keys ExternalSecret (wave {wave_of(keys[0])}) must be applied BEFORE the escrow hook (wave {jw}) that mounts it: at the same or a later wave the hook pod cannot start")
            if not (sw < jw < es_wave < 0):
                bad.append(f"the machine key must be minted, escrowed and read back BEFORE wave 0, in that order: zitadel-setup wave {sw}, escrow Job wave {jw}, ExternalSecret wave {es_wave}. Any ExternalSecret wave >= 0 deadlocks a restore (the platform-operator at wave 0 waits for the PAT, and Argo waits for wave 0 before applying it); any wave at or before the escrow is Degraded on a fresh bootstrap")
    jobs = [d for d in docs if d.get("kind") == "Job" and d["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow"]
    if not jobs:
        bad.append("no Job carries app.kubernetes.io/component=iam-admin-pat-escrow: nothing writes the minted PAT to OpenBao")
    else:
        script = " ".join(c.get("args", [""])[0] for c in jobs[0]["spec"]["template"]["spec"]["containers"])
        for secret, (data_key, store_key) in MINTED.items():
            if f"{secret}:{data_key}:{store_key}:" not in script:
                bad.append(f"the escrow Job does not copy Secret {secret}/{data_key} to secret/{store_key}")
        # And the Role behind the Job's ServiceAccount must let it read each
        # one: a name missing from resourceNames reads as "not there yet".
        sa = jobs[0]["spec"]["template"]["spec"].get("serviceAccountName")
        roles = [d for d in docs if d.get("kind") == "Role" and d["metadata"]["name"] == sa]
        readable = set()
        for r in roles:
            for rule in r.get("rules", []):
                if "secrets" in (rule.get("resources") or []) and "get" in (rule.get("verbs") or []):
                    readable.update(rule.get("resourceNames") or ["*"])
        for secret in MINTED:
            if secret not in readable and "*" not in readable:
                bad.append(f"the Role {sa!r} does not let the escrow Job get Secret {secret}: it would wait its whole window on a Secret that is there")
        if "restore path" not in script:
            bad.append("the escrow Job must consult the store BEFORE waiting for the Secret: on a restore the Secret is materialised at wave 1, after this hook, and waiting for it deadlocks the sync")
    # The network half (D76): the Cilium policies select pods by label, so
    # the escrow Job must reach OpenBao on 8200 through its labels, and the
    # store must admit it. Measured 2026-09-08 (loop #7): egress allowed,
    # ingress not, 130 s connect timeouts, the sync waiting on the hook for an hour.
    if jobs:
        rules = cp.rules(docs, "gibson")
        job = cp.endpoint("gibson", jobs[0]["spec"]["template"]["metadata"].get("labels") or {})
        bao = [cp.endpoint("gibson", d["spec"]["template"]["metadata"].get("labels") or {}) for d in docs
               if d.get("kind") == "StatefulSet" and (d["spec"]["template"]["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "openbao"]
        if not bao or not cp.reaches(rules, job, bao[0], 8200):
            bad.append("the network policy does not let app.kubernetes.io/component=iam-admin-pat-escrow reach OpenBao on 8200: the escrow Job cannot reach the store")
    return bad
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for planted in list(MINTED) + ["iam-admin-pat"]:
    mut = [d for d in copy.deepcopy(docs) if not (d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == planted)]
    if not any(planted in b for b in check(mut)):
        sys.exit(f"self-test broken: removing the {planted} ExternalSecret was not detected")
# The deadlock of 2026-09-08, planted: the ExternalSecret at wave 1.
late = copy.deepcopy(docs)
for d in late:
    if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin":
        d["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = "1"
if not any("BEFORE wave 0" in b for b in check(late)):
    sys.exit("self-test broken: the ExternalSecret at wave 1 (the restore deadlock) was not detected")
# The stall of run 34271811189, planted: the Role naming the PAT alone.
narrow = copy.deepcopy(docs)
for d in narrow:
    if d.get("kind") == "Role" and d["metadata"]["name"] == "iam-admin-pat-escrow":
        for rule in d.get("rules", []):
            if "secrets" in (rule.get("resources") or []):
                rule["resourceNames"] = ["iam-admin"]
if not any("does not let the escrow Job get Secret" in b for b in check(narrow)):
    sys.exit("self-test broken: a Role naming the machine key alone was not detected")
# charts#407, planted: the PAT ExternalSecret back on Orphan.
orphan = copy.deepcopy(docs)
for d in orphan:
    if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat":
        d["spec"]["target"]["creationPolicy"] = "Orphan"
if not any("creationPolicy Owner" in b for b in check(orphan)):
    sys.exit("self-test broken: the PAT ExternalSecret on Orphan was not detected")
# The stall of run 34258967223, planted: gibson-openbao-keys at wave 0.
keys0 = copy.deepcopy(docs)
for d in keys0:
    if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "gibson-openbao-keys":
        d["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = "0"
if not any("gibson-openbao-keys" in b for b in check(keys0)):
    sys.exit("self-test broken: gibson-openbao-keys at wave 0 (the hook that cannot start) was not detected")
# The network half, planted: the escrow Job loses its OpenBao client label.
unlabeled = copy.deepcopy(docs)
for d in unlabeled:
    if d.get("kind") == "Job" and d["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow":
        d["spec"]["template"]["metadata"]["labels"].pop("gibson.zeroroot.ai/client-openbao", None)
if not any("reach OpenBao on 8200" in b for b in check(unlabeled)):
    sys.exit("self-test broken: an escrow Job with no OpenBao client label was not detected")
bad = check(docs)
if bad:
    print("✗ check-iam-admin-pat-escrow:", file=sys.stderr)
    for b in bad: print("   " + b, file=sys.stderr)
    sys.exit(1)
print("✅ self-test: each removed ExternalSecret, a Role naming the machine key alone, a wave-1 ExternalSecret, a wave-0 gibson-openbao-keys and a PAT ExternalSecret on Orphan are detected; iam-admin and login-client are escrowed by a covered Job and read back by Orphan ExternalSecrets, and iam-admin-pat has one writer, its Owner ExternalSecret")
PY
