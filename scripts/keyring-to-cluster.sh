#!/usr/bin/env bash
# keyring-to-cluster.sh — write the bringup keyring into the cluster (ADR-0015).
#
# The ONE producer of the in-cluster keyring Secrets. `make recreate` owns
# this step (deploy#1737); scripts/vanilla-up.sh calls the same script for an
# install on a cluster the operator brought. The interim producers this
# replaces are deleted: kind/gibson/keyring.tf and the inline server-side
# apply blocks vanilla-up.sh carried (deploy#1730, deploy#1731, deploy#1732,
# deploy#1734).
#
# What it writes, from the keyring file substrate.env names:
#
#   Secret <NS>/bringup-keyring          bucket-access-key, bucket-secret-key,
#                                        openbao-seal-key, velero-repo-password,
#                                        ghcr-pull-token, llm-keys-json
#   Secret velero/velero-repo-credentials    key repository-password. The name
#                                        and the key are fixed by Velero
#                                        (pkg/repository/keys/keys.go).
#   Secret velero/velero-bucket-credential   key cloud, an AWS shared-credentials
#                                        file built from the bucket members.
#
# Server-side apply, one field manager per producer concern, so the members
# never overwrite each other and a future producer of one member owns exactly
# that member. Empty seed inputs are written empty: that is the documented
# "not supplied" value, and the openbao-auto-init sidecar seeds the key empty
# so the ExternalSecret resolves and the platform starts.
#
# EVERY data value below is QUOTED. `base64 -w0` of an empty member prints an
# empty string, and an unquoted empty YAML scalar is `null`, not "". The API
# server refuses that outright — `unknown object type "nil" in
# Secret.data.<key>` — so the documented "not supplied" value was the one
# value this script could not write (measured live on staging, deploy#1746:
# the staging keyring carries an empty GHCR_PULL_TOKEN and LLM_KEYS_JSON).
# A new member added here must be quoted too, and
# tests/harness/keyring-to-cluster.bats fails when one is not.
#
# Usage: keyring-to-cluster.sh <substrate.env>
# Env:   NS            release namespace (default gibson)
#        KEYRING_FILE  override the keyring path substrate.env names
set -euo pipefail

SUBSTRATE_ENV="${1:-}"
[ -n "$SUBSTRATE_ENV" ] || { echo "usage: keyring-to-cluster.sh <substrate.env>" >&2; exit 2; }
[ -s "$SUBSTRATE_ENV" ] || { echo "FATAL: no substrate.env at ${SUBSTRATE_ENV}. Run make substrate ENV=<env> first (ADR-0015)." >&2; exit 2; }
NS="${NS:-gibson}"

log() { printf '\033[1;32m▶\033[0m %s\n' "$*"; }

substrate_get() { grep -E "^$1=" "$SUBSTRATE_ENV" | head -n1 | cut -d= -f2-; }
BUCKET_ENDPOINT="$(substrate_get BUCKET_ENDPOINT)"
BUCKET_NAME="$(substrate_get BUCKET_NAME)"
KEYRING_FILE="${KEYRING_FILE:-$(substrate_get KEYRING_FILE)}"
[ -n "$BUCKET_ENDPOINT" ] && [ -n "$BUCKET_NAME" ] && [ -s "$KEYRING_FILE" ] \
  || { echo "FATAL: ${SUBSTRATE_ENV} is missing BUCKET_ENDPOINT, BUCKET_NAME or a readable KEYRING_FILE" >&2; exit 2; }
keyring_get() { grep -E "^$1=" "$KEYRING_FILE" | head -n1 | cut -d= -f2- || true; }

BUCKET_ACCESS_KEY="$(keyring_get BUCKET_ACCESS_KEY)"
BUCKET_SECRET_KEY="$(keyring_get BUCKET_SECRET_KEY)"
[ -n "$BUCKET_ACCESS_KEY" ] && [ -n "$BUCKET_SECRET_KEY" ] \
  || { echo "FATAL: ${KEYRING_FILE} has no BUCKET_ACCESS_KEY / BUCKET_SECRET_KEY member" >&2; exit 1; }

# The OpenBao seal member (deploy#1731). OpenBao runs the static seal and the
# chart mounts exactly this key from the bringup-keyring Secret. A store
# started under any other key comes up sealed and stays sealed.
OPENBAO_SEAL_KEY="$(keyring_get OPENBAO_SEAL_KEY)"
[ "${#OPENBAO_SEAL_KEY}" -eq 44 ] \
  || { echo "FATAL: ${KEYRING_FILE} has no 44-character OPENBAO_SEAL_KEY member (base64 of 32 bytes); rotate the keyring (docs/runbooks/substrate-kind.md)" >&2; exit 1; }

