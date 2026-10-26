#!/usr/bin/env bats
# tests/require-helm.bats — scripts/require-helm.sh refuses another Helm, so a
# workstation with Helm 3 cannot write Helm 3 goldens (charts#789). Each
# assertion uses `run` and `$status`, never a negated command.

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  BIN="$BATS_TEST_TMPDIR/bin"; mkdir -p "$BIN"
  cat > "$BIN/helm" <<'SH'
#!/usr/bin/env bash
[ "$1" = version ] && { printf '%s' "$STUB_HELM_VERSION"; exit 0; }
exit 0
SH
  chmod +x "$BIN/helm"
}

@test "the Helm of Argo CD passes" {
  STUB_HELM_VERSION=v4.2.1 PATH="$BIN:$PATH" run "$ROOT/scripts/require-helm.sh" v4.2.1
  [ "$status" -eq 0 ]
}

@test "FAILING FIXTURE: Helm 3 is refused, and the message names the version to install" {
  STUB_HELM_VERSION=v3.18.4 PATH="$BIN:$PATH" run "$ROOT/scripts/require-helm.sh" v4.2.1
  [ "$status" -eq 1 ]
  [[ "$output" == *"helm is v3.18.4"* ]]
  [[ "$output" == *"Helm v4.2.1"* ]]
}

@test "FAILING FIXTURE: another Helm 4 patch is refused too" {
  STUB_HELM_VERSION=v4.2.0 PATH="$BIN:$PATH" run "$ROOT/scripts/require-helm.sh" v4.2.1
  [ "$status" -eq 1 ]
}

@test "FAILING FIXTURE: golden.sh stops at the version check, before it renders" {
  STUB_HELM_VERSION=v3.18.4 PATH="$BIN:$PATH" run "$ROOT/scripts/golden.sh" check
  [ "$status" -eq 1 ]
  [[ "$output" == *"helm is v3.18.4"* ]]
}
