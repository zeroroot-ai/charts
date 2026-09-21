#!/usr/bin/env bash
# Retry `helm dependency update` for one chart.
#
# Sub-chart tarballs come from GitHub release assets and vendor CDNs, which
# answer 504 or reset the TLS connection mid-download now and then
# (hosted#147, deploy#1633). One such answer must not fail a render or lint
# job, so retry up to 5 times with a growing backoff before giving up. The
# last attempt's helm output is printed on failure, so a real chart error
# reads as one and a network error names the URL.
#
# Usage: scripts/helm-dep-update.sh <chart-dir>
# HELM_DEP_UPDATE_BACKOFF: seconds per attempt of backoff (default 3). The
# check script sets it to 0.
set -euo pipefail

chart="${1:?usage: helm-dep-update.sh <chart-dir>}"
attempts=5
backoff="${HELM_DEP_UPDATE_BACKOFF:-3}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

for attempt in $(seq 1 "$attempts"); do
  if helm dependency update "$chart" > "$log" 2>&1; then
    exit 0
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    echo "  ⚠ helm dependency update $chart failed (attempt $attempt/$attempts); retrying..." >&2
    sleep $((attempt * backoff))
  fi
done

echo "ERROR: helm dependency update $chart failed after $attempts attempts:" >&2
tail -n 20 "$log" >&2
exit 1
