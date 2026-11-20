#!/usr/bin/env bash
# check-tenant-neo4j-size.sh — the chart gives the tenant operator one default
# size for a tenant Neo4j, and no plan selects a size (gibson#1526).
#
# The chart had tenantNeo4j.tiers, a table keyed by plan name. It is deleted.
# Helm ignores a values key that no template reads, so an environment that
# still set tenantNeo4j.tiers would install the chart default with no message.
# This check proves three things:
#   1. each golden render has the tenant-neo4j-size ConfigMap with a storage,
#      a cpu and a memory quantity, and no key that names a plan;
#   2. a values file that sets tenantNeo4j.tiers fails the render;
#   3. a default size with a missing field fails the render.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fail=0

# check_render <render>: 0 when the render has a usable tenant-neo4j-size.
check_render() {
  python3 - "$1" <<'PY'
import re, sys, yaml
quantity = re.compile(r"^[0-9]+(\.[0-9]+)?(m|[KMGTPE]i?)?$")
found = False
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") != "ConfigMap":
        continue
    name = d["metadata"]["name"]
    if name == "tenant-neo4j-template":
        sys.exit("the render still has the tenant-neo4j-template ConfigMap of the plan table")
    if name != "tenant-neo4j-size":
        continue
    found = True
    data = d.get("data") or {}
    if sorted(data) != ["cpu", "memory", "storage"]:
        sys.exit(f"tenant-neo4j-size has the keys {sorted(data)}, want cpu, memory, storage")
    for key, value in data.items():
        if not quantity.match(str(value)):
            sys.exit(f"tenant-neo4j-size {key} is {value!r}, which is not a quantity")
if not found:
    sys.exit("no ConfigMap tenant-neo4j-size in the render")
PY
}

for f in "$ROOT"/helm/testdata/golden/values-*.yaml; do
  check_render "$f" 2> "$WORK/err" || { echo "❌ $(basename "$f"): $(cat "$WORK/err")"; fail=1; }
done

# FAILING FIXTURES for check_render: a render with the plan table, and a
# render with no ConfigMap, must fail the check.
GOLDEN="$ROOT/helm/testdata/golden/values-baseline.bare.yaml"
sed 's/^  name: tenant-neo4j-size$/  name: tenant-neo4j-template/' "$GOLDEN" > "$WORK/plan-table.yaml"
sed 's/^  name: tenant-neo4j-size$/  name: another-name/' "$GOLDEN" > "$WORK/absent.yaml"
sed 's/^  memory: "1Gi"$/  memory: "a lot"/' "$GOLDEN" > "$WORK/bad-quantity.yaml"
for fixture in plan-table absent bad-quantity; do
  if cmp -s "$GOLDEN" "$WORK/$fixture.yaml"; then
    echo "❌ fixture $fixture did not change the golden; the fixture is stale"; fail=1
  elif check_render "$WORK/$fixture.yaml" 2> /dev/null; then
    echo "❌ FAILING FIXTURE $fixture passed the check"; fail=1
  fi
done

BASE=("$ROOT/helm/gibson" --namespace gibson
      -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml")

must_fail() { # <label> <expected message fragment> <helm args...>
  local label="$1" want="$2"; shift 2
  if helm template gibson "${BASE[@]}" "$@" > /dev/null 2> "$WORK/err"; then
    echo "❌ $label rendered; the guard did not fire"; fail=1
  elif ! grep -qE -- "$want" "$WORK/err"; then
    echo "❌ $label failed, but not on the size: $(tail -n1 "$WORK/err")"; fail=1
  fi
}

helm template gibson "${BASE[@]}" > /dev/null 2> "$WORK/err" \
  || { echo "❌ the baseline must render: $(tail -n1 "$WORK/err")"; fail=1; }
# THE FIXTURES THIS EXISTS FOR.
must_fail "a values file with tenantNeo4j.tiers" "tenantNeo4j.tiers was deleted" \
  --set "gibson-workloads.tenantNeo4j.tiers.team.cpu=500m"
must_fail "a default size with no memory" "tenantNeo4j.size.memory is required" \
  --set "gibson-workloads.tenantNeo4j.size.memory=null"

[ "$fail" -eq 0 ] || exit 1
echo "✓ tenant-neo4j-size: each golden has one default size, and the deleted plan table and a size with a missing field fail the render"
