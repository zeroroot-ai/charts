#!/usr/bin/env bash
# Retry `helm dependency update` for one chart.
#
# Third-party chart downloads (neo4j, openfga, zitadel, spire, cnpg) come from
# vendor CDNs that intermittently reset the TLS connection mid-download
# (deploy#1633). A single reset must not fail a render or lint job, so retry up
# to 3 times with a short backoff before giving up.
#
# Usage: scripts/helm-dep-update.sh <chart-dir>
set -euo pipefail

chart="${1:?usage: helm-dep-update.sh <chart-dir>}"

for attempt in 1 2 3; do
  if helm dependency update "$chart" > /dev/null; then
    exit 0
  fi
  echo "  ⚠ helm dependency update $chart failed (attempt $attempt/3); retrying..." >&2
  sleep $((attempt * 3))
done

echo "ERROR: helm dependency update $chart failed after 3 attempts" >&2
exit 1
