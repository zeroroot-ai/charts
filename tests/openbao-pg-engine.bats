#!/usr/bin/env bats
# The database engine of the platform login roles (ADR-0171, row
# postgres-role-passwords).
#
# The test renders the chart, takes the real pg_engine_ensure out of the
# rendered openbao-auto-init sidecar, and runs it under sh with a stub curl
# that answers from files and records each write.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml" 2>/dev/null
  python3 - "$WORK/render.yaml" "$WORK/pg.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "pg_engine_ensure()" in a:
                    open(sys.argv[2], "w").write(a[a.index('PG_ENGINE_HOST="'):a.index("cg_key_rotate() {")])
                    sys.exit(0)
sys.exit("no pg_engine_ensure in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() {
  S="$(mktemp -d)"; export S
  mkdir -p "$S/su" "$S/tmp"
  printf 'postgres' > "$S/su/username"; printf 'superpw\n' > "$S/su/password"
  echo '{}' > "$S/kv.json"
}
teardown() { rm -rf "$S"; }

# run_engine [MOUNT_STATUS] [CONFIG_USER]: the mount answers MOUNT_STATUS
# (200 = enabled), and each database/config read names CONFIG_USER.
run_engine() {
  run sh -c '
    set -u
    TMPD="$S/tmp"; BAO_ADDR_LOCAL=http://b
    sed "s#/etc/openbao/pg-superuser#$S/su#" "$WORK/pg.sh" > "$S/pg.sh"
    . "$S/pg.sh"
    kv_get() { cat "$S/kv.json"; }
    kv_put() { printf "%s" "$3" > "$S/kv.json"; }
    curl() {
      out=""; method=GET; data=""; url=""
      while [ $# -gt 0 ]; do case "$1" in
        -o) out="$2"; shift 2 ;; -X) method="$2"; shift 2 ;;
        -d) data="$2"; shift 2 ;; --data-binary) data="$(cat)"; shift 2 ;;
        -w|-H) shift 2 ;; -sS) shift ;; *) url="$1"; shift ;; esac; done
      path="${url#http://b/v1/}"
      if [ "$method" = POST ]; then
        echo "POST $path" >> "$S/log"; printf "%s" "$data" > "$S/body.$(printf "%s" "$path" | tr / _)"
        [ -n "$out" ] && : > "$out"; printf 204; return 0
      fi
      case "$path" in
        sys/mounts/database) [ -n "$out" ] && : > "$out"; printf "%s" "${1:-$MOUNT}" ;;
        database/config/*) printf "{\"data\":{\"connection_details\":{\"username\":\"%s\"}}}" "$CFG_USER" ;;
      esac
    }
    pg_engine_ensure tok; echo "rc=$?"
  ' 
}

@test "a fresh store gets the engine, one connection and one role for each owner" {
  MOUNT=404 CFG_USER="" run_engine
  [[ "$output" == *"rc=0"* ]] || { echo "$output"; false; }
  grep -qx "POST sys/mounts/database" "$S/log"
  [ "$(grep -c '^POST database/config/' "$S/log")" -eq 5 ]
  [ "$(grep -c '^POST database/roles/' "$S/log")" -eq 5 ]
  jq -e '.connection_url | test("@platform-postgres-rw.gibson.svc:5432/zitadel\\?sslmode=require$")' "$S/body.database_config_zitadel"
  jq -e '.allowed_roles == ["zitadel"] and .password == "superpw" and .username == "postgres"' "$S/body.database_config_zitadel"
  jq -e '.db_name == "tenant-admin"' "$S/body.database_roles_tenant-admin"
  jq -e '.creation_statements[0] | endswith("IN ROLE \"tenant_admin\";")' "$S/body.database_roles_tenant-admin"
  jq -e '.revocation_statements == ["REASSIGN OWNED BY \"{{name}}\" TO \"spire\";", "DROP OWNED BY \"{{name}}\";", "DROP ROLE IF EXISTS \"{{name}}\";"]' "$S/body.database_roles_spire"
  jq -e '.default_ttl == "48h"' "$S/body.database_roles_openfga"
}

@test "an unchanged superuser credential writes no connection again" {
  MOUNT=404 CFG_USER="" run_engine
  rm -f "$S/log"
  MOUNT=200 CFG_USER=postgres run_engine
  [[ "$output" == *"rc=0"* ]] || { echo "$output"; false; }
  ! grep -q '^POST database/config/' "$S/log"
  ! grep -q '^POST sys/mounts' "$S/log"
}

@test "FAILING FIXTURE: a new superuser password writes each connection again" {
  MOUNT=404 CFG_USER="" run_engine
  rm -f "$S/log"
  printf 'otherpw\n' > "$S/su/password"
  MOUNT=200 CFG_USER=postgres run_engine
  [ "$(grep -c '^POST database/config/' "$S/log")" -eq 5 ]
  jq -e '.password == "otherpw"' "$S/body.database_config_openfga"
}

@test "no superuser Secret yet: the engine waits and fails nothing" {
  rm -f "$S/su/username" "$S/su/password"
  MOUNT=404 CFG_USER="" run_engine
  [[ "$output" == *"rc=0"* ]]
  [ ! -e "$S/log" ]
}
