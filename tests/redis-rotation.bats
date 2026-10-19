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
# KUBECTL_LOG: record each argv as one JSON array (scripts/check-kubectl-rbac.py).
[ -z "${KUBECTL_LOG:-}" ] || jq -cn '$ARGS.positional' --args -- "$@" >> "$KUBECTL_LOG"
args="$*"
case "$args" in
  # The whole Secret. Like kubectl since 1.21, managedFields appear only
  # with --show-managed-fields. The last write (ESO) is 10:00:00.
  *"get secret -n gibson gibson-redis-stack -o json"|*"get secret -n gibson gibson-redis-stack -o json --show-managed-fields")
    mf='[]'; [[ "$args" != *--show-managed-fields* ]] || mf='[{"manager": "external-secrets", "time": "2026-10-19T10:00:00Z"}]'
    jq -n --arg v "$(cat "$S/ver")" --arg p "$(b64 "$(cat "$S/pw")")" --argjson mf "$mf" \
      '{metadata: ({resourceVersion: $v} + (if $mf == [] then {} else {managedFields: $mf} end)), data: {"redis-password": $p}}' ;;
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
    # Run the rendered pod command (all argv after "--") with a fake
    # redis-cli on PATH, so the shell text that runs in the pod is under test.
    while [ "$1" != "--" ]; do shift; done; shift
    # lose_line: the next ACL SETUSER exec gets only its first stdin line,
    # as when a stdin frame of the exec stream is lost.
    if [ -f "$S/lose_line" ] && [[ "$*" == *SETUSER* ]]; then
      rm -f "$S/lose_line"
      head -n1 | env PATH="$S/podbin:$PATH" "$@"; exit $?
    fi
    # cut_line: the next ACL SETUSER exec gets its second line cut after two
    # bytes, with no newline: a partial password.
    if [ -f "$S/cut_line" ] && [[ "$*" == *SETUSER* ]]; then
      rm -f "$S/cut_line"
      in="$(cat)"; first="${in%%$'\n'*}"; second="${in#*$'\n'}"
      printf '%s\n%s' "$first" "${second:0:2}" | env PATH="$S/podbin:$PATH" "$@"; exit $?
    fi
    exec env PATH="$S/podbin:$PATH" "$@" ;;
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
    # The first pod (u0) started before the Secret write. Each new pod
    # started at 10:05:00, after the write and its margin of 60 s.
    # NEW_POD_TIME sets the start of each new pod.
    t="${NEW_POD_TIME:-2026-10-19T10:05:00Z}"; [ "$uid" != u0 ] || t="2026-01-01T00:00:00Z"
    jq -n --arg n "${name}-0" --arg u "$uid" --arg r "$r" --arg t "$t" \
      '{items: [{metadata: {name: $n, uid: $u, creationTimestamp: $t, ownerReferences: [{kind: "ReplicaSet", controller: true}]}, status: {phase: "Running", conditions: [{type: "Ready", status: $r}]}}]}' ;;
  *"delete pod"*"--wait=true"*) echo 'Error from server (Forbidden): pods is forbidden: cannot watch resource "pods"' >&2; exit 1 ;;
  "-n gibson delete pod "*"-0 --ignore-not-found --wait=false")
    name="${5%-0}"
    cat "$S/kind.$name" >> "$S/restarts"; echo >> "$S/restarts"
    echo "u$(( $(wc -l < "$S/restarts") ))" > "$S/uid.$name"
    if [ -s "$S/change_on_restart" ]; then
      cp "$S/change_on_restart" "$S/pw"; : > "$S/change_on_restart"; echo 99 > "$S/ver"
    fi ;;
  *) echo "stub kubectl: unexpected: $args" >&2; exit 3 ;;
esac
STUB
  # The fake redis-cli of the pod: the ACL is $S/acl, one accepted password
  # per line. acl_log records each password that ACL SETUSER adds.
  mkdir -p "$S/podbin"
  cat > "$S/podbin/redis-cli" <<'CLI'
#!/usr/bin/env bash
grep -qxF -- "$REDISCLI_AUTH" "$S/acl" || { echo "AUTH failed: WRONGPASS invalid username-password pair"; echo "NOAUTH Authentication required."; exit 0; }
[ "$1" = --no-auth-warning ] && shift
case "$1" in
  PING) echo PONG ;;
  ACL)
    shift 3; reset=""; [ "$1" = resetpass ] && { reset=1; shift; }
    new="${1#>}"; printf '[%s]\n' "$new" >> "$S/acl_log"
    if [ -n "$reset" ]; then printf '%s\n' "$new" > "$S/acl"; else printf '%s\n' "$new" >> "$S/acl"; fi
    echo OK ;;
esac
CLI
  chmod +x "$S/bin/kubectl" "$S/podbin/redis-cli"
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

