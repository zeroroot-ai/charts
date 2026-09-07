#!/usr/bin/env bash
# verify-selfhosted-seams.sh — live self-hosted seam verification (deploy#1039).
#
# Covers deploy#1039's acceptance criteria on a running self-hosted cluster:
#   - GET / resolves to the login page (no marketing surface)
#   - the SignupService self-serve path agrees with the deployed signup seam
#   - the operator-seeded first tenant converges and its admin credential exists
#   - docs are served at docs.<domain>, version-matched to the install
#   - www/marketing is not served by this cluster at all (404, no vhost)
#
# A NOTE ON THE SIGNUP CRITERION, because it is not as written
# ------------------------------------------------------------
# deploy#1039 says the self-serve path must return admin-only PermissionDenied.
# That is true only when the front door is closed. ADR-0006 §4 (as amended
# 2026-08-13, deploy#1039) matches the shipped default: self-serve signup is
# ON in both profiles (signupSelfServe: true — GitLab self-managed model),
# and closed registration is the operator override signupSelfServe: false.
# See helm/gibson/values-vanilla.yaml (+ values-eks.yaml on
# EKS) and the assertion at
# helm/gibson/tests/signup-seam.bats ("open card-free signup is the shipped
# OSS default").
#
# The suite therefore reads the deployed SIGNUP_SELF_SERVE value and asserts
# the RPC BEHAVIOUR MATCHES IT in both directions:
#   SIGNUP_SELF_SERVE unset/false -> Signup MUST return PermissionDenied
#   SIGNUP_SELF_SERVE true        -> Signup MUST NOT return PermissionDenied
# Either way the seam is proven coherent — both postures are owner-endorsed,
# so neither direction is hard-coded as "the" correct one.
#
# Usage:
#   scripts/verify-selfhosted-seams.sh
#   make verify-selfhosted-seams
#
# Env:
#   NAMESPACE      k8s namespace                     (default: gibson)
#   DOMAIN         platform domain                   (default: from the gibson-domains ConfigMap)
#   EDGE_ADDR      override derived edge <ip>:<port> (default: from the Envoy Service)
#   FIRST_TENANT       operator-seeded first tenant slug   (default: the one seeded Tenant CR)
#   FIRST_ADMIN_SECRET first-admin credential Secret name  (default: gibson-first-admin)
# Exit: 0 all assertions passed · 1 an assertion failed · 2 preflight failed
#
# Preflight failure is exit 2 and is NOT a pass.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/cluster-probe.sh
source "${SCRIPT_DIR}/lib/cluster-probe.sh"

NAMESPACE="${NAMESPACE:-gibson}"

# The install states its own domain in the gibson-domains ConfigMap, so read
# it from the cluster instead of guessing. The previous default (zeroroot.ai)
# probed hosts the install's Envoy does not serve: the SNI filter chains reset
# the handshake, every probe answered 000, and the suite reported ten product
# failures that were all the same wrong host (deploy#1766, run 33609089563).
# The env var stays as an override for probing an install whose ConfigMap is
# itself under suspicion.
if [ -z "${DOMAIN:-}" ]; then
  DOMAIN="$(kubectl -n "$NAMESPACE" get configmap gibson-domains     -o jsonpath='{.data.domain}' 2>/dev/null || true)"
fi
[ -n "${DOMAIN:-}" ] || DOMAIN="zeroroot.ai"

# The first tenant is seeded by the tenant-operator (global.firstTenant), not
# by this script — gibson#1496. Read the seeded Tenant CR from the cluster: a
# fresh install has exactly one, and a hardcoded default cannot work ("default"
# is on the reserved-names denylist, so no install can ever seed it). The env
# var stays as an override for a cluster with more than one tenant.
if [ -z "${FIRST_TENANT:-}" ]; then
  FIRST_TENANT="$(kubectl get tenants.gibson.zeroroot.ai     -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
fi
[ -n "${FIRST_TENANT:-}" ] || FIRST_TENANT="default"
FIRST_ADMIN_SECRET="${FIRST_ADMIN_SECRET:-gibson-first-admin}"

WWW_HOST="www.${DOMAIN}"
DOCS_HOST="docs.${DOMAIN}"
APP_HOST="app.${DOMAIN}"

# ---------------------------------------------------------------------------
step "Preflight"
# ---------------------------------------------------------------------------
probe_cluster "$NAMESPACE"

