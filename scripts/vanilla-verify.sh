#!/usr/bin/env bash
# vanilla-verify.sh — assert a vanilla install actually came up.
#
# Every assertion here was first checked by hand while building the install, so
# it lives in the repo instead of in a transcript. An install that "returned"
# is not an install that works: helm reports success the moment its manifests
# are accepted, long before OpenBao has bootstrapped or a single Secret exists.
#
# Env: NS (default gibson), RELEASE (default gibson), WAIT_SECS (default 600)
#      KEYRING_FILE — the bringup keyring file stage 0 wrote (scripts/keyring.sh
#      shape), set by vanilla-up.sh on the kind fixture to run the keyring
#      drill (section 5). Unset = skipped.
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
WAIT_SECS="${WAIT_SECS:-600}"
POD="${RELEASE}-openbao-0"

log()  { printf '\033[1;32m▶\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; }

deadline=$(( $(date +%s) + WAIT_SECS ))

# seal_status — one poll of /v1/sys/seal-status. Prints the body, or nothing
# and a non-zero status when the pod cannot be reached yet. Every caller is a
# deadline loop, so the caller decides; nothing here swallows the exit code.
seal_status() {
  kubectl -n "$NS" exec "$POD" -c openbao -- \
    sh -c 'wget -qO- http://127.0.0.1:8200/v1/sys/seal-status' 2>/dev/null
}

# --- 1. OpenBao is initialized AND unsealed -------------------------------
# devMode is false on this profile, so nothing mints a literal root token. The
# static seal opens the store from the bringup keyring Secret and the auto-init
# sidecar bootstraps it from a fresh PVC, or the platform has no secret store
# at all.
log "waiting for OpenBao to be initialized and unsealed"
while :; do
  status="$(seal_status)" || status=""
  case "$status" in
    *'"initialized":true'*'"sealed":false'*) log "OpenBao: initialized, unsealed"; break ;;
  esac
  if [ "$(date +%s)" -ge "$deadline" ]; then
    fail "OpenBao did not reach initialized+unsealed within ${WAIT_SECS}s"
    echo "last seal-status: ${status:-<unreachable>}" >&2
    if ! kubectl -n "$NS" logs "$POD" -c openbao-auto-init --tail=40 >&2; then
      echo "(sidecar logs unavailable)" >&2
    fi
    exit 1
  fi
  sleep 5
done

# --- 2. Exactly one secret backend, and it is Valid -----------------------
# (deploy#1733): the platform's own OpenBao is the one External
# Secrets backend, reached through one ClusterSecretStore. A store the chart
# renders but the cluster refuses reports InvalidProviderConfig and every read
# under it fails, so assert the store before the reads that depend on it.
log "asserting exactly one secret-backend ClusterSecretStore, Valid"
store_deadline=$(( $(date +%s) + 300 ))
while :; do
  backends="$(kubectl get clustersecretstore -o \
    jsonpath='{range .items[*]}{.metadata.name}{" "}{.spec.provider.vault.server}{"\n"}{end}' \
    2>/dev/null | awk 'NF==2{print $1}')"
  count="$(printf '%s' "$backends" | grep -c . || true)"
  ready="$(kubectl get clustersecretstore gibson-secrets -o \
    jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null || true)"
  if [ "$count" = "1" ] && [ "$backends" = "gibson-secrets" ] && [ "$ready" = "True" ]; then
    log "ClusterSecretStore gibson-secrets: one backend, Ready"
    break
  fi
  if [ "$(date +%s)" -ge "$store_deadline" ]; then
    fail "want exactly one backend store named gibson-secrets and Ready; got ${count} backend(s) [${backends}], Ready=${ready:-<absent>}"
    kubectl get clustersecretstore -o wide >&2 || true
    kubectl get clustersecretstore gibson-secrets \
      -o jsonpath='{.status.conditions[*].reason}: {.status.conditions[*].message}{"\n"}' >&2 || true
    exit 1
  fi
  sleep 5
done

# --- 3. Every ExternalSecret resolves, in EVERY namespace -----------------
# This is the assertion that catches the whole class of "the chart assumes a
# backend somebody else populated". If OpenBao seeding or the ESO auth role is
# wrong, the store still reports Ready and every read fails.
#
# Every namespace, not just the release namespace: the chart puts
# ExternalSecrets in the release namespace, in gibson, and beside the setec
# frontend, and a spot check of one namespace passes while another waits.
log "waiting for every ExternalSecret in every namespace to sync"
es_state() {  # NAMESPACE NAME READY, one line each
  kubectl get externalsecrets --all-namespaces -o \
    'jsonpath={range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.status.conditions[?(@.type=="Ready")].status}{"\n"}{end}' \
    2>/dev/null
}
while :; do
  state="$(es_state)"
  total="$(printf '%s\n' "$state" | grep -c . || true)"
  synced="$(printf '%s\n' "$state" | awk '$3=="True"' | grep -c . || true)"
  if [ "$total" != "0" ] && [ "$synced" = "$total" ]; then
    log "ExternalSecrets: ${synced}/${total} synced, across $(printf '%s\n' "$state" | awk '{print $1}' | sort -u | wc -l | tr -d ' ') namespaces"
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    fail "only ${synced}/${total} ExternalSecrets synced within ${WAIT_SECS}s"
    printf '%s\n' "$state" | awk '$3!="True"{print "  NOT SYNCED: "$1"/"$2" Ready="($3==""?"<none>":$3)}' >&2
    kubectl get clustersecretstore gibson-secrets \
      -o jsonpath='{.status.conditions[*].reason}: {.status.conditions[*].message}{"\n"}' >&2
    if ! kubectl -n "$NS" logs -l app.kubernetes.io/name=external-secrets \
         --tail=20 >&2; then
      echo "(external-secrets logs unavailable)" >&2
    fi
    exit 1
  fi
  sleep 10
