#!/usr/bin/env bash
# check-platform-owner-values.sh — the Platform owner AND the seeded first
# tenant's Owner install values are required, must differ, and always have a
# way to reach their owner (ADR-0093 decisions 6/8, hosted#201/#202). Both
# people are provisioned with no password, ever, and both reuse the exact
# same setup-link mechanism — never a second one (ADR-0027) — so one script
# proves both halves of the same guard.
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
  --set global.platformOwner.email=admin@localhost.zeroroot.ai

must_fail "the equality check is case-insensitive" \
  "must be different addresses" \
  --set global.platformOwner.email=ADMIN@LOCALHOST.ZEROROOT.AI

# --- mail vs. offline mode ---------------------------------------------------
# The baseline ships global.email.provider=log (no delivering transport) and
# offlineSetup=true. Turning offlineSetup off with no delivering transport
# must fail; turning it off WITH a delivering transport must pass.
must_fail "no mail transport and offlineSetup=false" \
  "has no way to reach its owner" \
  --set global.platformOwner.offlineSetup=false

# The one mail value global.email (hosted#223) feeds the daemon, the
# tenant-operator and Zitadel, so one smtp block is a delivering transport for
# all three.
must_pass "smtp transport and offlineSetup=false" \
  --set global.platformOwner.offlineSetup=false \
  --set global.email.provider=smtp \
  --set global.email.from=no-reply@localhost.zeroroot.ai \
  --set global.email.fromName=Gibson \
  --set global.email.smtp.host=smtp.example.com

# offlineSetup=true with no place to write the link must fail.
must_fail "offlineSetup=true with no setupSecretRef.name" \
  "setupSecretRef.name is empty" \
  --set global.platformOwner.setupSecretRef.name=

# offlineSetup=true with no key must fail too: the CRD's shared
# SecretKeyRef type requires key, so a values file that omits it would
# render clean here and then be refused at apply time. Catch it at render
# time instead.
must_fail "offlineSetup=true with no setupSecretRef.key" \
  "setupSecretRef.key is empty" \
  --set global.platformOwner.setupSecretRef.key=

# --- the same three rules, for the seeded first tenant's Owner (hosted#202) --
# The baseline ships firstTenant.enabled=true, global.email.provider=log (no
# delivering transport) and firstTenant.offlineSetup=true.

must_fail "firstTenant: no mail transport and offlineSetup=false" \
  "has no way to reach its Owner" \
  --set global.firstTenant.offlineSetup=false

must_pass "firstTenant: smtp transport and offlineSetup=false" \
  --set global.firstTenant.offlineSetup=false \
  --set global.email.provider=smtp \
  --set global.email.from=no-reply@localhost.zeroroot.ai \
  --set global.email.fromName=Gibson \
  --set global.email.smtp.host=smtp.example.com

must_fail "firstTenant: offlineSetup=true with no setupSecretRef.name" \
  "global.firstTenant.setupSecretRef.name is empty" \
  --set global.firstTenant.setupSecretRef.name=

must_fail "firstTenant: offlineSetup=true with no setupSecretRef.key" \
  "global.firstTenant.setupSecretRef.key is empty" \
  --set global.firstTenant.setupSecretRef.key=

# firstTenant disabled: the mail/offline rule does not apply at all — no
# Owner is being seeded, so there is nothing to reach.
must_pass "firstTenant disabled: no mail transport and offlineSetup=false is fine" \
  --set global.firstTenant.enabled=false \
  --set global.firstTenant.ownerEmail= \
  --set global.firstTenant.offlineSetup=false


# --- the one mail value (hosted#223) ----------------------------------------
# With smtp, Zitadel names the sender, so fromName is required.
must_fail "global.email smtp with no fromName" \
  "global.email.fromName is required" \
  --set global.platformOwner.offlineSetup=false \
  --set global.email.provider=smtp \
  --set global.email.from=no-reply@localhost.zeroroot.ai \
  --set global.email.smtp.host=smtp.example.com

must_fail "global.email smtp with no host" \
  "global.email.smtp.host is required" \
  --set global.platformOwner.offlineSetup=false \
  --set global.email.provider=smtp \
  --set global.email.from=no-reply@localhost.zeroroot.ai \
  --set global.email.fromName=Gibson

# The baseline (provider log) needs no smtp block at all: it is the fixture.
must_pass "global.email log: no smtp block is needed"

[ "$fail" -eq 0 ] && echo "✓ platform-owner-values: required, must-differ and mail/offline rules all fire for both the Platform owner and the first tenant's Owner, and the baseline satisfies every one of them"
exit "$fail"
