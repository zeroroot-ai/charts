# restart-pods.sh: restart a workload by deleting its pods, one at a time.
# The redis rotation CronJob includes this file in its script
# (templates/databases/redis-rotation-controller.yaml), and
# tests/restart-pods.bats sources it.
#
#   restart_pods <namespace> <kind/name> <timeout-seconds>
#
# WHY NOT `kubectl rollout restart`. The chart binds the ValidatingAdmissionPolicy
# gibson-platform-identity-workloads to the release namespace. It admits a
# CREATE or UPDATE of a workload whose pod template claims platform identity
# only from the controllers of the release and a restart by the Reloader
# ServiceAccount. A `rollout restart` patches the workload, so the policy
# denies it, and the redis rotation never completed (charts#725, drill run
# 39125128505). A pod delete is not a write of the workload: the controller
# of the workload makes the new pod, and the policy does not apply. The same
# method as zeroroot-ai/hosted scripts/lib/restart-pods.sh (hosted#776).
#
# The pods go one at a time, and each replacement must be Ready before the
# next delete, so a workload with more than one replica keeps serving. A KNOWN
# COST: a workload with one replica is down until its new pod is Ready. A
# rollout restart of a one-replica Deployment surged a new pod first, so this
# adds a short gap (ext-authz: Envoy cannot authorize a call in it). The
# daemon StatefulSet had the gap with a rollout restart too.
#
# The new pods are told apart from the old ones by UID, not by a clock.
#
# Three kinds of pod in the list get no replacement, so the restart neither
# deletes them nor waits for them:
#   - a pod that is being deleted, or is Failed or Succeeded (an evicted pod
#     stays in the list as Failed);
#   - a pod with no owner (no controller makes a new one, so a delete would
#     only remove it).
# A live pod with any owner counts. The selectors of the four workloads that
# the redis rotation restarts select no other workload today. A selector
# that later overlaps another workload makes the restart delete its pods too.
# An old pod in a crash loop is Running and not Ready. The waits count only
# the new pods, so it does not block the restart. It is deleted in its turn.
#
# Kinds: Deployment, StatefulSet (.spec.replicas) and DaemonSet
# (.status.desiredNumberScheduled). A workload that wants 0 pods has nothing
# to restart, and the call returns 0, as `rollout restart` did.
#
# Returns 0 when the workload has its pods again, each new and Ready, and 1
# with a reason on stderr at the timeout. Requires kubectl and jq.

