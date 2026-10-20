#!/usr/bin/env bash
# The fleet federation exit test (setec#251, ADR-0164). The workflow
# exit-test-fleet-federation.yml runs it after published-install.yml has
# installed the chart source with tests/federation/install-values.yaml.
#
# One kind cluster then runs three SPIFFE trust domains:
#
#   install   the trust domain of the platform (global.spire.trustDomain).
#             Its daemon dials the outside setec fleet.
#   fleet     fleet.test: a SPIRE server and a setec fleet whose frontend
#             enrolls the daemon (frontend.clients).
#   outside   outside.test: a SPIRE server whose caller trusts the fleet,
#             and whom the fleet does not trust.
#
# Each federation comes from chart values: the gibson chart (sandbox.setec
# federation, spire.spire-server.federation), the setec chart
# (frontend.clients[].federation) and the SPIRE chart
# (controllerManager.identities). The test writes no federation object by
# hand.
#
# It proves two things:
#   1. a workload with the SPIFFE ID of the daemon reaches the fleet
#      frontend and gets an application answer;
#   2. the caller of the outside domain is refused by the frontend in the
#      TLS handshake ("remote error: tls:"), and the caller does not reject
#      the frontend.
#
# The caller of 1 runs from a Job in the release namespace with the labels
# and the ServiceAccount of the daemon, so the SPIRE controller of the
# install gives it the registration of the daemon. The identity admission
# policy admits that Job because the installing identity of this cluster
# writes it (spire.identityAdmission.workloadCreators, scripts/baseline-up.sh).
#
# Environment: NS (the release namespace). With no
# SETEC_DIR it fetches setec at SETEC_REF and builds the frontend and the
# client into the kind cluster KIND_CLUSTER (default gibson).
set -euo pipefail

: "${NS:?set NS, the release namespace}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d)"
# shellcheck source=tests/federation/lib.sh
. "$(dirname "$0")/lib.sh"

# The setec fleet and the federation client come from one setec commit: the
# merge of setec#335, which added the client (test/federation/client). The
# frontend is built from the same commit, so its chart and its image agree.
SETEC_REF=f53e890e6326b051fba8e2e9b904de650b0c0889
KIND_CLUSTER="${KIND_CLUSTER:-gibson}"
FRONTEND_IMAGE="${FRONTEND_IMAGE:-setec-frontend:fed}"
CLIENT_IMAGE="${CLIENT_IMAGE:-setec-fedclient:fed}"
if [ -z "${SETEC_DIR:-}" ]; then
  SETEC_DIR="$WORK/setec"
  echo "::group::the setec fleet and the client at ${SETEC_REF}"
  git init -q "$SETEC_DIR"
  git -C "$SETEC_DIR" fetch -q --depth 1 https://github.com/zeroroot-ai/setec.git "$SETEC_REF"
  git -C "$SETEC_DIR" checkout -q FETCH_HEAD
  docker build -q --build-arg CMD=frontend -t "$FRONTEND_IMAGE" "$SETEC_DIR"
  docker build -q --build-arg CMD=fedclient --build-arg CMD_PATH=./test/federation/client \
    -t "$CLIENT_IMAGE" "$SETEC_DIR"
  kind load docker-image --name "$KIND_CLUSTER" "$FRONTEND_IMAGE" "$CLIENT_IMAGE"
  echo "::endgroup::"
fi

FLEET_NS=setec-fleet
FLEET_RELEASE=setec-fleet
FLEET_ID="spiffe://${TD_FLEET}/ns/${FLEET_NS}/sa/${FLEET_RELEASE}-frontend"
FLEET_ADDR="${FLEET_RELEASE}-frontend.${FLEET_NS}.svc.cluster.local:50051"

log() { echo "▶ $*"; }

diagnose() {
  echo "::group::diagnostics of the federation"
  kubectl get pods -A -o wide || true
  kubectl get clusterfederatedtrustdomains -o yaml || true
  kubectl get clusterspiffeids -o wide || true
  kubectl -n "$NS" exec gibson-server-0 -c spire-server -- /opt/spire/bin/spire-server federation list || true
  kubectl -n "$NS" exec gibson-server-0 -c spire-server -- /opt/spire/bin/spire-server bundle list -format spiffe || true
  kubectl -n "$NS" logs gibson-server-0 -c spire-server --tail=80 || true
  for x in fleet outside; do
    kubectl -n "spire-$x" exec "spire-$x-server-0" -c spire-server -- /opt/spire/bin/spire-server federation list || true
    kubectl -n "spire-$x" logs "spire-$x-server-0" -c spire-server --tail=80 || true
  done
  kubectl -n "$FLEET_NS" logs deploy/"$FLEET_RELEASE"-frontend --tail=80 || true
  kubectl -n "$NS" logs job/fed-daemon-caller --tail=-1 || true
  echo "::endgroup::"
}
trap 'rc=$?; if [ "$rc" -ne 0 ]; then diagnose; fi; rm -rf "$WORK"; exit "$rc"' EXIT

