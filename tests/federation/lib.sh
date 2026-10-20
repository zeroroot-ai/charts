# shellcheck shell=bash
# The shared names of the fleet federation exit test (before.sh, run.sh).
# The trust domains of the test are here and nowhere else.

SPIRE_REPO=https://spiffe.github.io/helm-charts-hardened
SPIRE_VERSION=0.28.4
TD_FLEET=fleet.test
TD_OUT=outside.test

# install_spire <name> <trust domain> <health port of the agent> [helm args...]
# The agents use the host network, so each one needs a port of its own and
# a socket directory of its own. The CRDs are the ones gibson-crds installed.
install_spire() {
  local x="$1" td="$2" port="$3"; shift 3
  kubectl create namespace "spire-$x" --dry-run=client -o yaml | kubectl apply -f -
  helm upgrade --install "spire-$x" spire --repo "$SPIRE_REPO" --version "$SPIRE_VERSION" \
    --namespace "spire-$x" \
    --set global.spire.trustDomain="$td" \
    --set global.spire.clusterName="fed-$x" \
    --set global.spire.namespaces.server.name="spire-$x" \
    --set global.spire.namespaces.system.name="spire-$x" \
    --set spiffe-csi-driver.enabled=false \
    --set spiffe-oidc-discovery-provider.enabled=false \
    --set "spire-agent.socketPath=/run/spire/agent-sockets-$x/spire-agent.sock" \
    --set "spire-agent.healthChecks.port=$port" \
    "$@" --wait --timeout 10m
}

bundle() { # <namespace> <server pod>: the SPIFFE bundle of its trust domain
  kubectl -n "$1" exec "$2" -c spire-server -- /opt/spire/bin/spire-server bundle show -format spiffe
}

