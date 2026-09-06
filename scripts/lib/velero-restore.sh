#!/usr/bin/env bash
# velero-restore.sh — the restore half of a recreate, for EVERY substrate
# (ADR-0014, ADR-0015, deploy#1739, deploy#1746).
#
# A recreate restores ONLY the backup named on the command line. There is no
# `latest`, no auto-detect and no default that turns the restore on. Every
# step below speaks kubectl, the durable bucket and Velero, and nothing here
# knows what the cluster runs on, so ENV=kind and ENV=staging run the SAME
# code and there is no second implementation to drift (ADR-0027).
#
# Source this file, then call, in this order:
#
#   resolve_named_backup   before anything is destroyed. Proves the named
#                          backup is in the bucket and resolves the ONE thing
#                          Postgres needs out of the same name, the CNPG base
#                          backup id. Sets RECOVERY_BACKUP_ID.
#   restore_secret_store   after stage 1 and the Velero release, before the
#                          platform Application exists.
#   wait_eso               every Secret is out of the restored store.
#   restore_data           the built-ins, then the tenant records.
#
# The caller supplies these before the first call:
#
#   RESTORE_FROM          the backup name a preserve teardown printed
#   BUCKET_NAME           the durable bucket
#   VELERO_BUCKET_PREFIX  the prefix the Velero release writes under
#   NS / VELERO_NS        release and velero namespaces
#   RESTORE_TIMEOUT       per-Restore and per-wait deadline, seconds
#   WORK                  a scratch directory the caller cleans up
#   s3()                  aws, with the keyring's OWN bucket credential
#   log() / die()         die MUST exit non-zero
#
# Nothing here copies bytes itself and nothing edits a live store by hand:
# the controller that wrote the backup is the controller that reads it back.

# ---------------------------------------------------------------------------
# resolve_named_backup — the named backup, proven to exist BEFORE anything is
# destroyed.
#
# This runs ahead of the destroy, on purpose. A recreate deletes the cluster
# it finds, so a RESTORE_FROM that turns out to name nothing would destroy the
# running environment and then discover it has nothing to put back. The check
# names the exact object it looked for, so a typo in the name is one line of
# output away from being obvious.
#
# It also resolves the ONE thing Postgres needs, out of the SAME name: the
# CNPG base backup id. The preserve teardown gives its CNPG `Backup` CR the
# same name as the Velero backup and takes it BEFORE the Velero backup, so
# the completed record with its `status.backupId` rides inside the archive.
# Reading it here is what keeps ADR-0014 true for Postgres too: the recovery
# target is the backup that was named, never the newest one in the catalog.
# ---------------------------------------------------------------------------
RECOVERY_BACKUP_ID=""

