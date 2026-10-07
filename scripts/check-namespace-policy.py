#!/usr/bin/env python3
"""check-namespace-policy.py: each namespace a render creates or fills has a default deny (D76).

A namespace with no policy lets each of its pods reach any address and
accept any caller. The check reads each golden render (the umbrella profiles
and each separate release) and collects the namespaces that it creates (a
Namespace object) or puts pods in (a Deployment, StatefulSet, DaemonSet, Job,
CronJob or Pod). A namespace passes when the same render holds one of:

  - a CiliumClusterwideNetworkPolicy that selects the namespace by
    k8s:io.kubernetes.pod.namespace and denies ingress and egress by default,
  - a NetworkPolicy in the namespace with an empty podSelector and both
    policy types (deny all).

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


def covered(docs: list) -> set[str]:
    out = set()
    for d in docs:
        spec = d.get("spec") or {}
        if d.get("kind") == "CiliumClusterwideNetworkPolicy":
            deny = spec.get("enableDefaultDeny") or {}
            ns = ((spec.get("endpointSelector") or {}).get("matchLabels") or {}).get(NS_LABEL)
            if ns and deny.get("ingress") is True and deny.get("egress") is True:
                out.add(ns)
        elif d.get("kind") == "NetworkPolicy":
            if spec.get("podSelector") in ({}, None) and set(spec.get("policyTypes") or []) >= {"Ingress", "Egress"}:
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
    ccnp = lambda ns, e=True: {"kind": "CiliumClusterwideNetworkPolicy", "spec": {
        "endpointSelector": {"matchLabels": {NS_LABEL: ns}}, "enableDefaultDeny": {"ingress": True, "egress": e}}}
    np = {"kind": "NetworkPolicy", "metadata": {"namespace": "b"}, "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}}
    if judge([pod("a"), ccnp("a"), pod("b"), np], set())[0]:
        print("SELFTEST FAIL: two covered namespaces must pass")
        return 1
    for what, docs, ex in (("a namespace with no policy", [pod("a")], set()),
                           ("a created namespace with no policy", [{"kind": "Namespace", "metadata": {"name": "a"}}], set()),
                           ("a default deny for another namespace", [pod("a"), ccnp("b")], set()),
                           ("a clusterwide policy that allows egress", [pod("a"), ccnp("a", e=False)], set()),
                           ("an ingress-only NetworkPolicy", [pod("b"), dict(np, spec={"podSelector": {}, "policyTypes": ["Ingress"]})], set()),
                           ("a pod with no namespace", [{"kind": "Job", "metadata": {"name": "j"}}], set())):
        if len(judge(docs, ex)[0]) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs, ex)[0]}")
            return 1
    if judge([pod("a")], {"a"})[0]:
        print("SELFTEST FAIL: an exempt namespace must pass")
        return 1
    print("  ✓ selftest: an uncovered, a created, a wrongly selected and a half-denied namespace fail; an exempt one passes")
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
