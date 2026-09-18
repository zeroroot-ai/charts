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
# Usage:
#   scripts/baseline-set-secret.sh <key> <property> <value>
#   scripts/baseline-set-secret.sh ghcr-pull-secret pat "$GHCR_TOKEN"
#   scripts/baseline-set-secret.sh ses-smtp-credentials password "$SMTP_PASSWORD"
#
# Env: NS (default gibson), RELEASE (default gibson)
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
POD="${RELEASE}-openbao-0"

# --selftest: prove, offline, that a hostile value never becomes shell text
# inside the pod. A stub kubectl records the argv it was handed and the script
# it was fed on stdin. The value must reach the stub only as an env argument,
# and the script text must be the fixed program with no trace of the value.
if [ "${1:-}" = "--selftest" ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/bin"
  cat > "$tmp/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  *"get secret"*) printf '%s' 'dG9rZW4tMTIz' ;;                 # VAULT_ADMIN_TOKEN=token-123
  *" exec "*)     printf '%s\n' "$@" > "$STUB_DIR/argv"; cat > "$STUB_DIR/stdin"; printf '200' ;;
  *"get externalsecrets"*) printf '%s' '{"items":[]}' ;;
esac
STUB
  chmod +x "$tmp/bin/kubectl"
  hostile="x'; touch /tmp/pwned; echo '"
  STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" "$0" ghcr-pull-secret pat "$hostile" >/dev/null
  grep -qF -- "SET_VALUE=$hostile" "$tmp/argv" \
    || { echo "SELFTEST FAIL: the value did not reach the pod as an env argument"; exit 1; }
  grep -qF -- "VAULT_TOKEN=token-123" "$tmp/argv" \
    || { echo "SELFTEST FAIL: the token did not reach the pod as an env argument"; exit 1; }
  if grep -qF -- "pwned" "$tmp/stdin" || grep -qF -- "token-123" "$tmp/stdin"; then
    echo "SELFTEST FAIL: a caller value is part of the script text the pod runs"; exit 1
  fi
  grep -qF -- '$SET_VALUE' "$tmp/stdin" \
    || { echo "SELFTEST FAIL: the pod script does not read the value from its environment"; exit 1; }
  echo "OK: baseline-set-secret.sh hands values to the pod as environment, never as script text"
  exit 0
fi

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <key> <property> <value>" >&2
  exit 2
fi
KEY="$1"; PROP="$2"; VALUE="$3"

# The admin token the sidecar minted. Never printed, never passed on a command
# line that lands in a log — handed to the exec'd shell as environment.
TOKEN="$(kubectl -n "$NS" get secret "${RELEASE}-platform-operator-vault" \
  -o jsonpath='{.data.VAULT_ADMIN_TOKEN}' | base64 -d)"
if [ -z "$TOKEN" ]; then
  echo "FATAL: no VAULT_ADMIN_TOKEN — has OpenBao finished bootstrapping?" >&2
  exit 1
fi

# Read-modify-write: a KV v2 write REPLACES the whole object, so writing one
# property naively would silently drop the others (ses-smtp-credentials holds two).
#
# The key, property, value and token travel as ENVIRONMENT of the exec'd
# shell, never as text of the script it runs. An earlier version expanded
# them into the heredoc, so a value with a single quote ended the quoting and
# the rest ran as shell inside the pod that holds the platform's root secret
# material. Operators paste vendor-issued values into this argument; the
# script must not care what is in them. The quoted heredoc below contains no
# expansion at all.
code="$(kubectl -n "$NS" exec -i "$POD" -c openbao-auto-init -- \
  env "SET_KEY=$KEY" "SET_PROP=$PROP" "SET_VALUE=$VALUE" "VAULT_TOKEN=$TOKEN" sh -s <<'EOF'
set -eu
cur=$(curl -sS -H "X-Vault-Token: $VAULT_TOKEN" \
  "http://127.0.0.1:8200/v1/secret/data/$SET_KEY" | jq -c '.data.data // {}')
merged=$(printf '%s' "$cur" | jq -c --arg p "$SET_PROP" --arg v "$SET_VALUE" '.[$p]=$v')
curl -sS -o /dev/null -w '%{http_code}' -X POST -H "X-Vault-Token: $VAULT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --argjson d "$merged" '{data:$d}')" \
  "http://127.0.0.1:8200/v1/secret/data/$SET_KEY"
EOF
)"
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
