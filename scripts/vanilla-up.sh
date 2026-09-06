#!/usr/bin/env bash
# vanilla-up.sh — stand the platform up on a plain Kubernetes cluster, in one
# command, with no manual steps.
#
# This is THE install path. The exit-test workflow runs this same script rather
# than its own copy of the steps, so "it works in CI" and "it works on my
# cluster" cannot drift apart, and anything discovered here is captured here
# instead of living in someone's shell history.
#
# It installs onto the CURRENT kube context: the cluster is stage 1 and is
# not this script's job. On kind, `make recreate ENV=kind` is the bringup
# verb that creates the cluster, runs the gVisor node prep and then installs
# the platform (deploy#1737). An operator on another cluster brings
# a cluster that already carries the stage 1 rows (docs/bringup.md).
#
# Usage:
#   scripts/vanilla-up.sh                 # use the current kube context
#
# Env:
#   NS             namespace to install into            (default: gibson)
#   RELEASE        helm release name                    (default: gibson)
#   CHART_DIR      chart root                           (default: helm)
#   VALUES         profile values file                  (default: helm/gibson/values-vanilla.yaml)
#   SUBSTRATE_ENV  stage 0 output, read for the bucket  (default: ${SUBSTRATE_DIR:-/bulk/substrate}/kind/substrate.env)
#   TIMEOUT        per-step helm timeout                (default: 10m)
set -euo pipefail

NS="${NS:-gibson}"
RELEASE="${RELEASE:-gibson}"
CHART_DIR="${CHART_DIR:-helm}"
VALUES="${VALUES:-helm/gibson/values-vanilla.yaml}"
# EXTRA_VALUES: optional space-separated list of additional helm values overlays
# layered on top of $VALUES, last-wins. The committed vanilla profile digest-pins
# first-party images to the current release (it must mirror a real customer
# install). A tester validating unreleased code on a kind-vanilla cluster passes
# an overlay here that floats those images to a moving tag, e.g.
#   EXTRA_VALUES=/tmp/main-images.yaml make recreate ENV=kind
# so nothing has to edit the release-mirror file. Absent, the install is
# byte-for-byte the customer path.
EXTRA_VALUES="${EXTRA_VALUES:-}"
EXTRA_VALUES_ARGS=()
for _ov in $EXTRA_VALUES; do EXTRA_VALUES_ARGS+=(-f "$_ov"); done
TIMEOUT="${TIMEOUT:-10m}"
# ToolHive operator version. Pinned; bump deliberately — the
# ConnectorInstance wrapper absorbs the change. Serves v1alpha1 as of 0.12.1.
TOOLHIVE_VERSION="${TOOLHIVE_VERSION:-0.12.1}"

log() { printf '\n\033[1;32m▶\033[0m %s\n' "$*"; }

# wait_crd_established <crd-name> — wait for a CRD to exist AND be Established.
#
# `kubectl wait --for=condition=Established crd/X` does NOT wait for the object
# to be created: if the CRD is absent the instant it runs, it exits non-zero
# with "Error from server (NotFound)" and never retries. helm --wait returns
# once a chart's workloads are Ready, which can be a moment before the apiserver
# has registered that chart's freshly-applied CRDs — a sub-second race that
# aborted the whole install under `set -e`. Poll for the object first, then wait
# for the condition. (`kubectl wait --for=create` would do this in one call, but
# it needs kubectl >= 1.31 and the install must not assume a client that new.)
wait_crd_established() {
  local crd="$1" deadline=$(( SECONDS + 120 ))
  until kubectl get "crd/$crd" >/dev/null 2>&1; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "timed out after 120s waiting for CRD $crd to be created" >&2
      return 1
    fi
    sleep 2
  done
  kubectl wait --for=condition=Established "crd/$crd" --timeout=120s
}

log "target cluster"
# NOT `| head -1`: head exits after one line, kubectl takes SIGPIPE, and
# `set -o pipefail` turns that into a fatal 141 before anything is installed.
# sed reads its input to the end, so there is no broken pipe.
kubectl cluster-info 2>/dev/null | sed -n '1p'


# ---------------------------------------------------------------------------
# The principal that applies the chart.
#
# The gibson-platform-identity-workloads ValidatingAdmissionPolicy refuses any
# pod template claiming a platform identity unless the writer is listed in
# spire.identityAdmission.workloadCreators. The profile ships ZeroRoot's Argo CD
# controller ServiceAccount, which does not exist on anyone else's cluster, so
# an unmodified install is denied with "written by <principal>, which is not a
# permitted creator".
#
# It is discovered, never typed: SelfSubjectReview reports exactly the username
# the policy compares against.
# ---------------------------------------------------------------------------
log "discovering the installing principal"
PRINCIPAL="$(kubectl create --raw /apis/authentication.k8s.io/v1/selfsubjectreviews -f - <<'EOF' |
{"apiVersion":"authentication.k8s.io/v1","kind":"SelfSubjectReview"}
EOF
  sed -n 's/.*"username":"\([^"]*\)".*/\1/p')"
