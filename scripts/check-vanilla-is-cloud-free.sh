#!/usr/bin/env bash
# check-vanilla-is-cloud-free.sh — the vanilla profile must assume no cloud.
#
# ADR-0010 makes the vanilla Kubernetes cluster the supported self-hosted
# target, with EKS as one specialisation layered on top. The failure mode this
# guards is quiet: someone adds an IRSA annotation or a gp3 storage class to
# the base "because that is where the other one had it", and the profile
# silently stops installing anywhere but AWS. That is exactly how the previous
# file came to be named self-hosted while meaning self-hosted-on-AWS.
#
# It checks the RENDER, not the values file, because a cloud assumption can
# arrive through a sub-chart default just as easily as through this profile.
#
# Usage: scripts/check-vanilla-is-cloud-free.sh
# Exit:  0 clean · 1 a cloud assumption reached the vanilla render
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT

helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-vanilla.yaml" \
  --namespace gibson > "$RENDER"

# Patterns that mean "this only works on a cloud". Deliberately narrow: they
# match configuration we emit, not prose. Vendored CRDs document cloud fields
# in their descriptions and must not trip this.
fail=0
check() {
  local pattern="$1" why="$2"
  # Ignore matches inside CRD `description:` prose.
  local hits
  hits="$(grep -nE "$pattern" "$RENDER" | grep -viE "description:|^\s*#" || true)"
  if [ -n "$hits" ]; then
    echo "❌ $why"
    echo "$hits" | head -5 | sed 's/^/    /'
    fail=1
  fi
}

check 'eks\.amazonaws\.com/role-arn' \
  "IRSA annotation in the vanilla render — a non-EKS cluster cannot honour it. Put it in the substrate overlay (helm/gibson/values-eks.yaml)."
check 'service\.beta\.kubernetes\.io/aws-load-balancer' \
  "AWS load-balancer annotation in the vanilla render — inert off EKS. Put it in the substrate overlay (helm/gibson/values-eks.yaml)."
check '(storageClass|storageClassName): *"?gp3"?' \
  "gp3 storage class in the vanilla render — leave it empty so the cluster default applies."
check 'arn:aws:(kms|iam|secretsmanager)' \
  "An AWS ARN in the vanilla render — the vanilla profile must not name cloud resources."
check 'service: *SecretsManager' \
  "AWS Secrets Manager as the secret backend — the platform's own OpenBao is the one backend on every substrate (ADR-0015)."
# 172.20.0.0/16 is the EKS Service CIDR. kubeadm and kind default to
# 10.96.0.0/12, so a pinned ClusterIP from the EKS range is not merely
# suboptimal off EKS — the API server REJECTS the Service outright:
#   failed to allocate IP 172.20.0.250: the provided IP is not in the valid
#   range. The range of valid IPs is 10.96.0.0/16
# That is deploy#1627: the profile was extracted from the EKS one and carried
# the address across. No AWS name appears in it, so every check above passed.
check 'clusterIP: *"?172\.20\.' \
  "A ClusterIP from the EKS Service CIDR (172.20.0.0/16) in the vanilla render — kind and kubeadm use 10.96.0.0/12 and will refuse to allocate it. Pin one inside the target cluster's Service CIDR."

# ---------------------------------------------------------------------------
# Cloud-free is necessary but NOT sufficient. A profile can name no cloud
# resource and still be unusable, by suppressing an in-cluster datastore that
# something else assumes is there.
#
# That is deploy#1627: values-vanilla.yaml was extracted from the EKS profile
# and carried a flag across that suppressed the in-cluster CNPG Cluster, on
# the premise that terraform provisions a managed Postgres instead. On a
# vanilla cluster nothing does, so platform-postgres-rw had no cluster behind
# it, and the -6 postgres-setup pre-install hooks blocked on
# Cluster.status.currentPrimary forever. `helm install` hung for its full 25m
# timeout and then said only "failed pre-install: timed out waiting for the
# condition" — 25 minutes to learn nothing. The flag is gone (ADR-0015,
# deploy#1730) and this check stays as the structural statement of the rule.
#
# So: anything the render points AT, the render must also CREATE.
requires() {
  local pattern="$1" why="$2"
  if ! grep -Eq -- "$pattern" "$RENDER"; then
    echo "❌ $why"
    fail=1
  fi
}

# The chart references platform-postgres-rw from datastore DSNs, from SPIRE and
# Zitadel config, and from the postgres-setup hooks. If it is referenced, the
# Cluster that serves it has to be in the same render.
if grep -q "platform-postgres-rw" "$RENDER"; then
  requires '^kind: Cluster$' \
    "The vanilla render uses platform-postgres-rw but renders no CNPG Cluster. The Cluster is structural on every profile (ADR-0015); find what suppressed templates/postgres/platform-postgres-cluster.yaml."
fi

# Same rule for secrets: every ExternalSecret points at the gibson-secrets
# ClusterSecretStore, and deploy#1732 deleted the kind Terraform workspace's
# store, so nothing outside the chart makes one. The store must be a document
# in this render, not merely a name the ExternalSecrets repeat.
if grep -qE '^ +kind: ClusterSecretStore$' "$RENDER"; then
  if ! awk 'BEGIN{RS="\n---\n"} /(^|\n)kind: ClusterSecretStore\n/ && /name: "gibson-secrets"/{f=1} END{exit f?0:1}' "$RENDER"; then
    echo "❌ The vanilla render points ExternalSecrets at the gibson-secrets ClusterSecretStore but renders no such store. The store is structural on every profile (ADR-0015, deploy#1733)."
    fail=1
  fi
fi

# The structural rule that makes the umbrella installable at all: it renders NO
# CustomResourceDefinitions. CRDs belong to the gibson-crds release, which is
# installed first.
#
# This is not stylistic. Helm resolves every rendered manifest's kind against
# the live API server BEFORE it applies anything, so a CRD and a CR of that CRD
# can never share a release — no apply ordering fixes it, and Helm's kind-sorter
# does not help because the failure happens earlier, at manifest build. Every
# CRD that appears here is therefore a CR away from making the chart
# uninstallable, and it fails as an opaque `no matches for kind "X"` on a
# customer's very first command.
#
# deploy#1627 hit this three times in one day: ClusterSPIFFEID, PlatformBootstrap
# and ServiceMonitor together, then postgresql.cnpg.io/v1 Cluster once the
# managed-Postgres flag was corrected and the chart finally rendered a Cluster CR.
crd_count="$(grep -c '^kind: CustomResourceDefinition' "$RENDER" || true)"
if [ "$crd_count" -ne 0 ]; then
  echo "❌ The umbrella render contains $crd_count CustomResourceDefinition(s)."
  echo "   CRDs belong to helm/gibson-crds, which installs as its own release first."
  echo "   A CRD here becomes an uninstallable chart the moment anything renders a CR of it:"
  grep -B2 '^kind: CustomResourceDefinition' "$RENDER" \
    | grep -E '^# Source:' | sort -u | head -5 | sed 's/^/    /'
  fail=1
fi

if [ "$fail" -ne 0 ]; then
  echo
  echo "The vanilla profile is the one a customer on OpenShift, Rancher, kind or"
  echo "bare metal installs. Anything cloud-specific belongs in the EKS overlay,"
  echo "and anything the render depends on must be in the render."
  exit 1
fi

echo "✅ vanilla render is cloud-free and self-contained"
