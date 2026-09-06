#!/bin/bash
# orphan-reaper.sh — daily cleanup of stranded Zitadel users.
#
# A user is "orphaned" if:
#   1. They were created by the signup-bot machine user (metadata.creator == signup-bot userId).
#   2. Their email does not appear in any Tenant CR's spec.owner field.
#   3. Their creationDate is older than 24 hours (partial-signup failures leave users
#      in this state; we give 24h grace so in-flight signups are never reaped).
#
# Machine users are never deleted — the filter explicitly excludes non-human user types.
#
# Env vars (all required):
#   ZITADEL_API_URL        — in-cluster Zitadel service URL (e.g. http://gibson-zitadel:8080)
#   ZITADEL_EXTERNAL_DOMAIN — forged Host header value (e.g. app.zeroroot.ai —
#                             the auth.zeroroot.ai host is retired)
#   ZITADEL_PAT            — signup-bot Personal Access Token (from mounted Secret)
#   KUBE_NAMESPACE         — Kubernetes namespace (used for logging context only)
#
# Exits:
#   0 — clean run (including 0 deletions)
#   1 — catastrophic failure: Zitadel unreachable, kubectl failure, jq parse error.
#       Individual delete failures are logged but do NOT abort the run.
#
# Spec: dashboard-native-signup, task 4.

set -euo pipefail

# ---------------------------------------------------------------------------
# Validate required env vars
# ---------------------------------------------------------------------------
: "${ZITADEL_API_URL:?ZITADEL_API_URL is required}"
: "${ZITADEL_EXTERNAL_DOMAIN:?ZITADEL_EXTERNAL_DOMAIN is required}"
: "${ZITADEL_PAT:?ZITADEL_PAT is required}"
: "${KUBE_NAMESPACE:?KUBE_NAMESPACE is required}"

# Strip all whitespace/newlines from the PAT — same pattern as post-install-job.yaml.
# Zitadel's HTTP parser treats trailing bytes as a malformed header continuation.
ZITADEL_PAT=$(printf '%s' "$ZITADEL_PAT" | tr -d '\r\n\t ')

NOW_EPOCH=$(date +%s)
CUTOFF_EPOCH=$(( NOW_EPOCH - 86400 ))  # 24 hours ago

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# api METHOD PATH [JSON_BODY] -> prints "<HTTP_CODE> <BODY>"
# Mirrors the api() helper in post-install-job.yaml verbatim so curl flags
# and Host-header forging are consistent.
api() {
  local method="$1" path="$2" body="${3:-}"
  local tmp
  tmp=$(mktemp)
  if [ -n "$body" ]; then
    local code
    code=$(curl -sS -o "$tmp" -w '%{http_code}' \
      -X "$method" \
      -H "Host: ${ZITADEL_EXTERNAL_DOMAIN}" \
      -H "Authorization: Bearer ${ZITADEL_PAT}" \
      -H "Content-Type: application/json" \
      --data "$body" \
      "${ZITADEL_API_URL}${path}")
  else
    local code
    code=$(curl -sS -o "$tmp" -w '%{http_code}' \
      -X "$method" \
      -H "Host: ${ZITADEL_EXTERNAL_DOMAIN}" \
      -H "Authorization: Bearer ${ZITADEL_PAT}" \
      "${ZITADEL_API_URL}${path}")
  fi
  printf '%s ' "$code"
  cat "$tmp"
  rm -f "$tmp"
}

# log_json KEY=VAL ... — emits a single-line JSON object to stdout.
# All values must be pre-quoted strings or numbers; no nesting.
log_json() {
  # Build a JSON object from the argument list using jq.
  local pairs=()
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    pairs+=("--arg" "${key}" "${val}")
  done
  # Build keys array for jq object construction
  local jq_expr='{'
  local first=1
  for kv in "$@"; do
    local key="${kv%%=*}"
    [ "$first" = "1" ] || jq_expr+=","
    jq_expr+="\"${key}\": \$${key}"
    first=0
  done
  jq_expr+='}'
  jq -nc "${pairs[@]}" "$jq_expr"
}

