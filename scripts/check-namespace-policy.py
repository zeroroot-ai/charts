#!/usr/bin/env python3
"""check-namespace-policy.py: each namespace a render creates or fills has a default deny (D76).

A namespace with no policy lets each of its pods reach any address and
accept any caller. The check reads each golden render (the umbrella profiles
and each separate release) and collects the namespaces that it creates (a
Namespace object) or puts pods in (a Deployment, StatefulSet, DaemonSet, Job,
CronJob or Pod). A namespace passes when the same render holds one of:

  - a CiliumClusterwideNetworkPolicy that selects the namespace by
    k8s:io.kubernetes.pod.namespace, denies ingress and egress by default,
    has only empty ingress rules ({} allows nothing), and allows only DNS
    to kube-dns on port 53 as egress,
  - a NetworkPolicy in the namespace with an empty podSelector, both policy
    types, and no ingress or egress rule (deny all). A rule such as
    `egress: [{}]` allows all traffic, so that policy is not a deny
    (charts#506).

scripts/.namespace-policy-exemptions.txt names a namespace that another
chart must cover, with its reason. A stale entry fails.

  check-namespace-policy.py             exit 1 on a finding
  check-namespace-policy.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
EXEMPT = os.path.join("scripts", ".namespace-policy-exemptions.txt")
RELEASE_NS = "gibson"  # the --namespace of each golden render (scripts/golden.sh)
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"}
NS_LABEL = "k8s:io.kubernetes.pod.namespace"


def namespaces(docs: list) -> set[str]:
    out = set()
    for d in docs:
        if d.get("kind") == "Namespace":
            out.add(d["metadata"]["name"])
        elif d.get("kind") in POD_KINDS:
            out.add((d.get("metadata") or {}).get("namespace") or RELEASE_NS)
    return out


KUBE_DNS = {NS_LABEL: "kube-system", "k8s:k8s-app": "kube-dns"}


def dns_only(rule: dict) -> bool:
    """True when a Cilium egress rule reaches kube-dns on port 53 and nothing else."""
    if set(rule) - {"toEndpoints", "toPorts"}:
        return False
    peers = rule.get("toEndpoints") or []
    if not peers or any(p != {"matchLabels": KUBE_DNS} for p in peers):
        return False
    ports = [p for tp in rule.get("toPorts") or [] for p in tp.get("ports") or []]
    return bool(ports) and all(set(p) <= {"port", "protocol"} and str(p.get("port")) == "53"
                               and p.get("protocol") in ("UDP", "TCP") for p in ports)


def covered(docs: list) -> set[str]:
    out = set()
    for d in docs:
        spec = d.get("spec") or {}
        if d.get("kind") == "CiliumClusterwideNetworkPolicy":
            deny = spec.get("enableDefaultDeny") or {}
            sel = spec.get("endpointSelector") or {}
            ml = sel.get("matchLabels") or {}
            # Only a selector of the namespace alone covers each pod of it.
            ns = ml.get(NS_LABEL) if set(sel) == {"matchLabels"} and set(ml) == {NS_LABEL} else None
            if not (ns and deny.get("ingress") is True and deny.get("egress") is True):
                continue
            if any(r != {} for r in spec.get("ingress") or []):
                continue
            if not all(dns_only(r) for r in spec.get("egress") or []):
                continue
            out.add(ns)
        elif d.get("kind") == "NetworkPolicy":
            if (spec.get("podSelector") in ({}, None) and set(spec.get("policyTypes") or []) >= {"Ingress", "Egress"}
                    and not spec.get("ingress") and not spec.get("egress")):
                out.add((d.get("metadata") or {}).get("namespace") or RELEASE_NS)
    return out


def judge(docs: list, exempt: set[str]) -> tuple[list[str], set[str]]:
    docs = [d for d in docs if isinstance(d, dict)]
    open_ns = namespaces(docs) - covered(docs)
    return [f"namespace {n} has pods or is created, and no default deny covers it"
            for n in sorted(open_ns - exempt)], open_ns & exempt


def load_exempt(root: str) -> dict[str, str]:
    out = {}
    path = os.path.join(root, EXEMPT)
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#")[0].strip()
            if line:
                name, _, reason = line.partition(" ")
                out[name] = reason.strip()
    return out


def audit(root: str) -> tuple[list[str], int]:
    exempt = load_exempt(root)
    bad, used, seen = [], set(), 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = list(yaml.safe_load_all(open(f)))
        if not namespaces([d for d in docs if isinstance(d, dict)]):
            continue
        seen += 1
        found, hit = judge(docs, set(exempt))
        used |= hit
        bad += [f"{os.path.basename(f)}: {x}" for x in found]
    bad += [f"stale exemption, delete the line: {n} ({exempt[n]})" for n in sorted(set(exempt) - used)]
    if not seen:
        bad.append("no golden render holds a namespace with pods: this check is blind")
    return bad, seen


def selftest() -> int:
    pod = lambda ns: {"kind": "Deployment", "metadata": {"name": "p", "namespace": ns}}
    dns = {"toEndpoints": [{"matchLabels": KUBE_DNS}],
           "toPorts": [{"ports": [{"port": "53", "protocol": "UDP"}, {"port": "53", "protocol": "TCP"}],
                        "rules": {"dns": [{"matchPattern": "*"}]}}]}
    ccnp = lambda ns, e=True, ing=None, eg=None: {"kind": "CiliumClusterwideNetworkPolicy", "spec": {
        "endpointSelector": {"matchLabels": {NS_LABEL: ns}}, "enableDefaultDeny": {"ingress": True, "egress": e},
        "ingress": [{}] if ing is None else ing, "egress": [dns] if eg is None else eg}}
    np = {"kind": "NetworkPolicy", "metadata": {"namespace": "b"}, "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}}
    if judge([pod("a"), ccnp("a"), pod("b"), np], set())[0]:
        print("SELFTEST FAIL: two covered namespaces must pass")
        return 1
    for what, docs, ex in (("a namespace with no policy", [pod("a")], set()),
                           ("a created namespace with no policy", [{"kind": "Namespace", "metadata": {"name": "a"}}], set()),
                           ("a default deny for another namespace", [pod("a"), ccnp("b")], set()),
                           ("a clusterwide policy that allows egress", [pod("a"), ccnp("a", e=False)], set()),
                           ("an ingress-only NetworkPolicy", [pod("b"), dict(np, spec={"podSelector": {}, "policyTypes": ["Ingress"]})], set()),
                           # charts#506: an allow-all policy is not a default deny.
                           ("a NetworkPolicy that allows all egress",
                            [pod("b"), dict(np, spec=dict(np["spec"], egress=[{}]))], set()),
                           ("a NetworkPolicy that allows all ingress",
                            [pod("b"), dict(np, spec=dict(np["spec"], ingress=[{}]))], set()),
                           ("a clusterwide policy that allows the world",
                            [pod("a"), ccnp("a", eg=[dns, {"toEntities": ["world"]}])], set()),
                           ("a clusterwide policy that allows all ingress",
                            [pod("a"), ccnp("a", ing=[{"fromEntities": ["all"]}])], set()),
                           ("a clusterwide policy whose DNS port opens a range",
                            [pod("a"), ccnp("a", eg=[dict(dns, toPorts=[{"ports": [
                                {"port": "53", "endPort": 65535, "protocol": "UDP"}]}])])], set()),
                           ("a clusterwide policy that selects some pods of the namespace",
                            [pod("a"), dict(ccnp("a"), spec=dict(ccnp("a")["spec"], endpointSelector={
                                "matchLabels": {NS_LABEL: "a", "app": "x"}}))], set()),
                           ("a clusterwide policy whose DNS rule reaches each pod",
                            [pod("a"), ccnp("a", eg=[dict(dns, toEndpoints=[{}])])], set()),
                           ("a pod with no namespace", [{"kind": "Job", "metadata": {"name": "j"}}], set())):
        if len(judge(docs, ex)[0]) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs, ex)[0]}")
            return 1
    if judge([pod("a")], {"a"})[0]:
        print("SELFTEST FAIL: an exempt namespace must pass")
        return 1
    print("  ✓ selftest: an uncovered, a created, a wrongly selected, a half-denied and an allow-all namespace fail; an exempt one passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("a namespace of a render has no default deny (D76):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ namespace-policy: each namespace of {seen} renders has a default deny")
    return 0


if __name__ == "__main__":
    sys.exit(main())
