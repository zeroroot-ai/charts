#!/usr/bin/env bats
# The rotation of the Capability-Grant signing key set (ADR-0171, row
# platform-signing-keys).
#
# The test renders the chart, takes the real cg_key_rotate out of the rendered
# openbao-auto-init sidecar, and runs it under sh against a stub key store:
# three JSON files for the three slots, and a creation time for each.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/cg.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "cg_key_rotate()" in a:
                    open(sys.argv[2], "w").write(a[a.index("CG_KEY=\""):a.index("vault_write_policy() {")])
                    sys.exit(0)
sys.exit("no cg_key_rotate in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() {
  S="$(mktemp -d)"; export S
}
teardown() { rm -rf "$S"; }

# slot <name> <kid> <age in seconds>: one slot of the store.
slot() {
  if [ -n "$2" ]; then printf '{"kid":"%s","key":"%s"}' "$2" "$(printf '%s' "$2" | sha256sum | cut -c1-32)" > "$S/$1.json"
  else printf '{"kid":"","key":""}' > "$S/$1.json"; fi
  echo "$3" > "$S/$1.age"
}

run_rotate() {
  run sh -c '
    set -u
    . "$WORK/cg.sh"
    slotname() { case "$1" in *-next) echo next ;; *-previous) echo previous ;; *) echo current ;; esac; }
    kv_get() { cat "$S/$(slotname "$2").json"; }
    kv_put() { n=$(slotname "$2"); printf "%s" "$3" > "$S/$n.json"; echo 0 > "$S/$n.age"; echo "put $n $(printf "%s" "$3" | jq -r .kid)" >> "$S/log"; }
    kv_created_epoch() { echo $(( $(date +%s) - $(cat "$S/$(slotname "$2").age") )); }
    gen_rand() { echo "r$1"; }
    cg_key_rotate tok 2>/dev/null
    [ -f "$S/log" ] && cat "$S/log" || echo none
  '
}

@test "a young key set with no rotation in progress is left alone" {
  slot current cur 3600; slot next "" 0; slot previous "" 0
  run_rotate
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

@test "a key older than its lifetime publishes a next kid first" {
  slot current cur 2600000; slot next "" 0; slot previous "" 0
  run_rotate
  [ "$status" -eq 0 ]
  [ "$output" = "put next r8" ]
}

@test "a next kid younger than the promote wait is not promoted" {
  slot current cur 2600000; slot next nxt 60; slot previous "" 0
  run_rotate
  [ "$output" = "none" ]
}

@test "a published next kid is promoted in the safe order" {
  slot current cur 2600000; slot next nxt 700; slot previous "" 0
  run_rotate
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "put previous cur" ]
  [ "${lines[1]}" = "put current nxt" ]
  [ "${lines[2]}" = "put next " ]
}

@test "the previous kid is retired after the longest token life" {
  slot current nxt 90100; slot next "" 0; slot previous cur 90100
  run_rotate
  [ "$output" = "put previous " ]
}

@test "FAILING FIXTURE: the previous kid is kept inside the longest token life" {
  slot current nxt 3600; slot next "" 0; slot previous cur 3600
  run_rotate
  [ "$output" = "none" ]
}

@test "no rotation starts while a previous kid is still kept" {
  slot current nxt 2600000; slot next "" 0; slot previous cur 2600000
  run_rotate
  [ "$output" = "put previous " ]
}

@test "FAILING FIXTURE: a next with a kid and no seed is not promoted" {
  slot current cur 2600000; slot previous "" 0
  printf '{"kid":"nxt","key":""}' > "$S/next.json"; echo 700 > "$S/next.age"
  run_rotate
  [ "$output" = "none" ]
}

@test "FAILING FIXTURE: a next whose seed is not 32 bytes is not promoted" {
  slot current cur 2600000; slot previous "" 0
  printf '{"kid":"nxt","key":"too-short"}' > "$S/next.json"; echo 700 > "$S/next.age"
  run_rotate
  [ "$output" = "none" ]
}

@test "a next with a URL-safe base64 seed of 32 bytes is promoted" {
  slot current cur 2600000; slot previous "" 0
  printf '{"kid":"nxt","key":"%s"}' "$(head -c 32 /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_')" > "$S/next.json"; echo 700 > "$S/next.age"
  run_rotate
  [ "${lines[1]}" = "put current nxt" ]
}