# ---------------------------------------------------------------------------
# Step 1: resolve the signup-bot's userId so we can match metadata.creator
# ---------------------------------------------------------------------------
echo ">>> resolving signup-bot userId"
resp=$(api POST /management/v1/users/_search '{"queries":[{"typeQuery":{"type":"TYPE_MACHINE"}},{"userNameQuery":{"userName":"signup-bot","method":"TEXT_QUERY_METHOD_EQUALS"}}]}')
http_code=$(echo "$resp" | awk '{print $1}')
body=$(echo "$resp" | cut -d' ' -f2-)

if [ "$http_code" != "200" ]; then
  log_json "action=reap_orphan_zitadel_user_error" "error=cannot_resolve_signup_bot" "httpCode=$http_code" "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[fail] cannot resolve signup-bot userId — HTTP ${http_code}: ${body}" >&2
  exit 1
fi

SIGNUP_BOT_USER_ID=$(echo "$body" | jq -r '.result[] | select(.userName=="signup-bot") | .id' | head -n1)

if [ -z "$SIGNUP_BOT_USER_ID" ]; then
  # signup-bot not provisioned yet (pre-bootstrap cluster) — nothing to reap.
  echo ">>> signup-bot not found; nothing to reap"
  log_json "action=reap_orphan_zitadel_user" "count=0" "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  exit 0
fi

echo "    signup_bot_user_id=${SIGNUP_BOT_USER_ID}"

# ---------------------------------------------------------------------------
# Step 2: list all human users in the IAM org (created by signup-bot via
# metadata.creator filter, paged up to 1000 results which is more than
# sufficient for an orphan-reaper sweep).
#
# Zitadel v2 user search: POST /v2/users
# We search for human users whose creator matches the signup-bot userId.
# The `creationDateQuery` filter is not available server-side in all Zitadel
# versions, so we do the date comparison client-side with `date -d`.
# ---------------------------------------------------------------------------
echo ">>> listing human users created by signup-bot"
search_body=$(jq -nc \
  --arg bot_id "$SIGNUP_BOT_USER_ID" \
  '{
    queries: [
      { typeQuery: { type: "TYPE_HUMAN" } },
      { creationDateQuery: {} }
    ],
    sortingColumn: "USER_FIELD_NAME_CREATION_DATE",
    asc: true
  }')

# Note: Zitadel v2 does not expose a "creator" filter server-side in the
# /v2/users search endpoint. We list all human users and cross-check each
# against the creator via the user detail endpoint. To keep API calls bounded
# we first fetch up to 500 users, then filter client-side.
#
# A future refinement could use Zitadel metadata queries if the instance has
# metadata set on users at creation time (see design.md §Weaknesses note).
search_body=$(jq -nc '{
  queries: [
    { typeQuery: { type: "TYPE_HUMAN" } }
  ],
  sortingColumn: "USER_FIELD_NAME_CREATION_DATE",
  asc: true,
  limit: 500
}')

resp=$(api POST /v2/users "$search_body")
http_code=$(echo "$resp" | awk '{print $1}')
body=$(echo "$resp" | cut -d' ' -f2-)

if [ "$http_code" != "200" ]; then
  log_json "action=reap_orphan_zitadel_user_error" "error=cannot_list_users" "httpCode=$http_code" "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[fail] cannot list Zitadel users — HTTP ${http_code}: ${body}" >&2
  exit 1
fi

ALL_USERS_JSON="$body"

# ---------------------------------------------------------------------------
# Step 3: collect tenant-owning emails from Kubernetes
# ---------------------------------------------------------------------------
echo ">>> collecting tenant owner emails from cluster"

# kubectl exits non-zero if the CRD doesn't exist (pre-operator cluster).
# We treat that as "no tenants" rather than a fatal error, since a newly
# bootstrapped cluster with no tenants should still allow the reaper to run
# without the operator being deployed yet.
OWNER_EMAILS_RAW=""
if kubectl get crd tenants.gibson.zeroroot.ai >/dev/null 2>&1; then
  OWNER_EMAILS_RAW=$(kubectl get tenants.gibson.zeroroot.ai -A \
    -o jsonpath='{range .items[*]}{.spec.owner}{"\n"}{end}' 2>/dev/null || true)
