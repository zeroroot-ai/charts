#!/usr/bin/env bash
# check-openfga-authn.sh: the render fails when OpenFGA would run without its
# preshared key (charts#637, helm/gibson/templates/openfga-authn-guard.yaml).
#
# Outcomes, each from a real `helm template`:
#   1. the baseline renders, and OpenFGA reads its key list from the Secret
#   2. each fixture below fails the render and names the reason
#      (THE FIXTURES)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
err="$(mktemp)"
trap 'rm -f "$err"' EXIT
render() {
  helm template gibson helm/gibson -f helm/gibson/values-baseline.yaml \
    -f helm/testdata/render-inputs/gibson.yaml --namespace gibson "$@"
}
fail=0
if ! out="$(render 2>"$err")"; then
  echo "FAIL: the baseline does not render:"; tail -3 "$err"; exit 1
fi
if ! grep -q 'name: OPENFGA_AUTHN_METHOD' <<<"$out"; then
  echo "FAIL: the baseline OpenFGA has no authentication method"; fail=1
fi
# fixture: the --set arguments | the reason the render must name
fixtures=(
  "--set openfga.authn.method=null|must run with the preshared key method"
  "--set openfga.authn.method=none|must run with the preshared key method"
  "--set openfga.authn.preshared.keysSecret=other|must be \"gibson-openfga-preshared-key\""
  "--set openfga.authn.preshared.keys[0]=literal|holds a literal key"
  "--set openfga.authn.oidc.issuer=https://idp.example|runs with one method"
)
for f in "${fixtures[@]}"; do
  args="${f%%|*}"
  reason="${f#*|}"
  # shellcheck disable=SC2086 # the fixture is a word list of helm flags
  if render $args >/dev/null 2>"$err"; then
    echo "FAIL: '${args}' rendered. OpenFGA would run without its preshared key."; fail=1
  elif ! grep -qF -- "$reason" "$err"; then
    echo "FAIL: '${args}' failed the render, but not with the guard's reason:"; tail -3 "$err"; fail=1
  else
    echo "  ✓ '${args}' fails the render: ${reason}"
  fi
done
[ "$fail" -eq 0 ] && echo "  ✓ openfga-authn: the baseline renders with the preshared key, and each fixture fails"
exit "$fail"
