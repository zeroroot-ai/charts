#!/usr/bin/env bash
# check-cnpg-netpol-covers-jobs.sh — every pod CNPG creates for platform-postgres
# is selected by a NetworkPolicy that allows egress, the bootstrap Jobs
# included.
#
# The namespace carries a default-deny (Ingress+Egress). CNPG's instance
# pods carry cnpg.io/podRole=instance; its bootstrap Jobs (initdb,
# full-recovery, join, pgbasebackup) carry cnpg.io/jobRole and NO podRole.
# A policy written against podRole alone leaves the Jobs under default-deny,
# and a full-recovery Job that cannot reach the bucket fails every restore.
# Measured 2026-09-07: six failed recovery Jobs in 41 minutes, Postgres never
# up, the whole platform down behind it.
#
# The pods never appear in a render, so this guard renders the vanilla
# profile and evaluates the policies' selectors against the label sets CNPG
# stamps, the way the API server would. It self-tests first: the pre-fix
# policy shape MUST fail the same evaluation.
#
# Usage: scripts/check-cnpg-netpol-covers-jobs.sh
# Exit:  0 every CNPG pod shape has an egress-allowing policy · 1 one has not · 2 self-test broken
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT

helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-vanilla.yaml" --namespace gibson > "$RENDER"

python3 - "$RENDER" <<'PY'
import sys, yaml

def selects(sel, labels):
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in sel.get("matchExpressions") or []:
        k, op, vals = e["key"], e["operator"], e.get("values") or []
        if op == "In" and labels.get(k) not in vals: return False
        if op == "NotIn" and labels.get(k) in vals: return False
        if op == "Exists" and k not in labels: return False
        if op == "DoesNotExist" and k in labels: return False
    return True

def egress_allowed(policies, labels):
    """True when some policy selecting the pod carries an egress rule."""
    for p in policies:
        spec = p["spec"]
        if not selects(spec.get("podSelector") or {}, labels):
            continue
        if "Egress" in (spec.get("policyTypes") or []) and spec.get("egress"):
            return True
    return False

SHAPES = {
    "instance":       {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/podRole": "instance", "cnpg.io/instanceRole": "primary"},
    "full-recovery":  {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/jobRole": "full-recovery"},
    "initdb":         {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/jobRole": "initdb"},
    "join":           {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-2", "cnpg.io/jobRole": "join"},
}

# Self-test: the pre-fix shape, one policy on podRole only, must FAIL for the Jobs.
prefix_only = [{"spec": {"podSelector": {"matchLabels": {"cnpg.io/podRole": "instance"}},
                         "policyTypes": ["Ingress", "Egress"], "egress": [{}]}}]
if not egress_allowed(prefix_only, SHAPES["instance"]) or egress_allowed(prefix_only, SHAPES["full-recovery"]):
    sys.exit("self-test broken: the pre-fix policy shape should cover the instance and NOT the full-recovery Job")

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
policies = [d for d in docs if d.get("kind") == "NetworkPolicy" and d["metadata"].get("namespace", "gibson") == "gibson"]
if not policies:
    sys.exit("the vanilla render carries no NetworkPolicy in gibson; an empty set must never pass")
bad = [name for name, labels in SHAPES.items() if not egress_allowed(policies, labels)]
if bad:
    print("✗ check-cnpg-netpol-covers-jobs: no egress-allowing NetworkPolicy selects these CNPG pod shapes: " + ", ".join(bad), file=sys.stderr)
    print("  a bootstrap Job under the default-deny cannot reach the durable bucket, so every restore fails", file=sys.stderr)
    sys.exit(1)
print("✅ self-test: the podRole-only policy shape is rejected for the Jobs; %d policies rendered, every CNPG pod shape (%s) has an egress-allowing policy" % (len(policies), ", ".join(SHAPES)))
PY