resolve_named_backup() {
  # A Velero backup name is a DNS-1123 label and the preserve teardown builds
  # one. Refuse anything else here rather than let the API server refuse a
  # Restore twenty minutes into a bringup.
  case "$RESTORE_FROM" in
    *[!a-z0-9.-]*|-*|*-|.*|*.)
      die "RESTORE_FROM='${RESTORE_FROM}' is not a backup name. A preserve teardown prints preserve-<env>-<YYYYMMDD>-<HHMMSS>, lower case, digits, dashes and dots only" ;;
  esac
  BACKUP_KEY="${VELERO_BUCKET_PREFIX}/backups/${RESTORE_FROM}/velero-backup.json"
  log "restore: looking for backup '${RESTORE_FROM}' in the durable bucket"
  s3 s3 ls "s3://${BUCKET_NAME}/${BACKUP_KEY}" >/dev/null 2>&1 \
    || die "there is no backup named '${RESTORE_FROM}' in the durable bucket.
    looked for  s3://${BUCKET_NAME}/${BACKUP_KEY}
    A recreate restores ONLY a backup named on the command line (ADR-0014): there is no
    latest, no auto-detect and no fallback to an empty bringup. A preserve teardown
    prints the name it verified as its last line. Run without RESTORE_FROM to bootstrap
    every store empty."
  printf '  ✓ s3://%s/%s\n' "$BUCKET_NAME" "$BACKUP_KEY"

  # The CNPG record, out of the archive Velero wrote next to that manifest.
  # The tarball carries object JSON only (volume data lives in the kopia
  # repository), so it is small enough to read here.
  s3 s3 cp --no-progress "s3://${BUCKET_NAME}/${VELERO_BUCKET_PREFIX}/backups/${RESTORE_FROM}/${RESTORE_FROM}.tar.gz" \
      "$WORK/backup.tar.gz" >/dev/null \
    || die "backup '${RESTORE_FROM}' has a manifest but no archive at s3://${BUCKET_NAME}/${VELERO_BUCKET_PREFIX}/backups/${RESTORE_FROM}/${RESTORE_FROM}.tar.gz. The backup is incomplete and must not be restored"
  CNPG_MEMBER="$(tar -tzf "$WORK/backup.tar.gz" 2>/dev/null \
    | grep -E "backups\.postgresql\.cnpg\.io/.*/${RESTORE_FROM}\.json$" | head -n1 || true)"
  [ -n "$CNPG_MEMBER" ] \
    || die "backup '${RESTORE_FROM}' carries no CNPG Backup record named '${RESTORE_FROM}'.
    A preserve teardown takes the CNPG base backup FIRST and gives it the teardown's own
    name, so a Velero backup without that record was not written by a preserve teardown.
    Postgres has no recovery target, so this restore would bring the graph back over an
    empty database. Refusing."
  RECOVERY_BACKUP_ID="$(tar -xzOf "$WORK/backup.tar.gz" "$CNPG_MEMBER" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",{}).get("backupId",""))')"
  [ -n "$RECOVERY_BACKUP_ID" ] \
    || die "the CNPG Backup record inside '${RESTORE_FROM}' has no status.backupId: the base backup never completed, so there is nothing for Postgres to recover from"
  printf '  ✓ postgres recovers from base backup %s\n' "$RECOVERY_BACKUP_ID"
}

# restore_diag <name> — everything the Restore itself recorded, printed
# BEFORE the verb gives up.
#
# `phase: Completed` is not proof that data came back. A Restore narrowed by a
# labelSelector can finish Completed in six seconds having restored NOTHING,
# with no PodVolumeRestore, no warning and no error: a filter that matches no
# item is not an error to Velero (measured on kind 2026-09-02, deploy#1738).
# So the counts are what a reader needs, and a failure with no counts is a
# failure nobody can diagnose.
restore_diag() {
  printf '\n--- restore %s ---\n' "$1" >&2
  kubectl -n "$VELERO_NS" get "restore.velero.io/$1" \
    -o jsonpath='  phase={.status.phase} itemsRestored={.status.progress.itemsRestored}/{.status.progress.totalItems} warnings={.status.warnings} errors={.status.errors}{"\n"}  failureReason={.status.failureReason}{"\n"}' >&2 2>/dev/null || true
  printf '  PodVolumeRestores:\n' >&2
  kubectl -n "$VELERO_NS" get podvolumerestores.velero.io -l velero.io/restore-name="$1" \
    -o custom-columns=NAME:.metadata.name,POD:.spec.pod.name,VOLUME:.spec.volume,PHASE:.status.phase >&2 2>/dev/null || true
  printf '  PodVolumeBackups of the source backup:\n' >&2
  kubectl -n "$VELERO_NS" get podvolumebackups.velero.io -l velero.io/backup-name="$RESTORE_FROM" \
    -o custom-columns=NAME:.metadata.name,POD:.spec.pod.name,VOLUME:.spec.volume,PHASE:.status.phase,BYTES:.status.progress.totalBytes >&2 2>/dev/null || true
  restore_log_summary "$1"
}

