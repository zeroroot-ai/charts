#!/usr/bin/env bash
# check-vanilla-up-one-path.sh — the local and published installs are ONE path.
#
# scripts/vanilla-up.sh installs the platform two ways: from a chart checkout,
# which is how this repository is developed, and from the published OCI
# artifacts, which is what a stranger runs. Those two must install the SAME
# charts in the SAME order for "it works from source" and "it works from the
# registry" to mean the same thing.
#
# They drift the moment a release is added to one and not the other, or a
# chart is named directly instead of through chart_args(). That is not
# hypothetical: before this file existed, the published artifact had never
# been installed onto a local cluster by anything in the estate, so nothing
# would have noticed.
#
# What this asserts, against the script text rather than a run, because a run
# needs a cluster and this has to fail in the merge gate:
#
#   1. every first-party release is named through chart_args()
#   2. no helm install reaches for $CHART_DIR directly
#   3. the three releases install in the fixed order: CRDs, then velero, then
#      the umbrella, because the umbrella renders CRs of the CRDs and the
#      velero release owns its own namespace
#
# Self-tests run first, each against a copy of the script with one property
# broken. A guard that cannot fail is worse than no guard.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="${1:-$HERE/vanilla-up.sh}"
[ -r "$SCRIPT" ] || { echo "check-vanilla-up-one-path: cannot read $SCRIPT" >&2; exit 2; }

# The three first-party releases, in the order they must install.
# The order is load-bearing: the operator CRDs and the platform's own CRDs
# before anything that renders CRs of them, velero in its own namespace, the
# umbrella last.
ORDER=(gibson-operator-crds gibson-crds gibson-velero gibson)

# code <file> — executable lines only. A comment that mentions a chart must
# never satisfy an assertion; naming the property in prose is not shipping it.
code() { grep -vE '^[[:space:]]*#' "$1"; }

# assert_one_path <file> — the three properties. Prints the reason and returns
# 1 on the first failure, so the self-tests can drive it.
assert_one_path() {
  local f="$1" body seen=() name line
  body="$(code "$f")"

  # 2. No helm install may name the chart directory itself.
  if printf '%s\n' "$body" | grep -E 'helm upgrade --install' -A2 | grep -qF 'CHART_DIR}/'; then
    echo "a helm install names \$CHART_DIR directly, so the published mode cannot install that release"
    return 1
  fi

  # 1. Every release is named through chart_args.
  for name in "${ORDER[@]}"; do
    if ! printf '%s\n' "$body" | grep -qE "chart_args ${name}\b"; then
      echo "release ${name} is not named through chart_args(), so the two modes can disagree about it"
      return 1
    fi
  done

  # 3. The order is fixed.
  mapfile -t seen < <(printf '%s\n' "$body" | grep -oE "chart_args (gibson-operator-crds|gibson-crds|gibson-velero|gibson)\b" | awk '{print $2}')
  if [ "${#seen[@]}" -ne "${#ORDER[@]}" ]; then
    echo "expected ${#ORDER[@]} chart_args calls, found ${#seen[@]}"
    return 1
  fi
  for i in "${!ORDER[@]}"; do
    if [ "${seen[$i]}" != "${ORDER[$i]}" ]; then
      echo "install order is ${seen[*]}, and it must be ${ORDER[*]}: the umbrella renders CRs of the CRDs, so the CRDs cannot come second"
      return 1
    fi
  done
  return 0
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# --- self-test 1: a chart named directly is rejected -----------------------
sed 's|mapfile -t CRDS_CHART < <(chart_args gibson-crds)||; s|helm upgrade --install gibson-crds "${CRDS_CHART\[@\]}"|helm upgrade --install gibson-crds "${CHART_DIR}/gibson-crds"|' \
  "$SCRIPT" > "$WORK/direct.sh"
if assert_one_path "$WORK/direct.sh" >/dev/null 2>&1; then
  echo "✗ self-test broken: a release named through \$CHART_DIR must be rejected" >&2
  exit 1
fi

# --- self-test 2: a swapped order is rejected ------------------------------
#     CRDs after the umbrella is the failure this order exists to prevent.
python3 - "$SCRIPT" "$WORK/swapped.sh" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
s = open(src).read()
s = s.replace("chart_args gibson-crds", "chart_args __TMP__")
s = s.replace("chart_args gibson)", "chart_args gibson-crds)")
s = s.replace("chart_args __TMP__", "chart_args gibson)")
open(dst, "w").write(s)
PY
if assert_one_path "$WORK/swapped.sh" >/dev/null 2>&1; then
  echo "✗ self-test broken: installing the CRDs after the umbrella must be rejected" >&2
  exit 1
fi

# --- the real assertion ----------------------------------------------------
if ! reason="$(assert_one_path "$SCRIPT")"; then
  echo "✗ check-vanilla-up-one-path: ${reason}" >&2
  echo "  the chart checkout and the published artifact must install the same releases in the same order" >&2
  exit 1
fi

printf '✅ self-tests: a directly-named chart and a swapped order are both rejected; %s installs %s through one path, in that order, in both modes\n' \
  "$(basename "$SCRIPT")" "$(IFS=', '; echo "${ORDER[*]}")"