restart_pods() {
  local ns="$1" w="$2" timeout="$3" obj sel want pods old deadline pod n k
  obj="$(kubectl -n "$ns" get "$w" -o json)" \
    || { echo "restart_pods: cannot read ${ns}/${w}" >&2; return 1; }
  sel="$(jq -r '.spec.selector.matchLabels // {} | to_entries | map("\(.key)=\(.value)") | join(",")' <<<"$obj")"
  [ -n "$sel" ] || { echo "restart_pods: ${ns}/${w} has no selector" >&2; return 1; }
  want="$(jq -r 'if .kind == "DaemonSet" then .status.desiredNumberScheduled else .spec.replicas end // empty' <<<"$obj")"
  [[ "$want" =~ ^[0-9]+$ ]] || { echo "restart_pods: ${ns}/${w} has no wanted pod count" >&2; return 1; }
  if [ "$want" -eq 0 ]; then
    echo "restart_pods: ${ns}/${w} wants 0 pods, so there is nothing to restart" >&2
    return 0
  fi
  # One read gives the pods to delete and their UIDs. Only the live pods
  # that a controller replaces.
  pods="$(kubectl -n "$ns" get pods -l "$sel" -o json)" \
    || { echo "restart_pods: cannot list the pods of ${ns}/${w}" >&2; return 1; }
  for pod in $(jq -r "${_restart_pods_unowned}[] | .metadata.name" <<<"$pods"); do
    echo "restart_pods: pod ${ns}/${pod} matches ${w} and has no owner; no controller replaces it, so it stays" >&2
  done
  old="$(jq -c "${_restart_pods_live} | map(.metadata.uid)" <<<"$pods")"
  deadline=$(( $(date +%s) + timeout ))
  n=0
  for pod in $(jq -r "${_restart_pods_live}[] | .metadata.name" <<<"$pods"); do
    # --wait=false: a wait for the delete needs a watch on pods, which the
    # Role does not grant. The waits below tell a new pod by its UID, and a
    # pod that is being deleted is not live, so they need no delete wait.
    kubectl -n "$ns" delete pod "$pod" --ignore-not-found --wait=false >/dev/null \
      || { echo "restart_pods: cannot delete pod ${ns}/${pod}" >&2; return 1; }
    # After the Nth delete, N new pods are Ready (at most WANT: a surge can
    # list more pods than the workload wants).
    n=$(( n + 1 )); k=$(( n < want ? n : want ))
    until _restart_pods_new_ready "$ns" "$sel" "$k" "$old"; do
      [ "$(date +%s)" -lt "$deadline" ] || { echo "restart_pods: ${ns}/${w} has not ${k} new Ready pods within ${timeout}s, after the delete of ${pod}" >&2; return 1; }
      sleep "${RESTART_PODS_POLL:-5}"
    done
  done
  until _restart_pods_ready "$ns" "$sel" "$want" "$old"; do
    [ "$(date +%s)" -lt "$deadline" ] || { echo "restart_pods: an old pod of ${ns}/${w} still runs, or a new one is not Ready, within ${timeout}s" >&2; return 1; }
    sleep "${RESTART_PODS_POLL:-5}"
  done
}

# _restart_pods_live: the pods of the list that a controller replaces: not
# being deleted, not Failed or Succeeded, and with an owner.
_restart_pods_live='[.items[] | select(.metadata.deletionTimestamp == null)
  | select((.status.phase // "") as $ph | $ph != "Failed" and $ph != "Succeeded")
  | select((.metadata.ownerReferences // []) | length > 0)]'
# _restart_pods_unowned: the live pods of the list with no owner.
_restart_pods_unowned='[.items[] | select(.metadata.deletionTimestamp == null)
  | select((.status.phase // "") as $ph | $ph != "Failed" and $ph != "Succeeded")
  | select((.metadata.ownerReferences // []) | length == 0)]'
# _restart_pods_is_ready: the pod has the condition Ready True.
_restart_pods_is_ready='([.status.conditions[]? | select(.type == "Ready" and .status == "True")] | length > 0)'

# _restart_pods_new_ready NS SELECTOR N OLD_UIDS: true when at least N live
# pods are Ready and have no UID of OLD_UIDS (a JSON list). This is the wait
# after the Nth delete. It counts only the new pods, so an old pod that is
# not Ready (a crash loop) does not block it.
_restart_pods_new_ready() {
  kubectl -n "$1" get pods -l "$2" -o json | jq -e --argjson n "$3" --argjson old "$4" "
    ${_restart_pods_live} as \$p
    | [\$p[] | select(${_restart_pods_is_ready}) | select((.metadata.uid as \$u | \$old | index(\$u)) == null)]
    | length >= \$n" >/dev/null
}

# _restart_pods_ready NS SELECTOR WANT OLD_UIDS: true when at least WANT live
# pods are Ready, and no live pod has a UID of OLD_UIDS (a JSON list). This
# is the final check, after each old pod is deleted.
_restart_pods_ready() {
  kubectl -n "$1" get pods -l "$2" -o json | jq -e --argjson want "$3" --argjson old "$4" "
    ${_restart_pods_live} as \$p
    | ([\$p[] | select(${_restart_pods_is_ready})] | length) >= \$want
      and all(\$p[]; (.metadata.uid as \$u | \$old | index(\$u)) == null)" >/dev/null
}
