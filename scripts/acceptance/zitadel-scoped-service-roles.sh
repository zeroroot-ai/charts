#!/usr/bin/env bash
# zitadel-scoped-service-roles.sh — prove the daemon and the tenant-operator
# hold only the Zitadel rights their calls need (hosted#199, hosted#200).
#
# This does NOT re-run the full functional flows (invitations, first-tenant
# setup, agent enrollment, session revocation, profile lookup, a tenant
# provisioning to Ready and deleting cleanly). Those already have dedicated
# exit tests (exit-test-signup.yml and the gibson#13/#14 bank/tool exit tests
# — see the project notes this PR's hand-off links). Re-running those exit
# tests after this change lands, and seeing them stay green, IS the
# acceptance evidence that narrowing the role did not break any of them.
#
# What THIS script checks, against a live k3d cluster with the umbrella
# chart installed and Zitadel reachable, is the thing no functional exit test
# exercises: that the daemon's and the tenant-operator's Zitadel machine
# users (a) declare no IAM_OWNER, (b) can still do what they are meant to
# do, and (c) are refused an instance-wide action (something only IAM_OWNER
# could do) the way a real Zitadel refuses it.
#
# Usage:
#   NS=gibson RELEASE=gibson ./scripts/acceptance/zitadel-scoped-service-roles.sh
#
# Env:
#   NS       namespace the umbrella chart is installed into (default: gibson)
#   RELEASE  helm release name (default: gibson)
#
# Requires: kubectl (pointed at the k3d cluster), curl, jq.
# Read-only against Kubernetes; makes live Zitadel API calls as the daemon's
# and the tenant-operator's own service identities (no cluster mutation).
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"

log()  { printf '\033[1;32m▶\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
pass() { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }

failures=0
check() {
  local desc="$1" got="$2" want="$3"
  if [ "$got" = "$want" ]; then
    pass "$desc"
  else
    fail "$desc — expected $want, got $got"
    failures=$((failures + 1))
  fi
}

# --- 1. No component's OIDCClient declares IAM_OWNER ----------------------
log "checking OIDCClient role declarations for IAM_OWNER"
for name in gibson-daemon gibson-tenant-operator; do
  roles="$(kubectl -n "$NS" get oidcclient "$name" -o jsonpath='{.spec.roles}' 2>/dev/null || echo '[missing]')"
  case "$roles" in
    *IAM_OWNER*) fail "OIDCClient/$name still declares IAM_OWNER: $roles"; failures=$((failures + 1)) ;;
    *) pass "OIDCClient/$name does not declare IAM_OWNER (roles: $roles)" ;;
  esac
done

# --- 2. Resolve the live Zitadel connection facts from the running pods ---
# Read these from the deployed workloads themselves rather than recomputing
# chart templating, so the check is correct for whatever profile is
# installed (bare, EKS, guest, ...).
log "resolving ZITADEL_URL / ZITADEL_EXTERNAL_DOMAIN from the running daemon"
ZITADEL_URL="$(kubectl -n "$NS" get statefulset "${RELEASE}" -o jsonpath='{.spec.template.spec.containers[?(@.name=="gibson")].env[?(@.name=="ZITADEL_URL")].value}')"
ZITADEL_EXTERNAL_DOMAIN="$(kubectl -n "$NS" get statefulset "${RELEASE}" -o jsonpath='{.spec.template.spec.containers[?(@.name=="gibson")].env[?(@.name=="ZITADEL_EXTERNAL_DOMAIN")].value}')"
if [ -z "$ZITADEL_URL" ] || [ -z "$ZITADEL_EXTERNAL_DOMAIN" ]; then
  fail "could not resolve ZITADEL_URL/ZITADEL_EXTERNAL_DOMAIN from statefulset/${RELEASE} — adjust the jsonpath for this profile"
  exit 1
fi
log "ZITADEL_URL=$ZITADEL_URL ZITADEL_EXTERNAL_DOMAIN=$ZITADEL_EXTERNAL_DOMAIN"

# token CLIENT_ID CLIENT_SECRET SCOPE — obtains an access token via
# client_credentials, claiming the instance by header (ADR-0092), the same
# way the real Go clients do. Run through kubectl exec against a pod that
# already has network access to the in-cluster Zitadel Service and curl/jq
# available (the daemon pod itself qualifies).
token() {
  local client_id="$1" client_secret="$2" scope="$3"
  kubectl -n "$NS" exec "${RELEASE}-0" -c gibson -- curl -s -X POST \
    -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
    -H 'Content-Type: application/x-www-form-urlencoded' \
    --data-urlencode 'grant_type=client_credentials' \
    --data-urlencode "client_id=${client_id}" \
    --data-urlencode "client_secret=${client_secret}" \
    --data-urlencode "scope=${scope}" \
    "${ZITADEL_URL}/oauth/v2/token" | jq -r '.access_token'
}

# call TOKEN METHOD PATH [BODY] — one authenticated call, prints the HTTP
# status only.
call() {
  local tok="$1" method="$2" path="$3" body="${4:-{\}}"
  kubectl -n "$NS" exec "${RELEASE}-0" -c gibson -- curl -s -o /dev/null -w '%{http_code}' -X "$method" \
    -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
    -H "Authorization: Bearer ${tok}" \
    -H 'Content-Type: application/json' \
    -d "$body" \
    "${ZITADEL_URL}${path}"
}

MGMT_SCOPE='openid urn:zitadel:iam:org:project:id:zitadel:aud'

