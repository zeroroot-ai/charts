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
    if d and d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "gibson-zitadel-masterkey-guard":
        open(f"{sys.argv[2]}/guard.sh", "w").write(d["data"]["guard.sh"])
for f in ("rewrap.sh", "write.sh", "guard.sh"):
    open(f"{sys.argv[2]}/{f}")
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
  for f in rewrap write guard; do
    sed -e "s#/tmp/#$D/tmp/#g" -e "s#/work/#$D/work/#g" "$WORK_DIR/$f.sh" > "$D/$f.sh"
  done
  export ROWS_MD5=0123456789abcdef0123456789abcdef
  # Helpers of the rendered step, for the test's own encryption.
  sed -n '/^hex_of()/,/^marker_hex=/p' "$D/rewrap.sh" > "$D/crypto.sh"
  cat > "$D/bin/psql" <<'SH'
#!/usr/bin/env bash
# A stub psql: the marker and the encryption keys live in files under $D/db.
args="$*"
case "$args" in
  *"CREATE SCHEMA"*) exit 0 ;;
  *"SELECT marker"*) cat "$D/db/marker" 2>/dev/null; exit 0 ;;
  *"INSERT INTO gibson_rotation.masterkey_marker"*)
    printf '%s' "$args" | sed -n "s/.*VALUES (1, '\([^']*\)').*/\1/p" > "$D/db/marker"; exit 0 ;;
  *"to_regclass"*) [ -e "$D/db/marker" ] && echo t || echo f; exit 0 ;;
  *"REPEATABLE READ"*)
    echo "$ROWS_MD5"
    for f in "$D"/db/key.*; do [ -e "$f" ] && printf '%s|%s\n' "${f##*/key.}" "$(cat "$f")"; done; exit 0 ;;
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
  [ "$(sed -n 2p "$D/db/last.sql")" = "LOCK TABLE system.encryption_keys IN EXCLUSIVE MODE;" ]
  grep -q "<> '${ROWS_MD5}' THEN RAISE EXCEPTION" "$D/db/last.sql"
  [ "$(tail -n1 "$D/db/last.sql")" = "COMMIT;" ]
  [ ! -e "$D/work/rows" ]
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

# A stub curl for the write step: it records the body of the POST.
stub_curl() {
  cat > "$D/bin/curl" <<'SH'
#!/usr/bin/env bash
for a in "$@"; do [ "$a" = "@-" ] && cat > "$D/posted"; done
printf '%s' "${CURL_CODE:-200}"
SH
  chmod +x "$D/bin/curl"
}
run_write() { run env PATH="$D/bin:$PATH" VAULT_TOKEN=t BAO_ADDR=http://bao sh -ec "$(cat "$D/write.sh")"; }

@test "the write step on flip writes value = next and an empty next" {
  stub_curl; kv "$CUR" "$NXT"; echo flip > "$D/work/action"
  run_write
  [ "$status" -eq 0 ]
  [ "$(jq -r .data.value "$D/posted")" = "$NXT" ]
  [ "$(jq -r .data.next "$D/posted")" = "" ]
}

@test "FAILING FIXTURE: the write step writes nothing when step 2 did not flip" {
  stub_curl; kv "$CUR" "$NXT"
  run_write
  [ "$status" -eq 0 ]
  [ ! -e "$D/posted" ]
}

@test "FAILING FIXTURE: a refused write fails the step" {
  stub_curl; kv "$CUR" "$NXT"; echo flip > "$D/work/action"
  CURL_CODE=403 run_write
  [ "$status" -ne 0 ]
}

run_guard() { run env PATH="$D/bin:$PATH" ZITADEL_MASTERKEY="$1" bash "$D/guard.sh"; }

@test "the guard passes a Zitadel workload when no marker exists yet" {
  run_guard "$CUR"
  [ "$status" -eq 0 ]
  [[ "$output" == *"no marker yet"* ]]
}

@test "the guard passes the masterkey that opens the marker" {
  enc "$marker_text" "$NXT" > "$D/db/marker"
  run_guard "$NXT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"opens the marker"* ]]
}

@test "FAILING FIXTURE: the guard stops a Zitadel workload that starts with the old masterkey after a rewrap" {
  kv "$CUR" "$NXT"
  enc "$marker_text" "$CUR" > "$D/db/marker"
  enc "first-zitadel-key-value-0123456" "$CUR" > "$D/db/key.idA"
  run_step
  [ "$status" -eq 0 ]
  run_guard "$CUR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not open the marker"* ]]
  run_guard "$NXT"
  [ "$status" -eq 0 ]
}

@test "FAILING FIXTURE: the guard refuses a masterkey shorter than 32 characters" {
  run_guard "short"
  [ "$status" -ne 0 ]
}

@test "each Zitadel workload runs the guard, with the image of postgresSetup" {
  python3 - "$WORK_DIR/render.yaml" "$ROOT/helm/gibson/values.yaml" <<'PY'
import sys, yaml
v = yaml.safe_load(open(sys.argv[2]))
want = "{repository}:{tag}".format(**v["postgresSetup"]["image"])
seen = set()
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") not in ("Deployment", "Job"):
        continue
    ps = d["spec"]["template"]["spec"]
    for c in ps.get("initContainers") or []:
        if c["name"] == "masterkey-guard":
            assert c["image"] == want, f"{d['metadata']['name']}: guard image {c['image']} is not {want}"
            seen.add(d["metadata"]["name"])
need = {"gibson-zitadel", "gibson-zitadel-init", "gibson-zitadel-setup"}
assert need <= seen, f"no masterkey guard on {sorted(need - seen)}"
PY
}
