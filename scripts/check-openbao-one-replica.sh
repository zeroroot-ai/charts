#!/usr/bin/env bash
# check-openbao-one-replica.sh — the render fails above one OpenBao replica.
#
# The chart writes `storage "file"` for OpenBao, so each pod holds its own data
# directory. The prod overlay set `openbao.replicas: 3` (hosted#400): three
# separate secret stores behind one Service, on the one External Secrets
# backend. Nothing failed, because the StatefulSet renders any replica count.
#
# Three outcomes, each from a real `helm template`:
#   1. the baseline (one replica) renders                      (must pass)
#   2. three replicas fail the render and name the reason      (THE FIXTURE)
#   3. the failure is the guard's, not an unrelated render error
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
render() {
  helm template gibson helm/gibson -f helm/gibson/values-baseline.yaml \
    -f helm/testdata/render-inputs/gibson.yaml --namespace gibson "$@"
}
fail=0
if ! render >/dev/null 2>"${TMPDIR:-/tmp}/bao-one.err"; then
  echo "FAIL: the baseline does not render:"; tail -3 "${TMPDIR:-/tmp}/bao-one.err"; fail=1
fi
if out="$(render --set gibson-workloads.openbao.replicas=3 2>&1 >/dev/null)"; then
  echo "FAIL: three OpenBao replicas rendered. Each one would be a separate secret store."; fail=1
elif ! printf '%s' "$out" | grep -q "each replica would be a separate secret store"; then
  echo "FAIL: three replicas failed the render, but not with the guard's reason:"; printf '%s\n' "$out" | tail -3; fail=1
else
  echo "  ✓ one OpenBao replica renders, and three fail the render with the reason"
fi
exit $fail
