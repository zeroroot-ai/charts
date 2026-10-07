#!/usr/bin/env bats
# tests/preflight-kvm.bats — each verdict of scripts/preflight-kvm.sh
# (charts#413, ADR-0083). kubectl is a stub (tests/kvm-kubectl-stub.sh). Each
# assertion uses `run` and `$status`, never a negated command.

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  SCRIPT="$ROOT/scripts/preflight-kvm.sh"
  export STUB_DIR="$BATS_TEST_TMPDIR"
  export KUBECTL="$ROOT/tests/kvm-kubectl-stub.sh"
  export POLL_SECONDS=0 KVM_TIMEOUT=2
  export STUB_NODES='' STUB_PHASE=Succeeded STUB_LOGS=kvm-present STUB_BAD_NODE=''
  unset FLEET_NODE_SELECTOR
  printf 'gibson-workloads:\n  setec:\n    enabled: false\n' > "$STUB_DIR/seam-off.yaml"
  printf 'gibson-workloads:\n  setec:\n    enabled: true\n' > "$STUB_DIR/seam-on.yaml"
}

@test "no fleet node fails" {
  run "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no node matches"* ]]
  [ ! -e "$STUB_DIR/applied" ]
}

@test "a node with /dev/kvm passes, and the probe is pinned to that node" {
  export STUB_NODES='node-a\n'
  run "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"node node-a exposes /dev/kvm"* ]]
  grep -q 'nodeName: node-a' "$STUB_DIR/applied"
  grep -q 'hostPath: { path: /dev' "$STUB_DIR/applied"
}

@test "a node with no /dev/kvm fails, and the message names metal and nested virtualization" {
  export STUB_NODES='node-a\n' STUB_BAD_NODE=node-a
  run "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"node node-a has no /dev/kvm"* ]]
  [[ "$output" == *"metal"* ]]
  [[ "$output" == *"nested virtualization"* ]]
}

@test "a probe that succeeds with no kvm-present line fails" {
  export STUB_NODES='node-a\n' STUB_LOGS=''
  run "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"has no /dev/kvm"* ]]
}

@test "the first bad node of two fails the preflight" {
  export STUB_NODES='node-a\nnode-b\n' STUB_BAD_NODE=node-b
  run "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"node node-a exposes /dev/kvm"* ]]
  [[ "$output" == *"node node-b has no /dev/kvm"* ]]
}

@test "the setec seam off skips the nodes of this cluster" {
  export STUB_NODES='node-a\n' STUB_BAD_NODE=node-a
  run "$SCRIPT" -f "$STUB_DIR/seam-off.yaml"
  [ "$status" -eq 0 ]
  [[ "$output" == *"seam is off"* ]]
  [ ! -e "$STUB_DIR/applied" ]
}

@test "a later values file turns the seam back on" {
  export STUB_NODES='node-a\n' STUB_BAD_NODE=node-a
  run "$SCRIPT" -f "$STUB_DIR/seam-off.yaml" -f "$STUB_DIR/seam-on.yaml"
  [ "$status" -eq 1 ]
  [[ "$output" == *"has no /dev/kvm"* ]]
}

@test "an unknown argument fails" {
  run "$SCRIPT" --bogus
  [ "$status" -eq 2 ]
}
