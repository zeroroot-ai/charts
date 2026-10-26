#!/usr/bin/env bash
# A kubectl stand-in for tests/purge-tenant-backup.bats. It answers from
# STUB_* environment variables and records what it created in $STUB_DIR.
set -uo pipefail
args="$*"
case "$args" in
  "get tenants.gibson.zeroroot.ai "*)
    case "${STUB_TENANT_EXISTS:-0}" in
      1) echo "tenant.gibson.zeroroot.ai/acme"; exit 0 ;;
      forbidden) echo 'Error from server (Forbidden): tenants.gibson.zeroroot.ai "acme" is forbidden: User "x" cannot get resource "tenants"' >&2; exit 1 ;;
      timeout) echo 'Unable to connect to the server: dial tcp 127.0.0.1:6443: i/o timeout' >&2; exit 1 ;;
      nocrd) echo 'error: the server doesn'"'"'t have a resource type "tenants"' >&2; exit 1 ;;
      unserved) echo 'Error from server (NotFound): the server could not find the requested resource' >&2; exit 1 ;;
      # A deleted tenant: --ignore-not-found answers nothing, with exit 0.
      *) case " $* " in *" --ignore-not-found "*) exit 0 ;; esac
         echo 'Error from server (NotFound): tenants.gibson.zeroroot.ai "acme" not found' >&2; exit 1 ;;
    esac ;;
  *"get backups.velero.io -l "*)
    printf '%b' "${STUB_ROWS:-}"; exit 0 ;;
  "create -f - -o name")
    body="$(cat)"
    printf '%s\n' "$body" >> "$STUB_DIR/created"
    name="$(printf '%s\n' "$body" | awk '$1 == "backupName:" {print $2}')"
    echo "deletebackuprequest.velero.io/stub-purge-x"
    [ "${STUB_DELETE_FAILS:-0}" = 1 ] || touch "$STUB_DIR/deleted-$name"
    exit 0 ;;
  *"get backups.velero.io "*)
    # The name of the Backup is the last argument.
    name="${*: -1}"
    [ -e "$STUB_DIR/deleted-$name" ] && exit 1 || exit 0 ;;
  *"get deletebackuprequest.velero.io/stub-purge-x -o jsonpath={.status.phase}")
    echo Processed; exit 0 ;;
  *"get deletebackuprequest.velero.io/stub-purge-x -o jsonpath={.status.errors}")
    echo '["the bucket refused the delete"]'; exit 0 ;;
esac
echo "kubectl-stub: unexpected call: $args" >&2
exit 2
