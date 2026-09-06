#!/usr/bin/env bash
# preserve-backup.sh — the preserve half of a teardown, for EVERY substrate
# (ADR-0015, ADR-0014, deploy#1738, deploy#1746).
#
# A preserve teardown is four steps before the destroy, and not one of them
# knows what the cluster runs on. They speak kubectl, the durable bucket and
# the keyring, so kind and EKS run the SAME code and there is no second
# implementation to drift (ADR-0027). What differs per substrate is the
# destroy, and the destroy stays in the caller.
#
# Source this file, then call `preserve_backup <backup-name>`. The caller
# supplies these before the call, and the function reads nothing else:
#
#   SUBSTRATE_ENV  path to substrate.env            (required)
#   REPO           repo root                        (required)
#   HERE           the scripts/ directory           (required)
#   MODE           preserve, for the log prefix     (required)
#   NS             release namespace                (default gibson)
#   VELERO_NS      velero namespace                 (default velero)
#   SCHEDULE       the shipped Velero Schedule      (default platform-hourly)
#   CNPG_CLUSTER   the CNPG Cluster CR              (default platform-postgres)
#   BACKUP_TIMEOUT / VERIFY_TIMEOUT  seconds        (defaults 900 / 240)
#   WORK           a scratch directory the caller cleans up (required)
#
# The caller also supplies `log` and `die`. `die` MUST exit non-zero without
# destroying anything: every failure below leaves the estate standing, names
# the step, and never falls back to a wipe (CONTEXT.md § Preserve teardown).
#
# The steps, in the order docs/bringup.md § A preserve teardown states:
#
#   0. backup-verify record — plant the canary that has to ride IN the backup,
#      and record a ciphertext sealed under the LIVE store's key. It runs
#      FIRST because it needs the keyring: a teardown that cannot read its
#      keyring must abort before it has touched Postgres, Velero or anything
#      else.
#   1. test/dr/cnpg-backup-and-wait.sh — a CNPG Backup CR, waited to
#      `completed`. This is a script step and NOT a Velero hook: Velero's only
#      hook mechanism is an exec inside a target pod, and "create a Backup CR
#      and watch its status" is an API interaction. A timeout ABORTS the
#      teardown (deploy#1735).
#   2. the Velero backup, built from the shipped `platform-hourly` Schedule
#      template — the same object `velero backup create --from-schedule`
#      builds, built with kubectl so no velero CLI is needed on a runner.
#      PartiallyFailed is a FAILURE, not a warning: it is what a spec that
#      asks for Secrets comes back as, and it means items are missing.
#   3. backup-verify verify — the canary decrypt gate.
#
# One name covers all three artifacts, so a later recreate finds every piece
# of a restore from the single string the teardown prints (ADR-0014).

