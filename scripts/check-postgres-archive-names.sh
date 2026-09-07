#!/usr/bin/env bash
# check-postgres-archive-names.sh — a recovered Postgres never writes into the
# archive it recovers from, and a recovery always names its source archive.
#
# CNPG archives WAL to <destinationPath>/<serverName>/ and refuses, on the
# first segment, a directory that already holds WAL from another cluster
# (barman-cloud-check-wal-archive). The durable bucket outlives the cluster
# by design (ADR-0015), so every bringup after the first must archive under
# a new name, and a recovery must read the OLD name and write a NEW one.
# Measured 2026-09-07: a fresh cluster into a used archive went
# ContinuousArchiving=False and every base backup sat in walArchivingFailing.
#
# This guard drives the render with four fixtures and fails on the first
# wrong answer. It cannot pass vacuously: two of the fixtures MUST fail to
# render, and the guard checks that they do.
#
# Usage: scripts/check-postgres-archive-names.sh
# Exit:  0 the four fixtures render as required · 1 one of them did not
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson}"
VALUES="$CHART_DIR/values-vanilla.yaml"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

render() {  # render <label> <extra --set args...>; stdout = rendered Cluster, rc = helm rc
  local label="$1"; shift
  helm template gibson "$CHART_DIR" -f "$VALUES" --namespace gibson "$@" \
    > "$WORK/$label.yaml" 2> "$WORK/$label.err"
}
cluster_field() {  # cluster_field <label> <python expr on the Cluster dict d>
  python3 - "$WORK/$1.yaml" "$2" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
d = next(x for x in docs if x.get("kind") == "Cluster" and x["metadata"]["name"] == "platform-postgres")
print(eval(sys.argv[2], {"d": d}))
PY
}
fail() { printf '\033[0;31m✗ check-postgres-archive-names: %s\033[0m\n' "$*" >&2; exit 1; }

# 1. A nameless bootstrap renders initdb and no recovery source.
render plain || fail "the plain vanilla render failed: $(tail -n1 "$WORK/plain.err")"
[ "$(cluster_field plain '"initdb" in d["spec"]["bootstrap"]')" = True ] \
  || fail "the plain render does not bootstrap with initdb"
[ "$(cluster_field plain '"externalClusters" in d["spec"]')" = False ] \
  || fail "the plain render carries a recovery source it was never given"

# 2. A stamped archive name lands on backup.barmanObjectStore.serverName.
render stamped --set platformPostgres.backup.serverName=platform-postgres-20260907T000000Z \
  || fail "the stamped render failed: $(tail -n1 "$WORK/stamped.err")"
[ "$(cluster_field stamped 'd["spec"]["backup"]["barmanObjectStore"].get("serverName")')" = platform-postgres-20260907T000000Z ] \
  || fail "backup.serverName did not reach the Cluster"

# 3. A recovery with no source name MUST refuse to render.
if render nosource --set platformPostgres.recovery.backupID=20260907T000000; then
  fail "a recovery with no recovery.serverName rendered; it must refuse"
fi
grep -q 'recovery.serverName is REQUIRED' "$WORK/nosource.err" \
  || fail "the refusal did not name recovery.serverName: $(tail -n1 "$WORK/nosource.err")"

# 4. A recovery whose source is the archive it would write MUST refuse.
if render samename --set platformPostgres.recovery.backupID=20260907T000000 \
     --set platformPostgres.recovery.serverName=platform-postgres-a \
     --set platformPostgres.backup.serverName=platform-postgres-a; then
  fail "a recovery into its own source archive rendered; it must refuse"
fi
grep -q 'cannot recover from the archive it is about to write into' "$WORK/samename.err" \
  || fail "the refusal did not explain the collision: $(tail -n1 "$WORK/samename.err")"

# 5. A recovery with distinct names reads the old archive and writes the new.
render distinct --set platformPostgres.recovery.backupID=20260907T000000 \
  --set platformPostgres.recovery.serverName=platform-postgres-a \
  --set platformPostgres.backup.serverName=platform-postgres-b \
  || fail "the distinct-names render failed: $(tail -n1 "$WORK/distinct.err")"
[ "$(cluster_field distinct 'd["spec"]["externalClusters"][0]["barmanObjectStore"]["serverName"]')" = platform-postgres-a ] \
  || fail "the recovery source is not the named old archive"
[ "$(cluster_field distinct 'd["spec"]["backup"]["barmanObjectStore"]["serverName"]')" = platform-postgres-b ] \
  || fail "the recovered cluster does not archive under the new name"
[ "$(cluster_field distinct 'd["spec"]["bootstrap"]["recovery"]["recoveryTarget"]["backupID"]')" = 20260907T000000 ] \
  || fail "the recovery target is not the named backup id"

echo "✅ postgres archive names: initdb renders no source; a stamped name lands; a recovery refuses a missing or colliding source and reads old/writes new"
