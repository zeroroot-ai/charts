#!/usr/bin/env bats
# The lifetime of a seed key (ADR-0171, D83).
#
# The test renders the chart, takes the real seed functions out of the
# rendered openbao-auto-init sidecar, and runs them under sh, the shell of the
# sidecar. seed_one runs against a stub key store (kv_get, kv_put,
# kv_created_epoch, seed_generate). kv_created_epoch runs against a stub curl
# that answers with a real KV v2 metadata body. The logic under test is the
# rendered text.

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
  sh -c '
    set -u
    STORE="$1"; AGE="$2"; SPECS="$3"
    seed_checked=0; seed_written=0
    . "$WORK/seed.sh"
    kv_get() { printf "%s" "$STORE"; }
    kv_put() { printf "%s" "$3" > "$WORK/put.json"; }
    kv_created_epoch() { [ -z "$AGE" ] || echo $(( $(date +%s) - AGE )); }
    seed_generate() { printf "new-%s" "$2"; }
    rm -f "$WORK/put.json"
    seed_one tok gibson-redis-password "$SPECS" >/dev/null 2>&1 || { echo "<failed>"; exit 0; }
    if [ -f "$WORK/put.json" ]; then cat "$WORK/put.json"; else echo "<unchanged>"; fi
  ' _ "$1" "$2" "$3"
}

# run_created <HTTP code> <metadata body>: kv_created_epoch against a stub curl.
# Prints the epoch, <empty>, or <failed>.
run_created() {
  sh -c '
    set -u
    CODE="$1"; BODY="$2"
    TMPD="$(mktemp -d)"; BAO_ADDR_LOCAL=http://stub
    . "$WORK/seed.sh"
    curl() {
      while [ $# -gt 0 ]; do
        case "$1" in -o) printf "%s" "$BODY" > "$2"; shift 2 ;; *) shift ;; esac
      done
      printf "%s" "$CODE"
    }
    out=$(kv_created_epoch tok some-key 2>/dev/null) || { echo "<failed>"; exit 0; }
    [ -n "$out" ] && echo "$out" || echo "<empty>"
  ' _ "$1" "$2"
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

@test "a lifetime that is not @<hours>h fails the key and writes nothing" {
  for bad in '@30d' '@h' '@0h' '@7x2h'; do
    run run_seed '{"password":"old"}' 3600 "password:pw $bad"
    [ "$status" -eq 0 ]
    [ "$output" = "<failed>" ] || { echo "accepted $bad: $output"; return 1; }
  done
}

@test "the creation time of the current version is read from KV v2 metadata" {
  run run_created 200 '{"data":{"current_version":2,"versions":{"1":{"created_time":"2026-01-01T00:00:00.5Z"},"2":{"created_time":"2026-09-01T10:00:00.123456789Z"}}}}'
  [ "$status" -eq 0 ]
  [ "$output" = "1788256800" ]
}

@test "an absent key has no creation time" {
  run run_created 404 '{"errors":[]}'
  [ "$status" -eq 0 ]
  [ "$output" = "<empty>" ]
}

@test "metadata with no entry for the current version fails" {
  run run_created 200 '{"data":{"current_version":3,"versions":{"1":{"created_time":"2026-01-01T00:00:00Z"}}}}'
  [ "$status" -eq 0 ]
  [ "$output" = "<failed>" ]
}

@test "a refused metadata read fails" {
  run run_created 403 '{"errors":["permission denied"]}'
  [ "$status" -eq 0 ]
  [ "$output" = "<failed>" ]
}

@test "the redis password has a lifetime in the seed table" {
  grep -q '^gibson-redis-password password:pw @[0-9][0-9]*h$' \
    "$ROOT/helm/gibson-workloads/files/openbao-seed-keys.txt"
}