# restore_log_summary <restore> — WHY the restore did what it did, in the
# words Velero recorded.
#
# The counts above say a restore skipped an item and never say which item or
# why. Velero writes that to the restore's OWN log, stored beside the restore
# in the durable bucket, and never to the server pod's stdout, so
# `kubectl logs deploy/velero` does not hold it either. MEASURED 2026-09-02
# (deploy#1740): two dispatches of the blocker 3 loop stopped on a secret
# store restore that reported `itemsRestored=4/4 warnings=1` and moved no
# volume data, and neither the counts nor the server log named the one
# warning that explained it.
#
# The log comes back through a DownloadRequest: Velero signs a URL for the
# object, so this needs no bucket credential and no velero CLI, and it works
# the same on kind's MinIO and on S3. WARNINGS are printed as well as errors,
# because a restore that skips an item warns and stays Completed.
#
# This function only ever prints. Every failure inside it is swallowed: it
# runs on the way to an abort and must not replace the real message with one
# about diagnostics.
restore_log_summary() {
  local name="$1" dr="recreate-explain-${1}" url="" deadline
  kubectl apply -f - >/dev/null 2>&1 <<YAML
apiVersion: velero.io/v1
kind: DownloadRequest
metadata:
  name: ${dr}
  namespace: ${VELERO_NS}
spec:
  target:
    kind: RestoreLog
    name: ${name}
YAML
  deadline=$(( $(date +%s) + 60 ))
  while :; do
    url="$(kubectl -n "$VELERO_NS" get "downloadrequest.velero.io/${dr}" -o jsonpath='{.status.downloadURL}' 2>/dev/null)" || url=""
    [ -n "$url" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf '  (Velero signed no download URL for the log of restore %s within 60s)\n' "$name" >&2
      return 0
    fi
    sleep 2
  done
  if ! curl -fsSL "$url" -o "$WORK/restore-log.gz" 2>/dev/null; then
    printf '  (the signed URL for the log of restore %s did not answer)\n' "$name" >&2
    return 0
  fi
  if ! python3 -c 'import gzip,sys;sys.stdout.write(gzip.open(sys.stdin.buffer,"rt",errors="replace").read())' \
       < "$WORK/restore-log.gz" > "$WORK/restore.log" 2>/dev/null; then
    printf '  (the log of restore %s came back unreadable)\n' "$name" >&2
    return 0
  fi
  # Everything that makes two reports of the same fault look different goes
  # before the grouping, the same way scripts/teardown-kind.sh does it for a
  # backup: the timestamp, the log source, the restore name and the namespace.
  local grouped="$WORK/restore-errors.txt"
  grep -E 'level=(error|warning)' "$WORK/restore.log" \
    | sed -e 's/^time="[^"]*" //' \
          -e 's/ logSource="[^"]*"//' -e 's/ restore=[^ ]*//' \
          -e 's/ error\.function="[^"]*"//' \
          -e 's/ namespace=[^ ]*//' \
          -e 's/ in the namespace \\"[^\\]*\\"//' \
    | sort | uniq -c | sort -rn > "$grouped"
  printf '  %s distinct warnings and errors in the restore log, most frequent first, at most 40 shown:\n' \
    "$(wc -l < "$grouped")" >&2
  head -40 "$grouped" >&2
  kubectl -n "$VELERO_NS" delete "downloadrequest.velero.io/${dr}" --ignore-not-found >/dev/null 2>&1 || true
}

# wait_restore <name> — poll one Restore to a terminal phase and demand
# Completed. PartiallyFailed is a FAILURE here for the same reason it is one
# in a preserve teardown (deploy#1738): it means items the spec asked for are
# missing. A half-restored environment that reports success is the worst
# outcome this verb has.
wait_restore() {
  local name="$1" phase="" deadline=$(( $(date +%s) + RESTORE_TIMEOUT ))
  while :; do
    phase="$(kubectl -n "$VELERO_NS" get "restore.velero.io/$name" -o jsonpath='{.status.phase}' 2>/dev/null)" || phase=""
    case "$phase" in
      Completed) printf '  ✓ restore %s Completed\n' "$name"; return 0 ;;
      PartiallyFailed|Failed|FailedValidation)
        restore_diag "$name"
        die "restore ${name} finished ${phase}, not Completed. This environment is HALF restored: nothing here falls back to an empty bringup. Fix the cause and run the recreate again with the same RESTORE_FROM" ;;
    esac
    printf '  restore %s: %s          \r' "$name" "${phase:-<none>}"
    [ "$(date +%s)" -lt "$deadline" ] \
      || die "restore ${name} did not finish within ${RESTORE_TIMEOUT}s (last phase: ${phase:-<none>})"
    sleep 10
  done
}

