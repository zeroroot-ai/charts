#!/usr/bin/env bats
# The rotation of the Zitadel System API key (ADR-0171, row
# platform-generated-secrets).
#
# The test renders the chart, takes the real sys_key_rotate out of the
# rendered openbao-auto-init sidecar, and runs it under sh against a stub key
# store: the active slot and the two slot keys, each with an age.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/sys.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "sys_key_rotate()" in a:
                    open(sys.argv[2], "w").write(a[a.index('SYS_ACTIVE_KEY="'):a.index("vault_write_policy() {")])
                    sys.exit(0)
sys.exit("no sys_key_rotate in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() { S="$(mktemp -d)"; export S; }
teardown() { rm -rf "$S"; }

# state <active slot> <age of the flip> <age of key a> <age of key b>
state() {
  if [ -n "$1" ]; then printf '{"slot":"%s"}' "$1" > "$S/active.json"; else printf '{}' > "$S/active.json"; fi
  echo "$2" > "$S/active.age"; echo "$3" > "$S/a.age"; echo "$4" > "$S/b.age"
}

run_rotate() {
  run sh -c '
    set -u
    . "$WORK/sys.sh"
    name() { case "$1" in *-active) echo active ;; *-key-b) echo b ;; *) echo a ;; esac; }
    kv_get() { cat "$S/$(name "$2").json" 2>/dev/null || printf "{}"; }
    kv_put() { n=$(name "$2"); echo 0 > "$S/$n.age"
      case "$n" in active) echo "put active $(printf "%s" "$3" | jq -r .slot)" ;;
                   *) echo "put key $n $(printf "%s" "$3" | jq -r .private_key)" ;; esac >> "$S/log"; }
    kv_created_epoch() { echo $(( $(date +%s) - $(cat "$S/$(name "$2").age") )); }
    transit_private_pem() { echo "priv-of-$2" | sed "s/-[0-9]*$//"; }
    transit_public_pem() { echo "pub-of-$2"; }
    sys_key_rotate tok 2>/dev/null; rc=$?
    [ -f "$S/log" ] && cat "$S/log" || echo none
    echo "rc=$rc"
  '
}

@test "a young active key with a refreshed other slot is left alone" {
  state a 3600 3600 1800
  run_rotate
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "an active key older than its lifetime makes the other slot active" {
  state a 2600000 2600000 2500000
  run_rotate
  [ "${lines[0]}" = "put active b" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "FAILING FIXTURE: the old slot keeps its key inside the wait after a flip" {
  state b 300 2600000 2500000
  run_rotate
  [ "${lines[0]}" = "none" ]
}

@test "after the wait the old slot gets a key from a new transit key" {
  state b 1000 2600000 2500000
  run_rotate
  [ "${lines[0]}" = "put key a priv-of-zitadel-system-bot-a" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "the slot flips back to a when b has been active longer than its lifetime" {
  state b 2600000 2500000 2600000
  run_rotate
  [ "${lines[0]}" = "put active a" ]
}

@test "FAILING FIXTURE: a slot with a young flip stays, even when its key waited a lifetime in the other slot" {
  state b 600 300 2592300
  run_rotate
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "FAILING FIXTURE: no flip to an other-slot key younger than the refresh wait" {
  state a 2600000 2600000 600
  run_rotate
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "no slot yet is the first seed pass, and nothing changes" {
  state "" 0 0 0
  run_rotate
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "FAILING FIXTURE: a slot that is not a or b fails the pass" {
  state c 10 10 10
  run_rotate
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=1" ]
}