# The install trust domain and the SPIFFE ID of the daemon, from the
# registration that the chart rendered: no file holds the domain.
DAEMON_ID="$(kubectl get clusterspiffeid gibson-platform-daemon -o jsonpath='{.spec.spiffeIDTemplate}')"
TD_INSTALL="$(sed -E 's#^spiffe://([^/]+)/.*#\1#' <<<"$DAEMON_ID")"
[ -n "$TD_INSTALL" ] && [ "$TD_INSTALL" != "$DAEMON_ID" ] || { echo "::error::cannot read the trust domain of the daemon"; exit 1; }
log "install domain ${TD_INSTALL}, daemon ${DAEMON_ID}, fleet ${FLEET_ID}"

# The daemon registration lists the fleet domain (charts#755).
fw="$(kubectl get clusterspiffeid gibson-platform-daemon -o jsonpath='{.spec.federatesWith}')"
[[ "$fw" == *"\"${TD_FLEET}\""* ]] || { echo "::error::the daemon registration does not federate with ${TD_FLEET}: ${fw}"; exit 1; }

echo "::group::the SPIRE servers of the fleet domain and the outside domain"
# tests/federation/before.sh started the fleet server before the install,
# with no controller manager, because the SPIRE CRDs come with gibson-crds.
# Its controller manager starts now.
bundle spire-fleet spire-fleet-server-0 > "$WORK/fleet.json"
install_spire outside "$TD_OUT" 9984 -f - <<YAML
spire-server:
  controllerManager:
    identities:
      clusterSPIFFEIDs:
        default:
          federatesWith: ["${TD_FLEET}"]
      clusterFederatedTrustDomains:
        fleet:
          trustDomain: "${TD_FLEET}"
          bundleEndpointURL: "https://spire-fleet-server.spire-fleet.svc.cluster.local:8443"
          bundleEndpointProfile:
            type: https_spiffe
            endpointSPIFFEID: "spiffe://${TD_FLEET}/spire/server"
          trustDomainBundle: $(jq -c . "$WORK/fleet.json" | jq -Rs .)
YAML
echo "::endgroup::"

bundle "$NS" gibson-server-0 > "$WORK/install.json"

echo "::group::the setec fleet in ${TD_FLEET}"
# The fleet enrolls the daemon, with the bundle endpoint of the install.
jq -n --arg ns "$FLEET_NS" --arg td "$TD_FLEET" --arg daemon "$DAEMON_ID" \
  --arg tdi "$TD_INSTALL" --arg relns "$NS" --rawfile bundle "$WORK/install.json" \
  --arg image "${FRONTEND_IMAGE%%:*}" --arg tag "${FRONTEND_IMAGE##*:}" '{
    namespace: $ns,
    webhook: {enabled: false}, replicas: 0, podDisruptionBudget: {enabled: false},
    credentials: {spiffe: {
      trustDomain: $td, className: "spire-fleet-spire-fleet",
      socketPath: "/run/spire/agent-sockets-fleet/spire-agent.sock"}},
    frontend: {enabled: true, image: {repository: $image, tag: $tag, pullPolicy: "Never"},
      clients: [{name: "install",
        spiffeID: $daemon,
        federation: {
          bundleEndpointURL: ("https://gibson-server." + $relns + ".svc.cluster.local:8443"),
          bundleEndpointProfile: "https_spiffe",
          endpointSPIFFEID: ("spiffe://" + $tdi + "/spire/server"),
          trustDomainBundle: $bundle}}]},
    systemPolicy: {frontendCallers: [
      {namespace: $relns, podLabels: {"app.kubernetes.io/component": "daemon"}},
      {namespace: "client-outside", podLabels: {app: "fed-client"}}]}}' > "$WORK/fleet-values.json"
kubectl create namespace "$FLEET_NS" --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install "$FLEET_RELEASE" "$SETEC_DIR/charts/setec" --namespace "$FLEET_NS" --skip-crds \
  -f "$SETEC_DIR/hack/chart-launcher-values.yaml" -f "$WORK/fleet-values.json"
