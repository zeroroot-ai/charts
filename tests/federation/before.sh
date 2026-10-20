#!/usr/bin/env bash
# The first half of the fleet federation exit test (setec#251). The workflow
# exit-test-fleet-federation.yml runs it before the platform installs.
#
# SPIRE gives the daemon an SVID only when it holds a bundle of each domain
# that the daemon registration federates with, and it verifies an
# https_spiffe bundle endpoint only with a bundle of its domain. So the
# install takes the first bundle of the fleet domain as a chart value
# (gibson-workloads gibson.sandbox.setec.federation.trustDomainBundle), the
# way an operator gives it, and the fleet SPIRE server must run before the
# install. Its controller manager stays off until run.sh: the SPIRE CRDs
# come with gibson-crds, which the install brings.
#
# It writes the values file of that bundle and names it in SCENARIO_VALUES
# of $GITHUB_ENV, which the install step layers on the install.
set -euo pipefail
: "${GITHUB_ENV:?run in a GitHub Actions job}"
# shellcheck source=tests/federation/lib.sh
. "$(dirname "$0")/lib.sh"

install_spire fleet "$TD_FLEET" 9983 \
  --set spire-server.federation.enabled=true \
  --set spire-server.controllerManager.enabled=false
out="${RUNNER_TEMP:-/tmp}/fleet-bundle.json"
bundle spire-fleet spire-fleet-server-0 > "${out}.bundle"
jq -n --rawfile b "${out}.bundle" \
  '{"gibson-workloads": {gibson: {sandbox: {setec: {federation: {trustDomainBundle: $b}}}}}}' > "$out"
rm -f "${out}.bundle"
echo "SCENARIO_VALUES=$out" >> "$GITHUB_ENV"
echo "▶ the first bundle of ${TD_FLEET} is in $out"
