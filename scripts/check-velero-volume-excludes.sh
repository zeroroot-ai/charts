#!/usr/bin/env bash
# check-velero-volume-excludes.sh — every pod the chart renders tells Velero
# which of its volumes are not data.
#
# The shipped Schedule sets defaultVolumesToFsBackup, so Velero snapshots
# every pod volume it is not told to skip: sockets, scratch dirs, files an
# init container renders. Each one costs a kopia upload, and on restore each
# becomes a PodVolumeRestore that can finish only once the pod runs — the
# restored pod sits behind a restore-wait init container until then.
# Measured 2026-09-07: 9 of 22 PodVolumeRestores in a preserve backup were
# such volumes, and the runner's preserve backup took 11 minutes.
#
# The rule: a pod template's backup.velero.io/backup-volumes-excludes
# annotation names EXACTLY its emptyDir and CSI-ephemeral volumes, and never
# a PersistentVolumeClaim or volumeClaimTemplate. It checks the RENDER, so
# a subchart pod is covered through its values as well as a first-party one.
# It self-tests first: a render with one annotation removed MUST fail.
#
# Usage: scripts/check-velero-volume-excludes.sh
# Exit:  0 every pod's list is exact · 1 a pod's list is wrong · 2 self-test broken
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT
helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-vanilla.yaml" --namespace gibson > "$RENDER"

python3 - "$RENDER" <<'PY'
import sys, yaml, copy

ANN = "backup.velero.io/backup-volumes-excludes"
# Hook Jobs and test pods live and die inside one sync; a Schedule never sees them.
SKIP_KINDS = {"Job", "CronJob"}

def pod_templates(docs):
    for d in docs:
        k = d.get("kind")
        if k in ("Deployment", "StatefulSet", "DaemonSet"):
            yield f'{k}/{d["metadata"]["name"]}', d["spec"]["template"], d["spec"].get("volumeClaimTemplates") or []

def check(docs):
    bad = []
    for name, tpl, vcts in pod_templates(docs):
        vols = tpl.get("spec", {}).get("volumes") or []
        ephemeral = sorted(v["name"] for v in vols if "emptyDir" in v or "csi" in v)
        claims = {v["name"] for v in vols if "persistentVolumeClaim" in v} | {t["metadata"]["name"] for t in vcts}
        ann = (tpl.get("metadata", {}).get("annotations") or {}).get(ANN, "")
        listed = sorted(x for x in ann.split(",") if x)
        if listed != ephemeral:
            bad.append(f"{name}: annotation lists {listed or 'nothing'}, the pod's emptyDir/CSI volumes are {ephemeral or 'none'}")
        for x in listed:
            if x in claims:
                bad.append(f"{name}: {x} is a claim, excluding it would drop DATA from the backup")
    return bad

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
pods = list(pod_templates(docs))
if not pods:
    sys.exit("the vanilla render carries no pod templates; an empty set must never pass")

# Self-test: drop one annotation from a copy of the render and expect a finding.
mutated = copy.deepcopy(docs)
victim = next((d for d in mutated if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet")
               and ANN in (d["spec"]["template"].get("metadata", {}).get("annotations") or {})), None)
if victim is None:
    sys.exit("self-test broken: no rendered pod template carries the annotation, so nothing can be removed to prove the guard fails")
del victim["spec"]["template"]["metadata"]["annotations"][ANN]
if not check(mutated):
    sys.exit("self-test broken: removing an annotation was not detected")

bad = check(docs)
if bad:
    print("✗ check-velero-volume-excludes: %d pod template(s) do not tell Velero which volumes are not data:" % len(bad), file=sys.stderr)
    for b in bad:
        print("   " + b, file=sys.stderr)
    sys.exit(1)
n = sum(1 for _, t, _ in pods if ANN in (t.get("metadata", {}).get("annotations") or {}))
print("✅ self-test: a removed annotation is detected; %d pod templates rendered, %d carry an exact volume-excludes list, none excludes a claim" % (len(pods), n))
PY