[ -n "$PRINCIPAL" ] || { echo "FATAL: could not determine the installing principal" >&2; exit 1; }
echo "principal: ${PRINCIPAL}"

# ---------------------------------------------------------------------------
# The pinned Envoy ClusterIP.
#
# The chart REQUIRES one: consuming pods reach Envoy through a hostAlias, and a
# hostAlias takes an IP, not a Service name (deploy#200). That IP has to sit
# inside the cluster's Service CIDR, which differs per distribution — kind and
# kubeadm use 10.96.0.0/12, EKS uses 172.20.0.0/16. A profile can only ship one
# guess, and the wrong guess is fatal: the API server refuses the Service with
# "failed to allocate IP ...: the provided IP is not in the valid range"
# (deploy#1627).
#
# So it is DISCOVERED. The `kubernetes` Service in `default` is always the first
# address of the Service CIDR, which makes it a reliable anchor on any
# distribution without asking the operator what their CIDR is.
log "discovering the Service CIDR"
K8S_SVC_IP="$(kubectl get svc kubernetes -n default -o jsonpath='{.spec.clusterIP}')"
case "$K8S_SVC_IP" in
  *.*.*.*) ;;
  *) echo "FATAL: could not read the kubernetes Service ClusterIP" >&2; exit 1 ;;
esac
ENVOY_CLUSTER_IP="${ENVOY_CLUSTER_IP:-${K8S_SVC_IP%.*.*}.0.250}"
echo "service CIDR anchor: ${K8S_SVC_IP} -> pinning envoy at ${ENVOY_CLUSTER_IP}"

log "building chart dependencies (bottom-up)"
helm dependency update "${CHART_DIR}/gibson-crds" >/dev/null
helm dependency update "${CHART_DIR}/gibson-operators" >/dev/null
helm dependency update "${CHART_DIR}/gibson-workloads" >/dev/null
helm dependency update "${CHART_DIR}/gibson" >/dev/null
# gibson-velero is its own release (deploy#1762) with a remote `velero`
# dependency, so it needs the same build the four charts above get. Without
# this line `helm upgrade --install velero` fails in seconds with "found in
# Chart.yaml, but missing in charts/ directory: velero" — exit-test run
# 33572682175, the first run after deploy#1762 merged. The Makefile targets
# carry the stamp (CHART_DEPS_VELERO); this script is the one other place
# that builds dependencies, and it must stay complete.
helm dependency update "${CHART_DIR}/gibson-velero" >/dev/null

# ---------------------------------------------------------------------------
# Cluster prerequisite: ToolHive.
#
# cert-manager, external-secrets, external-dns and the CloudNativePG operator
# are subcharts of the umbrella (deploy#1728) and their CRDs ship in
# gibson-crds, so nothing installs them ahead of the platform any more.
# ToolHive is the one operator still installed here: the gibson
# connector-operator renders MCPServer / MCPRemoteProxy CRs, and ToolHive is a
# third-party release with its own CRD chart, not an umbrella dependency.
# ---------------------------------------------------------------------------
log "prerequisite: toolhive"
# ToolHive is the runtime for third-party MCP connectors. The version
# is pinned; the ConnectorInstance wrapper absorbs a ToolHive upgrade. ToolHive
# serves toolhive.stacklok.dev/v1alpha1 as of this version. The CRD chart
# installs first, then the operator, into the same release namespace.
helm upgrade --install toolhive-operator-crds \
  oci://ghcr.io/stacklok/toolhive/toolhive-operator-crds \
  --namespace toolhive-system --create-namespace --version "$TOOLHIVE_VERSION" \
  --wait --timeout "$TIMEOUT"
helm upgrade --install toolhive-operator \
  oci://ghcr.io/stacklok/toolhive/toolhive-operator \
  --namespace toolhive-system --version "$TOOLHIVE_VERSION" \
  --wait --timeout "$TIMEOUT"
wait_crd_established mcpservers.toolhive.stacklok.dev

log "phase 1 — CRDs"
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"
helm upgrade --install gibson-crds "${CHART_DIR}/gibson-crds" \
  --namespace "$NS" --wait --timeout 5m
# Established, not merely created: a CRD the API server has not accepted yet is
# indistinguishable from a missing one when the umbrella's manifests are
# validated, which is the exact failure this phase exists to prevent.
# The operator CRDs the umbrella renders CRs for come from this release too
# (deploy#1728): one of each family is enough to prove the vendored set landed.
for crd in platformbootstraps.gibson.zeroroot.ai \
           clusterspiffeids.spire.spiffe.io \
           servicemonitors.monitoring.coreos.com \
           certificates.cert-manager.io \
           externalsecrets.external-secrets.io \
           clusters.postgresql.cnpg.io; do
  wait_crd_established "$crd"
done

