#!/usr/bin/env bats
# baseline-set-secret.bats: the operator's value is on no argv, at any level.
#
# Three processes see the value: this script, kubectl exec, and the shell
# inside the pod with its curl and jq. A stub kubectl records its argv and
# its stdin. The pod script, taken from that recorded argv, then runs here
# with a stub curl and a recording jq on PATH. The value, the ServiceAccount
# token and the OpenBao token must appear in no recorded argv, and the KV
# write body must hold the value byte for byte. A fixture with the value on
# one argv (the old `env SET_VALUE=...` form) must fail the check.

SCRIPT="$BATS_TEST_DIRNAME/../scripts/baseline-set-secret.sh"

setup() {
  STUB="$(mktemp -d)"
  export STUB
  mkdir -p "$STUB/bin"
  REAL_JQ="$(command -v jq)"
  export REAL_JQ
  # The hostile value: a quote that ends a shell string, a command
  # substitution, a double quote, a backslash and a second line.
  HOSTILE="x'; touch /tmp/pwned; echo '\$(id) \"q\" back\\slash
SECONDLINE-\$HOME"
  export HOSTILE
  cat > "$STUB/bin/kubectl" <<'K'
#!/usr/bin/env bash
case "$*" in
  *" exec "*) printf '%s\n' "$@" > "$STUB/kubectl.argv"; cat > "$STUB/kubectl.stdin"; printf '200' ;;
  *"get externalsecrets"*) printf '%s' '{"items":[]}' ;;
  *) printf '%s\n' "$@" >> "$STUB/kubectl.other.argv" ;;
esac
K
  # jq: record each argv, then run the real jq.
  cat > "$STUB/bin/jq" <<'J'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$STUB/jq.argv"
exec "$REAL_JQ" "$@"
J
  # curl: record each argv, answer as OpenBao would, keep the write body.
  cat > "$STUB/bin/curl" <<'C'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$STUB/curl.argv"
out=""; body=""; url=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    --data-binary) body="${2#@}"; shift ;;
    -H) [ "${2#@}" != "$2" ] && cat "${2#@}" >> "$STUB/curl.hdr"; shift ;;
    http*) url="$1" ;;
  esac
  shift
