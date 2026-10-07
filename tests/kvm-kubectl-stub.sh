#!/usr/bin/env bash
# A kubectl stand-in for tests/preflight-kvm.bats. It answers from STUB_*
# environment variables and records each probe Pod it applied in $STUB_DIR.
#
#   STUB_NODES      the node names, one per line
#   STUB_PHASE      the phase of each probe Pod        (default: Succeeded)
#   STUB_LOGS       the log line of each probe Pod     (default: kvm-present)
#   STUB_BAD_NODE   one node whose probe exits with phase Failed and the log kvm-absent
#   STUB_STUCK_NODE one node whose probe Pod stays Pending with no log, as a
#                   refused hostPath or an image pull failure leaves it
set -uo pipefail
args="$*"
case "$args" in
  "get nodes -l "*)
    printf '%s\n' "${args#get nodes -l }" | cut -d' ' -f1 > "$STUB_DIR/selector"
    printf '%b' "${STUB_NODES:-}"; exit 0 ;;
  *" delete pod "*)
    exit 0 ;;
  *" apply -f -")
    cat >> "$STUB_DIR/applied"
    exit 0 ;;
  *" get pod fleet-probe-"*" -o jsonpath={.status.phase}")
    pod="${args#* get pod }"; pod="${pod%% *}"
    [ "$pod" = "fleet-probe-${STUB_BAD_NODE:-}" ] && { echo Failed; exit 0; }
    [ "$pod" = "fleet-probe-${STUB_STUCK_NODE:-}" ] && { echo Pending; exit 0; }
    echo "${STUB_PHASE:-Succeeded}"; exit 0 ;;
  *" logs fleet-probe-"*)
    pod="${args#* logs }"
    [ "$pod" = "fleet-probe-${STUB_BAD_NODE:-}" ] && { printf 'kvm-absent'; exit 0; }
    [ "$pod" = "fleet-probe-${STUB_STUCK_NODE:-}" ] && exit 0
    printf '%s' "${STUB_LOGS-kvm-present}"; exit 0 ;;
  *" get events --field-selector involvedObject.name=fleet-probe-"*)
    echo "Failed: Error: ErrImagePull"; exit 0 ;;
esac
echo "kvm-kubectl-stub: unexpected call: $args" >&2
exit 2
