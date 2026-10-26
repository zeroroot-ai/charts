#!/usr/bin/env bash
# require-helm.sh <version> — refuse to go on unless `helm` is that version.
#
# The goldens record what Argo CD deploys, so they are rendered with the Helm
# that Argo CD bundles (charts#789): Helm 4 keeps null defaults that Helm 3
# drops. A workstation with another Helm would render other goldens, and
# golden-update would write them back without a word. scripts/golden.sh calls
# this first.
#
# Exit: 0 the version matches · 1 another version, or no helm
set -euo pipefail
want="${1:?usage: require-helm.sh <version>}"
command -v helm >/dev/null 2>&1 || { echo "require-helm: helm is not on PATH; install Helm ${want}" >&2; exit 1; }
have="$(helm version --template '{{.Version}}' 2>/dev/null || true)"
if [ "$have" != "$want" ]; then
  echo "require-helm: helm is ${have:-unknown}, and the goldens need Helm ${want}, the Helm of the Argo CD that deploys the chart (charts#789)." >&2
  echo "  Install it, or put it first on PATH: https://get.helm.sh/helm-${want}-linux-amd64.tar.gz" >&2
  exit 1
fi