# Refuse to run against a SaaS cluster: these assertions would be checking the
# opposite of that cluster's correct behaviour.
require_profile "$NAMESPACE" "self-hosted"

require_workload "$NAMESPACE" deploy gibson-dashboard "the login front door"

EDGE="$(resolve_edge "$NAMESPACE")" \
  || preflight_die "no usable Envoy edge in ns/${NAMESPACE} — cannot determine the edge address to probe"
pass "edge resolved to ${EDGE}"

# ---------------------------------------------------------------------------
step "Front door — GET / is login, not marketing"
# ---------------------------------------------------------------------------
# ADR-0006 §4: "Self-hosted GET / is the login page (no marketing)."
# dashboard/src/lib/host-routing.ts:19-20: with WWW_URL unset there is no host
# split, so GET / renders the root page, which redirects to /login.
ROOT_CODE="$(edge_status "$EDGE" "$APP_HOST" "/")"
case "$ROOT_CODE" in
  200|302|307)
    pass "GET https://${APP_HOST}/ -> ${ROOT_CODE}"
    ;;
  *)
    fail "GET https://${APP_HOST}/ -> ${ROOT_CODE} (expected 200 or a redirect to /login)"
    ;;
esac

ROOT_BODY="$(edge_body "$EDGE" "$APP_HOST" "/")"
if [ -z "$ROOT_BODY" ] && [ "$ROOT_CODE" = "200" ]; then
  fail "GET / returned 200 with an empty body — cannot establish what it served"
elif printf '%s' "$ROOT_BODY" | grep -qiE 'sign in|log in|login'; then
  pass "GET / serves the login surface"
else
  fail "GET / body carries no login affordance — self-hosted must not serve marketing here"
fi

# The marketing pages the dashboard shed (dashboard#911 / ADR-0006) must not be
# reachable on the self-hosted app host.
for path in /pricing /contact-sales /features; do
  code="$(edge_status "$EDGE" "$APP_HOST" "$path")"
  if [ "$code" = "404" ] || [ "$code" = "302" ] || [ "$code" = "307" ]; then
    pass "GET ${path} -> ${code} (marketing page not served on self-hosted)"
  elif [ "$code" = "200" ]; then
    fail "GET https://${APP_HOST}${path} -> 200 — a marketing page is being served on a self-hosted install (ADR-0006: marketing is SaaS-only)"
  else
    fail "GET https://${APP_HOST}${path} -> ${code} (unexpected; expected 404 or a redirect)"
  fi
done

# ---------------------------------------------------------------------------
step "www is not served by this cluster"
# ---------------------------------------------------------------------------
# The marketing site is an off-cluster surface (deploy ADR-0009): no chart
# deploys it in either audience, and the edge has no filter chain for the www
# host at all. The listener matches by SNI (files/envoy/envoy.yaml: api., then
# app./apex/docs. as one public chain) and there is no default chain, so a
# handshake for www.<domain> matches nothing and Envoy closes the connection.
# curl reports that as 000.
#
# The assertion is therefore the RESET, and it is meaningful only when the app
# host on the same edge answered a moment ago (ROOT_CODE above): then 000 on
# www is the SNI refusal, not an unreachable edge. Any HTTP status on www
# means a chain has started claiming the host — 404 from a catch-all, 503 from
# a vhost with no endpoints, 200 from a marketing surface — and every one of
# them is the regression this guards: a cluster that terminates TLS for
# www.<domain> takes the hostname from the CDN (owner call 2026-09-07).
WWW_CODE="$(edge_status "$EDGE" "$WWW_HOST" "/")"
case "$ROOT_CODE:$WWW_CODE" in
  000:*)
    fail "GET https://${WWW_HOST}/ cannot be judged: the app host answered 000 too, so the edge itself is unreachable"
    ;;
  *:000)
    pass "GET https://${WWW_HOST}/ -> connection closed (no www filter chain on the edge; the marketing site is off-cluster)"
    ;;
  *:200)
    fail "GET https://${WWW_HOST}/ -> 200 — this cluster is serving a marketing surface it must not own"
    ;;
  *:503)
    fail "GET https://${WWW_HOST}/ -> 503 — a www vhost has returned to the edge. Remove it: this cluster must not claim www.<domain> (ADR-0009)."
    ;;
  *)
    fail "GET https://${WWW_HOST}/ -> ${WWW_CODE} — the edge terminates TLS for www.<domain>; it must have no chain for that host (ADR-0009)"
    ;;