# The Velero repository password (deploy#1734). Velero seals its kopia
# repository with it. Velero's Role carries no create on secrets, so a
# missing Secret stops the server instead of letting it seal the repository
# with its built-in default.
VELERO_REPO_PASSWORD="$(keyring_get VELERO_REPO_PASSWORD)"
[ "${#VELERO_REPO_PASSWORD}" -eq 44 ] \
  || { echo "FATAL: ${KEYRING_FILE} has no 44-character VELERO_REPO_PASSWORD member (base64 of 32 bytes); rotate the keyring (docs/runbooks/substrate-kind.md)" >&2; exit 1; }

log "keyring ${KEYRING_FILE} (sha256:$(sha256sum < "$KEYRING_FILE" | cut -c1-16)) -> Secret ${NS}/bringup-keyring"
kubectl create namespace "$NS" --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# The two bucket members, under their own field manager.
kubectl apply --server-side --field-manager=bringup-bucket-credential -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: bringup-keyring
  namespace: ${NS}
data:
  bucket-access-key: "$(printf '%s' "$BUCKET_ACCESS_KEY" | base64 -w0)"
  bucket-secret-key: "$(printf '%s' "$BUCKET_SECRET_KEY" | base64 -w0)"
YAML

log "OpenBao seal key -> Secret ${NS}/bringup-keyring key openbao-seal-key (sha256:$(printf '%s' "$OPENBAO_SEAL_KEY" | sha256sum | cut -c1-16))"
kubectl apply --server-side --field-manager=bringup-openbao-seal -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: bringup-keyring
  namespace: ${NS}
data:
  openbao-seal-key: "$(printf '%s' "$OPENBAO_SEAL_KEY" | base64 -w0)"
YAML

# The two seed inputs (deploy#1732), each value allowed to be empty.
GHCR_PULL_TOKEN_VALUE="$(keyring_get GHCR_PULL_TOKEN)"
LLM_KEYS_JSON_VALUE="$(keyring_get LLM_KEYS_JSON)"
if [ -n "$GHCR_PULL_TOKEN_VALUE" ]; then
  log "GHCR pull token -> Secret ${NS}/bringup-keyring key ghcr-pull-token (sha256:$(printf '%s' "$GHCR_PULL_TOKEN_VALUE" | sha256sum | cut -c1-16))"
else
  log "keyring member GHCR_PULL_TOKEN is empty; first-party images will not pull until scripts/vanilla-set-secret.sh ghcr-pull-secret pat <token>"
fi
if [ -n "$LLM_KEYS_JSON_VALUE" ]; then
  log "LLM keys -> Secret ${NS}/bringup-keyring key llm-keys-json (sha256:$(printf '%s' "$LLM_KEYS_JSON_VALUE" | sha256sum | cut -c1-16))"
else
  log "keyring member LLM_KEYS_JSON is empty; gibson-llm-keys is seeded empty"
fi
kubectl apply --server-side --field-manager=bringup-seed-inputs -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: bringup-keyring
  namespace: ${NS}
data:
  ghcr-pull-token: "$(printf '%s' "$GHCR_PULL_TOKEN_VALUE" | base64 -w0)"
  llm-keys-json: "$(printf '%s' "$LLM_KEYS_JSON_VALUE" | base64 -w0)"
YAML
GHCR_PULL_TOKEN_VALUE=""; LLM_KEYS_JSON_VALUE=""

# The Velero member in the release namespace, so one Secret carries the whole
# keyring, and its two consumers in Velero's own namespace. The velero
# namespace holds exactly these two objects: the velero server reads its
# repository password through a cache restricted to its own namespace, so its
# namespace is a namespace where it can read every Secret
# (helm/gibson-velero/Chart.yaml).
kubectl apply --server-side --field-manager=bringup-velero-keyring -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: bringup-keyring
  namespace: ${NS}
data:
  velero-repo-password: "$(printf '%s' "$VELERO_REPO_PASSWORD" | base64 -w0)"
YAML
log "Velero repository password and bucket credential -> namespace velero"
kubectl create namespace velero --dry-run=client -o yaml | kubectl apply -f - >/dev/null
kubectl apply --server-side --field-manager=bringup-velero-credentials -f - >/dev/null <<YAML
apiVersion: v1
kind: Secret
metadata:
  name: velero-repo-credentials
  namespace: velero
data:
  repository-password: "$(printf '%s' "$VELERO_REPO_PASSWORD" | base64 -w0)"
---
apiVersion: v1
kind: Secret
metadata:
  name: velero-bucket-credential
  namespace: velero
data:
  cloud: "$(printf '[default]\naws_access_key_id=%s\naws_secret_access_key=%s\n' "$BUCKET_ACCESS_KEY" "$BUCKET_SECRET_KEY" | base64 -w0)"
YAML
log "bringup keyring written: ${NS}/bringup-keyring, velero/velero-repo-credentials, velero/velero-bucket-credential"
