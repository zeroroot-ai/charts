#!/usr/bin/env bash
# preflight-kvm.sh — each fleet node exposes /dev/kvm (ADR-0083, charts#413).
#
# Every sandbox is a Firecracker machine in a launcher Pod. The device plugin
# of setec hands /dev/kvm of the node to that Pod, so a node with no /dev/kvm
# runs no sandbox, and nothing else says so before the first sandbox stays
# pending. scripts/baseline-up.sh runs this before it installs anything.
#
# The fleet is each node that FLEET_NODE_SELECTOR names. The default is the
# node selector of the device plugin DaemonSet of setec (amd64 and linux), so
# it names each node where the plugin offers /dev/kvm. A cluster
# that keeps its fleet on labeled nodes sets the selector to that label, for
# example setec.zeroroot.ai/sandbox-host=true. For each fleet node this runs
# one short Pod on that node, with /dev of the node mounted read-only, and
# fails on the first node where the device is absent. A probe Pod that does
# not run to an answer (for example a refused hostPath, or an image that the
# node cannot pull) is a separate failure: the message names its phase and
# its events, and it does not claim that the device is absent.
#
# The setec seam (ADR-0087) selects whose fleet runs. When the layered values
# turn the seam off, the fleet is in another cluster, and the nodes of this
# cluster are not checked. The values files are the -f arguments, layered
# left to right, as helm layers them.
#
# Usage:
#   scripts/preflight-kvm.sh [-f values.yaml]...
#
# Env:
#   KUBECTL              the kubectl command           (default: kubectl)
#   FLEET_NODE_SELECTOR  a label selector of the fleet (default: kubernetes.io/arch=amd64,kubernetes.io/os=linux)
#   KVM_PROBE_NS         the namespace of the probe    (default: kube-system)
#   KVM_PROBE_IMAGE      the probe image               (default: the alpine mirror)
#   KVM_TIMEOUT          seconds to wait for one probe (default: 180)
#   POLL_SECONDS         seconds between two polls     (default: 3)
#
# Exit: 0 each fleet node has /dev/kvm, or the seam is off · 1 a node has
# none, a probe did not run, or no fleet node exists · 2 bad arguments.
set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
SELECTOR="${FLEET_NODE_SELECTOR:-kubernetes.io/arch=amd64,kubernetes.io/os=linux}"
NS="${KVM_PROBE_NS:-kube-system}"
IMAGE="${KVM_PROBE_IMAGE:-ghcr.io/zeroroot-ai/mirror/alpine:3.21@sha256:48b0309ca019d89d40f670aa1bc06e426dc0931948452e8491e3d65087abc07d}"
TIMEOUT="${KVM_TIMEOUT:-180}"
POLL="${POLL_SECONDS:-3}"

VALUES_FILES=()
while [ $# -gt 0 ]; do
  case "$1" in
    -f) [ $# -ge 2 ] || { echo "preflight-kvm: -f needs a file" >&2; exit 2; }
        VALUES_FILES+=("$2"); shift 2 ;;
    *)  echo "preflight-kvm: unknown argument $1" >&2; exit 2 ;;
  esac
done

# seam_on — "true" when the layered values keep the setec seam on. The key is
# gibson-workloads.setec.enabled of the umbrella, and the chart default is on.
# Later files win, as with helm.
seam_on() {
  python3 - "${VALUES_FILES[@]}" <<'PY'
import sys
import yaml

on = True
for f in sys.argv[1:]:
    with open(f) as fh:
        doc = yaml.safe_load(fh) or {}
    v = ((doc.get("gibson-workloads") or {}).get("setec") or {}).get("enabled")
    if v is not None:
        on = bool(v)
print("true" if on else "false")
PY
}

if [ "$(seam_on)" != true ]; then
  echo "  ✓ the setec seam is off: the fleet is in another cluster, so the nodes of this cluster are not checked"
  exit 0
fi

nodes="$($KUBECTL get nodes -l "$SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')"
if [ -z "$nodes" ]; then
  echo "FATAL: no node matches FLEET_NODE_SELECTOR=${SELECTOR}, so this cluster has no sandbox fleet. Each sandbox is a Firecracker machine on a fleet node that exposes /dev/kvm (ADR-0083). When the fleet is a node pool that scales from zero, start one node of the pool before the install." >&2
  exit 1
fi

for node in $nodes; do
  pod="fleet-probe-$(printf '%s' "$node" | tr -c 'a-z0-9' '-' | cut -c1-40)"
  $KUBECTL -n "$NS" delete pod "$pod" --ignore-not-found --wait=true >/dev/null 2>&1 || true
  $KUBECTL -n "$NS" apply -f - >/dev/null <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${pod}
  labels:
    app.kubernetes.io/name: fleet-probe
spec:
  nodeName: ${node}
  restartPolicy: Never
  tolerations: [{ operator: Exists }]
  containers:
    - name: probe
      image: ${IMAGE}
      command: ["sh", "-c", "if test -c /host-dev/kvm; then echo kvm-present; else echo kvm-absent; exit 3; fi"]
      securityContext:
        allowPrivilegeEscalation: false
        capabilities: { drop: ["ALL"] }
      volumeMounts: [{ name: dev, mountPath: /host-dev, readOnly: true }]
  volumes:
    - name: dev
      hostPath: { path: /dev, type: Directory }
YAML
  phase=""
  deadline=$(( SECONDS + TIMEOUT ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    phase="$($KUBECTL -n "$NS" get pod "$pod" -o jsonpath='{.status.phase}' 2>/dev/null || true)"
    case "$phase" in Succeeded|Failed) break ;; esac
    sleep "$POLL"
  done
  out="$($KUBECTL -n "$NS" logs "$pod" 2>/dev/null || true)"
  events=""
  if [ "$phase" != Succeeded ] || [ "$out" != kvm-present ]; then
    events="$($KUBECTL -n "$NS" get events --field-selector "involvedObject.name=${pod}" \
      -o jsonpath='{range .items[*]}{.reason}: {.message}{"\n"}{end}' 2>/dev/null || true)"
  fi
  $KUBECTL -n "$NS" delete pod "$pod" --ignore-not-found --wait=false >/dev/null 2>&1 || true
  if [ "$phase" = Succeeded ] && [ "$out" = kvm-present ]; then
    :
  elif [ "$phase" = Failed ] && [ "$out" = kvm-absent ]; then
    echo "FATAL: node ${node} has no /dev/kvm. Each sandbox is a Firecracker machine (ADR-0083), so each fleet node needs KVM: a metal node, or an instance type with nested virtualization (on AWS: c8i, m8i, r8i or a metal type). On kind, mount /dev/kvm of the host into the node, as helm/kind-config.yaml does. When the fleet runs on labeled nodes only, set FLEET_NODE_SELECTOR to that label." >&2
    exit 1
  else
    {
      echo "FATAL: the probe Pod ${pod} on node ${node} did not run to an answer (phase ${phase:-none}, log '${out}'). The preflight cannot tell if the node has the device."
      echo "The Pod runs in namespace ${NS} with a read-only hostPath mount of /dev and the image ${IMAGE}. Check the Pod Security label of ${NS}, a policy engine that refuses hostPath, and the registry access of the node. When either one is the cause, set the namespace or the image of the probe (the Env list of scripts/preflight-kvm.sh)."
      [ -n "$events" ] && { echo "Events of the Pod:"; printf '%s\n' "$events"; }
    } >&2
    exit 1
  fi
  printf '  ✓ node %s exposes /dev/kvm\n' "$node"
done
