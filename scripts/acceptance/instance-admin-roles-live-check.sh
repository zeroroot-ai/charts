#!/usr/bin/env bash
# instance-admin-roles-live-check.sh — least privilege holds on a live
# cluster (hosted#207). Reads the actual Zitadel instance members and the
# actual OpenFGA holders of platform_owner/platform_operator on
# system_tenant:_system, and fails on anything unexpected.
#
# This is the live counterpart to scripts/check-instance-admin-roles-scoped.py
# (a static render guard) and scripts/acceptance/zitadel-scoped-service-roles.sh
# (the daemon/tenant-operator call-level check from hosted#199/#200). Those
# two do not see what actually got minted on a running install; this script
# does.
#
# What it checks:
#   1. Zitadel instance (IAM) members (`POST /admin/v1/members/_search`):
#      - at most one member holds IAM_OWNER, and it must be a HUMAN user
#        (the Platform owner, ADR-0093 decision 5) — never a machine user.
#      - no member holds IAM_ADMIN_IMPERSONATOR, IAM_END_USER_IMPERSONATOR,
#        or IAM_OWNER_VIEWER.
#      - every other member's roles are a subset of {IAM_ORG_MANAGER}.
#   2. OpenFGA holders of `platform_operator` on `system_tenant:_system`:
#      exactly the numeric subs of gibson-iam-admin and gibson-tenant-operator
#      (resolved from the gibson-sa-identity-map ConfigMap), nothing else.
#   3. OpenFGA holders of `platform_owner` on `system_tenant:_system`: exactly
#      one user, and that user must be the same one holding IAM_OWNER in (1).
#
# This script authenticates with the escrowed Zitadel owner PAT
# (Secret/iam-admin-pat, key `pat`) — the one credential ADR-0093/hosted#192
# reserve for bootstrap and inspection, never for a running service. Reading
# it here is a one-time, read-only audit call, run by an operator or the
# release coordinator; it is not something any workload does on a schedule.
#
# Usage:
#   NS=gibson RELEASE=gibson ./scripts/acceptance/instance-admin-roles-live-check.sh
#
# Env:
#   NS       namespace the umbrella chart is installed into (default: gibson)
#   RELEASE  helm release name (default: gibson)
#
# Requires: kubectl (pointed at the cluster), curl, jq, on a pod that can
# reach Zitadel and OpenFGA in-cluster (the daemon pod qualifies). Read-only:
# every call here is a search/read, never a write.
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"

log()  { printf '\033[1;32m▶\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }
pass() { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }

failures=0

log "resolving ZITADEL_URL / ZITADEL_EXTERNAL_DOMAIN from the running daemon"
ZITADEL_URL="$(kubectl -n "$NS" get statefulset "${RELEASE}" -o jsonpath='{.spec.template.spec.containers[?(@.name=="gibson")].env[?(@.name=="ZITADEL_URL")].value}')"
ZITADEL_EXTERNAL_DOMAIN="$(kubectl -n "$NS" get statefulset "${RELEASE}" -o jsonpath='{.spec.template.spec.containers[?(@.name=="gibson")].env[?(@.name=="ZITADEL_EXTERNAL_DOMAIN")].value}')"
if [ -z "$ZITADEL_URL" ] || [ -z "$ZITADEL_EXTERNAL_DOMAIN" ]; then
  fail "could not resolve ZITADEL_URL/ZITADEL_EXTERNAL_DOMAIN from statefulset/${RELEASE}"
  exit 1
fi

log "reading the escrowed Zitadel owner PAT (Secret/iam-admin-pat)"
IAM_ADMIN_PAT="$(kubectl -n "$NS" get secret iam-admin-pat -o jsonpath='{.data.pat}' | base64 -d)"
if [ -z "$IAM_ADMIN_PAT" ]; then
  fail "Secret/iam-admin-pat has no 'pat' key — cannot list Zitadel instance members"
  exit 1
fi

exec_curl() {
  kubectl -n "$NS" exec "${RELEASE}-0" -c gibson -- curl -sS "$@"
}

# --- 1. Zitadel instance (IAM) members --------------------------------------
log "listing Zitadel instance (IAM) members"
MEMBERS_JSON="$(exec_curl -X POST \
  -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
  -H "Authorization: Bearer ${IAM_ADMIN_PAT}" \
  -H 'Content-Type: application/json' \
  -d '{}' \
  "${ZITADEL_URL}/admin/v1/members/_search")"

