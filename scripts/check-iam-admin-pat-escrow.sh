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
def check(docs):
    bad = []
    es = [d for d in docs if d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat"]
    if not es:
        bad.append("no ExternalSecret targets the Secret iam-admin-pat: a restore cannot bring the PAT back")
    else:
        e = es[0]
        if e["spec"]["target"].get("creationPolicy") != "Orphan":
            bad.append("the iam-admin-pat ExternalSecret must use creationPolicy Orphan: the setup Job creates that Secret first on a fresh bootstrap and Owner refuses it")
        keys = [x["remoteRef"]["key"] for x in e["spec"].get("data", [])]
        if KEY not in keys:
            bad.append(f"the iam-admin-pat ExternalSecret does not read {KEY}")
    jobs = [d for d in docs if d.get("kind") == "Job" and d["spec"]["template"]["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "iam-admin-pat-escrow"]
    if not jobs:
        bad.append("no Job carries app.kubernetes.io/component=iam-admin-pat-escrow: nothing writes the minted PAT to OpenBao")
    else:
        script = " ".join(c.get("args", [""])[0] for c in jobs[0]["spec"]["template"]["spec"]["containers"])
        if KEY not in script:
            bad.append(f"the escrow Job does not write {KEY}")
    pols = [d for d in docs if d.get("kind") == "NetworkPolicy"]
    covered = any("iam-admin-pat-escrow" in (e.get("values") or []) for p in pols for e in (p["spec"].get("podSelector", {}).get("matchExpressions") or []))
    if not covered:
        bad.append("no NetworkPolicy selects app.kubernetes.io/component=iam-admin-pat-escrow: the namespace default-deny severs the escrow Job")
    return bad
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
mut = [d for d in copy.deepcopy(docs) if not (d.get("kind") == "ExternalSecret" and d["spec"].get("target", {}).get("name") == "iam-admin-pat")]
if not check(mut):
    sys.exit("self-test broken: removing the ExternalSecret was not detected")
bad = check(docs)
if bad:
    print("✗ check-iam-admin-pat-escrow:", file=sys.stderr)
    for b in bad: print("   " + b, file=sys.stderr)
    sys.exit(1)
print("✅ self-test: a removed ExternalSecret is detected; iam-admin-pat is escrowed to OpenBao by a covered Job and read back by an Orphan ExternalSecret")
PY
