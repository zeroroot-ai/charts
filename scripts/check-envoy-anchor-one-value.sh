#!/usr/bin/env bash
# check-envoy-anchor-one-value.sh — every subchart that pins Envoy's ClusterIP
# is given the SAME discovered value.
#
# WHY THIS EXISTS
#
# The Envoy Service takes a fixed ClusterIP, and every pod that has to dial the
# platform by its public hostname gets a hostAlias pointing at it
# (gibson-common's gibson.hostAliases). A subchart builds that alias from ITS
# OWN `envoy.service.clusterIP`, so each subchart that declares one needs the
# value the installer discovered for this cluster.
#
# THE INSTALLER SET TWO OF THREE. gibson-workloads got it, the dashboard got
# it, and gibson-operators did not — so the tenant-operator kept the shipped
# default, 10.96.0.250.
#
# ON KIND THAT IS RIGHT BY COINCIDENCE. kind's Service CIDR is 10.96.0.0/12,
# so the stale default happened to be a real address and everything worked. k3s
# uses 10.43.0.0/16. There the tenant-operator's hostAlias pointed at an
# address that does not exist, and the failure surfaced nowhere near the cause:
#
#   first-tenant seed enqueue failed; will retry
#     error: entitlements: fetch token: OAuth2TokenSource:
#     Post "https://app.selfhosted.example.com/oauth/v2/token":
#     dial tcp 10.96.0.250:443: i/o timeout
#
# so no tenant was ever enqueued, no Tenant CR was created, and the first-admin
# Job timed out ten minutes later waiting for a Tenant to report a Zitadel org.
# Measured on zeroroot-ai/charts run 35104587335, k3d.
#
# THE LIST IS DERIVED, NEVER WRITTEN DOWN. It reads the paths out of the
# profile itself, so a subchart that starts pinning an anchor is covered the
# day it does, with no allowlist to re-pin.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PROFILE="$ROOT/helm/gibson/values-baseline.yaml"
INSTALLER="$ROOT/scripts/baseline-up.sh"

for f in "$PROFILE" "$INSTALLER"; do
  [ -r "$f" ] || { echo "check-envoy-anchor: cannot read $f" >&2; exit 2; }
done

# paths <file> — every values path ending in envoy.service.clusterIP.
paths() {
  python3 - "$1" <<'PY'
import sys, yaml
doc = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
def walk(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else k
            if p.endswith("envoy.service.clusterIP") and isinstance(v, str) and v:
                print(p)
            walk(v, p)
walk(doc)
PY
}

# --- failing fixture: a path the installer does not set must be caught ------
if printf 'gibson-nowhere.envoy.service.clusterIP\n' \
   | while read -r p; do grep -q -- "--set \"${p}=" "$INSTALLER" && echo found; done | grep -q found; then
  echo "✗ self-test: the installer appears to set a path that does not exist" >&2
  exit 2
fi
echo "✅ self-test: a path the installer does not set is seen as unset"

declare -a missing=()
n=0
while IFS= read -r p; do
  [ -n "$p" ] || continue
  n=$((n + 1))
  grep -q -- "--set \"${p}=" "$INSTALLER" || missing+=("$p")
done < <(paths "$PROFILE")

[ "$n" -gt 0 ] || { echo "✗ check-envoy-anchor: the profile declares no Envoy anchor at all; this guard is measuring nothing" >&2; exit 2; }

if [ ${#missing[@]} -gt 0 ]; then
  echo "✗ check-envoy-anchor: the installer does not pass the discovered anchor to:" >&2
  for p in "${missing[@]}"; do echo "    $p" >&2; done
  echo "  Those subcharts keep the profile's shipped default. That default is a" >&2
  echo "  kind Service CIDR address, so it is correct on kind and wrong on every" >&2
  echo "  other cluster, and the symptom appears far from the cause." >&2
  exit 1
fi

printf '✅ check-envoy-anchor: all %d subchart(s) pinning Envoy get the discovered value\n' "$n"
