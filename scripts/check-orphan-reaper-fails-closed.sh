#!/usr/bin/env bash
# check-orphan-reaper-fails-closed.sh — the orphan reaper deletes nothing
# when it cannot prove who created a user.
#
# The reaper's ownership check is "the user lives in the IAM admin org, the
# org the signup-bot lives in". It used to skip that check when the bot
# lookup failed, and call the skip "for safety": with the check skipped,
# every human user older than 24 hours without a Tenant CR was deleted.
# This runs the real script offline against a stub curl and a stub kubectl
# and asserts three outcomes:
#
#   1. bot lookup fails            -> exit 1, no DELETE issued   (THE FIXTURE)
#   2. user in another org         -> exit 0, no DELETE issued
#   3. orphan in the IAM admin org -> exit 0, one DELETE issued  (the stub drives the real path)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REAPER="$ROOT/helm/gibson-workloads/files/orphan-reaper.sh"
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"

# The stub curl answers on METHOD + path and appends every DELETE it sees.
cat > "$tmp/bin/curl" <<'STUB'
#!/usr/bin/env bash
method=GET; out=""; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -X) method="$2"; shift ;;
    -o) out="$2"; shift ;;
    -H|--data|-w) shift ;;
    http*) url="$1" ;;
  esac
  shift
done
path="${url#*://*/}"; path="/${path}"
code=200; body='{}'
case "$method $path" in
  "POST /management/v1/users/_search") body='{"result":[{"id":"bot-1","userName":"signup-bot"}]}' ;;
  "POST /v2/users") body='{"result":[{"userId":"u-1","human":{"email":"old@example.test"},"details":{"creationDate":"2000-01-01T00:00:00Z"}}]}' ;;
  "GET /management/v1/users/u-1") body="{\"user\":{\"details\":{\"resourceOwner\":\"${STUB_USER_ORG}\"}}}" ;;
  "GET /management/v1/users/bot-1") code="${STUB_BOT_CODE}"; body="{\"user\":{\"details\":{\"resourceOwner\":\"org-iam\"}}}" ;;
  "DELETE /v2/users/u-1") echo "DELETE u-1" >> "$STUB_LOG"; body='{}' ;;
esac
[ -n "$out" ] && printf '%s' "$body" > "$out"
printf '%s' "$code"
STUB
cat > "$tmp/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"get crd"*) exit 0 ;;
  *"get tenants"*) printf '' ;;
esac
STUB
chmod +x "$tmp/bin/curl" "$tmp/bin/kubectl"

run_reaper() { # <bot lookup code> <user org> -> prints exit code
  : > "$tmp/deletes"
  set +e
  STUB_LOG="$tmp/deletes" STUB_BOT_CODE="$1" STUB_USER_ORG="$2" \
  ZITADEL_API_URL=http://zitadel.stub ZITADEL_EXTERNAL_DOMAIN=app.stub ZITADEL_PAT=pat KUBE_NAMESPACE=gibson \
  PATH="$tmp/bin:$PATH" bash "$REAPER" > "$tmp/out" 2>&1
  echo $?
  set -e
}
deletes() { grep -c . "$tmp/deletes" || true; }

# 1. THE FIXTURE THIS EXISTS FOR.
rc="$(run_reaper 500 org-iam)"
[ "$rc" -ne 0 ] || { echo "FAIL: the reaper exited 0 although the IAM admin org could not be resolved"; cat "$tmp/out"; exit 1; }
[ "$(deletes)" -eq 0 ] || { echo "FAIL: the reaper deleted a user without proving who created it"; cat "$tmp/out"; exit 1; }
grep -q "refusing to delete anything" "$tmp/out" || { echo "FAIL: the reaper did not say why it stopped"; cat "$tmp/out"; exit 1; }

# 2. A user in another org is not the reaper's to touch.
rc="$(run_reaper 200 org-tenant-x)"
[ "$rc" -eq 0 ] && [ "$(deletes)" -eq 0 ] || { echo "FAIL: a user outside the IAM admin org was deleted or the run failed (rc=$rc)"; cat "$tmp/out"; exit 1; }

# 3. The real orphan is deleted, so the stub exercises the delete path.
rc="$(run_reaper 200 org-iam)"
[ "$rc" -eq 0 ] && [ "$(deletes)" -eq 1 ] || { echo "FAIL: the orphan in the IAM admin org was not deleted (rc=$rc, deletes=$(deletes))"; cat "$tmp/out"; exit 1; }

echo "OK: the orphan reaper deletes nothing when it cannot resolve the IAM admin org, and only IAM-org orphans otherwise"
