#!/usr/bin/env bats
# The seal key rotation (ADR-0171, row keyring-generated-keys).
#
# 1. scripts/keyring.sh rotate and retire, against a real keyring file.
# 2. The rendered command of the openbao container, run under sh against two
#    key files, with a stub `bao` that prints the seal stanza it gets.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
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
  K="$(mktemp -d)/keyring.env"
  "$ROOT/scripts/keyring.sh" generate "$K" >/dev/null
}

get() { "$ROOT/scripts/keyring.sh" get "$K" "$1"; }

@test "a new keyring has an empty previous seal key and passes its shape" {
  [ -z "$(get OPENBAO_SEAL_KEY_PREVIOUS)" ]
  run "$ROOT/scripts/keyring.sh" fingerprints "$K"
  [ "$status" -eq 0 ]
  [[ "$output" == *KEYRING_FINGERPRINT_OPENBAO_SEAL_KEY_PREVIOUS=* ]]
}

@test "rotate keeps the old seal key as the previous one and writes a new key" {
  old="$(get OPENBAO_SEAL_KEY)"
  run "$ROOT/scripts/keyring.sh" rotate "$K" OPENBAO_SEAL_KEY
  [ "$status" -eq 0 ]
  [ "$(get OPENBAO_SEAL_KEY_PREVIOUS)" = "$old" ]
  new="$(get OPENBAO_SEAL_KEY)"
  [ "$new" != "$old" ]
  [ "$(printf '%s' "$new" | base64 -d | wc -c)" -eq 32 ]
  [ "$(stat -c %a "$K")" = 600 ]
  run "$ROOT/scripts/keyring.sh" fingerprints "$K"
  [ "$status" -eq 0 ]
}

@test "FAILING FIXTURE: a second rotate before retire is refused, and the keyring does not change" {
  "$ROOT/scripts/keyring.sh" rotate "$K" OPENBAO_SEAL_KEY >/dev/null
  before="$(sha256sum < "$K")"
  run "$ROOT/scripts/keyring.sh" rotate "$K" OPENBAO_SEAL_KEY
  [ "$status" -eq 2 ]
  [[ "$output" == *"retire first"* ]]
  [ "$(sha256sum < "$K")" = "$before" ]
}

@test "retire clears the previous seal key, and then a rotate runs again" {
  "$ROOT/scripts/keyring.sh" rotate "$K" OPENBAO_SEAL_KEY >/dev/null
  cur="$(get OPENBAO_SEAL_KEY)"
  run "$ROOT/scripts/keyring.sh" retire "$K" OPENBAO_SEAL_KEY
  [ "$status" -eq 0 ]
  [ -z "$(get OPENBAO_SEAL_KEY_PREVIOUS)" ]
  [ "$(get OPENBAO_SEAL_KEY)" = "$cur" ]
  run "$ROOT/scripts/keyring.sh" rotate "$K" OPENBAO_SEAL_KEY
  [ "$status" -eq 0 ]
}

@test "FAILING FIXTURE: a member with no previous member does not rotate in place" {
  run "$ROOT/scripts/keyring.sh" rotate "$K" BUCKET_ACCESS_KEY
  [ "$status" -eq 2 ]
  [[ "$output" == *"does not rotate in place"* ]]
}

# run_start CURRENT PREVIOUS: the rendered start command with the two key
# files and a stub bao. Prints the seal stanza.
run_start() {
  d="$(mktemp -d)"; mkdir -p "$d/seal" "$d/bin" "$d/tmp"
  printf '%s' "$1" > "$d/seal/openbao-seal-key"
  printf '%s' "$2" > "$d/seal/openbao-seal-previous-key"
  printf '#!/bin/sh\nfor a in "$@"; do case "$a" in -config=*seal.hcl) cat "${a#-config=}" ;; esac; done\n' > "$d/bin/bao"
  chmod +x "$d/bin/bao"
  sed -e "s#/etc/openbao/seal#$d/seal#g" -e "s#/tmp/seal.hcl#$d/tmp/seal.hcl#g" "$WORK/start.sh" > "$d/start.sh"
  PATH="$d/bin:$PATH" sh -e "$d/start.sh"
}

id_of() { printf 'keyring-%s' "$(printf '%s' "$1" | sha256sum | cut -c1-16)"; }

@test "the openbao container writes one seal key with its fingerprint id when no rotation runs" {
  run run_start "cur-key" ""
  [ "$status" -eq 0 ]
  [[ "$output" == *"current_key_id = \"$(id_of cur-key)\""* ]]
  [[ "$output" != *previous_key* ]]
}

@test "the openbao container writes both keys, each with its own id, during a rotation" {
  run run_start "new-key" "old-key"
  [ "$status" -eq 0 ]
  [[ "$output" == *"current_key_id = \"$(id_of new-key)\""* ]]
  [[ "$output" == *"previous_key_id = \"$(id_of old-key)\""* ]]
  [[ "$output" == *"previous_key    = \"file://"*"/openbao-seal-previous-key\""* ]]
}

@test "FAILING FIXTURE: an empty current seal key stops the start" {
  run run_start "" "old-key"
  [ "$status" -ne 0 ]
  [[ "$output" == *"seal key file is missing or empty"* ]]
}

@test "the Velero repository password rotates and keeps the old password for the old repository" {
  old="$(get VELERO_REPO_PASSWORD)"
  run "$ROOT/scripts/keyring.sh" rotate "$K" VELERO_REPO_PASSWORD
  [ "$status" -eq 0 ]
  [ "$(get VELERO_REPO_PASSWORD_PREVIOUS)" = "$old" ]
  [ "$(get VELERO_REPO_PASSWORD)" != "$old" ]
  [ -z "$(get OPENBAO_SEAL_KEY_PREVIOUS)" ]
}
