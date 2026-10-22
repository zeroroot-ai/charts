#!/usr/bin/env bats
# The sidecar login-client-pat-store of the Zitadel setup Job (charts#407).
#
# It writes the first login-client PAT to OpenBao when Zitadel writes the
# file, and it learns the end of the setup run from SIGTERM, with no
# Kubernetes API. A run that minted no PAT must find the PAT in OpenBao
# (metadata only) or fail, so a lost first PAT never passes as success.
#
# The test runs the rendered script with a stub curl. The paths /login-client
# and /tmp of the script move to a temporary directory.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK_DIR="$(mktemp -d)"
  export WORK_DIR
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK_DIR/render.yaml" 2>/dev/null
  python3 - "$WORK_DIR/render.yaml" > "$WORK_DIR/store.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "Job" and d["metadata"]["name"] == "gibson-zitadel-setup":
        for c in d["spec"]["template"]["spec"].get("initContainers") or []:
            if c["name"] == "login-client-pat-store":
                print(c["args"][0])
PY
}

teardown_file() { rm -rf "$WORK_DIR"; }

setup() {
  D="$(mktemp -d "$WORK_DIR/case.XXXX")"
  mkdir -p "$D/login-client" "$D/tmp" "$D/bin"
  sed -e "s#/login-client/pat#$D/login-client/pat#g" -e "s#^\( *\)T=/tmp\$#\1T=$D/tmp#" \
    "$WORK_DIR/store.sh" > "$D/store.sh"
  # The stub curl: the metadata read answers META_CODE with META_BODY, the
  # write answers WRITE_CODE and records the body.
  cat > "$D/bin/curl" <<'SH'
#!/usr/bin/env bash
out=""; url=""; post=""; body=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift 2 ;;
    -X) [ "$2" = POST ] && post=1; shift 2 ;;
    --data-binary) body="${2#@}"; shift 2 ;;
    -H|-w|--max-time) shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ -n "$post" ]; then
  cp "$body" "$STUB_DIR/written.json"
  printf '{}' > "$out"; printf '%s' "$WRITE_CODE"
else
  printf '%s' "$META_BODY" > "$out"; printf '%s' "$META_CODE"
fi
SH
  chmod +x "$D/bin/curl"
  export STUB_DIR="$D" PATH="$D/bin:$PATH" BAO_ADDR=https://bao VAULT_TOKEN=t
  export WRITE_CODE=204 META_CODE=404 META_BODY='{}'
}

# run_store [seconds before SIGTERM]: start the sidecar, stop it as the
# kubelet does when the setup container ends, and record its exit code.
run_store() {
  bash "$D/store.sh" > "$D/log" 2>&1 &
  local pid=$!
  sleep "${1:-4}"
  kill -TERM "$pid"
  local rc=0
  wait "$pid" || rc=$?
  echo "$rc" > "$D/rc"
}

@test "the store script renders" {
  [ -s "$WORK_DIR/store.sh" ]
}

@test "a minted PAT reaches OpenBao, and SIGTERM then exits 0" {
  printf 'pat-value\n' > "$D/login-client/pat"
  run_store 5
  [ "$(cat "$D/rc")" = 0 ]
  grep -q '^STORED' "$D/log"
  [ "$(jq -r .data.pat "$D/written.json")" = pat-value ]
}

@test "no PAT and an entry in OpenBao: the instance existed before, exit 0" {
  export META_CODE=200 META_BODY='{"data":{"current_version":3}}'
  run_store 3
  [ "$(cat "$D/rc")" = 0 ]
  grep -q 'the instance existed before this run' "$D/log"
  [ ! -e "$D/written.json" ]
}

@test "FAILING FIXTURE: no PAT and no entry in OpenBao fails with the recovery step" {
  run_store 3
  [ "$(cat "$D/rc")" = 1 ]
  grep -q '^ERROR: this setup run wrote no login-client PAT, and OpenBao holds none' "$D/log"
  grep -q '^RECOVERY:' "$D/log"
  run grep -q 'existed before' "$D/log"
  [ "$status" -ne 0 ]
}

@test "FAILING FIXTURE: no PAT and an entry with no live version fails" {
  export META_CODE=200 META_BODY='{"data":{"current_version":0}}'
  run_store 3
  [ "$(cat "$D/rc")" = 1 ]
  grep -q '^RECOVERY:' "$D/log"
}

@test "FAILING FIXTURE: a minted PAT that OpenBao refuses fails at SIGTERM" {
  export WRITE_CODE=403
  printf 'pat-value\n' > "$D/login-client/pat"
  run_store 4
  [ "$(cat "$D/rc")" = 1 ]
  grep -q '^ERROR: Zitadel minted the login-client PAT, and it did not reach' "$D/log"
}
