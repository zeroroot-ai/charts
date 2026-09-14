#!/usr/bin/env bash
# check-helm-record-size.sh — every published chart still fits in a Helm release.
#
# A Helm release is stored as ONE Secret, `sh.helm.release.v1.<name>.vN`, and
# the API server caps a Secret at 1048576 bytes. The record holds the rendered
# manifest as JSON, gzipped, then base64-encoded, so base64's four-thirds is
# the last multiplier and the one people forget.
#
# Over the cap, `helm install` does not degrade. It fails outright:
#
#   Error: create: failed to create: Secret "sh.helm.release.v1.gibson-crds.v1"
#   is invalid: data: Too long: must have at most 1048576 bytes
#
# gibson-crds crossed that line some time before 2026-09-14 and no gate noticed,
# because nothing we run installs these charts with Helm. Argo renders and
# applies server-side and writes no release record. The publish workflow's
# smoke test is `helm template`, which writes none either. A chart can render
# perfectly and be uninstallable, and every signal we had said it was fine.
# It was measured at 851 KB on 2026-09-01 with a note in the source saying
# External Secrets adds CRDs every release and to watch the number. Watching
# by hand is not a control. This is (charts#82).
#
# The margin is deliberate. Failing AT the cap means the build breaks on the
# release that crosses it, which is the release you least want to be blocked
# on. Failing at 80% leaves room to see it coming and act on a normal day.
#
# This measures the MANIFEST, which is what grows. The record also carries the
# top-level chart's own templates and files; the memory of the 2026-09-01
# measurement records that estimating the whole record offline over-states it
# by about half again, so a manifest-only number with a 20% margin is both
# honest and conservative. The real proof that a chart installs is an install,
# and scripts/vanilla-up.sh does that on a cluster.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# The Kubernetes Secret cap, and the share of it a chart may use.
CAP=1048576
MARGIN_PCT="${MARGIN_PCT:-80}"

# The charts that are PUBLISHED and therefore installed by helm. A chart that
# only ever renders through Argo is not listed, because Argo writes no record.
CHARTS=(gibson gibson-crds gibson-operator-crds gibson-velero)

# record_bytes <chart-dir> [values-file] — the manifest as Helm would store
# it: gzip, then base64. Prints the byte count.
#
# The render must SUCCEED or the number is a lie: the umbrella refuses to
# render without a profile, and a failed render gzips to twenty bytes, which
# would read as a chart comfortably under the cap. That is the exact shape of
# the bug this file exists to catch, so it is a hard failure here.
record_bytes() {
  local out
  if [ -n "${2:-}" ]; then
    out="$(helm template release "$1" -f "$2")" || return 1
  else
    out="$(helm template release "$1")" || return 1
  fi
  [ -n "$out" ] || return 1
  printf '%s' "$out" | gzip -9 | wc -c | awk '{printf "%d", $1 * 4 / 3}'
}

# values_for <chart> — the profile a chart needs to render at all. Only the
# umbrella has required values; the CRD charts render bare.
values_for() {
  case "$1" in
    gibson) printf '%s/helm/gibson/values-vanilla.yaml' "$ROOT" ;;
    *)      printf '' ;;
  esac
}

fail=0
printf '  %-24s %10s %8s\n' CHART RECORD "OF CAP"
for chart in "${CHARTS[@]}"; do
  dir="$ROOT/helm/$chart"
  [ -d "$dir" ] || { echo "✗ check-helm-record-size: no chart at helm/$chart" >&2; exit 2; }
  if ! bytes="$(record_bytes "$dir" "$(values_for "$chart")")"; then
    echo "✗ check-helm-record-size: helm template failed for $chart, so its record size is unknown" >&2
    exit 2
  fi
  pct=$(( bytes * 100 / CAP ))
  mark=" "
  if [ "$pct" -ge "$MARGIN_PCT" ]; then mark="✗"; fail=1; fi
  printf '  %s %-22s %7d KB %6d%%\n' "$mark" "$chart" "$((bytes / 1024))" "$pct"
done

if [ "$fail" = 1 ]; then
  cat >&2 <<EOF

✗ check-helm-record-size: a chart marked above is within ${MARGIN_PCT}% of the
  1 MiB Helm release-record cap. Over the cap, \`helm install\` of that chart
  FAILS on every cluster — it does not degrade, and \`helm template\` will keep
  passing, so nothing else will tell you.

  The lever that worked last time was splitting the release: gibson-crds and
  gibson-operator-crds are two artifacts precisely because together they were
  1131 KB against a 1024 KB cap. Trimming CRDs the platform never instantiates
  is the other lever; the per-CRD toggles in helm/gibson-crds/values.yaml are
  an example.
EOF
  exit 1
fi

printf '\n✅ check-helm-record-size: %d published charts, all under %d%% of the 1 MiB release-record cap\n' \
  "${#CHARTS[@]}" "$MARGIN_PCT"
