#!/usr/bin/env bash
# check-velero-no-hooks.sh — the Velero Schedule and BackupStorageLocation are
# ordinary resources, never hooks (charts#190).
#
# Argo leaves hooks out of its diff. While both objects were Sync hooks, a
# chart bump that changed only them left the Application Synced, auto-sync
# never ran the hooks again, and staging kept a stale Schedule after 0.132.1
# shipped the k3s excludes. So a hook annotation on either object is a defect:
# the next change to it would never reach an Argo-managed cluster.
#
# The failing fixture runs on every invocation: a guard that cannot fail is
# worse than no guard.
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson-velero}"
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT
helm template velero "$CHART_DIR" --namespace velero --set bucket.name=guard-fixture > "$RENDER"

python3 - "$RENDER" <<'PY'
import sys, yaml

KINDS = {"Schedule", "BackupStorageLocation"}
HOOK_KEYS = ("helm.sh/hook", "argocd.argoproj.io/hook")

def check(docs):
    seen, bad = set(), []
    for d in docs:
        if d.get("kind") not in KINDS:
            continue
        seen.add(d["kind"])
        ann = (d.get("metadata") or {}).get("annotations") or {}
        hooks = sorted(k for k in ann if k in HOOK_KEYS)
        if hooks:
            bad.append(f'{d["kind"]}/{d["metadata"]["name"]} carries {", ".join(hooks)}: '
                       "a change to it would never reach an Argo-managed cluster (charts#190)")
    for k in sorted(KINDS - seen):
        bad.append(f"the render has no {k}; an empty set must never pass")
    return bad

fixture = [
    {"kind": "Schedule", "metadata": {"name": "f", "annotations": {"argocd.argoproj.io/hook": "Sync"}}},
    {"kind": "BackupStorageLocation", "metadata": {"name": "f"}},
]
if not check(fixture):
    sys.exit("FIXTURE: a Schedule marked as an Argo hook passed; this guard cannot fail")

bad = check([d for d in yaml.safe_load_all(open(sys.argv[1])) if d])
if bad:
    for b in bad:
        print(f"::error::{b}")
    sys.exit(1)
print("ok: the Velero Schedule and BackupStorageLocation are ordinary resources")
PY
