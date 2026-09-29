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
# The baseline ships email.provider=log (no delivering transport) and
# offlineSetup=true. Turning offlineSetup off with no delivering transport
# must fail; turning it off WITH a delivering transport must pass.
must_fail "no mail transport and offlineSetup=false" \
  "has no way to reach its owner" \
  --set global.platformOwner.offlineSetup=false

must_pass "smtp transport and offlineSetup=false" \
  --set global.platformOwner.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromAddress=no-reply@localhost.zeroroot.ai \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromName=Gibson

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
# The baseline ships firstTenant.enabled=true, email.provider=log (no
# delivering transport) and firstTenant.offlineSetup=true.

must_fail "firstTenant: no mail transport and offlineSetup=false" \
  "has no way to reach its Owner" \
  --set global.firstTenant.offlineSetup=false

must_pass "firstTenant: smtp transport and offlineSetup=false" \
  --set global.firstTenant.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromAddress=no-reply@localhost.zeroroot.ai \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromName=Gibson

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


# --- Zitadel's own SMTP provider (hosted#189, helm/gibson/templates/zitadel-smtp-guard.yaml) --
# The daemon can send mail while Zitadel cannot: two separate transports, one
# render guard for the gap. must_pass above already proves the fixture that
# sets BOTH sides passes; these prove a render that sets only the daemon's
# side fails, on each field the guard names.
must_fail "gibson-workloads smtp on, gibson-operators smtp.host empty" \
  "gibson-operators.platformBootstrap.zitadel.smtp.host is empty" \
  --set global.platformOwner.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com

# The internal completeness of the smtp block (once .host is set) is
# platformbootstrap.yaml's own `required` calls, not this guard's job — see
# zitadel-smtp-guard.yaml's own comment. These two prove THOSE checks fire.
must_fail "gibson-operators smtp.host set but .fromAddress empty" \
  "platformBootstrap.zitadel.smtp.fromAddress is required" \
  --set global.platformOwner.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromAddress= \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromName=Gibson

must_fail "gibson-operators smtp.host set but .fromName empty" \
  "platformBootstrap.zitadel.smtp.fromName is required" \
  --set global.platformOwner.offlineSetup=false \
  --set gibson-workloads.gibson.email.provider=smtp \
  --set gibson-workloads.gibson.email.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.host=smtp.example.com \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromAddress=no-reply@localhost.zeroroot.ai \
  --set gibson-operators.platformBootstrap.zitadel.smtp.fromName=

# The daemon's mail off (provider=log, the baseline) never requires Zitadel's
# SMTP block at all — the baseline itself is the fixture.
must_pass "gibson-workloads mail off: gibson-operators smtp may stay unset"

[ "$fail" -eq 0 ] && echo "✓ platform-owner-values: required, must-differ and mail/offline rules all fire for both the Platform owner and the first tenant's Owner, and the baseline satisfies every one of them"
exit "$fail"
