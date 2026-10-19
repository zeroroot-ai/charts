#!/usr/bin/env bats
#
# restart-pods.bats: a platform workload restarts by a pod delete, never by a
# `rollout restart`, which the identity admission policy of the chart denies
# (drill run 39125128505, charts#725). The cases are those of
# zeroroot-ai/hosted tests/harness/restart-pods.bats (hosted#776), and one
# more: a pod with no owner gets no replacement.
#
# Offline. kubectl is a stub with one workload of two pods. Each pod gets a
# new UID when it is deleted.
#
# Run:
#   bats tests/restart-pods.bats

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
  TMP="$(mktemp -d)"; export TMP
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/kubectl" <<'SH'
#!/usr/bin/env bash
# STUB_KIND: Deployment (default) or DaemonSet (no .spec.replicas).
# STUB_NEVER_READY: the new pods never become Ready.
# STUB_OLD_STAYS: a deleted pod comes back with its old UID.
# STUB_OLD_DELETING: the list also holds the old pod, being deleted.
case "$*" in
  "-n gibson get deployment/gibson-ext-authz -o json"|"-n gibson get daemonset/gibson-ext-authz -o json")
    if [ "${STUB_KIND:-Deployment}" = DaemonSet ]; then
      echo '{"kind":"DaemonSet","spec":{"selector":{"matchLabels":{"app.kubernetes.io/component":"ext-authz"}}},"status":{"desiredNumberScheduled":'"${STUB_DS_WANT:-2}"'}}'
    else
      echo '{"kind":"Deployment","spec":{"replicas":2,"selector":{"matchLabels":{"app.kubernetes.io/component":"ext-authz"}}}}'
    fi ;;
  # A delete with --wait=true needs a watch on pods, which the Role of the
  # redis rotation does not grant: the stub refuses it.
  *"delete pod"*"--wait=true"*) echo 'Error from server (Forbidden): pods is forbidden: cannot watch resource "pods"' >&2; exit 1 ;;
  "-n gibson delete pod ext-authz-a --ignore-not-found --wait=false"|"-n gibson delete pod ext-authz-b --ignore-not-found --wait=false")
    echo "$5" >> "$TMP/deleted"; echo "delete $5" >> "$TMP/events" ;;
  "-n gibson get pods -l app.kubernetes.io/component=ext-authz -o json")
    echo read >> "$TMP/events"
    r=True; [ -z "${STUB_NEVER_READY:-}" ] || r=False
    ua=a0; ub=b0; rb="$r"
    # STUB_CRASHING_B: the old pod ext-authz-b is in a crash loop (Running,
    # not Ready) until it is deleted.
    [ -z "${STUB_CRASHING_B:-}" ] || rb=False
    if [ -z "${STUB_OLD_STAYS:-}" ]; then
      ! grep -qx ext-authz-a "$TMP/deleted" 2>/dev/null || ua=a1
      ! grep -qx ext-authz-b "$TMP/deleted" 2>/dev/null || { ub=b1; rb="$r"; }
    fi
    extra='[]'
    # STUB_EVICTED: an old pod evicted by the kubelet stays in the list.
    [ -z "${STUB_EVICTED:-}" ] || extra='[{"metadata":{"name":"ext-authz-z-evicted","uid":"z0","ownerReferences":[{"kind":"ReplicaSet"}]},"status":{"phase":"Failed","conditions":[]}}]'
    # STUB_GAP: right after a delete, the first read lists one pod fewer.
    if [ -n "${STUB_GAP:-}" ] && [ -e "$TMP/deleted" ] && [ ! -e "$TMP/gap.$(wc -l < "$TMP/deleted")" ]; then
      : > "$TMP/gap.$(wc -l < "$TMP/deleted")"
      jq -n '{items: [{metadata: {name: "ext-authz-b", uid: "b0", ownerReferences: [{kind: "ReplicaSet"}]}, status: {conditions: [{type: "Ready", status: "True"}]}}]}'
      exit 0
    fi
    # STUB_UNOWNED: a pod with the labels of the workload and no owner. No
    # controller replaces it, and the stub refuses its delete.
    [ -z "${STUB_UNOWNED:-}" ] || extra='[{"metadata":{"name":"ext-authz-bare","uid":"x0"},"status":{"phase":"Running","conditions":[{"type":"Ready","status":"True"}]}}]'
    [ -z "${STUB_OLD_DELETING:-}" ] || extra='[{"metadata":{"name":"ext-authz-old","uid":"a0","deletionTimestamp":"2026-10-18T12:00:00Z","ownerReferences":[{"kind":"ReplicaSet"}]},"status":{"conditions":[{"type":"Ready","status":"False"}]}}]'
    # STUB_TIME_A, STUB_TIME_B: the creationTimestamp of the first pods. A
    # replacement pod is created at 12:00:00.
    jq -n --arg r "$r" --arg rb "$rb" --arg ua "$ua" --arg ub "$ub" --argjson extra "$extra" --argjson own '[{"kind":"ReplicaSet","controller":true}]' \
      --arg ta "${STUB_TIME_A:-2026-01-01T00:00:00Z}" --arg tb "${STUB_TIME_B:-2026-01-01T00:00:00Z}" '{items: ([
      {metadata: {name: "ext-authz-a", uid: $ua, creationTimestamp: (if $ua == "a0" then $ta else "2026-10-19T12:00:00Z" end), ownerReferences: $own}, status: {conditions: [{type: "Ready", status: $r}]}},
      {metadata: {name: "ext-authz-b", uid: $ub, creationTimestamp: (if $ub == "b0" then $tb else "2026-10-19T12:00:00Z" end), ownerReferences: $own}, status: {conditions: [{type: "Ready", status: $rb}]}}] + $extra)}' ;;
  *) echo "kubectl stub: undeclared call: $*" >&2; exit 1 ;;
