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
# The pods never appear in a render, so this guard renders the baseline
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

helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-baseline.yaml" -f "$CHART_DIR/../testdata/render-inputs/gibson.yaml" --namespace gibson > "$RENDER"

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
    sys.exit("the baseline render carries no NetworkPolicy in gibson; an empty set must never pass")
bad = [name for name, labels in SHAPES.items() if not egress_allowed(policies, labels)]
if bad:
    print("✗ check-cnpg-netpol-covers-jobs: no egress-allowing NetworkPolicy selects these CNPG pod shapes: " + ", ".join(bad), file=sys.stderr)
    print("  a bootstrap Job under the default-deny cannot reach the durable bucket, so every restore fails", file=sys.stderr)
    sys.exit(1)
# ---------------------------------------------------------------------------
# The INGRESS half (2026-09-14). The pods CNPG creates must stay reachable by
# the CNPG operator on :8000 — the instance-status probe. Without it the
# operator reports "Cannot extract Pod status", the Cluster never reports a
# system ID, and the umbrella sync deadlocks on the wave that waits for the
# Cluster to be healthy. That is not hypothetical: a SaaS overlay (ADR-0006)
# shipped a NetworkPolicy of its own against these pods, allowing :5432 from
# its pods and nothing else. On a warm cluster the umbrella policy was
# already there and the union was fine; on the from-zero bringup of
# 2026-09-14 the overlay Application synced FIRST, its policy was the only
# one selecting the instance, and the operator was fenced out — 106 of 420
# resources applied, three syncs killed by Argo's 30-minute cap.
#
# So this half asserts the two ingress paths the chart owns, and its
# self-test IS that overlay policy: it must be rejected.
# ---------------------------------------------------------------------------
def ingress_allowed(policies, pod_labels, from_labels, port):
    """Kubernetes union semantics: a pod not selected by any Ingress policy is
    open; a selected pod allows only what the union of its policies allows."""
    selecting = [p for p in policies
                 if selects((p["spec"].get("podSelector") or {}), pod_labels)
                 and "Ingress" in (p["spec"].get("policyTypes") or [])]
    if not selecting:
        return True
    for p in selecting:
        for rule in (p["spec"].get("ingress") or []):
            ports = rule.get("ports")
            if ports and not any(pt.get("port") == port for pt in ports):
                continue
            froms = rule.get("from")
            if not froms:
                return True
            for f in froms:
                if "podSelector" in f and selects(f["podSelector"], from_labels):
                    return True
    return False

OPERATOR = {"app.kubernetes.io/name": "cloudnative-pg"}
CLIENT = {"gibson.zeroroot.ai/platform-postgres-client": "true"}

# Self-test: the overlay-shaped policy alone must deny the operator on :8000.
overlay_only = [{"spec": {"podSelector": {"matchLabels": {"cnpg.io/cluster": "platform-postgres"}},
                          "policyTypes": ["Ingress"],
                          "ingress": [{"from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/name": "gibson-billing-webhook"}}}],
                                       "ports": [{"protocol": "TCP", "port": 5432}]}]}}]
if ingress_allowed(overlay_only, SHAPES["instance"], OPERATOR, 8000):
    sys.exit("self-test broken: an overlay policy that allows only :5432 from its own pods must NOT admit the operator on :8000")

# Self-test: an instances policy whose :5432 from-list names only pods and
# instances — the shape this file shipped until 2026-09-14 — must be rejected
# for the operator's Jobs.
no_jobs = [{"spec": {"podSelector": {"matchLabels": {"cnpg.io/podRole": "instance"}},
                     "policyTypes": ["Ingress"],
                     "ingress": [{"from": [{"podSelector": {"matchLabels": {"cnpg.io/podRole": "instance"}}},
                                           {"podSelector": {"matchLabels": OPERATOR}}],
                                  "ports": [{"protocol": "TCP", "port": 5432}]}]}}]
if ingress_allowed(no_jobs, SHAPES["instance"], SHAPES["join"], 5432):
    sys.exit("self-test broken: an instances policy with no jobRole selector must NOT admit a join Job on :5432")

# The operator's OWN Jobs must reach the primary on :5432. A `join` Job is how
# every replica after the first is built: it connects to <cluster>-rw as
# streaming_replica and runs pg_basebackup. Denied, it retries for ever on
# "dial error: timeout", the Cluster sits in "Creating a new replica" at N-1
# of N, and NOTHING reports it — CNPG calls the Cluster healthy enough and
# Argo calls the Application Healthy, so a bringup declares success over a
# database that is one replica short and stays that way. Measured on staging
# 2026-09-14, at 2 of 3 instances, 28 minutes after the bringup said complete.
JOB_ROLES = [k for k in SHAPES if k != "instance"]
for role in JOB_ROLES:
    if not ingress_allowed(policies, SHAPES["instance"], SHAPES[role], 5432):
        print("✗ check-cnpg-netpol-covers-jobs: no NetworkPolicy admits the operator's %s Job to a platform-postgres instance on :5432" % role, file=sys.stderr)
        print("  every replica after the first is built by a Job, and a denied Job never reports a failure: it retries until somebody looks", file=sys.stderr)
        sys.exit(1)

for who, labels, port in (("the CNPG operator", OPERATOR, 8000), ("a platform-postgres-client pod", CLIENT, 5432)):
    if not ingress_allowed(policies, SHAPES["instance"], labels, port):
        print("✗ check-cnpg-netpol-covers-jobs: no NetworkPolicy admits %s to a platform-postgres instance on :%d" % (who, port), file=sys.stderr)
        print("  the operator path is how the Cluster reports ready; the client label is the seam overlays opt into", file=sys.stderr)
        sys.exit(1)

print("✅ self-tests: the podRole-only egress shape and the jobRole-less ingress shape are both rejected; %d policies rendered, every CNPG pod shape (%s) has egress, and the operator, its %d Job roles and a client-labelled pod all reach an instance" % (len(policies), ", ".join(SHAPES), len(JOB_ROLES)))
PY