done

# --- 4. The store survives a restart ---------------------------------------
# The point of the static seal: a killed OpenBao must come back
# ready-and-unsealed with no human action and no unseal key in the cluster.
# Rendering the chart proves nothing; only the round trip does.
log "restart round-trip: killing OpenBao and expecting it back unsealed"
kubectl -n "$NS" delete pod "$POD" --wait=true --timeout=180s
kubectl -n "$NS" wait --for=condition=Ready "pod/$POD" --timeout=300s

restart_deadline=$(( $(date +%s) + 180 ))
while :; do
  status="$(seal_status)" || status=""
  case "$status" in
    *'"initialized":true'*'"sealed":false'*) log "OpenBao returned unsealed with no human action"; break ;;
  esac
  if [ "$(date +%s)" -ge "$restart_deadline" ]; then
    fail "OpenBao did not auto-unseal after restart"
    echo "last seal-status: ${status:-<unreachable>}" >&2
    if ! kubectl -n "$NS" logs "$POD" -c openbao-auto-init --tail=40 >&2; then
      echo "(sidecar logs unavailable)" >&2
    fi
    exit 1
  fi
  sleep 5
done

# --- 5. KEYRING DRILL: without the keyring the store cannot be opened -----
# "Proven, not assumed" (deploy#1731). The seal key is a member of
# the bringup keyring held on the host. Swap the in-cluster copy for a key that
# never sealed this store and the store must come up SEALED and stay sealed
# (measured on 2.5.3: Initialized=true, Sealed=true, "cipher: message
# authentication failed" every 5 s). Put the keyring back and it must open
# again. That is the whole recovery story: the archive plus the keyring.
if [ -n "${KEYRING_FILE:-}" ] && [ "${SKIP_RESTORE_DRILL:-}" != "1" ]; then
  KEYRING_SECRET="${KEYRING_SECRET:-bringup-keyring}"
  KEYRING_KEY="${KEYRING_KEY:-openbao-seal-key}"
  log "keyring drill: starting the store under a key that never sealed it"
  kubectl -n "$NS" create secret generic "$KEYRING_SECRET" \
    --from-literal="${KEYRING_KEY}=$(head -c 32 /dev/urandom | base64 | tr -d '\n')" \
    --dry-run=client -o yaml | kubectl apply -n "$NS" -f - >/dev/null
  kubectl -n "$NS" delete pod "$POD" --wait=true --timeout=180s

  log "confirming OpenBao comes up SEALED without the keyring"
  sealed_seen=0
  drill_deadline=$(( $(date +%s) + 240 ))
  while [ "$(date +%s)" -lt "$drill_deadline" ]; do
    status="$(seal_status)" || status=""
    case "$status" in
      *'"initialized":true'*'"sealed":true'*) sealed_seen=1; break ;;
    esac
    sleep 5
  done
  if [ "$sealed_seen" != "1" ]; then
    fail "OpenBao did not come up sealed under a foreign key — the drill proves nothing"
    echo "last seal-status: ${status:-<unreachable>}" >&2
    exit 1
  fi
  log "sealed, as expected"

  log "putting the keyring back from ${KEYRING_FILE}"
  kubectl -n "$NS" create secret generic "$KEYRING_SECRET" \
    --from-literal="${KEYRING_KEY}=$(grep -E '^OPENBAO_SEAL_KEY=' "$KEYRING_FILE" | head -n1 | cut -d= -f2-)" \
    --dry-run=client -o yaml | kubectl apply -n "$NS" -f - >/dev/null
  kubectl -n "$NS" delete pod "$POD" --wait=true --timeout=180s
  restored=0
  drill_deadline=$(( $(date +%s) + 240 ))
  while [ "$(date +%s)" -lt "$drill_deadline" ]; do
    status="$(seal_status)" || status=""
    case "$status" in
      *'"initialized":true'*'"sealed":false'*) restored=1; break ;;
    esac
    sleep 5
  done
  if [ "$restored" != "1" ]; then
    fail "the keyring did NOT open the sealed store"
    echo "last seal-status: ${status:-<unreachable>}" >&2
    exit 1
  fi
  log "the keyring opened the sealed store"
fi

if [ -n "${KEYRING_FILE:-}" ] && [ "${SKIP_RESTORE_DRILL:-}" != "1" ]; then
  printf '\n\033[1;32m✅ vanilla install verified: OpenBao bootstrapped, one backend store, %s ExternalSecrets synced in every namespace, the store survives a restart, and it opens only under the bringup keyring\033[0m\n' "$total"
else
  printf '\n\033[1;32m✅ vanilla install verified: OpenBao bootstrapped, one backend store, %s ExternalSecrets synced in every namespace, the store survives a restart (keyring drill NOT run)\033[0m\n' "$total"
fi
