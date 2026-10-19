#!/usr/bin/env bats
# xtrace-secrets.bats: a script that holds a secret value in a shell variable
# never prints it under bash -x.
#
# An xtrace prints each expanded command to stderr. `VALUE="$(cat)"` then
# prints the value, and so does `printf '%s' "$KEY" | base64`. A person who
# debugs a bringup with `bash -x`, or a workflow with SHELLOPTS=xtrace,
# copies the secret into a terminal or a job log.
#
# scripts/check-xtrace-secrets.py finds each script under scripts/ that reads
# a secret into a variable, by its content, and asserts that it turns xtrace
# off first. This file proves that the line works: it runs the scripts that
# need no cluster under bash -x, with stub tools, one trace file per call.
# Each call must exit 0, so the trace covers the path that reads the value,
# and no trace may hold a secret. The FAILING FIXTURE runs the same calls with
# the `set +x` line removed and asserts that EACH call then leaks.

ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
SCRIPTS="baseline-set-secret.sh keyring.sh keyring-to-cluster.sh setec-disk-public-key.sh"

setup() {
  T="$(mktemp -d)"; export T
  mkdir -p "$T/bin" "$T/scripts"
  # kubectl: an exec answers 200 like the pod script, an apply -f - drains
  # its stdin, and every other call prints nothing.
  cat > "$T/bin/kubectl" <<'K'
#!/usr/bin/env bash
case "$*" in
  *" exec "*) cat >/dev/null; printf '200' ;;
  *"get externalsecrets"*) printf '%s' '{"items":[]}' ;;
  *"-f -"*) cat >/dev/null ;;
  *) ;;
esac
K
  chmod +x "$T/bin/kubectl"
  for s in $SCRIPTS; do cp "$ROOT/scripts/$s" "$T/scripts/$s"; done
  SECRET="XTRACESECRET-$(head -c 9 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
  export SECRET
}

teardown() { rm -rf "$T"; }

# no_secret_in <file> <secret...>: none of the secrets, nor its base64 form,
# is in the file.
no_secret_in() {
  local f="$1" s; shift
  for s in "$@"; do
    [ "${#s}" -ge 8 ] || continue
    if grep -qF -- "$s" "$f"; then echo "secret in $f: ${s:0:6}..."; return 1; fi
    if grep -qF -- "$(printf '%s' "$s" | base64 -w0)" "$f"; then echo "base64 of a secret in $f: ${s:0:6}..."; return 1; fi
  done
}

# keyring_values <file>: each member value of a keyring file, one per line.
keyring_values() { grep -vE '^\s*(#|$)' "$1" | cut -d= -f2-; }

# The calls of the harness, in order. Each writes $T/err.<id> and $T/rc.<id>.
CALLS="bss-property bss-object keyring-generate keyring-fingerprints keyring-verify keyring-to-cluster setec-public-key"

# call <id> <stdin> <command...>: run one traced call.
call() {
  local id="$1" in="$2"; shift 2
  local rc=0
  printf '%s' "$in" | PATH="$T/bin:$PATH" "$@" >"$T/out.$id" 2>"$T/err.$id" || rc=$?
  echo "$rc" > "$T/rc.$id"
}

# run_all <dir>: run each call of the harness with the scripts of <dir>.
run_all() {
  local d="$1"
  rm -f "$T/keyring" "$T"/err.* "$T"/rc.* "$T"/out.*
  call bss-property "$SECRET"$'\n' bash -x "$d/baseline-set-secret.sh" ghcr-pull-secret pat
  call bss-object "{\"kid\":\"k1\",\"key\":\"$SECRET\"}" bash -x "$d/baseline-set-secret.sh" --object gibson-cg-signing-key-next
  call keyring-generate "" bash -x "$d/keyring.sh" generate "$T/keyring"
  # substrate.env: the bucket, the keyring path and its fingerprints, as
  # stage 0 records them. Built by an untraced run.
  { printf 'BUCKET_ENDPOINT=http://minio.invalid:9000\nBUCKET_NAME=b\nKEYRING_FILE=%s\nSMTP_USER=u\n' "$T/keyring"
    bash "$d/keyring.sh" fingerprints "$T/keyring" </dev/null; } > "$T/substrate.env"
  call keyring-fingerprints "" bash -x "$d/keyring.sh" fingerprints "$T/keyring"
  call keyring-verify "" bash -x "$d/keyring.sh" verify "$T/keyring" "$T/substrate.env"
  call keyring-to-cluster "" bash -x "$d/keyring-to-cluster.sh" "$T/substrate.env"
  call setec-public-key "" bash -x "$d/setec-disk-public-key.sh" "$T/keyring"
}