# ---------------------------------------------------------------------------
# Operator-supplied seed inputs (deploy#1732). The registry pull token and the
# LLM keys are members of the bringup keyring (GHCR_PULL_TOKEN,
# LLM_KEYS_JSON, scripts/keyring.sh). scripts/keyring-to-cluster.sh writes
# them into the `bringup-keyring` Secret below, next to the bucket and seal
# members, and the openbao-auto-init sidecar copies them into OpenBao before
# any workload starts. There is no background loop and no second path: the old GHCR_TOKEN
# environment variable is refused so a caller cannot rely on it.
# ---------------------------------------------------------------------------
if [ -n "${GHCR_TOKEN:-}" ]; then
  cat >&2 <<MSG
FATAL: GHCR_TOKEN is set. The registry pull token now arrives through the
bringup keyring (deploy#1732). Put it there and rerun:

  GHCR_PULL_TOKEN=<token> make substrate ENV=kind      # kind stage 0 refreshes the member
  scripts/keyring.sh set <keyring-file> GHCR_PULL_TOKEN <token>   # any keyring

MSG
  exit 1
fi

# ---------------------------------------------------------------------------
# Stage 0 → the cluster (deploy#1730, deploy#1731, deploy#1732,
# deploy#1734, deploy#1737). The chart renders no Secret for the bucket
# credential, the OpenBao seal key, the Velero repository password or the
# two seed inputs: the bringup writes them from the keyring stage 0 wrote,
# BEFORE the chart, through the one producer scripts/keyring-to-cluster.sh
# (`make recreate` runs the same script). Stage 0 is `make substrate
# ENV=kind` on a workstation or a CI runner; an operator on-prem brings a
# substrate.env and keyring of the same shape
# (docs/runbooks/substrate-kind.md). There is no default bucket: a Postgres
# whose archive_command points at nothing fills its WAL volume and stops
# accepting writes.
# ---------------------------------------------------------------------------
SUBSTRATE_ENV="${SUBSTRATE_ENV:-${SUBSTRATE_DIR:-/bulk/substrate}/kind/substrate.env}"
if [ ! -s "$SUBSTRATE_ENV" ]; then
  cat >&2 <<MSG
FATAL: no substrate.env at ${SUBSTRATE_ENV}.

Stage 0 has not run. The platform's Postgres archives to the durable bucket
on every profile, so the install needs the bucket endpoint, the
bucket name and the keyring that stage 0 writes. Run:

  make substrate ENV=kind

or set SUBSTRATE_ENV to a file of that shape (BUCKET_ENDPOINT, BUCKET_NAME,
KEYRING_FILE) for a bucket you brought yourself.
MSG
  exit 1
fi
substrate_get() { grep -E "^$1=" "$SUBSTRATE_ENV" | head -n1 | cut -d= -f2-; }
BUCKET_ENDPOINT="$(substrate_get BUCKET_ENDPOINT)"
BUCKET_NAME="$(substrate_get BUCKET_NAME)"
KEYRING_FILE="${KEYRING_FILE:-$(substrate_get KEYRING_FILE)}"
log "stage 0: bucket s3://${BUCKET_NAME} at ${BUCKET_ENDPOINT}"
NS="$NS" KEYRING_FILE="$KEYRING_FILE" "$(dirname "$0")/keyring-to-cluster.sh" "$SUBSTRATE_ENV"
BUCKET_ARGS=(
  --set "platformPostgres.backup.destinationPath=s3://${BUCKET_NAME}/backups/postgres/"
  --set "platformPostgres.backup.endpointURL=${BUCKET_ENDPOINT}"
)

log "phase 1b — velero (its own release, namespace velero)"
helm upgrade --install velero "${CHART_DIR}/gibson-velero" \
  --namespace velero \
  --set "bucket.name=${BUCKET_NAME}" \
  --set "bucket.endpoint=${BUCKET_ENDPOINT}" \
  --wait --timeout 10m

log "phase 2 — the platform"
helm upgrade --install "$RELEASE" "${CHART_DIR}/gibson" \
  -f "$VALUES" \
  "${EXTRA_VALUES_ARGS[@]}" \
  "${BUCKET_ARGS[@]}" \
  --set-json "gibson-workloads.spire.identityAdmission.workloadCreators=[\"${PRINCIPAL}\"]" \
  --set "gibson-workloads.envoy.service.clusterIP=${ENVOY_CLUSTER_IP}" \
  --set "gibson-workloads.dashboard.envoy.service.clusterIP=${ENVOY_CLUSTER_IP}" \
  --namespace "$NS" --timeout 30m

log "waiting for OpenBao to bootstrap and ESO to converge"
# The keyring drill (swap the seal key, expect sealed; restore it, expect
# unsealed) is NOT run here: it rewrites the live keyring Secret twice, and
# this script may target a customer's cluster. The exit tests run it against
# their disposable kind fixture with KEYRING_FILE set (make vanilla-verify).
scripts/vanilla-verify.sh
