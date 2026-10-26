#!/usr/bin/env bash
# purge-tenant-backup.sh — delete the last backup of a deleted tenant before
# it expires (ADR-0075, charts#419).
#
# A tenant delete takes a last Velero backup that expires after 30 days. When
# an attempt fails, the tenant operator takes another one, so one delete can
# leave up to five attempts (gibson operators/tenant/internal/finalbackup,
# MaxAttempts). Each attempt can hold tenant data. A tenant owner can ask for
# an earlier purge. This script is the one reviewed way to do it, on every
# install type; the make verb of the hosted estate calls it and adds only the
# environment.
#
# It deletes every attempt of one delete, through one Velero
# DeleteBackupRequest each, which removes the Backup object and its data in the
# bucket. It selects the backups by the labels and the annotation that the
# tenant operator sets, never by a part of their name:
#
#   gibson.zeroroot.ai/tenant=<tenant>
#   gibson.zeroroot.ai/backup-type=final
#   annotation gibson.zeroroot.ai/tenant-uid=<uid of the deleted Tenant>
#
# It refuses:
#   - no tenant name, or a name that is not a Kubernetes name;
#   - a tenant that still exists (only the backup of a DELETED tenant goes);
#   - a tenant read that fails for another reason than NotFound;
#   - no matching backup;
#   - backups of more than one delete (more than one tenant uid), unless
#     TENANT_UID names one of them;
#   - a backup without the `final` label.
#
# Usage:
#   scripts/purge-tenant-backup.sh <tenant>
#   TENANT_UID=<uid> scripts/purge-tenant-backup.sh <tenant>
#
# Env:
#   VELERO_NS   the namespace of the Velero release   (default: velero)
#   TIMEOUT     seconds to wait for the delete         (default: 600)
#   KUBECTL     the kubectl command                    (default: kubectl)
set -euo pipefail

VELERO_NS="${VELERO_NS:-velero}"
TIMEOUT="${TIMEOUT:-600}"
KUBECTL="${KUBECTL:-kubectl}"
TENANT_UID="${TENANT_UID:-}"
L_TENANT="gibson.zeroroot.ai/tenant"
L_TYPE="gibson.zeroroot.ai/backup-type"
A_UID="gibson.zeroroot.ai/tenant-uid"

die() { echo "purge-tenant-backup: $*" >&2; exit 1; }

[ "$#" -eq 1 ] || die "give exactly one tenant name"
TENANT="$1"
[[ "$TENANT" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]] \
  || die "'$TENANT' is not a tenant name (a Kubernetes name)"

# 1. Only the backup of a DELETED tenant goes. With --ignore-not-found, an
#    answer with no name is the proof that the tenant is deleted. Any error (no
#    access, no answer, a resource the server does not serve) proves nothing,
#    so the purge stops. No error text is matched.
out="$($KUBECTL get tenants.gibson.zeroroot.ai "$TENANT" --ignore-not-found -o name 2>&1)" \
  || die "could not tell whether the tenant $TENANT exists, so nothing is purged: $out"
[ -z "$out" ] || die "the tenant $TENANT still exists. Only the last backup of a deleted tenant can be purged"

# 2. Select by label. Each line: <name> <tenant-uid> <backup-type>.
rows="$($KUBECTL -n "$VELERO_NS" get backups.velero.io \
          -l "${L_TENANT}=${TENANT},${L_TYPE}=final" \
          -o jsonpath="{range .items[*]}{.metadata.name}{' '}{.metadata.annotations.gibson\.zeroroot\.ai/tenant-uid}{' '}{.metadata.labels.gibson\.zeroroot\.ai/backup-type}{'\n'}{end}")" \
  || die "could not list the backups in namespace $VELERO_NS"
if [ -n "$TENANT_UID" ]; then
  rows="$(printf '%s\n' "$rows" | awk -v uid="$TENANT_UID" 'NF && $2 == uid')"
fi
count="$(printf '%s\n' "$rows" | awk 'NF' | wc -l | tr -d ' ')"
[ "$count" -ge 1 ] || die "no last backup of the tenant $TENANT${TENANT_UID:+ with the uid $TENANT_UID} in namespace $VELERO_NS"
uids="$(printf '%s\n' "$rows" | awk 'NF {print $2}' | sort -u)"
if [ "$(printf '%s\n' "$uids" | wc -l | tr -d ' ')" -gt 1 ]; then
  echo "purge-tenant-backup: the tenant name $TENANT has last backups of more than one delete:" >&2
  printf '%s\n' "$rows" | awk 'NF {print "  " $1 "  (" $2 ")"}' >&2
  die "set TENANT_UID to the uid of the deleted tenant whose backups go"
fi
BUID="$uids"
# Each row must be a last backup before any of them goes.
while read -r name _ type; do
  [ -n "$name" ] || continue
  [ "$type" = final ] || die "the backup $name is not a last backup (${L_TYPE}=$type)"
done <<<"$rows"
BACKUPS="$(printf '%s\n' "$rows" | awk 'NF {print $1}')"

echo "purge-tenant-backup: deleting $count backup(s) of the deleted tenant $TENANT (uid $BUID) in $VELERO_NS: $(printf '%s\n' "$BACKUPS" | tr '\n' ' ')"

# 3. One DeleteBackupRequest for each backup. Velero deletes the Backup object
#    and its data.
reqs=""
for BACKUP in $BACKUPS; do
  req="$($KUBECTL create -f - -o name <<YAML
apiVersion: velero.io/v1
kind: DeleteBackupRequest
metadata:
  generateName: ${BACKUP}-purge-
  namespace: ${VELERO_NS}
  labels:
    ${L_TENANT}: ${TENANT}
spec:
  backupName: ${BACKUP}
YAML
)" || die "could not create the DeleteBackupRequest for $BACKUP"
  echo "purge-tenant-backup: created $req for $BACKUP"
  reqs="$reqs $BACKUP=$req"
done

# 4. Wait until each Backup is gone. A processed request with errors fails.
deadline=$(( $(date +%s) + TIMEOUT ))
for pair in $reqs; do
  BACKUP="${pair%%=*}" req="${pair#*=}"
  while :; do
    if ! $KUBECTL -n "$VELERO_NS" get backups.velero.io "$BACKUP" >/dev/null 2>&1; then
      echo "purge-tenant-backup: backup $BACKUP is deleted"
      break
    fi
    phase="$($KUBECTL -n "$VELERO_NS" get "$req" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    errors="$($KUBECTL -n "$VELERO_NS" get "$req" -o jsonpath='{.status.errors}' 2>/dev/null || true)"
    if [ "$phase" = Processed ] && [ -n "$errors" ] && [ "$errors" != "[]" ]; then
      die "Velero could not delete $BACKUP: $errors"
    fi
    [ "$(date +%s)" -lt "$deadline" ] || die "the backup $BACKUP still exists after ${TIMEOUT}s (request $req, phase ${phase:-none})"
    sleep "${POLL_SECONDS:-5}"
  done
done
echo "purge-tenant-backup: every last backup of the tenant $TENANT (uid $BUID) is deleted"