# secrets_of <id>: the secrets that call <id> handles.
secrets_of() {
  case "$1" in
    bss-*) printf '%s\n' "$SECRET" ;;
    *) keyring_values "$T/keyring" ;;
  esac
}

# each_call_exits_0: every call of the last run_all exited 0.
each_call_exits_0() {
  local id
  for id in $CALLS; do
    [ "$(cat "$T/rc.$id")" = 0 ] || { echo "call $id exited $(cat "$T/rc.$id"):"; tail -5 "$T/err.$id"; return 1; }
  done
}

@test "each script that holds a secret turns xtrace off before it reads one" {
  for s in $SCRIPTS; do
    first="$(grep -nE '^set ' "$ROOT/scripts/$s" | head -n1)"
    [[ "$first" == *":set +x" ]] || { echo "$s: the first set command is not set +x: $first"; false; }
  done
}

@test "under bash -x no call prints a secret value, and each call reaches its value" {
  run_all "$T/scripts"
  run each_call_exits_0
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  mapfile -t members < <(keyring_values "$T/keyring")
  [ "${#members[@]}" -ge 5 ]
  for id in $CALLS; do
    mapfile -t secrets < <(secrets_of "$id")
    run no_secret_in "$T/err.$id" "${secrets[@]}"
    [ "$status" -eq 0 ] || { echo "call $id: $output"; false; }
    grep -q 'set +x' "$T/err.$id" || { echo "call $id: no trace of set +x, so bash -x did not run it"; false; }
  done
  [ "$(wc -c < "$T/out.setec-public-key")" -ge 44 ]
}

@test "FAILING FIXTURE: without set +x each call prints a secret under bash -x" {
  mkdir -p "$T/old"
  for s in $SCRIPTS; do sed '/^set +x$/d' "$T/scripts/$s" > "$T/old/$s"; done
  run_all "$T/old"
  run each_call_exits_0
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  for id in $CALLS; do
    mapfile -t secrets < <(secrets_of "$id")
    run no_secret_in "$T/err.$id" "${secrets[@]}"
    [ "$status" -eq 1 ] || { echo "call $id did not leak without set +x"; false; }
  done
}

@test "the scan finds each script that reads a secret, and fails on a new one that leaks" {
  run python3 "$ROOT/scripts/check-xtrace-secrets.py" --selftest
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  run python3 "$ROOT/scripts/check-xtrace-secrets.py" --root "$ROOT/scripts"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  # FAILING FIXTURE: a copy of scripts/ with one new script that reads a PAT
  # and has no set +x.
  cp -r "$ROOT/scripts" "$T/scan"
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail' \
    'PAT="$(kubectl get secret iam-admin-pat -o jsonpath={.data.pat} | base64 -d)"' > "$T/scan/new-check.sh"
  run python3 "$ROOT/scripts/check-xtrace-secrets.py" --root "$T/scan"
  [ "$status" -eq 1 ]
  [[ "$output" == *"new-check.sh:3 reads a secret"* ]]
  # FAILING FIXTURE: each script of this harness with its set +x removed.
  for s in $SCRIPTS; do
    sed -i '/^set +x$/d' "$T/scan/$s"
    run python3 "$ROOT/scripts/check-xtrace-secrets.py" --root "$T/scan"
    [[ "$output" == *"$s:"* ]] || { echo "the scan missed $s without set +x"; false; }
  done
}
