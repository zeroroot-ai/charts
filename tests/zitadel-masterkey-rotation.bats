#!/usr/bin/env bats
# The Zitadel masterkey rotation (ADR-0171, row platform-generated-secrets).
#
# The test renders the chart and runs the rendered rewrap step of the CronJob
# zitadel-masterkey-rotation under bash, with the real openssl and a stub
# psql that keeps the tables in files. A small Go-compatible check: each key
# that the step writes decrypts with the next masterkey to the plaintext it
# had, through the same AES-CFB the step uses.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK_DIR="$(mktemp -d)"
  export WORK_DIR
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK_DIR/render.yaml" 2>/dev/null
  python3 - "$WORK_DIR/render.yaml" "$WORK_DIR" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "CronJob" and d["metadata"]["name"].endswith("-zitadel-masterkey-rotation"):
        ps = d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
        for c in ps["initContainers"] + ps["containers"]:
            open(f"{sys.argv[2]}/{c['name']}.sh", "w").write(c["args"][0])
        sys.exit(0)
sys.exit("no masterkey rotation CronJob in the render")
PY
}

teardown_file() { rm -rf "$WORK_DIR"; }

CUR="abcdefghijklmnopqrstuvwxyz012345-and-more-than-32"
NXT="ZYXWVUTSRQPONMLKJIHGFEDCBA987654-and-more-than-32"

setup() {
  D="$(mktemp -d)"; export D
  mkdir -p "$D/work" "$D/tmp" "$D/bin" "$D/db"
  # The rendered step reads /work and /tmp; point them at the test dirs.
  # /tmp first: the test dir itself lies under /tmp.
  sed -e "s#/tmp/#$D/tmp/#g" -e "s#/work/#$D/work/#g" "$WORK_DIR/rewrap.sh" > "$D/rewrap.sh"
  # Helpers of the rendered step, for the test's own encryption.
  sed -n '/^hex_of()/,/^marker_hex=/p' "$D/rewrap.sh" | sed '$d' > "$D/crypto.sh"
  cat > "$D/bin/psql" <<'SH'
#!/usr/bin/env bash
# A stub psql: the marker and the encryption keys live in files under $D/db.
args="$*"
case "$args" in
  *"CREATE SCHEMA"*) exit 0 ;;
  *"SELECT marker"*) cat "$D/db/marker" 2>/dev/null; exit 0 ;;
  *"INSERT INTO gibson_rotation.masterkey_marker"*)
    printf '%s' "$args" | sed -n "s/.*VALUES (1, '\([^']*\)').*/\1/p" > "$D/db/marker"; exit 0 ;;
  *"SELECT id, key"*) for f in "$D"/db/key.*; do [ -e "$f" ] && printf '%s|%s\n' "${f##*/key.}" "$(cat "$f")"; done; exit 0 ;;
esac
# -f FILE: apply the UPDATEs of one transaction.
file=""; while [ $# -gt 0 ]; do [ "$1" = -f ] && file="$2"; shift; done
[ -n "$file" ] || { echo "stub psql: unexpected call: $args" >&2; exit 3; }
cp "$file" "$D/db/last.sql"
while IFS= read -r line; do
  case "$line" in
    "UPDATE system.encryption_keys SET key = '"*)
      k="$(printf '%s' "$line" | sed -n "s/.*SET key = '\([^']*\)' WHERE id = '\([^']*\)'.*/\1/p")"
      i="$(printf '%s' "$line" | sed -n "s/.*WHERE id = '\([^']*\)'.*/\1/p")"
      printf '%s' "$k" > "$D/db/key.$i" ;;
    "UPDATE gibson_rotation.masterkey_marker SET marker = '"*)
      printf '%s' "$line" | sed -n "s/.*SET marker = '\([^']*\)'.*/\1/p" > "$D/db/marker" ;;
  esac
done < "$file"
SH
  chmod +x "$D/bin/psql"
}
teardown() { rm -rf "$D"; }

