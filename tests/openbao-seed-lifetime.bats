#!/usr/bin/env bats
# The lifetime of a seed key (ADR-0171, D83).
#
# The test renders the chart, takes the real seed functions out of the
# rendered openbao-auto-init sidecar, and runs seed_one against a stub key
# store. kv_get, kv_put, kv_created_epoch and seed_generate are the stubs: the
# lifetime logic under test is the rendered text.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/seed.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "seed_one()" in a:
                    open(sys.argv[2], "w").write(a[a.index("seed_input() {"):a.index("vault_seed_platform_secrets() {")])
                    sys.exit(0)
sys.exit("no seed functions in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

# run_seed <stored JSON> <age of the current version in seconds, "" = absent> <specs>
# Prints the written JSON, or <unchanged>.
run_seed() {
  bash -c '
    set -u
    STORE="$1"; AGE="$2"; SPECS="$3"
    seed_checked=0; seed_written=0
    . "$WORK/seed.sh"
    kv_get() { printf "%s" "$STORE"; }
    kv_put() { printf "%s" "$3" > "$WORK/put.json"; }
    kv_created_epoch() { [ -z "$AGE" ] || echo $(( $(date +%s) - AGE )); }
    seed_generate() { printf "new-%s" "$2"; }
    rm -f "$WORK/put.json"
    seed_one tok gibson-redis-password "$SPECS" >/dev/null
    if [ -f "$WORK/put.json" ]; then cat "$WORK/put.json"; else echo "<unchanged>"; fi
  ' _ "$1" "$2" "$3"
}

@test "a key younger than its lifetime is kept" {
  run run_seed '{"password":"old"}' 3600 'password:pw @720h'
  [ "$status" -eq 0 ]
  [ "$output" = "<unchanged>" ]
}

@test "a key older than its lifetime gets a new generated value" {
  run run_seed '{"password":"old"}' $(( 721 * 3600 )) 'password:pw @720h'
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | jq -r .password)" = "new-pw" ]
}

@test "a key with no lifetime is never rotated" {
  run run_seed '{"password":"old"}' $(( 10000 * 3600 )) 'password:pw'
  [ "$status" -eq 0 ]
  [ "$output" = "<unchanged>" ]
}

@test "the lifetime is not written as a property" {
  run run_seed '{}' '' 'password:pw @720h'
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | jq -c 'keys')" = '["password"]' ]
}

@test "a rotation does not touch a literal property" {
  run run_seed '{"password":"old","mode":"fixed"}' $(( 721 * 3600 )) 'password:pw mode:literal:fixed @720h'
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | jq -r .password)" = "new-pw" ]
  [ "$(printf '%s' "$output" | jq -r .mode)" = "fixed" ]
}

@test "the redis password has a lifetime in the seed table" {
  grep -q '^gibson-redis-password password:pw @[0-9][0-9]*h$' \
    "$ROOT/helm/gibson-workloads/files/openbao-seed-keys.txt"
}