kubectl -n "$FLEET_NS" rollout status deploy/"$FLEET_RELEASE"-frontend --timeout=300s
# The controller manager of the fleet domain starts, and the fleet
# workloads get the bundle of the install domain. The setec chart wrote the
# federation with the install domain above, so SPIRE holds that bundle when
# it reads the registration.
helm upgrade spire-fleet spire --repo "$SPIRE_REPO" --version "$SPIRE_VERSION" \
  --namespace spire-fleet --reuse-values \
  --set spire-server.controllerManager.enabled=true \
  --set "spire-server.controllerManager.identities.clusterSPIFFEIDs.default.federatesWith={${TD_INSTALL}}" \
  --wait --timeout 10m
kubectl -n "$FLEET_NS" rollout restart deploy/"$FLEET_RELEASE"-frontend
kubectl -n "$FLEET_NS" rollout status deploy/"$FLEET_RELEASE"-frontend --timeout=300s
echo "::endgroup::"

echo "::group::a workload with the SPIFFE ID of the daemon reaches the fleet"
kubectl -n "$NS" delete job fed-daemon-caller --ignore-not-found --wait=true
kubectl -n "$NS" apply -f - <<YAML
apiVersion: batch/v1
kind: Job
metadata:
  name: fed-daemon-caller
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 900
  template:
    metadata:
      labels:
        app.kubernetes.io/component: daemon
        gibson.zeroroot.ai/net-role: platform
        gibson.zeroroot.ai/egress-namespaces: "true"
    spec:
      serviceAccountName: gibson
      restartPolicy: Never
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
        runAsGroup: 65532
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: client
          image: ${CLIENT_IMAGE}
          imagePullPolicy: Never
          args:
            - --spiffe-socket=/run/spire/sockets/api.sock
            - --addr=${FLEET_ADDR}
            - --server-spiffe-id=${FLEET_ID}
            - --expect=accepted
            - --window=10m
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: spire-agent-socket
              mountPath: /run/spire/sockets
              readOnly: true
      volumes:
        - name: spire-agent-socket
          csi:
            driver: csi.spiffe.io
            readOnly: true
YAML
kubectl -n "$NS" wait --for=condition=complete job/fed-daemon-caller --timeout=900s \
  || { kubectl -n "$NS" logs job/fed-daemon-caller --tail=-1 || true; echo "::error::the daemon identity was not accepted by the fleet"; exit 1; }
kubectl -n "$NS" logs job/fed-daemon-caller --tail=-1
kubectl -n "$NS" delete job fed-daemon-caller --wait=false
echo "::endgroup::"

echo "::group::the caller of ${TD_OUT}, which the fleet does not trust, is refused"
kubectl create namespace client-outside --dry-run=client -o yaml | kubectl apply -f -
kubectl -n client-outside create serviceaccount client --dry-run=client -o yaml | kubectl apply -f -
kubectl -n client-outside delete pod fed-client --ignore-not-found --wait=true
kubectl apply -f - <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: fed-client
  namespace: client-outside
  labels:
    app: fed-client
spec:
  serviceAccountName: client
  restartPolicy: Never
  containers:
    - name: client
      image: ${CLIENT_IMAGE}
      imagePullPolicy: Never
      args:
        - --spiffe-socket=/run/spire/agent-sockets-outside/spire-agent.sock
        - --addr=${FLEET_ADDR}
        - --server-spiffe-id=${FLEET_ID}
        - --expect=refused
        - --window=1m30s
      volumeMounts:
        - name: spire
          mountPath: /run/spire/agent-sockets-outside
          readOnly: true
  volumes:
    - name: spire
      hostPath:
        path: /run/spire/agent-sockets-outside
        type: Directory
YAML
phase=""
for _ in $(seq 1 120); do
  phase="$(kubectl -n client-outside get pod fed-client -o jsonpath='{.status.phase}' 2>/dev/null || true)"
  if [ "$phase" = Succeeded ] || [ "$phase" = Failed ]; then break; fi
  sleep 5
done
kubectl -n client-outside logs fed-client --tail=-1 || true
[ "$phase" = Succeeded ] || { echo "::error::the outside caller was not refused by the fleet in the TLS handshake (phase ${phase})"; exit 1; }
echo "::endgroup::"

echo "PASS: the fleet in ${TD_FLEET} accepted the daemon of ${TD_INSTALL} and refused ${TD_OUT}"