enc() { bash -c ". '$D/crypto.sh'; mk_encrypt_hex \"\$(printf '%s' \"\$1\" | hex_of)\" \"\$2\"" _ "$1" "${2:0:32}"; }
dec() { bash -c ". '$D/crypto.sh'; mk_decrypt_hex \"\$1\" \"\$2\" | sed 's/../\\\\x&/g' | xargs -0 printf '%b'" _ "$1" "${2:0:32}"; }
kv() { printf '%s' "$1" > "$D/work/value"; printf '%s' "$2" > "$D/work/next"; echo none > "$D/work/action"; }
run_step() { run env PATH="$D/bin:$PATH" bash -ec "$(cat "$D/rewrap.sh")"; }
marker_text="gibson-zitadel-masterkey-marker-v1"

@test "the first run writes the marker with the current masterkey, and changes nothing else" {
  kv "$CUR" ""
  run_step
  [ "$status" -eq 0 ]
  [ "$(dec "$(cat "$D/db/marker")" "$CUR")" = "$marker_text" ]
  [ "$(cat "$D/work/action")" = none ]
}

@test "with no rotation pending, a matching marker changes nothing" {
  kv "$CUR" ""
  enc "$marker_text" "$CUR" > "$D/db/marker"
  run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$D/work/action")" = none ]
}

@test "a pending rotation rewraps every key and the marker with the next masterkey, in one transaction" {
  kv "$CUR" "$NXT"
  enc "$marker_text" "$CUR" > "$D/db/marker"
  enc "first-zitadel-key-value-0123456" "$CUR" > "$D/db/key.idA"
  enc "second-zitadel-key-value-abcdef" "$CUR" > "$D/db/key.idB"
  run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$D/work/action")" = flip ]
  [ "$(dec "$(cat "$D/db/key.idA")" "$NXT")" = "first-zitadel-key-value-0123456" ]
  [ "$(dec "$(cat "$D/db/key.idB")" "$NXT")" = "second-zitadel-key-value-abcdef" ]
  [ "$(dec "$(cat "$D/db/marker")" "$NXT")" = "$marker_text" ]
  [ "$(head -n1 "$D/db/last.sql")" = "BEGIN;" ]
  [ "$(tail -n1 "$D/db/last.sql")" = "COMMIT;" ]
}

@test "rows that already use the next masterkey only move next to value" {
  kv "$CUR" "$NXT"
  enc "$marker_text" "$NXT" > "$D/db/marker"
  enc "first-zitadel-key-value-0123456" "$NXT" > "$D/db/key.idA"
  before="$(cat "$D/db/key.idA")"
  run_step
  [ "$status" -eq 0 ]
  [ "$(cat "$D/work/action")" = flip ]
  [ "$(cat "$D/db/key.idA")" = "$before" ]
  [ ! -e "$D/db/last.sql" ]
}

@test "FAILING FIXTURE: a marker that neither masterkey decrypts stops the step, and nothing changes" {
  kv "$CUR" "$NXT"
  enc "$marker_text" "some-other-masterkey-of-32-chars" > "$D/db/marker"
  enc "first-zitadel-key-value-0123456" "$CUR" > "$D/db/key.idA"
  before="$(cat "$D/db/key.idA")"
  run_step
  [ "$status" -ne 0 ]
  [[ "$output" == *"decrypts with neither"* ]]
  [ "$(cat "$D/work/action")" = none ]
  [ "$(cat "$D/db/key.idA")" = "$before" ]
}

@test "FAILING FIXTURE: a next masterkey shorter than 32 characters changes nothing" {
  kv "$CUR" "short"
  enc "$marker_text" "$CUR" > "$D/db/marker"
  run_step
  [ "$status" -ne 0 ]
  [ "$(cat "$D/work/action")" = none ]
}

@test "the write step moves next to value only on flip" {
  grep -q 'if \[ "$action" != flip \]' "$WORK_DIR/write.sh"
  grep -q '{data:{value:$v,next:""}}' "$WORK_DIR/write.sh"
}