# --- 3. The daemon's credential: allowed and refused calls -----------------
log "reading the daemon's own Zitadel credentials (idp-admin-credentials)"
DAEMON_CLIENT_ID="$(kubectl -n "$NS" get secret idp-admin-credentials -o jsonpath='{.data.client_id}' | base64 -d)"
DAEMON_CLIENT_SECRET="$(kubectl -n "$NS" get secret idp-admin-credentials -o jsonpath='{.data.client_secret}' | base64 -d)"
DAEMON_ORG_ID="$(kubectl -n "$NS" get secret idp-admin-credentials -o jsonpath='{.data.org_id}' | base64 -d)"
DAEMON_TOKEN="$(token "$DAEMON_CLIENT_ID" "$DAEMON_CLIENT_SECRET" "$MGMT_SCOPE")"
if [ -z "$DAEMON_TOKEN" ] || [ "$DAEMON_TOKEN" = "null" ]; then
  fail "could not obtain a token for the daemon's Zitadel credential"
  failures=$((failures + 1))
else
  # Allowed: user.read, within the org header (Management v1 user search) —
  # covered by IAM_ORG_MANAGER.
  status="$(kubectl -n "$NS" exec "${RELEASE}-0" -c gibson -- curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
    -H "Authorization: Bearer ${DAEMON_TOKEN}" \
    -H "x-zitadel-orgid: ${DAEMON_ORG_ID}" \
    -H 'Content-Type: application/json' \
    -d '{"limit":1}' \
    "${ZITADEL_URL}/management/v1/users/_search")"
  check "daemon: user.read (list users) succeeds under its granted role" "$status" "200"

  # Refused: iam.member.write (POST /admin/v1/members — adding an instance
  # IAM member). IAM_ORG_MANAGER does not carry iam.*; only IAM_OWNER does.
  # Zitadel refuses this as a 403 (or occasionally 401 if the token audience
  # rejects the admin surface outright — either way, not 200/201).
  status="$(call "$DAEMON_TOKEN" POST "/admin/v1/members" \
    '{"userId":"000000000000000001","roles":["IAM_OWNER"]}')"
  if [ "$status" = "200" ] || [ "$status" = "201" ]; then
    fail "daemon: adding an instance IAM member SUCCEEDED (status $status) — the daemon still holds an iam.* permission"
    failures=$((failures + 1))
  else
    pass "daemon: adding an instance IAM member is refused (status $status)"
  fi
fi

# --- 4. The tenant-operator's credential: allowed and refused calls -------
log "reading the tenant-operator's own Zitadel credentials"
OPERATOR_CLIENT_ID="$(kubectl -n "$NS" get secret gibson-zitadel-tenant-operator -o jsonpath='{.data.client_id}' | base64 -d)"
OPERATOR_CLIENT_SECRET="$(kubectl -n "$NS" get secret gibson-zitadel-tenant-operator -o jsonpath='{.data.client_secret}' | base64 -d)"
OPERATOR_PROJECT_ID="$(kubectl -n "$NS" get secret gibson-zitadel-tenant-operator -o jsonpath='{.data.project_id}' | base64 -d)"
OPERATOR_TOKEN="$(token "$OPERATOR_CLIENT_ID" "$OPERATOR_CLIENT_SECRET" "$MGMT_SCOPE")"
if [ -z "$OPERATOR_TOKEN" ] || [ "$OPERATOR_TOKEN" = "null" ]; then
  fail "could not obtain a token for the tenant-operator's Zitadel credential"
  failures=$((failures + 1))
else
  # Allowed: project.grant.read against the fixed gibson project — the exact
  # call EnsureProjectGrant makes.
  status="$(kubectl -n "$NS" exec "${RELEASE}-0" -c gibson -- curl -s -o /dev/null -w '%{http_code}' -X POST \
    -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
    -H "Authorization: Bearer ${OPERATOR_TOKEN}" \
    -H 'Content-Type: application/json' \
    -d "{\"filters\":[{\"inProjectIdsFilter\":{\"ids\":[\"${OPERATOR_PROJECT_ID}\"]}}]}" \
    "${ZITADEL_URL}/zitadel.project.v2.ProjectService/ListProjectGrants")"
  check "tenant-operator: project.grant.read (ListProjectGrants) succeeds" "$status" "200"

  # Refused: same iam.member.write probe as the daemon.
  status="$(call "$OPERATOR_TOKEN" POST "/admin/v1/members" \
    '{"userId":"000000000000000001","roles":["IAM_OWNER"]}')"
  if [ "$status" = "200" ] || [ "$status" = "201" ]; then
    fail "tenant-operator: adding an instance IAM member SUCCEEDED (status $status) — it still holds an iam.* permission"
    failures=$((failures + 1))
  else
    pass "tenant-operator: adding an instance IAM member is refused (status $status)"
  fi
fi

# --- 5. Open question from hosted#194's hand-off ---------------------------
# Not asserted here (needs a disposable tenant, out of this script's
# read-mostly scope) — see the coordinator note: after a tenant deletes,
# confirm ListProjectGrants no longer returns a grant for that tenant's org.
# If it still does, operators/tenant/internal/identity/provisioner.go's
# RemoveOrg needs an explicit DeleteProjectGrant call before this is closed.
log "NOTE: not checked here — confirm a deleted tenant's project grant is gone (see hand-off)"

if [ "$failures" -gt 0 ]; then
  fail "$failures check(s) failed"
  exit 1
fi
pass "all checks passed"