if ! echo "$MEMBERS_JSON" | jq -e '.result' >/dev/null 2>&1; then
  fail "ListIAMMembers did not return a result array — response: $(echo "$MEMBERS_JSON" | head -c 500)"
  failures=$((failures + 1))
else
  bad_roles=$(echo "$MEMBERS_JSON" | jq -r '
    [.result[] | select(.roles[]? as $r | ["IAM_ADMIN_IMPERSONATOR","IAM_END_USER_IMPERSONATOR","IAM_OWNER_VIEWER"] | index($r))]
    | length')
  narrow_ok=$(echo "$MEMBERS_JSON" | jq '
    [.result[] | select((.roles - ["IAM_OWNER"]) | length > 0)
               | select((.roles - ["IAM_ORG_MANAGER"]) | length > 0)] | length')

  if [ "$bad_roles" -gt 0 ]; then
    fail "an IAM member holds IAM_ADMIN_IMPERSONATOR, IAM_END_USER_IMPERSONATOR, or IAM_OWNER_VIEWER"
    echo "$MEMBERS_JSON" | jq '.result'
    failures=$((failures + 1))
  else
    pass "no IAM member holds IAM_ADMIN_IMPERSONATOR, IAM_END_USER_IMPERSONATOR, or IAM_OWNER_VIEWER"
  fi

  OWNER_IDS="$(echo "$MEMBERS_JSON" | jq -r '[.result[] | select(.roles[]? == "IAM_OWNER") | .userId] | .[]')"
  OWNER_USER_ID=""
  KNOWN_DEFAULT_ADMIN_FOUND=""
  UNEXPECTED_OWNER_FOUND=""

  while IFS= read -r uid; do
    [ -z "$uid" ] && continue
    userJSON="$(exec_curl -X GET \
      -H "x-zitadel-instance-host: ${ZITADEL_EXTERNAL_DOMAIN}" \
      -H "Authorization: Bearer ${IAM_ADMIN_PAT}" \
      "${ZITADEL_URL}/management/v1/users/${uid}")"
    username="$(echo "$userJSON" | jq -r '.user.userName // empty')"
    is_machine="$(echo "$userJSON" | jq -r '.user.machine // empty')"
    displayName="$(echo "$userJSON" | jq -r '.user.human.profile.displayName // empty')"

    # Zitadel's own DefaultInstance.Org.Human seed: username "zitadel-admin"
    # (optionally suffixed "@<org-domain>"), first/last name ZITADEL/Admin.
    # A parallel fix (hosted#207 follow-up) makes the platform-operator
    # remove or deactivate this account on install. Until that ships, this
    # check is EXPECTED to fail on kind and on any cluster it has not yet
    # reached — name it explicitly rather than reporting a bare count, so
    # the failure is actionable instead of a mystery extra owner.
    case "$username" in
      zitadel-admin|zitadel-admin@*)
        KNOWN_DEFAULT_ADMIN_FOUND="$uid ($username)"
        fail "IAM_OWNER is held by Zitadel's own default admin account, $username ($uid) — expected: removed or deactivated by the platform-operator's first-instance cleanup. If this fires, that cleanup has not run yet or has not reached this cluster."
        failures=$((failures + 1))
        continue
        ;;
    esac

    if [ -n "$is_machine" ]; then
      fail "IAM_OWNER is held by a MACHINE user ($uid, username=$username) — expected only the Platform owner (a human)"
      failures=$((failures + 1))
      UNEXPECTED_OWNER_FOUND="$uid"
      continue
    fi

    if [ -n "$OWNER_USER_ID" ]; then
      fail "more than one non-default human holds IAM_OWNER: $OWNER_USER_ID and $uid ($username) — expected exactly one, the Platform owner"
      failures=$((failures + 1))
      UNEXPECTED_OWNER_FOUND="$uid"
      continue
    fi

    OWNER_USER_ID="$uid"
    pass "the Platform owner ($uid, username=$username, displayName=$displayName) is a human user and holds IAM_OWNER"
  done <<EOF
$OWNER_IDS
EOF

  if [ -z "$OWNER_USER_ID" ] && [ -z "$KNOWN_DEFAULT_ADMIN_FOUND" ] && [ -z "$UNEXPECTED_OWNER_FOUND" ]; then
    fail "no IAM member holds IAM_OWNER — expected exactly one, the Platform owner"
    failures=$((failures + 1))
  fi

  if [ "$narrow_ok" -gt 0 ]; then
    fail "an IAM member holds a role outside {IAM_OWNER, IAM_ORG_MANAGER}"
    echo "$MEMBERS_JSON" | jq '.result'
    failures=$((failures + 1))
  else
    pass "every other IAM member's roles are a subset of {IAM_ORG_MANAGER}"
  fi
fi

# --- 2 & 3. OpenFGA holders of platform_operator / platform_owner ----------
log "reading OpenFGA store_id from gibson-fga-config"
STORE_ID="$(kubectl -n "$NS" get configmap gibson-fga-config -o jsonpath='{.data.store_id}' 2>/dev/null || echo '')"
FGA_SVC="gibson-openfga"
FGA_PORT="8080"
if [ -z "$STORE_ID" ]; then
  fail "gibson-fga-config has no store_id — fga-init has not run"
  failures=$((failures + 1))
else
  fga_read() {
    local relation="$1"
    exec_curl -k -X POST \
      -H 'Content-Type: application/json' \
      -d "{\"tuple_key\":{\"relation\":\"${relation}\",\"object\":\"system_tenant:_system\"}}" \
      "https://${FGA_SVC}:${FGA_PORT}/stores/${STORE_ID}/read"
  }

  log "resolving expected platform_operator numeric subs from gibson-sa-identity-map"
  MAP_JSON="$(kubectl -n "$NS" get configmap gibson-sa-identity-map -o json)"
  EXPECTED_OPERATOR_SUBS="$(echo "$MAP_JSON" | jq -r '
    [.data["gibson-iam-admin"], .data["gibson-tenant-operator"]]
    | map(select(. != null and . != "<unset>"))
    | sort | join(",")')"

  OPERATOR_JSON="$(fga_read platform_operator)"
  GOT_OPERATOR_SUBS="$(echo "$OPERATOR_JSON" | jq -r '
    [.tuples[]?.key.user | ltrimstr("user:")] | sort | join(",")')"

  if [ "$GOT_OPERATOR_SUBS" = "$EXPECTED_OPERATOR_SUBS" ]; then
    pass "platform_operator on system_tenant:_system is held by exactly {gibson-iam-admin, gibson-tenant-operator} ($GOT_OPERATOR_SUBS)"
  else
    fail "platform_operator holders are [$GOT_OPERATOR_SUBS], expected [$EXPECTED_OPERATOR_SUBS]"
    failures=$((failures + 1))
  fi

  OWNER_JSON="$(fga_read platform_owner)"
  OWNER_COUNT="$(echo "$OWNER_JSON" | jq '[.tuples[]?] | length')"
  if [ "$OWNER_COUNT" != "1" ]; then
    fail "platform_owner on system_tenant:_system has $OWNER_COUNT holder(s), expected exactly 1"
    failures=$((failures + 1))
  else
    FGA_OWNER_SUB="$(echo "$OWNER_JSON" | jq -r '.tuples[0].key.user | ltrimstr("user:")')"
    if [ -n "${OWNER_USER_ID:-}" ] && [ "$FGA_OWNER_SUB" = "$OWNER_USER_ID" ]; then
      pass "the sole platform_owner holder ($FGA_OWNER_SUB) is the same user as the sole IAM_OWNER member"
    else
      fail "platform_owner holder ($FGA_OWNER_SUB) does not match the IAM_OWNER member (${OWNER_USER_ID:-none found})"
      failures=$((failures + 1))
    fi
  fi
fi

if [ "$failures" -gt 0 ]; then
  fail "$failures check(s) failed"
  exit 1
fi
pass "all checks passed"
