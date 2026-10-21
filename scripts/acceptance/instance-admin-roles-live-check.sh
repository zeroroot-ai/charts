#!/usr/bin/env bash
# instance-admin-roles-live-check.sh: the live half of hosted#207.
#
# Reads the actual Zitadel instance administrators and the actual FGA holders
# of platform_owner and platform_operator, and fails on anything ADR-0093 does
# not allow (decisions 5 and 7):
#   - the Platform owner (a human, the email in PlatformBootstrap
#     spec.platformOwner.email) holds IAM_OWNER, and no other human is a member
#   - the bootstrap identity (machine user "iam-admin") holds IAM_OWNER
#   - the login client (machine user "login-client") holds IAM_LOGIN_CLIENT
#   - gibson-daemon and gibson-tenant-operator hold exactly IAM_ORG_MANAGER
#   - no other member exists
#   - platform_owner on system_tenant:_system is held by exactly the Platform owner
#   - platform_operator is held by exactly gibson-iam-admin and gibson-tenant-operator
#
# Read-only. It reaches Zitadel and OpenFGA through kubectl port-forward, so it
# needs no tool inside any image. It reads the escrowed owner PAT (Secret
# iam-admin-pat), the one credential reserved for bootstrap and inspection.
#
# Usage (kubectl pointed at the cluster, umbrella chart installed):
#   NS=gibson RELEASE=gibson ./scripts/acceptance/instance-admin-roles-live-check.sh
# Secret values pass through this script in shell variables. An xtrace (bash
# -x, set -x, or xtrace in an exported SHELLOPTS) prints each expanded
# command to stderr, value included, so the script turns it off first
# (scripts/check-xtrace-secrets.py).
set +x
set -euo pipefail
# shellcheck source=../lib/curl-config.sh
. "$(dirname "$0")/../lib/curl-config.sh"

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
ZPORT="${ZPORT:-19391}"
FPORT="${FPORT:-19392}"
failures=0
pass() { printf '\033[1;32m✓\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗\033[0m %s\n' "$*"; failures=$((failures + 1)); }
log()  { printf '\033[1;32m▶\033[0m %s\n' "$*"; }

pids=()
cleanup() { for p in "${pids[@]:-}"; do if [ -n "$p" ]; then kill "$p" 2>/dev/null || true; fi; done; }
trap cleanup EXIT
forward() { # svc local remote
  kubectl -n "$NS" port-forward "svc/$1" "$2:$3" >/dev/null 2>&1 &
  pids+=("$!")
  for _ in $(seq 1 30); do (echo >"/dev/tcp/127.0.0.1/$2") 2>/dev/null && return 0; sleep 1; done
  echo "port-forward to svc/$1 did not open" >&2; exit 2
}

EXT_DOMAIN="$(kubectl -n "$NS" get platformbootstrap -o jsonpath='{.items[0].spec.zitadel.externalDomain}')"
OWNER_EMAIL="$(kubectl -n "$NS" get platformbootstrap -o jsonpath='{.items[0].spec.platformOwner.email}')"
PAT="$(kubectl -n "$NS" get secret iam-admin-pat -o jsonpath='{.data.pat}' | base64 -d)"
[ -n "$EXT_DOMAIN" ] || { echo "PlatformBootstrap has no spec.zitadel.externalDomain" >&2; exit 2; }
[ -n "$PAT" ] || { echo "Secret iam-admin-pat has no pat" >&2; exit 2; }

forward "${RELEASE}-zitadel" "$ZPORT" 8080
log "Zitadel instance administrators (instance host ${EXT_DOMAIN})"
# The PAT goes to curl in a config on stdin, never on an argv.
MEMBERS="$(curl_cfg header "Authorization: Bearer ${PAT}" \
  | curl -sS -K - -X POST "http://127.0.0.1:${ZPORT}/admin/v1/members/_search" \
      -H "x-zitadel-instance-host: ${EXT_DOMAIN}" \
      -H 'Content-Type: application/json' -d '{}')"
echo "$MEMBERS" | jq -e '.result' >/dev/null || { echo "member search failed: $(echo "$MEMBERS" | head -c 300)" >&2; exit 2; }