@test "every kubectl call of the rotation is allowed by the rendered Role of its ServiceAccount" {
  # The paths of the job: the first run, a full rotation, a store that
  # already runs with the new password, and a Secret change with the same
  # password. scripts/check-kubectl-rbac.py maps each recorded call to its
  # API requests and checks them against the Role in the render. In charts
  # 0.150.0 the Role had pods/exec create and no pods get, so each job
  # failed at its first exec (drill run 39125128505).
  export KUBECTL_LOG="$S/calls.jsonl"
  world pw1 5 "" "" pw1;   run_job; [ "$status" -eq 0 ]
  world new 6 pw1 5 pw1;   run_job; [ "$status" -eq 0 ]
  world newer 7 new 6 newer; run_job; [ "$status" -eq 0 ]
  world newer 8 newer 7 newer; run_job; [ "$status" -eq 0 ]
  grep -q '"exec"' "$KUBECTL_LOG"
  grep -q '"delete"' "$KUBECTL_LOG"
  grep -q '"apply"' "$KUBECTL_LOG"
  run python3 "$ROOT/scripts/check-kubectl-rbac.py" --render "$WORK/render.yaml" \
    --sa gibson/gibson-redis-rotation --calls "$KUBECTL_LOG"
  echo "$output"
  [ "$status" -eq 0 ]
  # A floor: the four paths make at least 40 API requests, so the check
  # cannot pass on a log that lost most of the calls.
  n="$(sed -n 's/.*, \([0-9]*\) API requests,.*/\1/p' <<<"$output")"
  [ "${n:-0}" -ge 40 ] || { echo "only ${n:-0} API requests checked"; false; }
}