# explain_bad_backup <name> — WHY a backup is not Completed, in the words
# Velero itself recorded.
#
# `.status` carries a NUMBER of errors and not one of them. Velero writes the
# per-item errors to the backup's OWN log, which it stores beside the backup
# in the durable bucket and never sends to the server pod's stdout, so
# `kubectl logs deploy/velero` does not hold them either. An operator whose
# teardown aborted therefore had a count and no cause: measured on 2026-09-02
# (deploy#1740), three runs of the kind loop reported `errors: 134` and no way
# to learn which items those were without a live cluster.
#
# The log comes back through a DownloadRequest: Velero signs a URL for the
# object and this reads it. That path needs no bucket credential here and no
# velero CLI, and it works the same on kind's MinIO and on S3.
#
# This function only ever prints. Every failure inside it is swallowed,
# because it runs on the way to an abort and must not replace the real
# message with one about diagnostics.
explain_bad_backup() {
  local name="$1" dr="teardown-explain-${1}" url="" deadline
  kubectl apply -f - >/dev/null 2>&1 <<YAML
apiVersion: velero.io/v1
kind: DownloadRequest
metadata:
  name: ${dr}
  namespace: ${VELERO_NS}
spec:
  target:
    kind: BackupLog
    name: ${name}
YAML
  deadline=$(( $(date +%s) + 60 ))
  while :; do
    url="$(kubectl -n "$VELERO_NS" get "downloadrequest.velero.io/${dr}" -o jsonpath='{.status.downloadURL}' 2>/dev/null)"
    [ -n "$url" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf '  (Velero signed no download URL for the log of %s within 60s, so the errors cannot be named here)\n' "$name" >&2
      return 0
    fi
    sleep 2
  done
  if ! curl -fsSL "$url" -o "$WORK/backup-log.gz" 2>/dev/null; then
    printf '  (the signed URL for the log of %s did not answer)\n' "$name" >&2
    return 0
  fi
  if ! python3 -c 'import gzip,sys;sys.stdout.write(gzip.open(sys.stdin.buffer,"rt",errors="replace").read())' \
       < "$WORK/backup-log.gz" > "$WORK/backup.log" 2>/dev/null; then
    printf '  (the log of %s came back unreadable)\n' "$name" >&2
    return 0
  fi
  # Everything that makes two reports of the SAME fault look different goes
  # before the grouping: the timestamp, the log source, the backup name, the
  # Go function, and the NAMESPACE. The item collector lists every kind once
  # per included namespace, so one kind Velero cannot read is one error per
  # namespace; folding the namespace away turns those back into one line that
  # says which kind, and how many. Measured on 2026-09-02 (deploy#1740): with
  # the namespace left in, one kind filled four lines and the display cap hid
  # two thirds of the kinds behind the ones late in the alphabet.
  local grouped="$WORK/backup-errors.txt"
  grep 'level=error' "$WORK/backup.log" \
    | sed -e 's/^time="[^"]*" //' -e 's/^level=error //' \
          -e 's/ logSource="[^"]*"//' -e 's/ backup=[^ ]*//' \
          -e 's/ error\.function="[^"]*"//' \
          -e 's/ in the namespace \\"[^\\]*\\"//' \
    | sort | uniq -c | sort -rn > "$grouped"
  printf '\n  %s distinct errors in the backup log, most frequent first, at most 40 shown.\n' \
    "$(wc -l < "$grouped")" >&2
  printf '  The count is how many namespaces the fault was reported in.\n' >&2
  head -40 "$grouped" >&2
  kubectl -n "$VELERO_NS" delete "downloadrequest.velero.io/${dr}" --ignore-not-found >/dev/null 2>&1 || true
}

# preserve_backup <backup-name> — steps 0 through 3. Returns 0 only when the
# backup exists AND has been proven readable with the keyring.
preserve_backup() {
  local backup_name="$1"

  # --- 0. record ----------------------------------------------------------
  log "0/4 backup-verify record: the canary and the seal ciphertext, before the backup"
  "$HERE/backup-verify.sh" record \
    --substrate-env "$SUBSTRATE_ENV" \
    --backup-name "$backup_name" \
    --namespace "$NS" \
    --velero-namespace "$VELERO_NS" \
    --timeout "$VERIFY_TIMEOUT" \
    || die "backup-verify record failed. NOTHING was backed up and NOTHING was destroyed"

  # --- 1. Postgres --------------------------------------------------------
  log "1/4 CNPG base backup ${backup_name}, waited to completed"
  "$REPO/test/dr/cnpg-backup-and-wait.sh" \
    --namespace "$NS" --cluster "$CNPG_CLUSTER" \
    --name "$backup_name" --timeout "$BACKUP_TIMEOUT" \
    || die "the CNPG base backup did not complete. A preserve teardown MUST NOT proceed past this: the estate is still standing and nothing was destroyed"

  # --- 2. Velero ----------------------------------------------------------
  log "2/4 Velero backup ${backup_name} from the shipped Schedule ${SCHEDULE}"
  local schedule_json="$WORK/schedule.json" backup_json
  kubectl -n "$VELERO_NS" get "schedule.velero.io/$SCHEDULE" -o json > "$schedule_json" 2>/dev/null \
    || die "the Schedule ${VELERO_NS}/${SCHEDULE} is not in the cluster: the gibson-velero release is not installed, so there is no shipped backup spec to build from"
  backup_json="$(python3 "$HERE/lib/velero-backup-from-schedule.py" "$backup_name" < "$schedule_json")" \
    || die "the shipped Schedule ${SCHEDULE} cannot be used for a preserve backup (see the line above). Nothing was destroyed"
  printf '%s' "$backup_json" | kubectl apply -f - >/dev/null \
    || die "could not create the Velero Backup ${backup_name}"

  local deadline phase
  deadline=$(( $(date +%s) + BACKUP_TIMEOUT ))
  phase=""
  while :; do
    phase="$(kubectl -n "$VELERO_NS" get "backup.velero.io/$backup_name" -o jsonpath='{.status.phase}' 2>/dev/null)"
    printf '  backup %s phase=%s\n' "$backup_name" "${phase:-<none>}"
    case "$phase" in
      Completed) break ;;
      PartiallyFailed)
        kubectl -n "$VELERO_NS" get "backup.velero.io/$backup_name" -o jsonpath='{.status}' >&2
        explain_bad_backup "$backup_name"
        die "the Velero backup finished PartiallyFailed. That is a FAILURE, not a warning: items the spec asked for are missing from the backup, so it is not a backup this teardown may destroy behind. The estate is still standing" ;;
      Failed)
        kubectl -n "$VELERO_NS" get "backup.velero.io/$backup_name" -o jsonpath='{.status}' >&2
        explain_bad_backup "$backup_name"
        die "the Velero backup finished Failed. The estate is still standing" ;;
      FailedValidation)
        # A spec Velero refused. It ran nothing, so it wrote no log to read:
        # the reason is in the object itself.
        kubectl -n "$VELERO_NS" get "backup.velero.io/$backup_name" -o jsonpath='{.status}' >&2
        die "the Velero backup finished FailedValidation: Velero refused the spec this teardown built from the Schedule ${SCHEDULE}. The estate is still standing" ;;
    esac
    [ "$(date +%s)" -lt "$deadline" ] \
      || die "the Velero backup did not finish within ${BACKUP_TIMEOUT}s (last phase: ${phase:-<none>}). The estate is still standing"
    sleep 10
  done
  printf '  ✓ backup %s Completed\n' "$backup_name"

  # --- 3. backup-verify ---------------------------------------------------
  log "3/4 backup-verify: open the backup with the keyring the automation holds"
  "$HERE/backup-verify.sh" verify \
    --substrate-env "$SUBSTRATE_ENV" \
    --backup-name "$backup_name" \
    --cnpg-backup "$backup_name" \
    --namespace "$NS" \
    --velero-namespace "$VELERO_NS" \
    --cnpg-cluster "$CNPG_CLUSTER" \
    --timeout "$VERIFY_TIMEOUT" \
    || die "backup-verify failed on backup ${backup_name}. The teardown is ABORTED and the estate is STILL RUNNING. There is no fallback to MODE=wipe (CONTEXT.md § Preserve teardown)"
}