# volume_restores_completed <restore-name> — how many PodVolumeRestores of
# that restore finished. Object JSON rides in the backup tarball; VOLUME data
# rides in the kopia repository and arrives only through these. A restore
# that reports Completed with zero of them moved no data at all.
volume_restores_completed() {
  kubectl -n "$VELERO_NS" get podvolumerestores.velero.io \
    -l velero.io/restore-name="$1" \
    -o jsonpath='{range .items[*]}{.status.phase}{"\n"}{end}' 2>/dev/null \
    | grep -c '^Completed$' || true
}

# wait_backup_synced — the Backup CR for the named backup exists in this
# cluster. Velero learns about a backup by SYNCING the bucket into a Backup
# object on its own clock, and a Restore that names a backup the server has
# not synced yet is refused as FailedValidation with "backup not found". The
# bucket has been proven to hold it (stage 0b), so this is a wait and not a
# check.
wait_backup_synced() {
  local deadline=$(( $(date +%s) + RESTORE_TIMEOUT ))
  while ! kubectl -n "$VELERO_NS" get "backup.velero.io/$RESTORE_FROM" >/dev/null 2>&1; do
    [ "$(date +%s)" -lt "$deadline" ] \
      || die "Velero never synced backup ${RESTORE_FROM} from the durable bucket within ${RESTORE_TIMEOUT}s. The object is there (stage 0b proved it), so the BackupStorageLocation is what to look at: $(kubectl -n "$VELERO_NS" get backupstoragelocation.velero.io/default -o jsonpath='{.status.phase}: {.status.message}' 2>/dev/null)"
    printf '  waiting for Velero to sync backup %s from the bucket          \r' "$RESTORE_FROM"
    sleep 10
  done
  printf '  ✓ Velero synced backup %s from the bucket\n' "$RESTORE_FROM"
}

