#!/usr/bin/env bash
# guest-operators-up.sh — make a cluster a GUEST host: install the operators
# that values-guest.yaml expects the cluster to own (ADR-0087).
#
# values-guest.yaml turns off the four operator seams of the umbrella:
# cert-manager, External Secrets, external-dns and CloudNativePG. The cluster
# then supplies each operator and its CRDs. This script installs them, each in
# its own namespace and release, at the versions the umbrella pins in
# helm/gibson/Chart.yaml, plus the Prometheus Operator, so that the chart's
# ServiceMonitors and PrometheusRules render and apply.
#
# The exit test exit-test-published-guest-kind runs it on a disposable kind
# cluster before `OVERLAY=guest scripts/baseline-up.sh`.
#
# Usage: scripts/guest-operators-up.sh        (the current kube context)
set -euo pipefail

TIMEOUT="${TIMEOUT:-10m}"
# The versions the umbrella pins (helm/gibson/Chart.yaml), so the guest host
# runs what the chart would have run.
CHART_YAML="$(dirname "$0")/../helm/gibson/Chart.yaml"
dep_version() {
  python3 - "$CHART_YAML" "$1" <<'PY'
import sys, yaml
for d in yaml.safe_load(open(sys.argv[1]))["dependencies"]:
    if d["name"] == sys.argv[2]:
        print(d["version"]); break
else:
    sys.exit(f"no dependency {sys.argv[2]} in {sys.argv[1]}")
PY
}
CERT_MANAGER_VERSION="$(dep_version cert-manager)"
EXTERNAL_SECRETS_VERSION="$(dep_version external-secrets)"
EXTERNAL_DNS_VERSION="$(dep_version external-dns)"
CNPG_VERSION="$(dep_version cloudnative-pg)"
# Not an umbrella dependency: the chart ships no monitoring stack (ADR-0087).
KUBE_PROMETHEUS_STACK_VERSION="${KUBE_PROMETHEUS_STACK_VERSION:-91.9.0}"

log() { printf '\n==> %s\n' "$*"; }

log "cert-manager ${CERT_MANAGER_VERSION}"
helm upgrade --install cert-manager cert-manager --repo https://charts.jetstack.io \
  --version "$CERT_MANAGER_VERSION" --namespace cert-manager --create-namespace \
  --set crds.enabled=true --wait --timeout "$TIMEOUT"

log "External Secrets ${EXTERNAL_SECRETS_VERSION}"
helm upgrade --install external-secrets external-secrets --repo https://charts.external-secrets.io \
  --version "$EXTERNAL_SECRETS_VERSION" --namespace external-secrets --create-namespace \
  --set installCRDs=true --wait --timeout "$TIMEOUT"

log "external-dns ${EXTERNAL_DNS_VERSION} (inmemory provider, as the baseline sets)"
helm upgrade --install external-dns external-dns --repo https://kubernetes-sigs.github.io/external-dns/ \
  --version "$EXTERNAL_DNS_VERSION" --namespace external-dns --create-namespace \
  --set provider.name=inmemory --wait --timeout "$TIMEOUT"

log "CloudNativePG ${CNPG_VERSION}"
helm upgrade --install cnpg oci://ghcr.io/cloudnative-pg/charts/cloudnative-pg \
  --version "$CNPG_VERSION" --namespace cnpg-system --create-namespace \
  --wait --timeout "$TIMEOUT"

log "Prometheus Operator (kube-prometheus-stack ${KUBE_PROMETHEUS_STACK_VERSION}, the operator only)"
helm upgrade --install prometheus-operator kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts \
  --version "$KUBE_PROMETHEUS_STACK_VERSION" --namespace monitoring --create-namespace \
  --set grafana.enabled=false --set alertmanager.enabled=false --set prometheus.enabled=false \
  --set nodeExporter.enabled=false --set kubeStateMetrics.enabled=false \
  --set defaultRules.create=false --set kubernetesServiceMonitors.enabled=false \
  --wait --timeout "$TIMEOUT"

log "the guest host runs five operators"
kubectl get deploy -A | grep -E "cert-manager|external-secrets|external-dns|cnpg|prometheus-operator"
