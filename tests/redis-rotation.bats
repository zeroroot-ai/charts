#!/usr/bin/env bats
# The redis password rotation CronJob (deploy#517, ADR-0171).
#
# The test renders the chart, takes the real script out of the rendered
# CronJob, and runs it under bash against a stub kubectl. The stub keeps a
# small redis: the set of passwords its ACL accepts. It also records each
# restart. The rotation logic under test is the rendered text.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/rotation.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "CronJob" and d["metadata"]["name"].endswith("-redis-rotation"):
        c = d["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
        open(sys.argv[2], "w").write(c["command"][-1])
        sys.exit(0)
sys.exit("no redis-rotation CronJob in the render")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() {
  S="$(mktemp -d)"; export S
  mkdir -p "$S/bin"
  cat > "$S/bin/kubectl" <<'STUB'
#!/usr/bin/env bash
# State files: ver, pw (the Secret), state_ver, state_pw, acl (one accepted
# password per line), restarts, change_on_restart (a password the Secret
# takes during the first restart).
b64() { printf '%s' "$1" | base64 -w0; }
args="$*"
case "$args" in
  *"get secret -n gibson gibson-redis-stack"*resourceVersion*) cat "$S/ver" ;;
  *"get secret -n gibson gibson-redis-stack -o jsonpath={.data."*) b64 "$(cat "$S/pw")" ;;
  *"get secret -n gibson gibson-redis-stack"*) exit 0 ;;
  *"get secret -n gibson gibson-redis-rotation-state"*lastProcessedVersion*)
    [ -f "$S/state_ver" ] || exit 1; b64 "$(cat "$S/state_ver")" ;;
  *"get secret -n gibson gibson-redis-rotation-state"*lastKnownPassword*)
    [ -f "$S/state_pw" ] || exit 1; b64 "$(cat "$S/state_pw")" ;;
  *"create secret generic gibson-redis-rotation-state"*)
    for a in "$@"; do
      case "$a" in
        --from-literal=lastProcessedVersion=*) printf '%s' "${a#*=*=}" > "$S/state_ver" ;;
        --from-literal=lastKnownPassword=*) printf '%s' "${a#*=*=}" > "$S/state_pw" ;;
      esac
    done ;;
  *"apply -f -"*) cat >/dev/null ;;
  *"exec -i"*)
    read -r login
    grep -qxF -- "$login" "$S/acl" || { echo "AUTH failed: WRONGPASS invalid username-password pair"; echo "NOAUTH Authentication required."; exit 0; }
    case "$args" in
      *PING*) echo PONG ;;
      *"ACL SETUSER"*)
        read -r new
        if [ "${!#}" = resetpass ]; then printf '%s\n' "$new" > "$S/acl"; else printf '%s\n' "$new" >> "$S/acl"; fi
        echo OK ;;
    esac ;;
  *"rollout restart"*)
    echo "$3" >> "$S/restarts"
    if [ -s "$S/change_on_restart" ]; then
      cp "$S/change_on_restart" "$S/pw"; : > "$S/change_on_restart"; echo 99 > "$S/ver"
    fi ;;
  *"rollout status"*) exit 0 ;;
  *) echo "stub kubectl: unexpected: $args" >&2; exit 3 ;;
esac
STUB
  chmod +x "$S/bin/kubectl"
}

teardown() { rm -rf "$S"; }

# world <secret pw> <secret version> <state pw> <state version> <acl passwords...>
world() {
  printf '%s' "$1" > "$S/pw"; printf '%s' "$2" > "$S/ver"
  [ -z "$3" ] || printf '%s' "$3" > "$S/state_pw"
  [ -z "$4" ] || printf '%s' "$4" > "$S/state_ver"
  shift 4; : > "$S/acl"; for p in "$@"; do printf '%s\n' "$p" >> "$S/acl"; done
}

run_job() { run env PATH="$S/bin:$PATH" S="$S" bash "$WORK/rotation.sh"; }

@test "a rotation adds the new password, restarts the four clients, then removes the old one" {
  world new 2 old 1 old
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  [ "$(cat "$S/acl")" = "new" ]
  [ "$(wc -l < "$S/restarts")" -eq 4 ]
  grep -qx 'deployment/gibson-ext-authz' "$S/restarts"
  grep -qx 'deployment/gibson-ratelimit' "$S/restarts"
  [ "$(cat "$S/state_ver")" = 2 ]
  [ "$(cat "$S/state_pw")" = new ]
}

@test "the old password works during the restarts" {
  world new 2 old 1 old
  # Record the ACL at the first restart.
  sed -i 's|    echo "$3" >> "$S/restarts"|    echo "$3" >> "$S/restarts"; cp "$S/acl" "$S/acl_at_restart"|' "$S/bin/kubectl"
  run_job
  [ "$status" -eq 0 ]
  grep -qx old "$S/acl_at_restart"
  grep -qx new "$S/acl_at_restart"
}

@test "a store that already runs with the new password only still restarts the clients and completes" {
  world new 2 old 1 new
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$S/restarts")" -eq 4 ]
  [ "$(cat "$S/state_ver")" = 2 ]
}

@test "FAILING FIXTURE: a store that accepts neither password fails the job and keeps the state" {
  world new 2 old 1 other
  run_job
  [ "$status" -ne 0 ]
  [ ! -s "$S/restarts" ]
  [ "$(cat "$S/state_ver")" = 1 ]
}

@test "a new Secret version with the same password restarts nothing" {
  world same 2 same 1 same
  run_job
  [ "$status" -eq 0 ]
  [ ! -s "$S/restarts" ]
  [ "$(cat "$S/state_ver")" = 2 ]
}

@test "a second change during the restarts keeps the old password and leaves the state for the next run" {
  world new 2 old 1 old
  printf 'newer' > "$S/change_on_restart"
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  grep -qx old "$S/acl"
  [ "$(cat "$S/state_ver")" = 1 ]
  # The next run adds the newest password while the old one still works.
  run_job
  [ "$status" -eq 0 ]
  [ "$(cat "$S/acl")" = "newer" ]
  [ "$(cat "$S/state_pw")" = newer ]
}

@test "the first run records the state and restarts nothing" {
  world pw1 5 "" "" pw1
  run_job
  [ "$status" -eq 0 ]
  [ ! -s "$S/restarts" ]
  [ "$(cat "$S/state_ver")" = 5 ]
}
