#!/usr/bin/env bash
# vanilla-set-secret.sh — put an operator-supplied secret into the platform's
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
#   scripts/vanilla-set-secret.sh <key> <property> <value>
#   scripts/vanilla-set-secret.sh ghcr-pull-secret pat "$GHCR_TOKEN"
#   scripts/vanilla-set-secret.sh gibson-llm-keys anthropic_api_key "sk-ant-..."
#
# Env: NS (default gibson), RELEASE (default gibson)
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
POD="${RELEASE}-openbao-0"

if [ "$#" -ne 3 ]; then
  echo "usage: $0 <key> <property> <value>" >&2
  exit 2
fi
KEY="$1"; PROP="$2"; VALUE="$3"

# The admin token the sidecar minted. Never printed, never passed on a command
# line that lands in a log — piped into the exec'd shell's environment instead.
TOKEN="$(kubectl -n "$NS" get secret "${RELEASE}-platform-operator-vault" \
  -o jsonpath='{.data.VAULT_ADMIN_TOKEN}' | base64 -d)"
if [ -z "$TOKEN" ]; then
  echo "FATAL: no VAULT_ADMIN_TOKEN — has OpenBao finished bootstrapping?" >&2
  exit 1
fi

# Read-modify-write: a KV v2 write REPLACES the whole object, so writing one
# property naively would silently drop the others (gibson-llm-keys holds three).
code="$(kubectl -n "$NS" exec -i "$POD" -c openbao-auto-init -- sh -s <<EOF
set -eu
export T='${TOKEN}'
cur=\$(curl -sS -H "X-Vault-Token: \$T" \
  "http://127.0.0.1:8200/v1/secret/data/${KEY}" | jq -c '.data.data // {}')
merged=\$(printf '%s' "\$cur" | jq -c --arg p '${PROP}' --arg v '${VALUE}' '.[\$p]=\$v')
curl -sS -o /dev/null -w '%{http_code}' -X POST -H "X-Vault-Token: \$T" \
  -H 'Content-Type: application/json' \
  -d "\$(jq -nc --argjson d "\$merged" '{data:\$d}')" \
  "http://127.0.0.1:8200/v1/secret/data/${KEY}"
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
