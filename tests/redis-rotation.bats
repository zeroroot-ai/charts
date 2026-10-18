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
  *"get secret -n gibson gibson-redis-rotation-state -o name"*)
    if [ -n "${STATE_API_ERROR:-}" ]; then echo "Error from server (InternalError): etcdserver: request timed out" >&2; exit 1; fi
    [ -f "$S/state_ver" ] || { echo 'Error from server (NotFound): secrets "gibson-redis-rotation-state" not found' >&2; exit 1; }
    echo secret/gibson-redis-rotation-state ;;
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
    # kubectl names the default container on stderr when -c is absent and
    # the pod has init containers, as redis-stack does.
    case "$args" in *" -c redis-stack "*) ;; *) echo 'Defaulted container "redis-stack" out of: redis-stack, own-data (init), init-acl (init)' >&2 ;; esac
    if [ -n "${EXEC_FAILS:-}" ]; then echo 'error: unable to upgrade connection: container not found ("redis-stack")' >&2; exit 1; fi
    read -r login
    grep -qxF -- "$login" "$S/acl" || { echo "AUTH failed: WRONGPASS invalid username-password pair"; echo "NOAUTH Authentication required."; exit 0; }
    case "$args" in
      *PING*) echo PONG ;;
      *"ACL SETUSER"*)
        read -r new
        if [ "${!#}" = resetpass ]; then printf '%s\n' "$new" > "$S/acl"; else printf '%s\n' "$new" >> "$S/acl"; fi
        echo OK ;;
    esac ;;
  # A restart is a pod delete (charts#725). Each workload has one pod,
  # "<name>-0", owned by a controller. A delete gives the pod a new UID.
  *"rollout"*) echo "stub kubectl: a rollout restart patches the workload, and the identity admission policy denies it (charts#725)" >&2; exit 1 ;;
  "-n gibson get statefulset/"*" -o json"|"-n gibson get deployment/"*" -o json")
    w="${4}"; name="${w#*/}"
    printf '%s' "$w" > "$S/kind.$name"
    jq -n --arg n "$name" --arg k "$(case "$w" in statefulset/*) echo StatefulSet ;; *) echo Deployment ;; esac)" \
      '{kind: $k, spec: {replicas: 1, selector: {matchLabels: {app: $n}}}}' ;;
  "-n gibson get pods -l app="*" -o json")
    name="${6#app=}"; uid="u0"; [ ! -f "$S/uid.$name" ] || uid="$(cat "$S/uid.$name")"
    # NEVER_READY: a new pod never becomes Ready.
    r=True; [ -z "${NEVER_READY:-}" ] || [ "$uid" = u0 ] || r=False
    jq -n --arg n "${name}-0" --arg u "$uid" --arg r "$r" \
      '{items: [{metadata: {name: $n, uid: $u, ownerReferences: [{kind: "ReplicaSet", controller: true}]}, status: {phase: "Running", conditions: [{type: "Ready", status: $r}]}}]}' ;;
  "-n gibson delete pod "*"-0 --ignore-not-found --wait=true --timeout="*)
    name="${5%-0}"
    cat "$S/kind.$name" >> "$S/restarts"; echo >> "$S/restarts"
    echo "u$(( $(wc -l < "$S/restarts") ))" > "$S/uid.$name"
    if [ -s "$S/change_on_restart" ]; then
      cp "$S/change_on_restart" "$S/pw"; : > "$S/change_on_restart"; echo 99 > "$S/ver"
    fi ;;
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

run_job() { run env PATH="$S/bin:$PATH" S="$S" RESTART_PODS_POLL=0 bash "$WORK/rotation.sh"; }

@test "a rotation adds the new password, restarts the four clients by a pod delete, then removes the old one" {
  world new 2 old 1 old
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  [ "$(cat "$S/acl")" = "new" ]
  [ "$(wc -l < "$S/restarts")" -eq 4 ]
  grep -qx 'statefulset/gibson-gibson-workloads' "$S/restarts" || { cat "$S/restarts"; false; }
  grep -qx 'deployment/gibson-ext-authz' "$S/restarts"
  grep -qx 'deployment/gibson-ratelimit' "$S/restarts"
  [ "$(cat "$S/state_ver")" = 2 ]
  [ "$(cat "$S/state_pw")" = new ]
}

@test "the old password works during the restarts" {
  world new 2 old 1 old
  # Record the ACL at the first restart.
  sed -i 's|    cat "$S/kind.$name" >> "$S/restarts"; echo >> "$S/restarts"|    cat "$S/kind.$name" >> "$S/restarts"; echo >> "$S/restarts"; cp "$S/acl" "$S/acl_at_restart"|' "$S/bin/kubectl"
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

@test "FAILING FIXTURE: an API error on the state read fails the job and records nothing" {
  world new 2 old 1 old
  STATE_API_ERROR=1 run env PATH="$S/bin:$PATH" S="$S" STATE_API_ERROR=1 bash "$WORK/rotation.sh"
  [ "$status" -ne 0 ]
  [ "$(cat "$S/state_ver")" = 1 ]
  [ ! -s "$S/restarts" ]
}

@test "FAILING FIXTURE: a failed exec of an ACL step fails the job, names the pod, and keeps the state" {
  world new 2 old 1 old
  run env PATH="$S/bin:$PATH" S="$S" EXEC_FAILS=1 bash "$WORK/rotation.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"gibson-redis-stack-0"* ]]
  [ "$(cat "$S/state_ver")" = 1 ]
  [ ! -s "$S/restarts" ]
}

@test "FAILING FIXTURE: the rendered rotation never runs a rollout restart, which the identity admission policy denies (charts#725)" {
  # The rotation before charts#725 ran `kubectl rollout restart` and
  # `kubectl rollout status`. The pattern finds both forms, and not a comment.
  run grep -nE '^[^#]*kubectl[^#]*rollout (restart|status)' "$WORK/rotation.sh"
  [ "$status" -eq 1 ] || { echo "$output"; false; }
  printf '%s\n' '    kubectl rollout restart "$1" -n "$NS"' > "$S/old.sh"
  run grep -nE '^[^#]*kubectl[^#]*rollout (restart|status)' "$S/old.sh"
  [ "$status" -eq 0 ]
  printf '%s\n' '# kubectl rollout restart "$1"' > "$S/comment.sh"
  run grep -nE '^[^#]*kubectl[^#]*rollout (restart|status)' "$S/comment.sh"
  [ "$status" -eq 1 ]
}

@test "FAILING FIXTURE: a restart whose new pod never becomes Ready fails the job and keeps the old password" {
  world new 2 old 1 old
  # The daemon timeout drops to 1s so the test does not wait 900s.
  sed 's|restart "statefulset/${DAEMON_STS}" 900|restart "statefulset/${DAEMON_STS}" 1|' "$WORK/rotation.sh" > "$S/rotation.sh"
  run grep -c 'restart "statefulset/${DAEMON_STS}" 1$' "$S/rotation.sh"
  [ "$output" = 1 ]
  run env PATH="$S/bin:$PATH" S="$S" RESTART_PODS_POLL=0 NEVER_READY=1 bash "$S/rotation.sh"
  echo "$output"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the restart of statefulset/gibson-gibson-workloads did not finish"* ]]
  grep -qx old "$S/acl"
  [ "$(cat "$S/state_ver")" = 1 ]
}