esac

# ---------------------------------------------------------------------------
step "Signup seam coherence"
# ---------------------------------------------------------------------------
# The deployed value, read with jsonpath. The first version grepped the JSON
# for "name":"SIGNUP_SELF_SERVE","value":"…" and kubectl pretty-prints with
# spaces, so it never matched, read "absent", and asserted the wrong branch
# on every install (measured 2026-09-07 on a cluster that carried
# SIGNUP_SELF_SERVE=true on the dashboard).
SELF_SERVE="$(kubectl -n "$NAMESPACE" get deploy gibson-dashboard \
  -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="SIGNUP_SELF_SERVE")].value}' 2>/dev/null | awk '{print $1}')"

if [ -z "$SELF_SERVE" ]; then
  SELF_SERVE="false"
  info "SIGNUP_SELF_SERVE is absent from the dashboard -> the seam's fail-safe (closed registration) is active"
else
  info "SIGNUP_SELF_SERVE=${SELF_SERVE} on the dashboard"
fi

# The seam is asserted through the product surface, not the RPC. The daemon's
# gRPC reflection is off in production images and the edge's JWT filter has
# no exemption for it, so a grpcurl probe answers "Unauthenticated: Jwt is
# missing" before it can ask anything — that is the edge working, not the
# seam failing (owner call 2026-09-07). What a person sees is the contract:
# with self-serve on, GET /signup renders the signup page; with it off, the
# dashboard redirects /signup to /login (app/(public)/signup/page.tsx, deploy
# ADR-0006 §4 as amended). The redirect target is part of the assertion: a
# redirect elsewhere is a different surface, not the front door.
SIGNUP_CODE="$(edge_status "$EDGE" "$APP_HOST" "/signup")"
SIGNUP_REDIRECT="$(edge_redirect "$EDGE" "$APP_HOST" "/signup")"
if [ "$SELF_SERVE" = "true" ]; then
  case "$SIGNUP_CODE" in
    200)
      pass "GET https://${APP_HOST}/signup -> 200 with SIGNUP_SELF_SERVE=true (seam coherent; the shipped open default per ADR-0006 §4 as amended)"
      ;;
    30[1278])
      fail "GET https://${APP_HOST}/signup -> ${SIGNUP_CODE} to ${SIGNUP_REDIRECT:-?} while SIGNUP_SELF_SERVE=true — the seam and the deployed config disagree"
      ;;
    *)
      fail "GET https://${APP_HOST}/signup -> ${SIGNUP_CODE} with SIGNUP_SELF_SERVE=true (expected 200)"
      ;;
  esac
else
  case "$SIGNUP_CODE" in
    30[1278])
      case "$SIGNUP_REDIRECT" in
        *"/login"*) pass "GET https://${APP_HOST}/signup -> ${SIGNUP_CODE} to /login with self-serve off (closed registration enforced at the front door)" ;;
        *) fail "GET https://${APP_HOST}/signup -> ${SIGNUP_CODE} to ${SIGNUP_REDIRECT:-?} with self-serve off — expected the login front door" ;;
      esac
      ;;
    200)
      fail "GET https://${APP_HOST}/signup -> 200 with self-serve off — the closed-registration fail-safe is not enforced"
      ;;
    *)
      fail "GET https://${APP_HOST}/signup -> ${SIGNUP_CODE} with self-serve off (expected a redirect to /login)"
      ;;
  esac
fi

# ---------------------------------------------------------------------------
step "First-admin bring-up — operator-seeded tenant + generated credential"
# ---------------------------------------------------------------------------
# gibson#1496: the first tenant is seeded WITHOUT a session. The tenant-operator
# enqueues it over its SPIFFE identity (EnqueueTenantProvisioning), because the
# interactive AdminProvisionTenant RPC is gated by a session-revocation check no
# headless caller satisfies — the very failure this design removes. So the seam
# is verified by its OBSERVABLE END STATE on the running install, not by poking
# the removed path: the seeded tenant CR converges, and the first-admin Job has
# written the generated admin credential Secret. Both are idempotent, so
# re-running this suite is safe.
if ! kubectl -n "$NAMESPACE" get tenant "$FIRST_TENANT" >/dev/null 2>&1; then
  fail "first tenant ${FIRST_TENANT} does not exist — the operator seed (global.firstTenant → EnqueueTenantProvisioning) never produced a Tenant CR"
