#!/usr/bin/env bats
# xtrace-secrets.bats: a script that holds a secret value in a shell variable
# never prints it under bash -x.
#
# An xtrace prints each expanded command to stderr. `VALUE="$(cat)"` then
# prints the value, and so does `printf '%s' "$KEY" | base64`. A person who
# debugs a bringup with `bash -x`, or a workflow with SHELLOPTS=xtrace,
# copies the secret into a terminal or a job log.
#
# The scripts under scripts/ that hold a secret value in a variable:
#   baseline-set-secret.sh    the operator value of an OpenBao key
#   keyring.sh                each member of the bringup keyring
#   keyring-to-cluster.sh     each member, and its base64 form
#   setec-disk-public-key.sh  the disk signing seed
# (verify-selfhosted-seams.sh reads a password only into `grep -q`, so no
# variable holds it.) Each one runs `set +x` before it reads a value.
#
# Each test runs the script under bash -x with stub tools and asserts that no
# secret is in stderr. The FAILING FIXTURE runs the same scripts with the
# `set +x` line removed and asserts that each one then leaks.

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

# run_all <dir>: run each script of <dir> under bash -x. Each writes its
# trace to $T/<script>.err. Prints nothing.
run_all() {
  local d="$1"
  printf '%s\n' "$SECRET" | PATH="$T/bin:$PATH" bash -x "$d/baseline-set-secret.sh" ghcr-pull-secret pat \
    >/dev/null 2>"$T/baseline-set-secret.sh.err" || true
  printf '{"kid":"k1","key":"%s"}' "$SECRET" | PATH="$T/bin:$PATH" bash -x "$d/baseline-set-secret.sh" --object gibson-cg-signing-key-next \
    >/dev/null 2>>"$T/baseline-set-secret.sh.err" || true
  rm -f "$T/keyring"
  bash -x "$d/keyring.sh" generate "$T/keyring" </dev/null >/dev/null 2>"$T/keyring.sh.err" || true
  bash -x "$d/keyring.sh" verify "$T/keyring" </dev/null >/dev/null 2>>"$T/keyring.sh.err" || true
  printf 'BUCKET_ENDPOINT=http://minio.invalid:9000\nBUCKET_NAME=b\nKEYRING_FILE=%s\nSMTP_USER=u\n' "$T/keyring" > "$T/substrate.env"
  PATH="$T/bin:$PATH" bash -x "$d/keyring-to-cluster.sh" "$T/substrate.env" \
    </dev/null >/dev/null 2>"$T/keyring-to-cluster.sh.err" || true
  bash -x "$d/setec-disk-public-key.sh" "$T/keyring" </dev/null >/dev/null 2>"$T/setec-disk-public-key.sh.err" || true
}

@test "each script that holds a secret turns xtrace off before it reads one" {
  for s in $SCRIPTS; do
    first="$(grep -nE '^set ' "$ROOT/scripts/$s" | head -n1)"
    [[ "$first" == *":set +x" ]] || { echo "$s: the first set command is not set +x: $first"; false; }
  done
}

@test "under bash -x no script prints a secret value" {
  run_all "$T/scripts"
  [ -s "$T/keyring" ]
  mapfile -t members < <(keyring_values "$T/keyring")
  [ "${#members[@]}" -ge 5 ]
  run no_secret_in "$T/baseline-set-secret.sh.err" "$SECRET"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  for s in keyring.sh keyring-to-cluster.sh setec-disk-public-key.sh; do
    run no_secret_in "$T/$s.err" "${members[@]}"
    [ "$status" -eq 0 ] || { echo "$s: $output"; false; }
  done
  # The scripts did their work: the trace is not empty because they failed early.
  grep -q 'set +x' "$T/keyring-to-cluster.sh.err"
  [ "$(PATH="$T/bin:$PATH" bash "$T/scripts/keyring-to-cluster.sh" "$T/substrate.env" </dev/null >/dev/null 2>&1; echo $?)" = 0 ]
  [ "$(bash "$T/scripts/setec-disk-public-key.sh" "$T/keyring" </dev/null | wc -c)" -ge 44 ]
}

@test "FAILING FIXTURE: without set +x each script prints a secret under bash -x" {
  mkdir -p "$T/old"
  for s in $SCRIPTS; do sed '/^set +x$/d' "$T/scripts/$s" > "$T/old/$s"; done
  run_all "$T/old"
  mapfile -t members < <(keyring_values "$T/keyring")
  run no_secret_in "$T/baseline-set-secret.sh.err" "$SECRET"
  [ "$status" -eq 1 ] || { echo "baseline-set-secret.sh did not leak"; false; }
  for s in keyring.sh keyring-to-cluster.sh setec-disk-public-key.sh; do
    run no_secret_in "$T/$s.err" "${members[@]}"
    [ "$status" -eq 1 ] || { echo "$s did not leak without set +x"; false; }
  done
}
