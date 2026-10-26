#!/usr/bin/env bats
# tests/purge-tenant-backup.bats — each refusal of scripts/purge-tenant-backup.sh
# (charts#419). kubectl is a stub (tests/kubectl-stub.sh). Each assertion uses
# `run` and `$status`, never a negated command.

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  SCRIPT="$ROOT/scripts/purge-tenant-backup.sh"
  export STUB_DIR="$BATS_TEST_TMPDIR"
  export KUBECTL="$ROOT/tests/kubectl-stub.sh"
  export POLL_SECONDS=0 TIMEOUT=2
  export STUB_TENANT_EXISTS=0 STUB_DELETE_FAILS=0 STUB_ROWS=""
  unset TENANT_UID
}

@test "no tenant name fails" {
  run "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"exactly one tenant name"* ]]
}

@test "two tenant names fail" {
  run "$SCRIPT" acme other
  [ "$status" -eq 1 ]
}

@test "a name that is not a Kubernetes name fails" {
  run "$SCRIPT" 'acme;rm'
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not a tenant name"* ]]
}

@test "a tenant that still exists is refused" {
  export STUB_TENANT_EXISTS=1 STUB_ROWS='tenant-final-u1 u1 final\n'
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"still exists"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

@test "no matching backup fails" {
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"no last backup"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

@test "two last backups of one name are refused without TENANT_UID" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u2 u2 final\n'
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"set TENANT_UID"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

@test "TENANT_UID selects one of two backups" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u2 u2 final\n' TENANT_UID=u2
  run "$SCRIPT" acme
  [ "$status" -eq 0 ]
  grep -q "backupName: tenant-final-u2" "$STUB_DIR/created"
}

@test "a TENANT_UID that matches no backup fails" {
  export STUB_ROWS='tenant-final-u1 u1 final\n' TENANT_UID=u9
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [ ! -e "$STUB_DIR/created" ]
}

@test "a backup that is not a last backup is refused" {
  export STUB_ROWS='tenant-final-u1 u1 daily\n'
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not a last backup"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

@test "one last backup is deleted with one DeleteBackupRequest" {
  export STUB_ROWS='tenant-final-u1 u1 final\n'
  run "$SCRIPT" acme
  [ "$status" -eq 0 ]
  [[ "$output" == *"backup tenant-final-u1 is deleted"* ]]
  grep -q "kind: DeleteBackupRequest" "$STUB_DIR/created"
  grep -q "backupName: tenant-final-u1" "$STUB_DIR/created"
}

@test "a delete that Velero reports as failed fails" {
  export STUB_ROWS='tenant-final-u1 u1 final\n' STUB_DELETE_FAILS=1
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"could not delete"* ]]
}

# gibson#1324: a delete whose first attempt failed leaves more than one last
# backup with one uid. Each attempt can hold tenant data, so each one goes.
@test "every attempt of one delete is deleted" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u1-2 u1 final\ntenant-final-u1-3 u1 final\n'
  run "$SCRIPT" acme
  [ "$status" -eq 0 ]
  [ "$(grep -c 'kind: DeleteBackupRequest' "$STUB_DIR/created")" -eq 3 ]
  for b in tenant-final-u1 tenant-final-u1-2 tenant-final-u1-3; do
    grep -qx "  backupName: $b" "$STUB_DIR/created"
    [[ "$output" == *"backup $b is deleted"* ]]
  done
}

@test "TENANT_UID selects every attempt of one delete, and no backup of another" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u2 u2 final\ntenant-final-u2-2 u2 final\n' TENANT_UID=u2
  run "$SCRIPT" acme
  [ "$status" -eq 0 ]
  grep -qx "  backupName: tenant-final-u2" "$STUB_DIR/created"
  grep -qx "  backupName: tenant-final-u2-2" "$STUB_DIR/created"
  run grep -c "backupName: tenant-final-u1$" "$STUB_DIR/created"
  [ "$output" = 0 ]
}

@test "two attempts of one delete and a third of another are refused without TENANT_UID" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u1-2 u1 final\ntenant-final-u2 u2 final\n'
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"set TENANT_UID"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

@test "one attempt that is not a last backup stops the purge before any delete" {
  export STUB_ROWS='tenant-final-u1 u1 final\ntenant-final-u1-2 u1 daily\n'
  run "$SCRIPT" acme
  [ "$status" -eq 1 ]
  [[ "$output" == *"is not a last backup"* ]]
  [ ! -e "$STUB_DIR/created" ]
}

# Only NotFound proves that a tenant is deleted. A tenant read that fails for
# another reason must not let the backups of a live tenant go.
@test "a tenant read that fails for another reason than NotFound stops the purge" {
  for why in forbidden timeout nocrd; do
    rm -f "$STUB_DIR/created"
    export STUB_TENANT_EXISTS="$why" STUB_ROWS='tenant-final-u1 u1 final\n'
    run "$SCRIPT" acme
    [ "$status" -eq 1 ] || { echo "$why: $output"; false; }
    [[ "$output" == *"could not tell whether the tenant acme exists"* ]]
    [ ! -e "$STUB_DIR/created" ]
  done
}