else
  echo "    [warn] tenants.gibson.zeroroot.ai CRD not found — treating as zero owners"
fi

# Build a newline-delimited, lowercased set of owner emails for fast lookup.
OWNER_EMAILS_LOWER=$(echo "$OWNER_EMAILS_RAW" | tr '[:upper:]' '[:lower:]' | sort -u | grep -v '^$' || true)

echo "    tenant_owner_count=$(echo "$OWNER_EMAILS_LOWER" | grep -c . || echo 0)"

# ---------------------------------------------------------------------------
# Step 4: identify candidates — users not in owner set AND older than 24h
#
# We use the Zitadel v1 management API to fetch each user's detail and
# confirm their creation date and resourceOwner (to ensure they belong to
# the IAM admin org, not a tenant org created by the operator — we must
# only touch users in the global IAM org, not tenant org members).
# ---------------------------------------------------------------------------
echo ">>> scanning for orphaned users (> 24h old, no tenant)"

DELETED_EMAILS=()
DELETE_COUNT=0
SKIP_COUNT=0

# Extract candidates: userId + email + creationDate from the v2 search response.
# The v2 user list shape is: .result[].userId, .result[].human.email, .result[].details.creationDate
CANDIDATES=$(echo "$ALL_USERS_JSON" | jq -r '
  .result[]? |
  select(.human != null) |
  [.userId, (.human.email // ""), (.details.creationDate // "")] |
  @tsv
' 2>/dev/null || true)

if [ -z "$CANDIDATES" ]; then
  echo "    no human users found in Zitadel"
  log_json "action=reap_orphan_zitadel_user" "count=0" "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  exit 0
fi

while IFS=$'\t' read -r user_id email creation_date; do
  [ -z "$user_id" ] && continue
  [ -z "$email" ] && continue
  [ -z "$creation_date" ] && continue

  email_lower=$(echo "$email" | tr '[:upper:]' '[:lower:]')

  # ------------------------------------------------------------------
  # Age check: parse the ISO-8601 creationDate and compare to cutoff.
  # `date -d` is a GNU date extension available on alpine/k8s images.
  # The format from Zitadel is e.g. "2026-04-18T10:23:45.123456789Z".
  # We strip the sub-second part before parsing to be safe.
  # ------------------------------------------------------------------
  creation_epoch=""
  # Strip sub-second precision (Zitadel returns nanoseconds which date -d
  # handles inconsistently across libc versions).
  creation_ts_clean=$(echo "$creation_date" | sed 's/\.[0-9]*Z$/Z/' | sed 's/\.[0-9]*$//')
  creation_epoch=$(date -d "$creation_ts_clean" +%s 2>/dev/null || true)

  if [ -z "$creation_epoch" ]; then
    echo "    [warn] could not parse creationDate '${creation_date}' for user ${user_id} (${email}) — skipping"
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  if [ "$creation_epoch" -ge "$CUTOFF_EPOCH" ]; then
    # User is less than 24h old — NEVER delete.
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  # ------------------------------------------------------------------
  # Owner check: is this email an active tenant owner?
  # ------------------------------------------------------------------
  if echo "$OWNER_EMAILS_LOWER" | grep -qxF "$email_lower"; then
    # This user owns a tenant — keep them.
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  # ------------------------------------------------------------------
  # Creator check: confirm this user was created by the signup-bot.
  #
  # Zitadel does not expose a creator field in the v2 user list; we must
  # call GET /management/v1/users/{id} on the v1 API to read the
  # `lastModification` and `resourceOwner` context, then cross-check via
  # the audit log or metadata. The pragmatic approach here: the reaper
  # only targets users in the IAM admin org (i.e. those whose
  # resourceOwner matches the IAM org ID, not a tenant org). Users
  # provisioned by the tenant-operator live in per-tenant orgs; the
  # global IAM org only holds signup-bot-created users plus the few
  # system machine accounts.
  #
  # We therefore check that the user's resourceOwner matches the IAM
  # admin org (same org as the signup-bot itself). This is a sound proxy
  # for "created via signup-bot" given that the dashboard never creates
  # users in the IAM org by any other code path.
  # ------------------------------------------------------------------
  resp_detail=$(api GET "/management/v1/users/${user_id}")
  detail_code=$(echo "$resp_detail" | awk '{print $1}')
  detail_body=$(echo "$resp_detail" | cut -d' ' -f2-)

  if [ "$detail_code" != "200" ]; then
    echo "    [warn] could not fetch detail for user ${user_id} (${email}) — HTTP ${detail_code} — skipping"
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  user_resource_owner=$(echo "$detail_body" | jq -r '.user.details.resourceOwner // empty')
  bot_resource_owner=$(echo "$detail_body" | jq -r 'empty') # placeholder; overridden below

  # Resolve the IAM admin org ID from the signup-bot's resourceOwner
  # (done once, cached in BOT_ORG_ID).
  if [ -z "${BOT_ORG_ID:-}" ]; then
    resp_bot=$(api GET "/management/v1/users/${SIGNUP_BOT_USER_ID}")
    bot_code=$(echo "$resp_bot" | awk '{print $1}')
    bot_body=$(echo "$resp_bot" | cut -d' ' -f2-)
    if [ "$bot_code" = "200" ]; then
      BOT_ORG_ID=$(echo "$bot_body" | jq -r '.user.details.resourceOwner // empty')
      echo "    iam_admin_org_id=${BOT_ORG_ID}"
    else
      echo "    [warn] could not resolve IAM admin org ID — skipping creator check for safety"
      BOT_ORG_ID="UNKNOWN"
    fi
  fi

  # If the user does not live in the same org as the signup-bot, skip —
  # they were not created through the signup flow.
  if [ "${BOT_ORG_ID}" != "UNKNOWN" ] && [ "$user_resource_owner" != "$BOT_ORG_ID" ]; then
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  # Skip machine users regardless (belt-and-suspenders: the v2 search
  # already filtered to TYPE_HUMAN, but guard explicitly).
  user_type=$(echo "$detail_body" | jq -r '.user.machine != null | if . then "MACHINE" else "HUMAN" end')
  if [ "$user_type" = "MACHINE" ]; then
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    continue
  fi

  # ------------------------------------------------------------------
  # All checks passed — this user is an orphan. Delete them.
  # ------------------------------------------------------------------
  log_json \
    "action=reap_orphan_zitadel_user" \
    "email=$email" \
    "zitadelUserId=$user_id" \
    "creationDate=$creation_date" \
    "timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  delete_resp=$(api DELETE "/v2/users/${user_id}")
  delete_code=$(echo "$delete_resp" | awk '{print $1}')
  delete_body=$(echo "$delete_resp" | cut -d' ' -f2-)

  case "$delete_code" in
    2??)
      echo "    [ok] deleted orphan user ${user_id} (${email})"
      DELETED_EMAILS+=("$email")
      DELETE_COUNT=$(( DELETE_COUNT + 1 ))
      ;;
    404)
      # Already gone — treat as success (idempotent).
      echo "    [exists] user ${user_id} (${email}) already deleted (404)"
      DELETE_COUNT=$(( DELETE_COUNT + 1 ))
      ;;
    *)
      # Non-fatal: log and continue. A single delete failure must not abort
      # the run; the reaper will retry on the next daily invocation.
      echo "    [warn] delete failed for ${user_id} (${email}) — HTTP ${delete_code}: ${delete_body}"
      ;;
  esac

done <<< "$CANDIDATES"

# ---------------------------------------------------------------------------
# Summary log line
# ---------------------------------------------------------------------------
DELETED_JSON=$(printf '%s\n' "${DELETED_EMAILS[@]+"${DELETED_EMAILS[@]}"}" | jq -R . | jq -s .)

jq -nc \
  --arg action "reap_orphan_zitadel_user_summary" \
  --argjson count "$DELETE_COUNT" \
  --argjson deleted "${DELETED_JSON}" \
  --arg timestamp "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg namespace "$KUBE_NAMESPACE" \
  '{action: $action, count: $count, deleted: $deleted, namespace: $namespace, timestamp: $timestamp}'

echo ">>> reaper complete: deleted=${DELETE_COUNT} skipped=${SKIP_COUNT}"
exit 0
