#!/usr/bin/env bash
# A kubectl stand-in for tests/preflight-kvm.bats. It answers from STUB_*
# environment variables and records each probe Pod it applied in $STUB_DIR.
#
#   STUB_NODES      the node names, one per line
#   STUB_PHASE      the phase of each probe Pod        (default: Succeeded)
#   STUB_LOGS       the log line of each probe Pod     (default: kvm-present)
#   STUB_BAD_NODE   one node whose probe fails with phase Failed and no log
set -uo pipefail
args="$*"
case "$args" in
  "get nodes -l "*)
    printf '%b' "${STUB_NODES:-}"; exit 0 ;;
  *" delete pod "*)
    exit 0 ;;
  *" apply -f -")
    cat >> "$STUB_DIR/applied"
    exit 0 ;;
  *" get pod kvm-probe-"*" -o jsonpath={.status.phase}")
    pod="${args#* get pod }"; pod="${pod%% *}"
    [ "$pod" = "kvm-probe-${STUB_BAD_NODE:-}" ] && { echo Failed; exit 0; }
    echo "${STUB_PHASE:-Succeeded}"; exit 0 ;;
  *" logs kvm-probe-"*)
    pod="${args#* logs }"
    [ "$pod" = "kvm-probe-${STUB_BAD_NODE:-}" ] && exit 0
    printf '%s' "${STUB_LOGS-kvm-present}"; exit 0 ;;
esac
echo "kvm-kubectl-stub: unexpected call: $args" >&2
exit 2
