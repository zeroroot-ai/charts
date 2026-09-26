#!/usr/bin/env bash
# check-platform-owner-values.sh — the Platform owner install values are
# required, must differ, and always have a way to reach their owner
# (ADR-0093 decisions 6/8, hosted#201).
#
# The render guard is helm/gibson/templates/platform-owner-guard.yaml. This
# proves it actually fires on every rule it claims to enforce, and that the
# baseline profile (the fixture this guard exists for) satisfies all of them.
# A guard that cannot fail is worse than no guard.
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

# The baseline alone (+ installer inputs) must render clean: it is the
# fixture every rule below is measured against.
must_pass "baseline alone"

# --- missing values ---------------------------------------------------------
must_fail "missing global.platformOwner.email" \
  "global.platformOwner.email is REQUIRED" \
  --set global.platformOwner.email=

must_fail "missing global.firstTenant.ownerEmail while firstTenant.enabled" \
  "global.firstTenant.ownerEmail is REQUIRED" \
  --set global.firstTenant.ownerEmail=

# firstTenant.ownerEmail is NOT required when firstTenant is disabled
# (SaaS/staging/prod create their first tenant through self-serve signup).
must_pass "firstTenant disabled: ownerEmail may stay empty" \
  --set global.firstTenant.enabled=false \
  --set global.firstTenant.ownerEmail=

# --- equal addresses ---------------------------------------------------------
must_fail "platformOwner.email equals firstTenant.ownerEmail" \
  "must be different addresses" \
  --set global.platformOwner.email=admin@selfhosted.example.com

must_fail "the equality check is case-insensitive" \
  "must be different addresses" \
  --set global.platformOwner.email=ADMIN@SELFHOSTED.EXAMPLE.COM

# --- mail vs. offline mode ---------------------------------------------------
# The baseline ships email.provider=log (no delivering transport) and
# offlineSetup=true. Turning offlineSetup off with no delivering transport
# must fail; turning it off WITH a delivering transport must pass.
must_fail "no mail transport and offlineSetup=false" \
  "has no way to reach its owner" \
  --set global.platformOwner.offlineSetup=false

must_pass "smtp transport and offlineSetup=false" \
  --set global.platformOwner.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com

# offlineSetup=true with no place to write the link must fail.
must_fail "offlineSetup=true with no setupSecretRef.name" \
  "setupSecretRef.name is empty" \
  --set global.platformOwner.setupSecretRef.name=

[ "$fail" -eq 0 ] && echo "✓ platform-owner-values: required, must-differ and mail/offline rules all fire, and the baseline satisfies every one of them"
exit "$fail"
