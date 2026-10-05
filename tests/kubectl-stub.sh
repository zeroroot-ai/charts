#!/usr/bin/env bash
# A kubectl stand-in for tests/purge-tenant-backup.bats. It answers from
# STUB_* environment variables and records what it created in $STUB_DIR.
set -uo pipefail
args="$*"
case "$args" in
  "get tenants.gibson.zeroroot.ai "*)
    [ "${STUB_TENANT_EXISTS:-0}" = 1 ] && exit 0 || exit 1 ;;
  *"get backups.velero.io -l "*)
    printf '%b' "${STUB_ROWS:-}"; exit 0 ;;
  "create -f - -o name")
    cat > "$STUB_DIR/created"
    echo "deletebackuprequest.velero.io/stub-purge-x"
    [ "${STUB_DELETE_FAILS:-0}" = 1 ] || touch "$STUB_DIR/deleted"
    exit 0 ;;
  *"get backups.velero.io "*)
    [ -e "$STUB_DIR/deleted" ] && exit 1 || exit 0 ;;
  *"get deletebackuprequest.velero.io/stub-purge-x -o jsonpath={.status.phase}")
    echo Processed; exit 0 ;;
  *"get deletebackuprequest.velero.io/stub-purge-x -o jsonpath={.status.errors}")
    echo '["the bucket refused the delete"]'; exit 0 ;;
esac
echo "kubectl-stub: unexpected call: $args" >&2
exit 2