else
  # The seeded tenant must converge, not merely exist.
  DEADLINE=$(( $(date +%s) + 180 ))
  PHASE=""
  while [ "$(date +%s)" -lt "$DEADLINE" ]; do
    PHASE="$(kubectl -n "$NAMESPACE" get tenant "$FIRST_TENANT" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    case "$PHASE" in Ready|Active) break ;; esac
    sleep 5
  done
  case "$PHASE" in
    Ready|Active)
      pass "operator-seeded tenant ${FIRST_TENANT} reached phase=${PHASE}"
      ;;
    *)
      fail "operator-seeded tenant ${FIRST_TENANT} did not reach Ready within 180s (phase=${PHASE:-<absent>})"
      kubectl -n "$NAMESPACE" get tenant "$FIRST_TENANT" \
        -o go-template='{{range .status.conditions}}  {{.type}}: {{.status}} ({{.reason}}) {{.message}}{{"\n"}}{{end}}' 2>/dev/null || true
      ;;
  esac

  # The seeded tenant must report a Zitadel org — it is what the first-admin Job
  # waits on before it can create the owner user.
  ORG="$(kubectl -n "$NAMESPACE" get tenant "$FIRST_TENANT" -o jsonpath='{.status.zitadelOrgID}' 2>/dev/null || true)"
  if [ -n "$ORG" ]; then
    pass "tenant ${FIRST_TENANT} reports a Zitadel org (${ORG})"
  else
    fail "tenant ${FIRST_TENANT} has no status.zitadelOrgID — the first-admin Job cannot create the owner"
  fi

  # The first-admin Job's proof of completion: the generated credential Secret.
  # Create-only, so its mere existence means the owner was provisioned.
  if kubectl -n "$NAMESPACE" get secret "$FIRST_ADMIN_SECRET" >/dev/null 2>&1; then
    pass "first-admin credential Secret ${FIRST_ADMIN_SECRET} exists (owner provisioned; operator is told to read then delete it)"
  else
    fail "first-admin credential Secret ${FIRST_ADMIN_SECRET} is absent — the first-admin Job did not complete, so nobody can log in"
  fi
fi

# ---------------------------------------------------------------------------
step "Docs surface — served and version-matched"
# ---------------------------------------------------------------------------
DOCS_CODE="$(edge_status "$EDGE" "$DOCS_HOST" "/")"
if [ "$DOCS_CODE" = "200" ]; then
  pass "GET https://${DOCS_HOST}/ -> 200"
else
  fail "GET https://${DOCS_HOST}/ -> ${DOCS_CODE} (docs is core optional-by-toggle and default-on)"
fi

# Version-match: the dashboard's DOCS_URL must point at THIS install's docs host,
# not at the public fallback, when local docs are enabled.
DOCS_URL="$(kubectl -n "$NAMESPACE" get deploy gibson-dashboard \
  -o jsonpath='{.spec.template.spec.containers[*].env[?(@.name=="DOCS_URL")].value}' 2>/dev/null || true)"
if [ -z "$DOCS_URL" ]; then
  fail "dashboard DOCS_URL is unset — the docs seam is not wired"
elif printf '%s' "$DOCS_URL" | grep -q "${DOCS_HOST}"; then
  pass "dashboard DOCS_URL points at the in-cluster docs host (${DOCS_URL})"
else
  fail "dashboard DOCS_URL=${DOCS_URL} does not reference ${DOCS_HOST} — local docs are enabled but the dashboard links elsewhere"
fi

# The running docs image must be the one this chart release pinned. A drifted
# docs-svc is the concrete failure mode behind "version-matched to the install".
DOCS_IMAGE="$(kubectl -n "$NAMESPACE" get deploy -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.spec.template.spec.containers[0].image}{"\n"}{end}' 2>/dev/null \
  | grep -m1 'docs' | cut -d= -f2- || true)"
if [ -z "$DOCS_IMAGE" ]; then
  fail "no docs-svc Deployment found — docs.enabled is true at the edge but no workload serves it"
else
  pass "docs-svc runs ${DOCS_IMAGE}"
  info "version-match caveat: helm/gibson-workloads/values.yaml pins docs.image.digest, which overrides the .Chart.AppVersion default — so the shipped image tracks a docs-site build, not the install version. The toggle-off fallback path is covered offline by helm/gibson-workloads/tests/docs-seam.bats."
fi

summary "verify-selfhosted-seams"
