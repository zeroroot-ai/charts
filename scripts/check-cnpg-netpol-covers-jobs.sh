#!/usr/bin/env bash
# check-cnpg-netpol-covers-jobs.sh — every pod CNPG creates for platform-postgres
# gets the network policy it needs, the bootstrap Jobs included.
#
# The release namespace is default-deny (a CiliumClusterwideNetworkPolicy),
# and the Cilium policies select pods by label (D76,
# helm/gibson/templates/network-policies.yaml). CNPG's instance pods carry
# cnpg.io/podRole=instance. Its bootstrap Jobs (initdb, full-recovery, join,
# pgbasebackup) carry cnpg.io/jobRole and NO podRole. The network labels reach
# both through `inheritedMetadata` of the Cluster. A Job with no labels cannot
# reach the bucket, and a full-recovery Job that cannot reach the bucket fails
# every restore. Measured 2026-09-07: six failed recovery Jobs in 41 minutes,
# Postgres never up, the whole platform down behind it.
#
# The pods never appear in a render, so this guard renders the baseline
# profile, gives each CNPG pod shape the labels of the Cluster's
# inheritedMetadata, and evaluates the Cilium rules against them with
# scripts/lib/cilium_policy.py. Cilium allows a flow only when the egress side
# and the ingress side both allow it.
# It checks:
#   1. each pod shape reaches the archive bucket (the world entity);
#   2. the operator reaches an instance on :8000 (the instance status);
#   3. each Job role reaches an instance on :5432 (a join Job builds each
#      replica after the first);
#   4. a pod with the Postgres client label reaches an instance on :5432.
# It self-tests first: a Cluster with no network labels and a data store
# policy that admits no client MUST fail the same evaluation.
#
# Usage: scripts/check-cnpg-netpol-covers-jobs.sh
# Exit:  0 every path holds · 1 one does not · 2 self-test broken
set -euo pipefail

CHART_DIR="${CHART_DIR:-helm/gibson}"
RENDER="$(mktemp)"
trap 'rm -f "$RENDER"' EXIT

helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-baseline.yaml" -f "$CHART_DIR/../testdata/render-inputs/gibson.yaml" --namespace gibson > "$RENDER"

python3 - "$RENDER" "$(dirname "$0")/lib" <<'PY'
import copy, sys, yaml
sys.path.insert(0, sys.argv[2])
import cilium_policy as cp

NS = "gibson"
NS_KEY = cp.NS_KEY

def egress_world(rules, src):
    return any(cp.rule_selects(r, ns, src) and cp.egress_internet(r) for _, r, ns in rules)

SHAPES = {
    "instance":       {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/podRole": "instance", "cnpg.io/instanceRole": "primary"},
    "full-recovery":  {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/jobRole": "full-recovery"},
    "initdb":         {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-1", "cnpg.io/jobRole": "initdb"},
    "join":           {"cnpg.io/cluster": "platform-postgres", "cnpg.io/instanceName": "platform-postgres-2", "cnpg.io/jobRole": "join"},
}
CLIENT = {"gibson.zeroroot.ai/net-role": "platform", "gibson.zeroroot.ai/client-postgres": "true", NS_KEY: NS}

def judge(docs):
    policies = cp.rules(docs, NS)
    if not policies:
        return ["the baseline render carries no Cilium policy; an empty set must never pass"]
    cluster = next(d for d in docs if d.get("kind") == "Cluster" and d["metadata"]["name"] == "platform-postgres")
    inherited = ((cluster["spec"].get("inheritedMetadata") or {}).get("labels")) or {}
    pods = {k: {**v, **inherited, NS_KEY: NS} for k, v in SHAPES.items()}
    op = next(d for d in docs if d.get("kind") == "Deployment" and
              (d["spec"]["template"]["metadata"].get("labels") or {}).get("app.kubernetes.io/name") == "cloudnative-pg")
    operator = {**op["spec"]["template"]["metadata"]["labels"], NS_KEY: NS}
    bad = []
    for name, labels in pods.items():
        if not egress_world(policies, labels):
            bad.append(f"the CNPG {name} pod cannot reach the archive bucket (no egress to the internet)")
    if not cp.reaches(policies, operator, pods["instance"], 8000):
        bad.append("the CNPG operator cannot reach an instance on :8000 (the instance status)")
    for role in (k for k in SHAPES if k != "instance"):
        if not cp.reaches(policies, pods[role], pods["instance"], 5432):
            bad.append(f"the CNPG {role} Job cannot reach an instance on :5432")
    if not cp.reaches(policies, CLIENT, pods["instance"], 5432):
        bad.append("a pod with the Postgres client label cannot reach an instance on :5432")
    return bad

docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]

# Self-test 1: a Cluster with no network labels in inheritedMetadata must fail.
broken = copy.deepcopy(docs)
c = next(d for d in broken if d.get("kind") == "Cluster" and d["metadata"]["name"] == "platform-postgres")
c["spec"]["inheritedMetadata"]["labels"] = {k: v for k, v in c["spec"]["inheritedMetadata"]["labels"].items()
                                            if not k.startswith("gibson.zeroroot.ai/")}
if not judge(broken):
    print("self-test broken: a Cluster with no network labels must fail", file=sys.stderr)
    sys.exit(2)
# Self-test 2: a Postgres data store policy that admits no client must fail.
broken = copy.deepcopy(docs)
for d in broken:
    if d.get("kind") == "CiliumNetworkPolicy" and d["metadata"]["name"].endswith("datastore-postgres"):
        for r in d.get("specs") or []:
            r.pop("ingress", None)
if not judge(broken):
    print("self-test broken: a Postgres policy that admits no client must fail", file=sys.stderr)
    sys.exit(2)

bad = judge(docs)
if bad:
    print("✗ check-cnpg-netpol-covers-jobs: a pod CNPG creates does not get the network policy it needs:", file=sys.stderr)
    for b in bad:
        print("  " + b, file=sys.stderr)
    sys.exit(1)
print("✅ self-tests: a Cluster with no network labels and a Postgres policy that admits no client are rejected; "
      "every CNPG pod shape (%s) reaches the bucket, and the operator, the Job roles and a client reach an instance" % ", ".join(SHAPES))
PY
