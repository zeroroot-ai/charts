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
# HOW IT MEASURES (charts#815). scripts/helm-record.py builds the record the
# way Helm 3 stores it: a JSON document with the rendered manifest, the
# templates and files of the TOP-LEVEL chart (base64 inside the JSON), its
# values and its metadata, then gzip, then base64. The files of a dependency
# are not in the record, only what it renders into the manifest.
#
# The check used to multiply a manifest-only estimate by a factor for each
# chart, measured on kind. That factor depended on the shape of the chart. A
# new dependency (the agentgateway and Gateway API CRDs in gibson-crds) made
# the old factor of gibson-crds wrong by a factor of three, and only a new
# kind install could correct it. The encoding has no factor to re-measure.
#
# One safety factor stays. The kind measurements of 2026-09-14 read 1.14 to
# 1.34 times the encoded size of the same charts, possibly because they read
# the Secret as kubectl prints it, base64 a second time. SAFETY applies the
# largest ratio, so the check never reports less than kind measured.
#
#   measured 2026-09-14, kind, helm 3.18.4    actual
#     gibson                                   594 KB
#     gibson-crds                              136 KB
#     gibson-operator-crds                     699 KB
#     gibson-velero                             49 KB
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="${HELM_RECORD_ROOT:-$(cd "$HERE/.." && pwd)}"

# --selftest: the check must fail on a chart over the margin and on a chart
# that does not render, and pass on the real charts (gibson#807 condition 7).
if [ "${1:-}" = "--selftest" ]; then
  work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
  fails=0
  expect() { # expect <exit code> <desc> <env...>
    local want="$1" desc="$2"; shift 2
    local got=0
    env "$@" "$0" >/dev/null 2>&1 || got=$?
    if [ "$got" = "$want" ]; then echo "  ok    $desc -> exit $got"
    else echo "  FAIL  $desc -> exit $got, want $want"; fails=$((fails + 1)); fi
  }
  mkdir -p "$work/helm/gibson/templates"
  printf 'apiVersion: v2\nname: gibson\nversion: 0.0.0\n' > "$work/helm/gibson/Chart.yaml"
  printf '{{ fail "planted render failure" }}\n' > "$work/helm/gibson/templates/x.yaml"
  expect 0 "the real charts under the margin" MARGIN_PCT=80
  expect 1 "MUTATION a margin every chart is over" MARGIN_PCT=1
  expect 2 "MUTATION a chart that does not render" HELM_RECORD_ROOT="$work"
  echo "check-helm-record-size --selftest: $fails failure(s)"
  exit $(( fails > 0 ))
fi

# The Kubernetes Secret cap, and the share of it a chart may use.
CAP=1048576
MARGIN_PCT="${MARGIN_PCT:-80}"

# The largest ratio of a kind measurement to the encoded size, in hundredths.
SAFETY=134

# The charts that are PUBLISHED and therefore installed by helm. A chart that
# only ever renders through Argo is not listed, because Argo writes no record.
CHARTS=(gibson gibson-crds gibson-operator-crds gibson-velero)

# record_bytes <chart-dir> [values-file...] — the record as Helm stores it.
# Prints the byte count.
#
# The render must SUCCEED or the number is a lie: the umbrella refuses to
# render without a profile, and a failed render encodes to a few bytes, which
# would read as a chart comfortably under the cap. That is the exact shape of
# the bug this file exists to catch, so it is a hard failure here.
record_bytes() { # <chart dir> [values file...]
  python3 "$HERE/helm-record.py" "$@"
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
  estimate=$(( bytes * SAFETY / 100 ))
  pct=$(( estimate * 100 / CAP ))
  mark=" "
  if [ "$pct" -ge "$MARGIN_PCT" ]; then mark="✗"; fail=1; fi
  printf '  %s %-22s %7d KB %6d%%\n' "$mark" "$chart" "$((estimate / 1024))" "$pct"
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