# ---------------------------------------------------------------------------
# Restore 1 of 3 — THE SECRET STORE, before anything reads a Secret.
#
# OpenBao's file backend is a volume in the `gibson` namespace, so it rides in
# the same backup as everything else and comes back through the same
# mechanism. It is restored BEFORE the umbrella Application exists, which is
# what makes it deterministic: no StatefulSet is competing for the pod, and
# the claim is on disk before anything mounts it.
#
# Only four kinds, and only the objects the store's own pod needs to reach
# the point where the node-agent writes into its volume: the ServiceAccount
# (a pod naming an absent one is refused at admission), the config ConfigMap,
# the claim, and the pod itself. The StatefulSet is deliberately NOT restored
# — it would adopt the pod mid-restore, roll it for a controller-revision
# mismatch, and take the volume restore down with it. Argo brings the
# StatefulSet, and it adopts the restored claim.
#
# The seal is already in place: stage 1 wrote the bringup keyring into this
# namespace, so the restored store unseals from the keyring with no human
# step (ADR-0015). Without the keyring the archive is ciphertext, which is
# what the exit test's failing fixture measures.
# ---------------------------------------------------------------------------
restore_secret_store() {
  local name="${RESTORE_FROM}-openbao"
  log "restore 1/3: the secret store, from backup ${RESTORE_FROM}"
  wait_backup_synced
  kubectl apply -f - >/dev/null <<YAML
apiVersion: velero.io/v1
kind: Restore
metadata:
  name: ${name}
  namespace: ${VELERO_NS}
spec:
  backupName: ${RESTORE_FROM}
  includedNamespaces: ["${NS}"]
  # The LEGACY filter pair, on purpose. A Velero Restore has no scoped
  # filters: includedNamespaceScopedResources and its three siblings exist on
  # a Backup only (helm/gibson-velero/charts/velero-*.tgz, crds/restores.yaml).
  # The Restore CRD prunes a field it does not declare SILENTLY, so a scoped
  # filter here would not be rejected — it would simply not filter, and this
  # restore would bring back the whole namespace. tests/harness/restore.bats
  # checks every field of every Restore this script builds against that CRD.
  #
  # The kind persistentvolumes IS in this list, and it is what makes the restore move
  # data at all. Velero refuses to create a PodVolumeRestore when the restore's
  # resource filter excludes persistentvolumeclaims OR persistentvolumes
  # (pkg/restore/restore.go, "do not create podvolumerestore when current
  # restore excludes pv/pvc"). Without the line, this restore reported
  # Completed, itemsRestored 4 of 4, with ZERO PodVolumeRestores and not one
  # warning about it — measured on kind 2026-09-02 (deploy#1740), on three
  # dispatches of the blocker 3 loop. It is a gate on the FILTER, not a request
  # to restore a PV, and the line below is what keeps the PV itself out.
  includedResources:
    - serviceaccounts
    - configmaps
    - persistentvolumeclaims
    - persistentvolumes
    - pods
  # No cluster-scoped item. The PersistentVolume behind the claim is not
  # wanted: Velero clears the claim's binding so it is provisioned fresh, and
  # the node-agent writes the store's bytes into the new volume. This filter is
  # applied per item, so it keeps every PV out of the restore while the
  # resource list above still satisfies the pod-volume gate.
  labelSelector:
    matchLabels:
      app.kubernetes.io/component: openbao
  existingResourcePolicy: none
YAML
  wait_restore "$name"
  local moved
  moved="$(volume_restores_completed "$name")"
  if [ "${moved:-0}" -lt 1 ]; then
    restore_diag "$name"
  fi
  [ "${moved:-0}" -ge 1 ] \
    || die "the secret store restore reported Completed and moved NO volume data (0 PodVolumeRestores).
    Completed is NOT proof that data came back: a Restore whose labelSelector matches no
    item finishes Completed in seconds with no warning and no error (deploy#1738). The
    store's file backend is a volume, so this restore would leave the cluster with an EMPTY
    OpenBao wearing the name of a restored one. Three causes, all visible above: the
    selector matched nothing (itemsRestored 0); the restore's includedResources left out
    persistentvolumes or persistentvolumeclaims, which makes Velero create no pod volume
    restore at all and say nothing (deploy#1740); or the volume was a hostPath
    PersistentVolume, which the Velero node-agent skips — the default StorageClass must
    carry the annotation defaultVolumeType=local, which stage 1 sets."
  printf '  ✓ the secret store volume is back (%s PodVolumeRestore completed)\n' "$moved"
}

# wait_eso — every Secret is out of the restored store before ANY other
# restore runs.
#
# This is a gate, not a courtesy. Velero's backups never contain a Secret
# (the RBAC has no verb on them, CONTEXT.md § Velero backup), so every
# restored pod takes its Secrets from External Secrets. A tenant Neo4j pod
# restored before its auth Secret exists cannot mount it, sits in
# CreateContainerConfigError, and its volume restore never completes — a
# half-restored tenant that the Restore reports as a success.
# EVERY POLL IN THIS FILE ENDS WITH AN OR THAT EMPTIES THE VARIABLE, and that
# is not decoration. The caller runs under set -e, and an assignment from a
# command substitution carries that command's exit status. An object that is
# NOT THERE YET, the normal state of a poll, then kills the whole verb,
# silently, with the reason sent to /dev/null. MEASURED 2026-09-03 (deploy#1740), run
# 33699848618: the recreate printed the line below and exited, with no error
# and no diagnosis, because the ClusterSecretStore did not exist one second
# after the Application was applied. A poll must read "not yet" as not yet;
# the deadline below is what turns a wait that never ends into a loud failure.
wait_eso() {
  log "restore: waiting for External Secrets to sync every Secret out of the restored store"
  local deadline=$(( $(date +%s) + RESTORE_TIMEOUT )) total notready store
  while :; do
    store="$(kubectl get clustersecretstore gibson-secrets \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" || store=""
    if [ "$store" = True ]; then
      kubectl get externalsecrets --all-namespaces \
        -o 'jsonpath={range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
        2>/dev/null > "$WORK/es.txt" || true
      total="$(grep -c . "$WORK/es.txt" || true)"
      notready="$(awk '$3!="True"' "$WORK/es.txt" | wc -l)"
      if [ "${total:-0}" -gt 0 ] && [ "$notready" -eq 0 ]; then
        printf '  ✓ ClusterSecretStore gibson-secrets Ready, all %s ExternalSecrets synced\n' "$total"
        return 0
      fi
      printf '  ExternalSecrets: %s of %s synced          \r' "$(( ${total:-0} - notready ))" "${total:-0}"
    else
      printf '  ClusterSecretStore gibson-secrets: Ready=%s          \r' "${store:-<none>}"
    fi
    [ "$(date +%s)" -lt "$deadline" ] || {
      printf '\n' >&2
      awk '$3!="True"{print "    NOT SYNCED: "$1"/"$2" Ready="($3==""?"<none>":$3)}' "$WORK/es.txt" >&2 2>/dev/null || true
      die "External Secrets did not sync within ${RESTORE_TIMEOUT}s (ClusterSecretStore Ready=${store:-<none>}).
    The Velero restore below is NOT run: it would land pods whose Secrets do not exist yet,
    and they would sit in CreateContainerConfigError with their volumes never restored.
    A loud stop here beats a half-restored tenant that reports success."
    }
    sleep 10
  done
}

# ---------------------------------------------------------------------------
# Restores 2 and 3 of 3 — the built-ins, then the tenant records.
#
# The order is the one the tenant operator forces (CONTEXT.md § Velero
# backup): it ADOPTS a restored Neo4j claim rather than re-initialising it
# (gibson operators/tenant/internal/dataplane/neo4j.go, idempotent create).
# So the namespaces and the claims with the graph on them have to be on disk
# BEFORE the `Tenant` CRs arrive and the operator starts reconciling them.
#
# Restore 2 takes the backup whole except the `Tenant` CRs. Restore 3 takes
# the same backup and asks for the `Tenant` CRs and nothing else.
#
# Anything Argo has already created is skipped the same way. That is the
# design, not a gap: the umbrella is desired state and Argo owns it, and the
# only thing a restore has to bring is what is NOT in the chart.
#
# WHY THIS IS NOT A RACE WITH ARGO. Velero brings volume data back through a
# pod, so a store whose pod Argo has already created keeps the empty volume it
# was given. The umbrella's wave graph is what keeps that from happening: the
# ExternalSecrets this step waits on are wave -8, and EVERY volume-carrying
# store is wave 0 or later. Argo cannot reach wave 0 until wave -7 (the CNPG
# Cluster reaching Healthy) and wave -6 (the postgres-setup and Zitadel Jobs)
# are done, which is minutes of work. The one store that does not fit that
# rule is OpenBao itself, at wave -12, and it is restored by name above,
# before the umbrella Application exists at all.
# ---------------------------------------------------------------------------
restore_data() {
  local builtins="${RESTORE_FROM}-builtins" tenants="${RESTORE_FROM}-tenants" moved
  log "restore 2/3: namespaces, claims and volumes, from backup ${RESTORE_FROM}"
  kubectl apply -f - >/dev/null <<YAML
apiVersion: velero.io/v1
kind: Restore
metadata:
  name: ${builtins}
  namespace: ${VELERO_NS}
spec:
  backupName: ${RESTORE_FROM}
  existingResourcePolicy: none
  excludedResources:
    - tenants.gibson.zeroroot.ai
YAML
  wait_restore "$builtins"
  moved="$(volume_restores_completed "$builtins")"
  printf '  ✓ %s volume(s) restored\n' "${moved:-0}"

  log "restore 3/3: the tenant records, so the operator adopts what is already on disk"
  kubectl apply -f - >/dev/null <<YAML
apiVersion: velero.io/v1
kind: Restore
metadata:
  name: ${tenants}
  namespace: ${VELERO_NS}
spec:
  backupName: ${RESTORE_FROM}
  existingResourcePolicy: none
  includedResources:
    - tenants.gibson.zeroroot.ai
  includeClusterResources: true
YAML
  wait_restore "$tenants"
  printf '  ✓ tenants now in the cluster: %s\n' \
    "$(kubectl get tenants.gibson.zeroroot.ai -o jsonpath='{range .items[*]}{.metadata.name}{" "}{end}' 2>/dev/null)"
}