owner_id=""
while IFS=$'\t' read -r uid login type roles; do
  case "$type:$login" in
    TYPE_MACHINE:iam-admin)             want='["IAM_OWNER"]' ;;
    TYPE_MACHINE:login-client)          want='["IAM_LOGIN_CLIENT"]' ;;
    TYPE_MACHINE:gibson-daemon|TYPE_MACHINE:gibson-tenant-operator) want='["IAM_ORG_MANAGER"]' ;;
    TYPE_HUMAN:*)
      if [ -n "$OWNER_EMAIL" ] && [ "$(echo "$login" | tr '[:upper:]' '[:lower:]')" = "$(echo "$OWNER_EMAIL" | tr '[:upper:]' '[:lower:]')" ]; then
        want='["IAM_OWNER"]'; owner_id="$uid"
      else
        fail "human ${login} (${uid}) is a Zitadel administrator with ${roles}; only the Platform owner (${OWNER_EMAIL}) may be"
        continue
      fi ;;
    *) fail "unexpected administrator ${login} (${type}, ${uid}) with ${roles}"; continue ;;
  esac
  if [ "$roles" = "$want" ]; then pass "${login} holds ${roles}"; else fail "${login} holds ${roles}, expected ${want}"; fi
done < <(echo "$MEMBERS" | jq -r '.result[] | [.userId, .preferredLoginName, .userType, (.roles | sort | tojson)] | @tsv')
[ -n "$owner_id" ] || fail "the Platform owner (${OWNER_EMAIL}) is not a Zitadel administrator"

STORE_ID="$(kubectl -n "$NS" get configmap gibson-fga-config -o jsonpath='{.data.store_id}')"
forward "${RELEASE}-openfga" "$FPORT" 8080
# OpenFGA requires the preshared key (charts#645). The key goes to curl on
# stdin, never on an argv. A call that does not answer 200 stops the check
# by name: a 401 read as "no holders" once (hosted#634).
FGA_KEY="$(kubectl -n "$NS" get secret gibson-openfga-preshared-key -o jsonpath='{.data.key}' | base64 -d)"
[ -n "$FGA_KEY" ] || { echo "the Secret gibson-openfga-preshared-key holds no key" >&2; exit 1; }
fga_users() { # relation -> sorted user ids
  local out code
  out="$({ curl_cfg header "Authorization: Bearer ${FGA_KEY}"
           curl_cfg data "{\"tuple_key\":{\"relation\":\"$1\",\"object\":\"system_tenant:_system\"}}"; } \
    | curl -sSk -K - -w '\n%{http_code}' -X POST "https://127.0.0.1:${FPORT}/stores/${STORE_ID}/read" \
        -H 'Content-Type: application/json')"
  code="${out##*$'\n'}"
  [ "$code" = 200 ] || { echo "OpenFGA read of $1 answered HTTP ${code}: ${out%$'\n'*}" >&2; exit 1; }
  printf '%s' "${out%$'\n'*}" | jq -r '[.tuples[]?.key.user | sub("^user:"; "")] | sort | join(",")'
}
got_owner="$(fga_users platform_owner)" || exit 1
if [ -n "$owner_id" ] && [ "$got_owner" = "$owner_id" ]; then pass "platform_owner is held by exactly the Platform owner (${owner_id})"
else fail "platform_owner holders are [${got_owner}], expected [${owner_id:-the Platform owner}]"; fi

MAP="$(kubectl -n "$NS" get configmap "${RELEASE}-sa-identity-map" -o json 2>/dev/null || kubectl -n "$NS" get configmap gibson-sa-identity-map -o json)"
want_op="$(echo "$MAP" | jq -r '[.data["gibson-iam-admin"], .data["gibson-tenant-operator"]] | map(select(. != null)) | sort | join(",")')"
got_op="$(fga_users platform_operator)" || exit 1
if [ "$got_op" = "$want_op" ]; then pass "platform_operator is held by exactly gibson-iam-admin and gibson-tenant-operator"
else fail "platform_operator holders are [${got_op}], expected [${want_op}]"; fi

[ "$failures" -eq 0 ] || { echo "${failures} check(s) failed"; exit 1; }
pass "every Zitadel administrator and every platform relation is the one ADR-0093 allows"
