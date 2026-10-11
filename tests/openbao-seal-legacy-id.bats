#!/usr/bin/env bats
# A store from a chart before charts#568 unseals after the upgrade
# (charts#605, ADR-0171 row keyring-generated-keys).
#
# Before charts#568 the static seal had the fixed key id "bringup-keyring".
# The static wrapper unwraps by the id in each wrapped entry, so a new id for
# the same key leaves the store sealed. The test runs a real OpenBao server
# (BAO_BIN, the release binary that ci.yml installs, the version of the
# chart image) with file storage, as the chart does:
#
# 1. it initializes a store under the old stanza;
# 2. it starts the store with the rendered command of the openbao container,
#    and expects an unsealed store;
# 3. the failing fixture starts it with the legacy check blinded (an empty
#    core/hsm path), which is the command of charts#568, and expects a
#    sealed store;
# 4. a rotation after the upgrade rewraps the store under the new id, and a
#    retire then starts with the current key alone.
# The servers listen on 127.0.0.1 only. Nothing reaches a cluster.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  [ -x "${BAO_BIN:-}" ] || { echo "SETUP FAILURE: BAO_BIN must name the bao binary (ci.yml installs it)" >&2; return 1; }
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml" 2>/dev/null
  python3 - "$WORK/render.yaml" "$WORK/start.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            if c["name"] == "openbao":
                open(sys.argv[2], "w").write(c["command"][2])
                sys.exit(0)
sys.exit("no openbao container in the render")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() {
  T="$(mktemp -d)"; export T
  mkdir -p "$T/data" "$T/seal" "$T/empty"
  PORT="$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
  export PORT BAO_ADDR="http://127.0.0.1:$PORT"
  cat > "$T/base.hcl" <<HCL
listener "tcp" {
  address     = "127.0.0.1:$PORT"
  tls_disable = true
}
storage "file" { path = "$T/data" }
disable_mlock = true
HCL
  newkey > "$T/seal/openbao-seal-key"
}

teardown() {
  stop_server
  rm -rf "$T"
}

newkey() { head -c 32 /dev/urandom | base64 | tr -d '\n'; }

stop_server() {
  if [ -f "$T/pid" ]; then
    kill "$(cat "$T/pid")" 2>/dev/null || true
    for _ in $(seq 1 50); do kill -0 "$(cat "$T/pid")" 2>/dev/null || break; sleep 0.1; done
    rm -f "$T/pid"
  fi
}

# wait_up: the API answers (sealed or not).
wait_up() {
  for _ in $(seq 1 100); do
    rc=0; "$BAO_BIN" status >/dev/null 2>&1 || rc=$?
    if [ "$rc" -eq 0 ] || [ "$rc" -eq 2 ]; then return 0; fi
    kill -0 "$(cat "$T/pid")" 2>/dev/null || return 1
    sleep 0.1
  done
  return 1
}

sealed() { "$BAO_BIN" status -format=json 2>/dev/null | jq -r .sealed; }

# legacy_store: initialize a store under the stanza of a chart before charts#568.
legacy_store() {
  cat > "$T/legacy.hcl" <<HCL
seal "static" {
  current_key_id = "bringup-keyring"
  current_key    = "file://$T/seal/openbao-seal-key"
}
HCL
  "$BAO_BIN" server -config="$T/base.hcl" -config="$T/legacy.hcl" > "$T/legacy.log" 2>&1 &
  echo $! > "$T/pid"
  wait_up
  "$BAO_BIN" operator init -format=json > "$T/init.json"
  [ "$(sealed)" = false ]
  stop_server
}

# start_rendered [hsm path]: run the rendered openbao command with the paths
# of the test. An hsm path of an empty directory blinds the legacy check.
start_rendered() {
  hsm="${1:-$T/data/core/hsm}"
  sed -e "s#/etc/openbao/seal/#$T/seal/#g" \
      -e "s#^hsm=.*#hsm=$hsm#" \
      -e "s#/tmp/seal.hcl#$T/seal.hcl#g" \
      -e "s#exec bao server -config=/etc/openbao/config/openbao.hcl#exec \"$BAO_BIN\" server -config=$T/base.hcl#" \
      "$WORK/start.sh" > "$T/start.sh"
  grep -q "$T/base.hcl" "$T/start.sh" || { echo "the rendered command has no exec line to rewrite"; return 1; }
  sh -ec ". $T/start.sh" > "$T/start.log" 2>&1 &
  echo $! > "$T/pid"
  wait_up || { cat "$T/start.log"; return 1; }
  sleep 1
}

@test "a store from before charts#568 starts unsealed under the rendered command" {
  legacy_store
  start_rendered
  grep -q 'legacy seal key id bringup-keyring' "$T/start.log"
  [ "$(sealed)" = false ] || { cat "$T/start.log"; false; }
}

@test "FAILING FIXTURE: with the legacy check blinded, the store stays sealed" {
  legacy_store
  start_rendered "$T/empty"
  [ "$(sealed)" = true ] || { cat "$T/start.log"; false; }
}

@test "a rotation after the upgrade rewraps the store, and a retire starts with the current key alone" {
  legacy_store
  mv "$T/seal/openbao-seal-key" "$T/seal/openbao-seal-previous-key"
  newkey > "$T/seal/openbao-seal-key"
  start_rendered
  [ "$(sealed)" = false ] || { cat "$T/start.log"; false; }
  grep -q 'previous bringup-keyring' "$T/start.log"
  stop_server
  # retire: the previous key file is empty.
  : > "$T/seal/openbao-seal-previous-key"
  start_rendered
  [ "$(sealed)" = false ] || { cat "$T/start.log"; false; }
  if grep -q 'legacy seal key id' "$T/start.log"; then
    echo "the store still names the legacy id after the rotation"; false
  fi
}

@test "a new store starts under the id of its key" {
  start_rendered
  "$BAO_BIN" operator init -format=json > "$T/init.json"
  [ "$(sealed)" = false ]
  if grep -q 'legacy seal key id' "$T/start.log"; then
    echo "a new store must not use the legacy id"; false
  fi
  stop_server
  start_rendered
  [ "$(sealed)" = false ] || { cat "$T/start.log"; false; }
}