esac
SH
  chmod +x "$TMP/bin/kubectl"
}

teardown() { rm -rf "$TMP"; }

run_restart() {
  run env PATH="$TMP/bin:$PATH" RESTART_PODS_POLL=0 bash -c '. "$1"; restart_pods gibson "$2" 3' _ \
    "$REPO_ROOT/helm/gibson-workloads/files/restart-pods.sh" "${1:-deployment/gibson-ext-authz}"
}

@test "<since>: a pod created before or at the time is deleted, a pod created after it stays" {
  export STUB_TIME_A=2026-10-19T09:59:59Z STUB_TIME_B=2026-10-19T10:00:00Z
  run env PATH="$TMP/bin:$PATH" RESTART_PODS_POLL=0 bash -c '. "$1"; restart_pods gibson deployment/gibson-ext-authz 3 2026-10-19T10:00:00Z' _ \
    "$REPO_ROOT/helm/gibson-workloads/files/restart-pods.sh"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
  rm -f "$TMP/deleted"
  export STUB_TIME_B=2026-10-19T10:00:01Z
  run env PATH="$TMP/bin:$PATH" RESTART_PODS_POLL=0 bash -c '. "$1"; restart_pods gibson deployment/gibson-ext-authz 3 2026-10-19T10:00:00Z' _ \
    "$REPO_ROOT/helm/gibson-workloads/files/restart-pods.sh"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  # b started after <since>: it is already new, so it stays and counts as new.
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a " ]
  [[ "$output" == *"ext-authz-b started after 2026-10-19T10:00:00Z"* ]]
}

@test "<since> left out: every live pod is old, whatever its creationTimestamp" {
  export STUB_TIME_A=2030-01-01T00:00:00Z STUB_TIME_B=2030-01-01T00:00:00Z
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
}

@test "restart_pods deletes each pod of the workload, one at a time, and waits for new Ready pods" {
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
}

@test "a DaemonSet restarts by its desiredNumberScheduled" {
  export STUB_KIND=DaemonSet
  run_restart daemonset/gibson-ext-authz
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "an old pod that is being deleted does not count against the restart" {
  export STUB_OLD_DELETING=1
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "a workload that wants 0 pods has nothing to restart" {
  export STUB_KIND=DaemonSet STUB_DS_WANT=0
  run_restart daemonset/gibson-ext-authz
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [[ "$output" == *"wants 0 pods, so there is nothing to restart"* ]]
  [ ! -e "$TMP/deleted" ]
}

@test "FAILING FIXTURE: an evicted old pod in the list does not block the restart" {
  export STUB_EVICTED=1
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  # The evicted pod gets no replacement, so it is not deleted and not waited for.
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
}

@test "FAILING FIXTURE: right after a delete, a list with one pod fewer does not count as restarted" {
  # The count check: without it the next delete would take the second replica
  # before the first came back.
  export STUB_GAP=1
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ -e "$TMP/gap.1" ] && [ -e "$TMP/gap.2" ]
  # The short read after the first delete must not end the wait: a second
  # read comes before the second delete.
  [ "$(awk '/^delete/{n++} n==1 && /^read/{r++} END{print r+0}' "$TMP/events")" -ge 2 ] \
    || { cat "$TMP/events"; false; }
}

@test "FAILING FIXTURE: an old pod in a crash loop does not block the restart" {
  export STUB_CRASHING_B=1
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
}

@test "FAILING FIXTURE: a delete does not wait, because a wait needs a watch on pods that the Role does not grant" {
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(grep -c '^delete' "$TMP/events")" -eq 2 ]
  # The delete of hosted#776 (--wait=true) is refused by the stub.
  run env PATH="$TMP/bin:$PATH" kubectl -n gibson delete pod ext-authz-a --ignore-not-found --wait=true --timeout=3s
  [ "$status" -ne 0 ]
  [[ "$output" == *"cannot watch"* ]]
}

@test "FAILING FIXTURE: a workload whose new pods never become Ready fails the restart" {
  export STUB_NEVER_READY=1
  run_restart
  [ "$status" -ne 0 ]
  [[ "$output" == *"has not 1 new Ready pods within 3s, after the delete of ext-authz-a"* ]] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a pod that still has its old UID is no restart" {
  export STUB_OLD_STAYS=1
  run_restart
  [ "$status" -ne 0 ]
  [[ "$output" == *"has not 1 new Ready pods within 3s, after the delete of ext-authz-a"* ]] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a pod with no owner is not deleted and does not block the restart" {
  export STUB_UNOWNED=1
  run_restart
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ "$(tr '\n' ' ' < "$TMP/deleted")" = "ext-authz-a ext-authz-b " ]
  [[ "$output" == *"pod gibson/ext-authz-bare matches deployment/gibson-ext-authz and has no owner"* ]] || { echo "$output"; false; }
}

