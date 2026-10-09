#!/usr/bin/env python3
"""check-egress-policy-type.py: one policy type for the network policy (ADR-0165 rule 4, D76, charts#395).

The chart renders Cilium policies only: one CiliumClusterwideNetworkPolicy
that denies all traffic of the release namespace by default, and a few
CiliumNetworkPolicy objects that select pods by label
(helm/gibson/templates/network-policies.yaml). Every cluster runs Cilium
(ADR-0087, hosted#436). Two things fail, over the committed golden renders
(helm/testdata/golden/):

  1. A second policy type: a Kubernetes NetworkPolicy in the release
     namespace, Calico NetworkPolicy and GlobalNetworkPolicy, an Istio
     ServiceEntry or Sidecar, a GKE FQDNNetworkPolicy, an AdminNetworkPolicy.
     Cilium unions the allow rules of each type, so a second type could open
     what a label policy closes. The chart ships no egress proxy and no mesh.
  2. A Cilium policy rule that selects no rendered pod. A label with no pod
     is a typo, and the rule then protects nothing.

scripts/check-secure-pod.py checks what the policies allow (rule 4).

  check-egress-policy-type.py             exit 1 on a finding
  check-egress-policy-type.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
NAMESPACE = "gibson"
NS_KEY = "io.kubernetes.pod.namespace"
OTHER_TYPES = {
    ("crd.projectcalico.org", "NetworkPolicy"), ("crd.projectcalico.org", "GlobalNetworkPolicy"),
    ("projectcalico.org", "NetworkPolicy"), ("projectcalico.org", "GlobalNetworkPolicy"),
    ("networking.istio.io", "ServiceEntry"), ("networking.istio.io", "Sidecar"),
    ("networking.gke.io", "FQDNNetworkPolicy"),
    ("policy.networking.k8s.io", "AdminNetworkPolicy"),
}
CILIUM = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "Pod")


def group(d: dict) -> str:
    av = str(d.get("apiVersion", ""))
    return av.split("/")[0] if "/" in av else ""


def selects(selector: dict, labels: dict) -> bool:
    """A label selector against the labels of one pod. Cilium writes keys with
    a source prefix (k8s:). The check drops it."""
    def norm(k: str) -> str:
        return k.split(":", 1)[1] if ":" in k else k

    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(norm(k)) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        k, op, vals = norm(e.get("key")), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and k in labels and labels[k] in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return True


def pods(docs: list) -> list[dict]:
    """The labels of each rendered pod template, with its namespace. A CNPG
    Cluster counts as the pods that its operator creates."""
    out = []
    for d in docs:
        if d.get("kind") == "CronJob":
            t = (((d.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}
        elif d.get("kind") == "Pod":
            t = d
        elif d.get("kind") in WORKLOADS:
            t = (d.get("spec") or {}).get("template") or {}
        elif d.get("kind") == "Cluster" and group(d) == "postgresql.cnpg.io":
            # The CNPG operator creates the Postgres pods at run time. They
            # carry the labels of inheritedMetadata.
            t = {"metadata": (d.get("spec") or {}).get("inheritedMetadata") or {}}
        else:
            continue
        labels = (t.get("metadata") or {}).get("labels") or {}
        ns = d["metadata"].get("namespace") or NAMESPACE
        out.append({**labels, NS_KEY: ns})
        if d.get("kind") == "Deployment" and labels.get("app.kubernetes.io/name") == "velero":
            # The Velero server creates a repository maintenance Job at run
            # time. Its pod carries velero.io/repo-name, and the velero policy
            # selects it by that label (charts#494).
            out.append({"velero.io/repo-name": "runtime", NS_KEY: ns})
    return out


def judge(docs: list) -> list[str]:
    bad = []
    docs = [d for d in docs if isinstance(d, dict)]
    for d in docs:
        if (group(d), d.get("kind")) in OTHER_TYPES:
            bad.append(f"{d['kind']}/{d['metadata']['name']}: a second policy type ({group(d)}); "
                       "the chart renders Cilium policies only")
        if d.get("kind") == "NetworkPolicy" and (d["metadata"].get("namespace") or NAMESPACE) == NAMESPACE:
            bad.append(f"NetworkPolicy/{d['metadata']['name']}: a Kubernetes NetworkPolicy in the release namespace; "
                       "the chart renders Cilium policies only")
    rendered = pods(docs)
    for c in docs:
        if c.get("kind") not in CILIUM:
            continue
        ns = None if c["kind"] == "CiliumClusterwideNetworkPolicy" else (c["metadata"].get("namespace") or NAMESPACE)
        rules = ([c["spec"]] if c.get("spec") else []) + list(c.get("specs") or [])
        for i, r in enumerate(rules):
            sel = r.get("endpointSelector") or {}
            if not any((ns is None or ep[NS_KEY] == ns) and selects(sel, ep) for ep in rendered):
                bad.append(f"{c['kind']}/{c['metadata']['name']} rule {i + 1}: the selector {sel} selects no rendered pod")
    return sorted(set(bad))


def golden_docs(root: str) -> list:
    out = []
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        out += [d for d in yaml.safe_load_all(open(f)) if d]
    return out


def selftest() -> int:
    pod = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "a", "namespace": NAMESPACE},
           "spec": {"template": {"metadata": {"labels": {"app": "a", "gibson.zeroroot.ai/net-role": "platform"}}}}}
    ccnp = {"apiVersion": "cilium.io/v2", "kind": "CiliumClusterwideNetworkPolicy", "metadata": {"name": "deny"},
            "spec": {"endpointSelector": {"matchLabels": {f"k8s:{NS_KEY}": NAMESPACE}}}}
    cnp = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
           "metadata": {"name": "platform", "namespace": NAMESPACE},
           "specs": [{"endpointSelector": {"matchLabels": {"gibson.zeroroot.ai/net-role": "platform"}}}]}
    if judge([pod, ccnp, cnp]):
        print(f"SELFTEST FAIL: the label policies must pass: {judge([pod, ccnp, cnp])}")
        return 1
    np_release = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
                  "metadata": {"name": "np", "namespace": NAMESPACE}, "spec": {"podSelector": {}}}
    np_other = dict(np_release, metadata={"name": "np", "namespace": "setec-sandboxes"})
    if judge([pod, ccnp, cnp, np_other]):
        print("SELFTEST FAIL: a NetworkPolicy of another chart in its own namespace must pass")
        return 1
    pg = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
          "metadata": {"name": "datastore-postgres", "namespace": NAMESPACE},
          "specs": [{"endpointSelector": {"matchLabels": {"gibson.zeroroot.ai/datastore": "postgres"}}}]}
    cluster = {"apiVersion": "postgresql.cnpg.io/v1", "kind": "Cluster", "metadata": {"name": "pg", "namespace": NAMESPACE},
               "spec": {"inheritedMetadata": {"labels": {"gibson.zeroroot.ai/datastore": "postgres"}}}}
    if judge([pod, cluster, pg]):
        print(f"SELFTEST FAIL: a policy for the pods of a CNPG Cluster must pass: {judge([pod, cluster, pg])}")
        return 1
    velero = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "velero", "namespace": NAMESPACE},
              "spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/name": "velero"}}}}}
    repo = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": "velero", "namespace": NAMESPACE},
            "specs": [{"endpointSelector": {"matchExpressions": [{"key": "velero.io/repo-name", "operator": "Exists"}]}}]}
    if judge([velero, repo]):
        print(f"SELFTEST FAIL: a policy for the maintenance Jobs of a Velero server must pass: {judge([velero, repo])}")
        return 1
    se = {"apiVersion": "networking.istio.io/v1", "kind": "ServiceEntry", "metadata": {"name": "se"}}
    typo = {**cnp, "metadata": {"name": "typo", "namespace": NAMESPACE},
            "specs": [{"endpointSelector": {"matchLabels": {"gibson.zeroroot.ai/net-role": "platfrom"}}}]}
    other_ns = {**ccnp, "metadata": {"name": "elsewhere"},
                "spec": {"endpointSelector": {"matchLabels": {f"k8s:{NS_KEY}": "nowhere"}}}}
    for what, docs in (("an Istio ServiceEntry", [pod, se]),
                       ("a Kubernetes NetworkPolicy in the release namespace", [pod, np_release]),
                       ("a label policy that selects no pod", [pod, typo]),
                       ("a clusterwide policy for a namespace with no pod", [pod, other_ns]),
                       ("a policy for maintenance Jobs with no Velero server", [pod, repo])):
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: a second policy type, a NetworkPolicy in the release namespace and a Cilium rule "
          "that selects no pod fail; the label policies and the Velero maintenance Jobs pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    docs = golden_docs(ROOT)
    if not docs:
        print("the golden renders hold nothing: this check is blind", file=sys.stderr)
        return 1
    bad = judge(docs)
    if bad:
        print("the network policy uses one policy type, and each rule selects a pod (ADR-0165 rule 4, D76):",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    n = sum(1 for d in docs if isinstance(d, dict) and d.get("kind") in CILIUM)
    print(f"  ✓ egress-policy-type: {len(docs)} rendered objects, no second policy type, "
          f"{n} Cilium policy objects, each rule selects a pod")
    return 0


if __name__ == "__main__":
    sys.exit(main())
