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
  *" exec "*)     printf '%s\n' "$@" > "$STUB_DIR/argv"; cat > "$STUB_DIR/stdin"; printf '200' ;;
  *"get externalsecrets"*) printf '%s' '{"items":[]}' ;;
esac
STUB
  chmod +x "$tmp/bin/kubectl"
  hostile="x'; touch /tmp/pwned; echo '"
  STUB_DIR="$tmp" PATH="$tmp/bin:$PATH" "$0" ghcr-pull-secret pat "$hostile" >/dev/null
  grep -qF -- "SET_VALUE=$hostile" "$tmp/argv" \
    || { echo "SELFTEST FAIL: the value did not reach the pod as an env argument"; exit 1; }
  if grep -qF -- "VAULT_TOKEN=" "$tmp/argv"; then
    echo "SELFTEST FAIL: a token left the cluster: the pod must log in with its own ServiceAccount"; exit 1
  fi
  grep -qF -- "auth/kubernetes/login" "$tmp/stdin" \
    || { echo "SELFTEST FAIL: the pod script does not log in with the openbao-seeder role"; exit 1; }
  if grep -qF -- "pwned" "$tmp/stdin"; then
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

# No token leaves the cluster. The exec'd shell runs in the openbao-auto-init
# sidecar and logs in there with the openbao-seeder role, from the projected
# ServiceAccount token of the pod, the same login the sidecar uses for its own
# seed pass. That token lives 15 minutes, and the script revokes it at the end.
# No consumer token can write an arbitrary KV key: each holds only its own paths.
# The listener serves only TLS (charts#540). The sidecar sets CURL_CA_BUNDLE to
# the chart CA, and kubectl exec keeps the container env, so curl verifies it.

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
  env "SET_KEY=$KEY" "SET_PROP=$PROP" "SET_VALUE=$VALUE" sh -s <<'EOF'
set -eu
VAULT_TOKEN=$(jq -nc --arg j "$(cat /var/run/secrets/kubernetes.io/serviceaccount/token)" '{role:"openbao-seeder",jwt:$j}' \
  | curl -sS -X POST -H 'Content-Type: application/json' -d @- \
    "https://127.0.0.1:8200/v1/auth/kubernetes/login" | jq -r '.auth.client_token // empty')
[ -n "$VAULT_TOKEN" ] || { echo 000; exit 0; }
trap 'curl -sS -o /dev/null -X POST -H "X-Vault-Token: $VAULT_TOKEN" https://127.0.0.1:8200/v1/auth/token/revoke-self || true' EXIT
cur=$(curl -sS -H "X-Vault-Token: $VAULT_TOKEN" \
  "https://127.0.0.1:8200/v1/secret/data/$SET_KEY" | jq -c '.data.data // {}')
merged=$(printf '%s' "$cur" | jq -c --arg p "$SET_PROP" --arg v "$SET_VALUE" '.[$p]=$v')
curl -sS -o /dev/null -w '%{http_code}' -X POST -H "X-Vault-Token: $VAULT_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "$(jq -nc --argjson d "$merged" '{data:$d}')" \
  "https://127.0.0.1:8200/v1/secret/data/$SET_KEY"
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
