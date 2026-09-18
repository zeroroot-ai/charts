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
# THIS IS AN EARLY WARNING, NOT A PROOF. The proof that a chart installs is an
# install, and scripts/baseline-up.sh does one on a cluster.
#
# The estimate here is the manifest plus the chart's own templates and files,
# gzipped and base64-scaled. Helm stores rather more: the manifest sits
# uncompressed inside a JSON document that also holds the chart metadata and
# the resolved values, and the whole document is gzipped. So the estimate runs
# LOW, and by a factor that depends on the shape of the chart rather than its
# size — a chart that is mostly manifest lands near 1.3, one with many small
# top-level templates near 2.1, a tiny chart near 4.5 where the fixed overhead
# dominates.
#
# A single fudge factor therefore cannot work: applied high enough to protect
# the umbrella it fails the operator CRDs, which are nowhere near the cap.
# So each chart carries its OWN factor, measured by installing it on a kind
# cluster and reading the Secret back. Every number below is a measurement,
# not a guess, and it is dated. A chart with no entry uses the worst factor
# seen, and says so.
#
#   measured 2026-09-14, kind, helm 3.18.4    estimate   actual   factor
#     gibson                                    283 KB   594 KB     2.10
#     gibson-crds                                43 KB   136 KB     3.16
#     gibson-operator-crds                      520 KB   699 KB     1.34
#     gibson-velero                              11 KB    49 KB     4.45
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# The Kubernetes Secret cap, and the share of it a chart may use.
CAP=1048576
MARGIN_PCT="${MARGIN_PCT:-80}"

# The charts that are PUBLISHED and therefore installed by helm, each with the
# factor measured for it (see the header). A chart that only ever renders
# through Argo is not listed, because Argo writes no record.
CHARTS=(gibson gibson-crds gibson-operator-crds gibson-velero)
FACTOR_gibson=210
FACTOR_gibson_crds=316
FACTOR_gibson_operator_crds=134
FACTOR_gibson_velero=445
# Used for a chart nobody has measured yet. The worst seen, so a new chart is
# over-reported rather than under-reported, and the message says to measure it.
FACTOR_DEFAULT=445

# record_bytes <chart-dir> [values-file] — the manifest as Helm would store
# it: gzip, then base64. Prints the byte count.
#
# The render must SUCCEED or the number is a lie: the umbrella refuses to
# render without a profile, and a failed render gzips to twenty bytes, which
# would read as a chart comfortably under the cap. That is the exact shape of
# the bug this file exists to catch, so it is a hard failure here.
record_bytes() { # <chart dir> [values file...]
  local out chart="$1"; shift
  local args=() f
  for f in "$@"; do args+=(-f "$f"); done
  out="$(helm template release "$chart" "${args[@]}")" || return 1
  [ -n "$out" ] || return 1
  printf '%s' "$out" | gzip -9 | wc -c | awk '{printf "%d", $1 * 4 / 3}'
}

# values_for <chart> — the profile a chart needs to render at all. Only the
# umbrella has required values; the CRD charts render bare.
values_for() { # one path per line: the profile, then the installer inputs the render needs
  case "$1" in
    gibson)        printf '%s/helm/gibson/values-baseline.yaml\n%s/helm/testdata/render-inputs/gibson.yaml\n' "$ROOT" "$ROOT" ;;
    gibson-velero) printf '%s/helm/testdata/render-inputs/gibson-velero.yaml\n' "$ROOT" ;;
    *)             printf '' ;;
  esac
}

fail=0
printf '  %-24s %10s %8s\n' CHART RECORD "OF CAP"
for chart in "${CHARTS[@]}"; do
  dir="$ROOT/helm/$chart"
  [ -d "$dir" ] || { echo "✗ check-helm-record-size: no chart at helm/$chart" >&2; exit 2; }
  mapfile -t vfiles < <(values_for "$chart")
  if ! bytes="$(record_bytes "$dir" "${vfiles[@]}")"; then
    echo "✗ check-helm-record-size: helm template failed for $chart, so its record size is unknown" >&2
    exit 2
  fi
  # The chart's own measured factor, in hundredths.
  var="FACTOR_${chart//-/_}"
  factor="${!var:-}"
  measured=yes
  if [ -z "$factor" ]; then factor="$FACTOR_DEFAULT"; measured=no; fi
  estimate=$(( bytes * factor / 100 ))
  pct=$(( estimate * 100 / CAP ))
  mark=" "
  if [ "$pct" -ge "$MARGIN_PCT" ]; then mark="✗"; fail=1; fi
  note=""
  [ "$measured" = no ] && note="  (no measured factor; using the worst seen — install it once and record its own)"
  printf '  %s %-22s %7d KB %6d%%%s\n' "$mark" "$chart" "$((estimate / 1024))" "$pct" "$note"
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
