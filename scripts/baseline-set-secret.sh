#!/usr/bin/env bash
# baseline-set-secret.sh — put an operator-supplied secret into the platform's
# OpenBao KV, where ExternalSecrets read it.
#
# The auto-init sidecar generates everything the platform can generate, but
# some values can only come from the operator: a registry pull token, vendor
# API keys. Those are seeded EMPTY so the ExternalSecret resolves and the
# platform starts; this is how you fill them in.
#
# It is the general tool on purpose. "How does an operator get a secret into
# the backend" is a question every self-hosted install asks, and the answer
# should not be a paragraph in a runbook.
#
# Usage: the value comes on stdin. One trailing newline is removed, nothing
# else is changed. The script takes exactly two arguments and refuses a third.
#   printf '%s\n' "$value" | scripts/baseline-set-secret.sh <key> <property>
#   printf '%s\n' "$GHCR_TOKEN"    | scripts/baseline-set-secret.sh ghcr-pull-secret pat
#   printf '%s\n' "$SMTP_PASSWORD" | scripts/baseline-set-secret.sh ses-smtp-credentials password
#
# Env: NS (default gibson), RELEASE (default gibson)
#
# The value travels on stdin at every step: into this script, into the
# exec'd shell of the pod, and inside the pod into files of a mode-0700
# directory that the shell removes on exit. It is on no argv of any process,
# so no process listing, audit log or shell history holds it. The test is
# tests/baseline-set-secret.bats.
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
POD="${RELEASE}-openbao-0"

usage() {
  echo "usage: printf '%s\\n' \"\$value\" | $0 <key> <property>" >&2
  echo "the value comes on stdin, never as an argument" >&2
}

if [ "$#" -ne 2 ]; then
  usage
  exit 2
fi
KEY="$1"; PROP="$2"

if [ -t 0 ]; then
  echo "FATAL: stdin is a terminal; pipe the value in" >&2
  usage
  exit 2
fi
VALUE="$(cat; printf x)"
VALUE="${VALUE%x}"
VALUE="${VALUE%$'\n'}"
if [ -z "$VALUE" ]; then
  echo "FATAL: stdin holds no value" >&2
  usage
  exit 2
fi

# No token leaves the cluster. The exec'd shell runs in the openbao-auto-init
# sidecar and logs in there with the openbao-seeder role, from the projected
# ServiceAccount token of the pod, the same login the sidecar uses for its own
# seed pass. That token lives 15 minutes, and the script revokes it at the end.
# No consumer token can write an arbitrary KV key: each holds only its own paths.
# The listener serves only TLS (charts#540). The sidecar sets CURL_CA_BUNDLE to
# the chart CA, and kubectl exec keeps the container env, so curl verifies it.
#
# Read-modify-write: a KV v2 write REPLACES the whole object, so writing one
# property naively would silently drop the others (ses-smtp-credentials holds two).
#
# The key and the property are names. They travel as environment of the
# exec'd shell. The value travels on its stdin, and inside the pod it lives
# only in files: the login body, the token header (curl -H @file), the
# current data and the write body (curl --data-binary @file). jq reads the
# value with --rawfile. The script text below is fixed: a quoted heredoc with
# no expansion, so no value of the caller is ever shell text or an argument
# inside the pod that holds the platform's root secret material.
POD_SCRIPT="$(cat <<'EOF'
set -eu
umask 077
BAO="https://127.0.0.1:8200"
d="$(mktemp -d /tmp/baseline-set-secret.XXXXXX)"
trap 'rm -rf "$d"' EXIT
cat > "$d/value"
jq -Rs -c '{role:"openbao-seeder",jwt:rtrimstr("\n")}' \
  < "${SA_TOKEN_FILE:-/var/run/secrets/kubernetes.io/serviceaccount/token}" > "$d/login"
curl -sS -X POST -H 'Content-Type: application/json' --data-binary "@$d/login" \
  -o "$d/auth" "$BAO/v1/auth/kubernetes/login"
jq -r '"X-Vault-Token: " + (.auth.client_token // empty)' "$d/auth" > "$d/hdr"
grep -q 'X-Vault-Token: .' "$d/hdr" || { echo 000; exit 0; }
trap 'curl -sS -o /dev/null -X POST -H "@$d/hdr" "$BAO/v1/auth/token/revoke-self" || true; rm -rf "$d"' EXIT
curl -sS -H "@$d/hdr" -o "$d/cur" "$BAO/v1/secret/data/$SET_KEY"
jq -c --arg p "$SET_PROP" --rawfile v "$d/value" \
  '{data:((.data.data // {}) | .[$p]=$v)}' "$d/cur" > "$d/body"
curl -sS -o /dev/null -w '%{http_code}' -X POST -H "@$d/hdr" \
  -H 'Content-Type: application/json' --data-binary "@$d/body" \
  "$BAO/v1/secret/data/$SET_KEY"
EOF
)"

# printf is a bash builtin: the value reaches kubectl on a pipe, not an argv.
code="$(printf '%s' "$VALUE" | kubectl -n "$NS" exec -i "$POD" -c openbao-auto-init -- \
  env "SET_KEY=$KEY" "SET_PROP=$PROP" sh -c "$POD_SCRIPT")"
case "$code" in
  200|204) ;;
  *) echo "FATAL: writing secret/${KEY} returned HTTP ${code}" >&2; exit 1 ;;
esac

# Force the ExternalSecrets that read this key to refresh now rather than at
# the next interval, so the caller sees the effect immediately.
for es in $(kubectl -n "$NS" get externalsecrets -o json \
    | jq -r --arg k "$KEY" '.items[] | select([.spec.data[]?.remoteRef.key, .spec.dataFrom[]?.extract.key] | index($k)) | .metadata.name'); do
  kubectl -n "$NS" annotate externalsecret "$es" \
    "force-sync=$(date +%s)" --overwrite >/dev/null
  echo "refreshed externalsecret/${es}"
done

echo "set secret/${KEY}.${PROP}"
