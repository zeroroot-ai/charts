#!/usr/bin/env bash
# purge-tenant-backup.sh — delete the last backup of a deleted tenant before
# it expires (ADR-0075, charts#419).
#
# A tenant delete takes one last Velero backup that expires after 30 days. A
# tenant owner can ask for an earlier purge. This script is the one reviewed
# way to do it, on every install type; the make verb of the hosted estate
# calls it and adds only the environment.
#
# It deletes exactly one backup, through a Velero DeleteBackupRequest, which
# removes the Backup object and its data in the bucket. It selects the backup
# by the labels that the tenant operator sets (gibson
# operators/tenant/internal/finalbackup), never by a part of its name:
#
#   gibson.zeroroot.ai/tenant=<tenant>
#   gibson.zeroroot.ai/backup-type=final
#   annotation gibson.zeroroot.ai/tenant-uid=<uid of the deleted Tenant>
#
# It refuses:
#   - no tenant name, or a name that is not a Kubernetes name;
#   - a tenant that still exists (only the backup of a DELETED tenant goes);
#   - no matching backup;
#   - more than one matching backup, unless TENANT_UID names exactly one;
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

# 1. Only the backup of a DELETED tenant goes.
if $KUBECTL get tenants.gibson.zeroroot.ai "$TENANT" >/dev/null 2>&1; then
  die "the tenant $TENANT still exists. Only the last backup of a deleted tenant can be purged"
fi

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
if [ "$count" -gt 1 ]; then
  echo "purge-tenant-backup: the tenant name $TENANT has $count last backups, one for each delete:" >&2
  printf '%s\n' "$rows" | awk 'NF {print "  " $1 "  (" $2 ")"}' >&2
  die "set TENANT_UID to the uid of the deleted tenant whose backup goes"
fi
read -r BACKUP BUID TYPE <<<"$(printf '%s\n' "$rows" | awk 'NF' | head -n1)"
[ "$TYPE" = final ] || die "the backup $BACKUP is not a last backup (${L_TYPE}=$TYPE)"

echo "purge-tenant-backup: deleting backup $BACKUP of the deleted tenant $TENANT (uid $BUID) in $VELERO_NS"

# 3. One DeleteBackupRequest. Velero deletes the Backup object and its data.
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
echo "purge-tenant-backup: created $req"

# 4. Wait until the Backup is gone. A processed request with errors fails.
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  if ! $KUBECTL -n "$VELERO_NS" get backups.velero.io "$BACKUP" >/dev/null 2>&1; then
    echo "purge-tenant-backup: backup $BACKUP is deleted"
    exit 0
  fi
  phase="$($KUBECTL -n "$VELERO_NS" get "$req" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  errors="$($KUBECTL -n "$VELERO_NS" get "$req" -o jsonpath='{.status.errors}' 2>/dev/null || true)"
  if [ "$phase" = Processed ] && [ -n "$errors" ] && [ "$errors" != "[]" ]; then
    die "Velero could not delete $BACKUP: $errors"
  fi
  [ "$(date +%s)" -lt "$deadline" ] || die "the backup $BACKUP still exists after ${TIMEOUT}s (request $req, phase ${phase:-none})"
  sleep "${POLL_SECONDS:-5}"
done
