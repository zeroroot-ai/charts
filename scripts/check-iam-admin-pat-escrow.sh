#!/usr/bin/env bash
# check-iam-admin-pat-escrow.sh — the Zitadel IAM_OWNER PAT survives a restore.
#
# The setup Job mints iam-admin-pat once and writes only a Kubernetes Secret;
# Secrets are excluded from the backup, so after a restore the PAT was gone
# and the platform could not come back (2026-09-07, blocker 3 loop). The
# chart escrows it: a post-sync Job writes it to OpenBao and an
# ExternalSecret reads it back into `iam-admin-pat`, with creationPolicy
# Orphan because the setup Job creates that Secret first on a fresh
# bootstrap. This guard renders the vanilla profile and checks all three
# halves are there and agree; it self-tests by dropping the ExternalSecret
# from a copy of the render.
#
# Usage: scripts/check-iam-admin-pat-escrow.sh
# Exit:  0 escrow present and coherent · 1 a half is missing · 2 self-test broken
set -euo pipefail
CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"; trap 'rm -f "$RENDER"' EXIT
helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-vanilla.yaml" --namespace gibson > "$RENDER"
python3 - "$RENDER" <<'PY'
import sys, yaml, copy
KEY = "gibson-zitadel-iam-admin-pat"
# Every Secret the Zitadel setup Job mints, and the store key each rides in.
MINTED = {
    "iam-admin-pat": ("pat", "gibson-zitadel-iam-admin-pat"),
    "iam-admin": ("iam-admin.json", "gibson-zitadel-iam-admin-machinekey"),
    "login-client": ("pat", "gibson-zitadel-login-client-pat"),
}
def check(docs):
    bad = []
    es = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat"]
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
        # The order that works on BOTH paths: the setup Job mints the PAT,
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
                bad.append(f"the PAT must be minted, escrowed and read back BEFORE wave 0, in that order: zitadel-setup wave {sw}, escrow Job wave {jw}, ExternalSecret wave {es_wave}. Any ExternalSecret wave >= 0 deadlocks a restore (the platform-operator at wave 0 waits for the PAT, and Argo waits for wave 0 before applying it); any wave at or before the escrow is Degraded on a fresh bootstrap")
    jobs = [d for d in docs if d.get("kind") == "Job" and d["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow"]
    if not jobs:
        bad.append("no Job carries app.kubernetes.io/component=iam-admin-pat-escrow: nothing writes the minted PAT to OpenBao")
    else:
        script = " ".join(c.get("args", [""])[0] for c in jobs[0]["spec"]["template"]["spec"]["containers"])
        for secret, (data_key, store_key) in MINTED.items():
            if f"{secret}:{data_key}:{store_key}:" not in script:
                bad.append(f"the escrow Job does not copy Secret {secret}/{data_key} to secret/{store_key}")
        if "restore path" not in script:
            bad.append("the escrow Job must consult the store BEFORE waiting for the Secret: on a restore the Secret is materialised at wave 1, after this hook, and waiting for it deadlocks the sync")
    pols = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    covered = any("iam-admin-pat-escrow" in (e.get("values") or []) for p in pols for e in (p["spec"].get("podSelector", {}).get("matchExpressions") or []))
    if not covered:
        bad.append("no NetworkPolicy selects app.kubernetes.io/component=iam-admin-pat-escrow: the namespace default-deny severs the escrow Job")
    # And the other end: the store's own policy must admit the Job on 8200.
    # Measured 2026-09-08 (loop #7): egress allowed, ingress not, 130 s
    # connect timeouts, the sync waiting on the hook for an hour.
    admitted = False
    for p in pols:
        if (p["spec"].get("podSelector", {}).get("matchLabels") or {}).get("app.kubernetes.io/component") != "openbao":
            continue
        for rule in p["spec"].get("ingress") or []:
            ports = [x.get("port") for x in (rule.get("ports") or [])]
            froms = [((f.get("podSelector") or {}).get("matchLabels") or {}).get("app.kubernetes.io/component") for f in (rule.get("from") or [])]
            if 8200 in ports and "iam-admin-pat-escrow" in froms:
                admitted = True
    if not admitted:
        bad.append("the openbao NetworkPolicy does not admit app.kubernetes.io/component=iam-admin-pat-escrow on 8200: the escrow Job cannot reach the store")
    return bad
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for planted in MINTED:
    mut = [d for d in copy.deepcopy(docs) if not (d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == planted)]
    if not any(planted in b for b in check(mut)):
        sys.exit(f"self-test broken: removing the {planted} ExternalSecret was not detected")
# The deadlock of 2026-09-08, planted: the ExternalSecret at wave 1.
late = copy.deepcopy(docs)
for d in late:
    if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat":
        d["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = "1"
if not any("BEFORE wave 0" in b for b in check(late)):
    sys.exit("self-test broken: the ExternalSecret at wave 1 (the restore deadlock) was not detected")
# The stall of run 34258967223, planted: gibson-openbao-keys at wave 0.
keys0 = copy.deepcopy(docs)
for d in keys0:
    if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "gibson-openbao-keys":
        d["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = "0"
if not any("gibson-openbao-keys" in b for b in check(keys0)):
    sys.exit("self-test broken: gibson-openbao-keys at wave 0 (the hook that cannot start) was not detected")
bad = check(docs)
if bad:
    print("✗ check-iam-admin-pat-escrow:", file=sys.stderr)
    for b in bad: print("   " + b, file=sys.stderr)
    sys.exit(1)
print("✅ self-test: each of the three removed ExternalSecrets, a wave-1 ExternalSecret and a wave-0 gibson-openbao-keys are detected; iam-admin, iam-admin-pat and login-client are escrowed to OpenBao by a covered Job and read back by Orphan ExternalSecrets")
PY