# own_restarts FILE...: each line, not a comment, that runs `rollout restart`
# on a workload: a workload argument or a flag follows, so prose such as "a
# manual `kubectl rollout restart`" is not a call. A `rollout status` only
# reads, and the policy does not judge a read, so it is not found. Only the
# namespaces that the identity policy does not bind may restart: velero and
# kube-system, with -n before or after the verb. The guard reads one line at
# a time: a call that breaks with a backslash right after `rollout restart`
# is found, but its workload on the next line is not read.
own_restarts() {
  grep -nE '^[^#]*kubectl[^#]*rollout restart[[:space:]]+["$a-z\\-]' "$@" \
    | grep -vE 'kubectl -n (velero|kube-system) rollout restart|rollout restart (-n|--namespace)[ =](velero|kube-system)[[:space:]]'
}

@test "no chart template, chart file, script, Makefile or workflow restarts a workload of the release namespace with rollout restart" {
  mapfile -t files < <(cd "$REPO_ROOT" && git ls-files 'helm/*/templates/*' 'helm/*/files/*' 'scripts/*.sh' 'scripts/lib/*.sh' Makefile '.github/workflows/*' | sed "s|^|$REPO_ROOT/|")
  printf '%s\n' "${files[@]}" | grep -q '/.github/workflows/published-install.yml$' || { echo "the guard does not read the workflows"; false; }
  [ "${#files[@]}" -gt 100 ] || { echo "only ${#files[@]} files: the guard is blind"; false; }
  run own_restarts "${files[@]}"
  [ "$status" -eq 1 ] || { echo "status ${status}: $output"; false; }
}

@test "FAILING FIXTURE: a rollout restart by name, by variable or by label is found, and a comment is not" {
  printf 'kubectl -n "$NS" rollout restart deployment/gibson-ext-authz >/dev/null\n' > "$TMP/a.sh"
  printf '  kubectl rollout restart "$1" -n "$NS"\n' > "$TMP/b.sh"
  printf 'kubectl -n "$NS" rollout restart deployment -l app.kubernetes.io/component=ratelimit\n' > "$TMP/c.sh"
  printf '# never: kubectl -n x rollout restart deployment/gibson-ext-authz\n' > "$TMP/d.sh"
  printf 'rotations require manual `kubectl rollout restart`.\n' > "$TMP/e.sh"
  printf 'kubectl -n kube-system rollout restart deployment/coredns\n' > "$TMP/f.sh"
  printf 'kubectl rollout restart -n kube-system deployment/coredns\n' > "$TMP/f2.sh"
  printf 'kubectl -n velero rollout restart deployment/velero\n' > "$TMP/v.sh"
  printf 'kubectl rollout restart --namespace velero daemonset/node-agent\n' > "$TMP/v2.sh"
  printf '  kubectl -n "$NS" rollout restart \\\n    deployment/gibson-ext-authz\n' > "$TMP/bs.sh"
  printf '          kubectl -n "$NS" rollout restart deploy/gibson-platform-operator deploy/gibson-tenant-operator\n' > "$TMP/wf.yml"
  printf 'kubectl -n "$NS" rollout status deploy/gibson-envoy --timeout=300s\n' > "$TMP/st.sh"
  printf 'kubectl rollout restart -n "$NS" "$1"\n' > "$TMP/g.sh"
  printf 'kubectl rollout restart --namespace gibson statefulset/x\n' > "$TMP/h.sh"
  run own_restarts "$TMP/a.sh"; [ "$status" -eq 0 ]
  run own_restarts "$TMP/b.sh"; [ "$status" -eq 0 ]
  run own_restarts "$TMP/c.sh"; [ "$status" -eq 0 ]
  run own_restarts "$TMP/d.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/e.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/f.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/f2.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/v.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/v2.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/bs.sh"; [ "$status" -eq 0 ]
  # The line that published-install.yml ran before this guard read workflows.
  run own_restarts "$TMP/wf.yml"; [ "$status" -eq 0 ]
  run own_restarts "$TMP/st.sh"; [ "$status" -eq 1 ]
  run own_restarts "$TMP/g.sh"; [ "$status" -eq 0 ]
  run own_restarts "$TMP/h.sh"; [ "$status" -eq 0 ]
}