done
case "$url" in
  */login) printf '%s' '{"auth":{"client_token":"s.FAKEBAOTOKEN42"}}' > "$out" ;;
  */token/revoke-self) ;;
  */v1/secret/data/*) if [ -n "$body" ]; then cp "$body" "$STUB/write.body"; printf '200'
                      else case "${STUB_READ_CODE:-200}" in
                             200) printf '%s' '{"data":{"data":{"other":"keep"}}}' > "$out" ;;
                             *) printf '%s' '{"errors":["permission denied"]}' > "$out" ;;
                           esac
                           printf '%s' "${STUB_READ_CODE:-200}"; fi ;;
esac
C
  chmod +x "$STUB/bin/"*
  printf 'eyJFAKESATOKEN.payload.sig' > "$STUB/sa-token"
}

teardown() { rm -rf "$STUB"; }

# no_value_on_argv <argv file>: each line of the value, the SA token and the
# OpenBao token are absent from the recorded argv.
no_value_on_argv() {
  local f="$1" line
  [ -f "$f" ] || { echo "no argv recorded in $f"; return 1; }
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    if grep -qF -- "$line" "$f"; then echo "value line on argv in $f: $line"; return 1; fi
  done <<< "$HOSTILE"
  for tok in eyJFAKESATOKEN s.FAKEBAOTOKEN42 SET_VALUE=; do
    if grep -qF -- "$tok" "$f"; then echo "$tok on argv in $f"; return 1; fi
  done
}

run_script() { PATH="$STUB/bin:$PATH" "$SCRIPT" "$@"; }

# The pod script is the argument of `sh -c`, the last argv of the exec.
pod_script_from_argv() {
  python3 -c '
import sys
a = open(sys.argv[1]).read().split("\n")
i = max(k for k in range(1, len(a)) if a[k] == "-c" and a[k - 1] == "sh")
print("\n".join(a[i + 1:]).rstrip("\n"))' "$STUB/kubectl.argv"
}

@test "the value reaches kubectl exec on stdin, and no argv holds it" {
  printf '%s\n' "$HOSTILE" | run_script ghcr-pull-secret pat
  run no_value_on_argv "$STUB/kubectl.argv"
  [ "$status" -eq 0 ]
  grep -qxF -- 'SET_KEY=ghcr-pull-secret' "$STUB/kubectl.argv"
  grep -qxF -- 'SET_PROP=pat' "$STUB/kubectl.argv"
  printf '%s' "$HOSTILE" | cmp -s - "$STUB/kubectl.stdin"
  [ ! -e /tmp/pwned ]
}

@test "one trailing newline is removed and nothing else" {
  printf 'v\n\n' | run_script k p
  printf 'v\n' | cmp -s - "$STUB/kubectl.stdin"
  printf ' v ' | run_script k p
  printf ' v ' | cmp -s - "$STUB/kubectl.stdin"
}

@test "inside the pod the value, the SA token and the OpenBao token are on no argv, and the write holds the value" {
  printf '%s\n' "$HOSTILE" | run_script ses-smtp-credentials password
  # The pod script is the last argv of the exec: the argument of sh -c.
  pod_script="$(pod_script_from_argv)"
  run bash -c 'printf "%s" "$HOSTILE" | SET_KEY=ses-smtp-credentials SET_PROP=password SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script"
  [ "$status" -eq 0 ]
  [ "$output" = "200" ]
  run no_value_on_argv "$STUB/curl.argv"
  [ "$status" -eq 0 ]
  run no_value_on_argv "$STUB/jq.argv"
  [ "$status" -eq 0 ]
  # The token reached curl through a header file, and the login through a body file.
  grep -qF -- 'X-Vault-Token: s.FAKEBAOTOKEN42' "$STUB/curl.hdr"
  grep -qF -- '--data-binary' "$STUB/curl.argv"
  # The write body holds the value byte for byte and keeps the other property.
  "$REAL_JQ" -j '.data.password' "$STUB/write.body" | cmp -s - <(printf '%s' "$HOSTILE")
  [ "$("$REAL_JQ" -r '.data.other' "$STUB/write.body")" = "keep" ]
  # The command inside the value never ran.
  [ ! -e /tmp/pwned ]
  # The temporary directory of the pod script is gone.
  [ -z "$(find /tmp -maxdepth 1 -name 'baseline-set-secret.*' -newer "$STUB/sa-token" 2>/dev/null)" ]
}

@test "FAILING FIXTURE: the old exec form with env SET_VALUE= on the argv fails the check" {
  printf '%s\n' "env" "SET_KEY=k" "SET_PROP=p" "SET_VALUE=$HOSTILE" "sh" "-s" > "$STUB/old.argv"
  run no_value_on_argv "$STUB/old.argv"
  [ "$status" -eq 1 ]
  [[ "$output" == *"value line on argv"* ]]
}

@test "FAILING FIXTURE: the old in-pod jq --arg form puts the value on the jq argv and fails the check" {
  printf '%s' "$HOSTILE" | PATH="$STUB/bin:$PATH" sh -c 'jq -nc --arg v "$(cat)" "{v:\$v}"' >/dev/null
  run no_value_on_argv "$STUB/jq.argv"
  [ "$status" -eq 1 ]
}

@test "a third argument is refused with exit 2 and no kubectl call" {
  run bash -c 'printf "v\n" | PATH="$STUB/bin:$PATH" "$0" k p "secret-on-argv"' "$SCRIPT"
  [ "$status" -eq 2 ]
  [[ "$output" == *"never as an argument"* ]]
  [ ! -f "$STUB/kubectl.argv" ]
}

@test "an empty stdin is refused with exit 2 and no kubectl call" {
  run bash -c 'printf "" | PATH="$STUB/bin:$PATH" "$0" k p' "$SCRIPT"
  [ "$status" -eq 2 ]
  [ ! -f "$STUB/kubectl.argv" ]
  run bash -c 'printf "\n" | PATH="$STUB/bin:$PATH" "$0" k p' "$SCRIPT"
  [ "$status" -eq 2 ]
}

@test "a failed login returns 000 and the script exits 1" {
  cat > "$STUB/bin/curl" <<'C'
#!/usr/bin/env bash
out=""; while [ "$#" -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done
[ -n "$out" ] && printf '%s' '{"errors":["permission denied"]}' > "$out"
C
  printf '%s\n' "$HOSTILE" | run_script k p
  pod_script="$(pod_script_from_argv)"
  run bash -c 'printf "%s" "$HOSTILE" | SET_KEY=k SET_PROP=p SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script"
  [ "$status" -eq 0 ]
  [ "$output" = "000" ]
}

# --object <key>: the properties of one key, as a JSON object on stdin, in ONE
# KV write. A slot of two properties (kid and key) must never hold one without
# the other, and clearing a slot writes empty strings (hosted rotation drill,
# platform-signing-keys step 3).
run_object_in_pod() { # run_object_in_pod <key> <stdin json>
  printf '%s' "$2" | run_script --object "$1"
  pod_script="$(pod_script_from_argv)"
  run bash -c 'cat "$STUB/kubectl.stdin" | SET_KEY="$2" SET_PROP= SET_MODE=object SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script" "$1"
}

@test "--object clears a slot: empty strings for kid and key in one write" {
  run_object_in_pod gibson-cg-signing-key-previous '{"kid":"","key":""}'
  [ "$status" -eq 0 ]
  [ "$output" = "200" ]
  grep -qxF -- 'SET_MODE=object' "$STUB/kubectl.argv"
  [ "$("$REAL_JQ" -c '.data | {kid,key}' "$STUB/write.body")" = '{"kid":"","key":""}' ]
  # The other property of the key stays, as in the single-property mode.
  [ "$("$REAL_JQ" -r '.data.other' "$STUB/write.body")" = "keep" ]
  # One write: the stub curl saw one body.
  [ "$(grep -c -- '--data-binary' "$STUB/curl.argv")" -eq 2 ] # the login and the KV write
}

@test "--object writes kid and key together, and no argv holds a value" {
  obj="$("$REAL_JQ" -nc --arg k "$HOSTILE" '{kid:"kid-2",key:$k}')"
  run_object_in_pod gibson-cg-signing-key-next "$obj"
  [ "$status" -eq 0 ]
  "$REAL_JQ" -j '.data.key' "$STUB/write.body" | cmp -s - <(printf '%s' "$HOSTILE")
  [ "$("$REAL_JQ" -r '.data.kid' "$STUB/write.body")" = "kid-2" ]
  for f in "$STUB/kubectl.argv" "$STUB/curl.argv" "$STUB/jq.argv"; do
    run no_value_on_argv "$f"
    [ "$status" -eq 0 ]
  done
  [ ! -e /tmp/pwned ]
}

@test "--object refuses a non-object, an empty object and a non-string value, with no kubectl call" {
  for bad in '' '"kid"' '[]' '{}' '{"kid":1}' '{"kid":null}' '{"kid":"a","key":{"x":"y"}}' 'not json'; do
    run bash -c 'printf "%s" "$1" | PATH="$STUB/bin:$PATH" "$0" --object k' "$SCRIPT" "$bad"
    [ "$status" -eq 2 ] || { echo "accepted: $bad"; return 1; }
    [ ! -f "$STUB/kubectl.argv" ]
  done
}

@test "FAILING FIXTURE: --object refuses a stream of several JSON documents, and no refusal prints the value" {
  # jq -e alone judges only the last document, so these streams passed the
  # check, and a later parse error printed the whole value (a private key).
  secret='-----BEGIN PRIVATE KEY-----SECRETMATERIAL42'
  for bad in "{\"a\":1} {\"kid\":\"x\"}" \
             "\"$secret\" {\"kid\":\"x\"}" \
             "{\"kid\":\"k1\",\"key\":\"$secret\"}{\"kid\":\"k2\",\"key\":\"$secret\"}" \
             "{\"kid\":\"k1\",\"key\":\"$secret\"} not-json" \
             "{\"kid\":\"k1\",\"key\":\"$secret\""; do
    run bash -c 'printf "%s" "$1" | PATH="$STUB/bin:$PATH" "$0" --object k' "$SCRIPT" "$bad"
    [ "$status" -eq 2 ] || { echo "accepted: $bad"; return 1; }
    [ ! -f "$STUB/kubectl.argv" ]
    [[ "$output" != *SECRETMATERIAL42* ]] || { echo "the refusal printed the value: $output"; return 1; }
  done
}

@test "inside the pod a body that jq cannot build prints a fixed message, never the value" {
  printf '%s' '{"kid":"k"}' | run_script --object k
  pod_script="$(pod_script_from_argv)"
  # The value reaches the pod as two documents: the host check is bypassed here.
  run bash -c 'printf "%s" "{\"key\":\"SECRETMATERIAL42\"} {\"x\":\"y\"}" | SET_KEY=k SET_PROP= SET_MODE=object SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script"
  [ "$status" -eq 0 ]
  [[ "$output" == *"000"* ]]
  [[ "$output" == *"the write body could not be built"* ]]
  [[ "$output" != *SECRETMATERIAL42* ]]
}

@test "--object takes exactly one key and no property" {
  run bash -c 'printf "{\"kid\":\"\"}" | PATH="$STUB/bin:$PATH" "$0" --object k p' "$SCRIPT"
  [ "$status" -eq 2 ]
  [ ! -f "$STUB/kubectl.argv" ]
}

# The read of the current entry must succeed before the merge. A refused read
# wrote the new property alone and dropped every other property of the key,
# for example the `value` of the Zitadel master key while its `next` is set.
@test "FAILING FIXTURE: a refused read of the current entry writes nothing, and the script exits 1" {
  printf '%s\n' "$HOSTILE" | run_script k p
  pod_script="$(pod_script_from_argv)"
  for code in 403 500 503; do
    rm -f "$STUB/write.body"
    run bash -c 'printf "%s" "$HOSTILE" | STUB_READ_CODE="$2" SET_KEY=k SET_PROP=p SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script" "$code"
    [ "$status" -eq 0 ]
    [[ "$output" == *"returned HTTP $code, so nothing was written"* ]] || { echo "code $code: $output"; return 1; }
    [ "${lines[${#lines[@]}-1]}" = "000" ]
    [ ! -e "$STUB/write.body" ] || { echo "code $code wrote: $(cat "$STUB/write.body")"; return 1; }
  done
  # The host side turns the 000 into exit 1.
  cat > "$STUB/bin/kubectl" <<'K'
#!/usr/bin/env bash
case "$*" in *" exec "*) cat >/dev/null; printf '000' ;; esac
K
  run bash -c 'printf "v\n" | PATH="$STUB/bin:$PATH" "$0" k p' "$SCRIPT"
  [ "$status" -eq 1 ]
}

@test "a key with no entry yet (404) is written with the new property alone" {
  printf '%s\n' "$HOSTILE" | run_script k p
  pod_script="$(pod_script_from_argv)"
  run bash -c 'printf "%s" "v1" | STUB_READ_CODE=404 SET_KEY=k SET_PROP=p SA_TOKEN_FILE="$STUB/sa-token" PATH="$STUB/bin:$PATH" sh -c "$1"' _ "$pod_script"
  [ "$status" -eq 0 ]
  [ "$output" = "200" ]
  [ "$("$REAL_JQ" -c '.data' "$STUB/write.body")" = '{"p":"v1"}' ]
}
