#!/usr/bin/env bash
# wait-apps.sh — "every Argo Application is Synced+Healthy", for EVERY
# substrate (ADR-0015, deploy#1737, deploy#1746).
#
# Slow is fine; stuck is not. Getting that distinction right is the whole
# reason this is one file and not two: ENV=kind learned it the hard way
# (deploy#1737) and the AWS arm shipped a simpler copy that had not, which is
# a duplicate with drift (ADR-0027).
#
# What the AWS copy got wrong, measured on staging (deploy#1746): it counted
# only the Synced+Healthy APPLICATION count. A cold three-instance CNPG
# bringup streams a base backup for minutes while no Application flips, so the
# stall clock ran to 16 of its 20 minutes on a cluster that was working
# perfectly. The third replica finished with four minutes to spare. The next
# slow thing would not have been so lucky.
#
# Source this file, then call `wait_apps`. The caller supplies:
#
#   WAIT_APPS_TIMEOUT / WAIT_APPS_STALL  the hard cap and the stall window
#   HERE                                 the scripts/ directory, for
#                                        lib/argo-app-unhealthy.py
#   WORK                                 a scratch directory the caller cleans
#   die()                                MUST exit non-zero
#
wait_apps() {
  # Wait until every Argo Application in the argocd namespace is
  # Synced+Healthy. Slow is fine; stuck is not: fail after WAIT_APPS_STALL
  # seconds with NO progress, or at the hard cap.
  #
  # Progress is more than the Synced+Healthy app count. A cold wave-0
  # convergence pulls every first-party image onto a fresh node and rolls
  # dozens of workloads: the app count sits still for twenty minutes while
  # pods go Ready one by one (measured, deploy#1737 run B). So the progress
  # fingerprint is the app count PLUS the cluster's pod phase tally: any pod
  # moving (pulled, started, Ready, completed) resets the stall clock, and
  # only a cluster where nothing moves at all is "stuck".
  local hard_deadline=$(( $(date +%s) + WAIT_APPS_TIMEOUT ))
  local start_ts=$(date +%s)
  local last_fp="" fp pods last_progress=$(date +%s) now total healthy stalled
  while :; do
    now=$(date +%s)
    total=$(kubectl -n argocd get applications --no-headers 2>/dev/null | wc -l)
    healthy=$(kubectl -n argocd get applications \
      -o jsonpath='{range .items[*]}{.status.sync.status},{.status.health.status}{"\n"}{end}' 2>/dev/null \
      | grep -c '^Synced,Healthy$' || true)
    if [ "$total" -gt 0 ] && [ "$healthy" -eq "$total" ]; then printf '\n'; return 0; fi
    # Finished pods are EXCLUDED. The platform runs a redis-rotation CronJob
    # every minute, so counting Completed pods made the cluster look busy for
    # ever: the stall clock never reached its limit and a wedged bringup
    # burned the whole hard cap instead of failing at the stall (measured,
    # deploy#1737 run 33633490342). Only pods that are still working count as
    # movement.
    pods=$(kubectl get pods -A --no-headers 2>/dev/null \
      | awk '$4!="Completed" && $4!="Succeeded" {print $1"/"$2":"$3":"$4}' \
      | sort | sha256sum | cut -c1-16)
    fp="${healthy}/${total}:${pods}"
    if [ "$fp" != "$last_fp" ]; then last_fp="$fp"; last_progress=$now; fi
    stalled=$(( now - last_progress ))
    # Ask Argo to re-evaluate every app that is not yet Synced+Healthy.
    #
    # An Application's health is recomputed on a refresh, not on a timer that
    # can be relied on here. MEASURED (kind, deploy#1737): gibson-crds went
    # Progressing -> Degraded while its 58 CRDs were establishing, its last
    # reconcile was 38 s later, and it then sat Degraded for over four
    # minutes with every CRD Established and no child reporting anything but
    # Healthy. One refresh request flipped it to Synced+Healthy immediately.
    # Nothing else was going to move: the CRDs had settled, so no resource
    # event was coming.
    #
    # This is exactly what `argocd app get --refresh` and the Refresh button
    # do, and it cannot make a broken app report healthy: the refresh
    # recomputes health from live cluster state. Every 60 s, so a converging
    # app is not hammered.
    if [ $(( (now - start_ts) % 60 )) -lt 10 ]; then
      kubectl -n argocd get applications \
        -o jsonpath='{range .items[*]}{.metadata.name}={.status.sync.status}/{.status.health.status}{"\n"}{end}' 2>/dev/null \
        | awk -F= '$2!="Synced/Healthy"{print $1}' \
        | while read -r app; do
            [ -n "$app" ] && kubectl -n argocd annotate application "$app" \
              argocd.argoproj.io/refresh=normal --overwrite >/dev/null 2>&1 || true
          done
    fi
    if [ "$now" -ge "$hard_deadline" ] || [ "$stalled" -ge "$WAIT_APPS_STALL" ]; then
      printf '\n'
      kubectl -n argocd get applications \
        -o jsonpath='{range .items[*]}  {.metadata.name}: sync={.status.sync.status} health={.status.health.status}{"\n"}{end}' >&2 || true
      # An app list alone says an app is unhappy, never WHICH object made it
      # so, and that is the one thing needed to fix it. Name every resource
      # Argo itself considers out of sync or unhealthy, plus the operation
      # message and any app condition.
      for app in $(kubectl -n argocd get applications \
                     -o jsonpath='{range .items[*]}{.metadata.name} {.status.sync.status}/{.status.health.status}{"\n"}{end}' 2>/dev/null \
                     | awk '$2!="Synced/Healthy"{print $1}'); do
        printf '\n===== %s =====\n' "$app" >&2
        kubectl -n argocd get app "$app" -o json 2>/dev/null \
          | python3 "$HERE/lib/argo-app-unhealthy.py" > "$WORK/unhealthy.txt" 2>&1 || true
        grep -v "^DRIFT" "$WORK/unhealthy.txt" >&2 || true
        # Dump field ownership for every object that is OutOfSync with no
        # health problem — the signature of a controller-defaulted custom
        # resource. It has to happen HERE, while the cluster still exists.
        while IFS="$(printf '\t')" read -r _ kind name ns; do
          [ -n "$kind" ] || continue
          printf '  --- field owners of %s/%s\n' "$kind" "$name" >&2
          if [ -n "$ns" ]; then
            kubectl -n "$ns" get "$kind" "$name" \
              -o jsonpath='{range .metadata.managedFields[*]}    manager={.manager} op={.operation} sub={.subresource}{"\n"}{end}' >&2 2>/dev/null || true
          else
            kubectl get "$kind" "$name" \
              -o jsonpath='{range .metadata.managedFields[*]}    manager={.manager} op={.operation} sub={.subresource}{"\n"}{end}' >&2 2>/dev/null || true
          fi
        done < <(grep "^DRIFT" "$WORK/unhealthy.txt" || true)
      done
      if [ "$now" -ge "$hard_deadline" ]; then
        die "not every Application reached Synced+Healthy within $(( WAIT_APPS_TIMEOUT / 60 ))m"
      fi
      die "no Synced+Healthy progress for $(( stalled / 60 ))m (stuck, not slow)"
    fi
    printf '  apps: %d/%d Synced+Healthy  [%dm since last progress; stall-fail at %dm]\r' \
      "$healthy" "$total" "$(( stalled / 60 ))" "$(( WAIT_APPS_STALL / 60 ))"
    sleep 10
  done
}
