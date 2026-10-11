#!/usr/bin/env bats
# The tenant operator gets TENANT_NEO4J_IMAGE, a mirror image pinned by
# digest (gibson#1061, charts#608).
#
# From gibson 0.159 the tenant operator exits at start when the variable is
# empty or has no sha256 digest. The test reads the tenant-operator
# Deployment of each profile of the goldens.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
}

teardown_file() { rm -rf "$WORK"; }

# check_image <render>: 0 when the tenant operator gets a digest-pinned
# mirror Neo4j image.
check_image() {
  python3 - "$1" <<'PY'
import re, sys, yaml
found = False
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") != "Deployment" or d["metadata"]["name"] != "gibson-tenant-operator":
        continue
    found = True
    env = {e["name"]: e.get("value", "") for c in d["spec"]["template"]["spec"]["containers"] for e in c.get("env") or []}
    v = env.get("TENANT_NEO4J_IMAGE", "")
    if not re.fullmatch(r"ghcr\.io/zeroroot-ai/mirror/neo4j:[^@]+@sha256:[0-9a-f]{64}", v):
        print(f"TENANT_NEO4J_IMAGE is {v!r}, want ghcr.io/zeroroot-ai/mirror/neo4j:<tag>@sha256:<digest>")
        sys.exit(1)
if not found:
    print("no Deployment gibson-tenant-operator in the render")
    sys.exit(1)
PY
}

@test "each profile of the goldens gives the tenant operator a digest-pinned Neo4j image" {
  for f in "$ROOT"/helm/testdata/golden/values-*.yaml; do
    run check_image "$f"
    [ "$status" -eq 0 ] || { echo "$(basename "$f"): $output"; false; }
  done
}

@test "FAILING FIXTURE: a tenant operator with no TENANT_NEO4J_IMAGE fails the check" {
  python3 - "$ROOT/helm/testdata/golden/values-baseline.withcaps.yaml" "$WORK/broken.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for d in docs:
    if d.get("kind") == "Deployment" and d["metadata"]["name"] == "gibson-tenant-operator":
        for c in d["spec"]["template"]["spec"]["containers"]:
            c["env"] = [e for e in c.get("env") or [] if e["name"] != "TENANT_NEO4J_IMAGE"]
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run check_image "$WORK/broken.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"TENANT_NEO4J_IMAGE is ''"* ]] || { echo "$output"; false; }
}
