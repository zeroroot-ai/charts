#!/usr/bin/env bash
# check-app-url-links.sh — an explicit gibson.appUrl override must be a
# public, product-surface origin, never the API-plane host or an in-cluster
# address, because it lands in a link a human's browser must open.
#
# The render guard is helm/gibson/templates/app-url-guard.yaml. This proves
# it actually fires on every shape it claims to refuse, and that the
# baseline profile (the fixture this guard exists for) renders clean with
# no override set. A guard that cannot fail is worse than no guard.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CHART="$ROOT/helm/gibson"
BASE="$CHART/values-baseline.yaml"
INPUTS="$ROOT/helm/testdata/render-inputs/gibson.yaml"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fail=0

must_fail() { # <label> <expected message fragment> <extra helm --set args...>
  local label="$1" want="$2"; shift 2
  if helm template gibson "$CHART" -f "$BASE" -f "$INPUTS" --namespace gibson "$@" >/dev/null 2>"$WORK/err"; then
    echo "❌ $label rendered; the guard did not fire"; fail=1
  elif ! grep -qF -- "$want" "$WORK/err"; then
    echo "❌ $label failed, but not on the expected message: $(tail -n1 "$WORK/err")"; fail=1
  fi
}
must_pass() { # <label> <extra helm --set args...>
  local label="$1"; shift
  helm template gibson "$CHART" -f "$BASE" -f "$INPUTS" --namespace gibson "$@" >/dev/null 2>"$WORK/err" \
    || { echo "❌ $label must render: $(tail -n1 "$WORK/err")"; fail=1; }
}

# The baseline alone (+ installer inputs) must render clean: neither subchart
# overrides gibson.appUrl, so both fall back to gibson.externalOrigin.
must_pass "baseline alone"

# --- API-plane host ----------------------------------------------------------
must_fail "gibson-workloads override set to the API-plane host" \
  "which is the API-plane host" \
  --set gibson-workloads.gibson.appUrl=https://api.selfhosted.example.com

must_fail "gibson-operators override set to the API-plane host" \
  "which is the API-plane host" \
  --set gibson-operators.gibson.appUrl=https://api.selfhosted.example.com

# --- in-cluster address ------------------------------------------------------
# The literal old broken default the tenant-operator's DASHBOARD_URL var
# used to carry, before it was replaced by GIBSON_APP_URL.
must_fail "gibson-operators override set to the old in-cluster default" \
  "looks like an in-cluster address" \
  --set gibson-operators.gibson.appUrl=http://gibson-dashboard:3000

must_fail "gibson-workloads override set to a bare in-cluster Service name" \
  "looks like an in-cluster address" \
  --set gibson-workloads.gibson.appUrl=http://gibson-dashboard

must_fail "gibson-operators override set to a .svc.cluster.local address" \
  "looks like an in-cluster address" \
  --set gibson-operators.gibson.appUrl=http://gibson-dashboard.gibson.svc.cluster.local:3000

# --- a normal public override must pass --------------------------------------
must_pass "gibson-workloads override set to a normal product-surface origin" \
  --set gibson-workloads.gibson.appUrl=https://app.selfhosted.example.com

must_pass "gibson-operators override set to a normal product-surface origin" \
  --set gibson-operators.gibson.appUrl=https://app.selfhosted.example.com

[ "$fail" -eq 0 ] && echo "✓ app-url-links: the API-plane-host and in-cluster-address rules both fire on either subchart, and the baseline plus a real product origin both render clean"
exit "$fail"