@test "FAILING FIXTURE: the same calls against the 0.150.0 Role (no pods get) fail the check" {
  export KUBECTL_LOG="$S/calls.jsonl"
  world new 2 old 1 old; run_job; [ "$status" -eq 0 ]
  # The render with the pods rule of the Role removed: the 0.150.0 shape.
  python3 - "$WORK/render.yaml" "$S/render-0150.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for d in docs:
    if d.get("kind") == "Role" and d["metadata"]["name"] == "gibson-redis-rotation":
        d["rules"] = [r for r in d["rules"] if r.get("resources") != ["pods"]]
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run python3 "$ROOT/scripts/check-kubectl-rbac.py" --render "$S/render-0150.yaml" \
    --sa gibson/gibson-redis-rotation --calls "$KUBECTL_LOG"
  echo "$output"
  [ "$status" -eq 1 ]
  [[ "$output" == *"needs get pods gibson-redis-stack-0"* ]]
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
  echo "$output"
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

@test "FAILING FIXTURE: a lost stdin line never adds an empty password, and the step is sent again" {
  world new 2 old 1 old
  : > "$S/lose_line"
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  # No ACL SETUSER ever added an empty password.
  run grep -cxF '[]' "$S/acl_log"
  [ "$output" = 0 ] || { cat "$S/acl_log"; false; }
  [ ! -e "$S/lose_line" ]
  [ "$(cat "$S/acl")" = "new" ]
  [ "$(cat "$S/state_ver")" = 2 ]
}

@test "FAILING FIXTURE: an empty password in the Secret fails the job before any ACL step or restart" {
  world "" 2 old 1 old
  run_job
  echo "$output"
  [ "$status" -ne 0 ]
  [[ "$output" == *"holds an empty password"* ]]
  [ ! -s "$S/acl_log" ]
  [ ! -s "$S/restarts" ]
  [ "$(cat "$S/acl")" = "old" ]
  [ "$(cat "$S/state_ver")" = 1 ]
}

@test "FAILING FIXTURE: after a failed restart, the next run does not delete the new pod again" {
  world new 2 old 1 old
  sed 's|restart "statefulset/${DAEMON_STS}" 900|restart "statefulset/${DAEMON_STS}" 1|' "$WORK/rotation.sh" > "$S/rotation.sh"
  for tick in 1 2 3; do
    run env PATH="$S/bin:$PATH" S="$S" RESTART_PODS_POLL=0 NEVER_READY=1 bash "$S/rotation.sh"
    echo "$output"
    [ "$status" -ne 0 ]
  done
  # One delete of the daemon pod in three runs: its new pod started after
  # the Secret write, so it already reads the new password.
  [ "$(grep -cx 'statefulset/gibson-gibson-workloads' "$S/restarts")" -eq 1 ] || { cat "$S/restarts"; false; }
  grep -qx old "$S/acl"
  [ "$(cat "$S/state_ver")" = 1 ]
}

@test "FAILING FIXTURE: a second line cut with no newline never adds a partial password" {
  world newpass 2 old 1 old
  : > "$S/cut_line"
  run_job
  echo "$output"
  [ "$status" -eq 0 ]
  [ ! -e "$S/cut_line" ]
  run grep -cxF '[ne]' "$S/acl_log"
  [ "$output" = 0 ] || { cat "$S/acl_log"; false; }
  [ "$(cat "$S/acl")" = "newpass" ]
}

@test "FAILING FIXTURE: kubectl prints no managedFields, so the skip of a new pod falls back to a restart" {
  # The job must ask for managedFields. The stub prints them only with
  # --show-managed-fields, as kubectl does since 1.21.
  run grep -c -- '-o json --show-managed-fields' "$WORK/rotation.sh"
  [ "$output" -ge 1 ]
  sed 's| --show-managed-fields||' "$WORK/rotation.sh" > "$S/rotation-nomf.sh"
  sed -i 's|restart "statefulset/${DAEMON_STS}" 900|restart "statefulset/${DAEMON_STS}" 1|' "$S/rotation-nomf.sh"
  world new 2 old 1 old
  for tick in 1 2; do
    run env PATH="$S/bin:$PATH" S="$S" RESTART_PODS_POLL=0 NEVER_READY=1 bash "$S/rotation-nomf.sh"
  done
  # Without the flag the job deletes the new pod again: the defect of #739.
  [ "$(grep -cx 'statefulset/gibson-gibson-workloads' "$S/restarts")" -eq 2 ]
}

# fake_jq16: a jq on PATH with the summer-time defect of jq 1.6, which the
# job image runs. jq 1.6 adds the local summer-time hour in fromdateiso8601
# when TZ names a zone on summer time; jq 1.7 does not. The fake adds that
# hour on ANY jq when TZ is not UTC, so the test does not depend on the jq of
# the runner or on its tzdata. It always runs the real jq in UTC, so a real
# jq 1.6 does not add a second hour.
fake_jq16() {
  mkdir -p "$S/jq16"
  printf '%s\n' '#!/usr/bin/env bash' \
    'case "${TZ:-UTC}" in UTC|UTC0|:UTC|Etc/UTC) TZ=UTC exec "$REAL_JQ" "$@" ;; esac' \
    'args=(); for a in "$@"; do args+=("${a//fromdateiso8601/(fromdateiso8601 + 3600)}"); done' \
    'TZ=UTC exec "$REAL_JQ" "${args[@]}"' > "$S/jq16/jq"
  chmod +x "$S/jq16/jq"
}

# two_ticks <new pod start> <TZ>: two runs where the daemon pod never becomes
# Ready, with the jq 1.6 fake; prints the number of deletes of the daemon pod.
two_ticks() {
  world new 2 old 1 old
  : > "$S/restarts"; rm -f "$S"/uid.*
  sed 's|restart "statefulset/${DAEMON_STS}" 900|restart "statefulset/${DAEMON_STS}" 1|' "$WORK/rotation.sh" > "$S/rotation.sh"
  fake_jq16
  for tick in 1 2; do
    env PATH="$S/jq16:$S/bin:$PATH" REAL_JQ="$(command -v jq)" S="$S" RESTART_PODS_POLL=0 NEVER_READY=1 NEW_POD_TIME="$1" TZ="$2" \
      bash "$S/rotation.sh" >/dev/null 2>&1 || true
  done
  grep -cx 'statefulset/gibson-gibson-workloads' "$S/restarts"
}

@test "FAILING FIXTURE: the 60 s margin and TZ=UTC hold in a summer-time zone" {
  # The Secret write is 10:00:00Z. America/New_York is on summer time in
  # October, and jq 1.6 applies that offset in fromdateiso8601 when the
  # job does not pin TZ=UTC. fake_jq16 gives that defect on any jq.
  zone=America/New_York
  # The fake must have the defect, or the test proves nothing.
  fake_jq16
  [ "$(echo '"2026-10-19T10:00:00Z"' | TZ="$zone" REAL_JQ="$(command -v jq)" "$S/jq16/jq" -r 'fromdateiso8601 | todate')" = 2026-10-19T11:00:00Z ]
  [ "$(echo '"2026-10-19T10:00:00Z"' | TZ=UTC REAL_JQ="$(command -v jq)" "$S/jq16/jq" -r 'fromdateiso8601 | todate')" = 2026-10-19T10:00:00Z ]
  # A pod that started 30 s after the write is inside the margin: it may
  # hold the old password from the kubelet cache, so the next run restarts
  # it again. Without the margin it counts as new: 1 delete.
  run two_ticks 2026-10-19T10:00:30Z "$zone"
  [ "$output" = 2 ] || { echo "pod inside the margin: $output deletes, want 2"; false; }
  # A pod that started 5 min after the write is new, so the next run does
  # not delete it again. Without TZ=UTC the write time moves one hour later
  # and the pod counts as old: 2 deletes.
  run two_ticks 2026-10-19T10:05:00Z "$zone"
  [ "$output" = 1 ] || { echo "pod after the margin: $output deletes, want 1"; false; }
}
