#!/usr/bin/env python3
"""check-metrics-policy.py: the cluster scraper reaches only metrics ports (D76).

A metrics rule that opens a list of ports on every pod also opens the API
ports in that list. A pod label alone does not identify the scraper, because
the person who creates a pod chooses its labels.

The check reads each golden render and fails on:

  1. a spec of the metrics policy (component network-metrics) that does not
     select exactly the pods with one value of the label
     gibson.zeroroot.ai/metrics-port,
  2. an ingress rule of that spec that opens a port other than that value,
     admits an entity, or admits a peer whose namespace is not one fixed
     value,
  3. a pod with the metrics-port label and no spec for its port,
  4. a pod with the metrics-port label whose port a Service of the same
     render publishes as an API port, that is under a name with no
     "metrics" or "prometheus" in it.

  check-metrics-policy.py             exit 1 on a finding
  check-metrics-policy.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
RELEASE_NS = "gibson"  # the --namespace of each golden render (scripts/golden.sh)
LABEL = "gibson.zeroroot.ai/metrics-port"
NS_LABEL = "k8s:io.kubernetes.pod.namespace"
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job"}
METRICS_NAMES = ("metrics", "prometheus")


def ns_of(doc: dict) -> str:
    return (doc.get("metadata") or {}).get("namespace") or RELEASE_NS


def pods(docs: list):
    """Yield (name, namespace, labels, containers) for each pod template."""
    for d in docs:
        kind, spec = d.get("kind"), d.get("spec") or {}
        if kind == "CronJob":
            spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
        if kind in POD_KINDS or kind == "CronJob":
            t = spec.get("template") or {}
            yield (f"{kind} {d['metadata']['name']}", ns_of(d), (t.get("metadata") or {}).get("labels") or {},
                   (t.get("spec") or {}).get("containers") or [])
        elif kind == "Cluster" and str(d.get("apiVersion", "")).split("/")[0] == "postgresql.cnpg.io":
            yield (f"Cluster {d['metadata']['name']}", ns_of(d),
                   (spec.get("inheritedMetadata") or {}).get("labels") or {}, [])


def judge_policy(docs: list) -> tuple[list[str], set[str]]:
    bad, ports = [], set()
    for d in docs:
        if d.get("kind") != "CiliumNetworkPolicy":
            continue
        if ((d.get("metadata") or {}).get("labels") or {}).get("app.kubernetes.io/component") != "network-metrics":
            continue
        name = d["metadata"]["name"]
        specs = d.get("specs") or ([d["spec"]] if d.get("spec") else [])
        if not specs:
            bad.append(f"{name}: the metrics policy has no spec")
        for s in specs:
            sel = s.get("endpointSelector") or {}
            ml = sel.get("matchLabels") or {}
            if sel.get("matchExpressions") or set(ml) != {LABEL}:
                bad.append(f"{name}: a spec selects {sel}, not one value of {LABEL}")
                continue
            port = str(ml[LABEL])
            ports.add(port)
            for rule in s.get("ingress") or []:
                if rule.get("fromEntities") or rule.get("fromCIDR") or rule.get("fromCIDRSet"):
                    bad.append(f"{name}: the rule for port {port} admits an entity or a CIDR")
                for peer in rule.get("fromEndpoints") or [{}]:
                    pml = peer.get("matchLabels") or {}
                    if not pml.get(NS_LABEL) or any(e.get("key") == NS_LABEL for e in peer.get("matchExpressions") or []):
                        bad.append(f"{name}: the rule for port {port} admits a scraper from any namespace")
                    if set(pml) <= {NS_LABEL}:
                        bad.append(f"{name}: the rule for port {port} admits each pod of a namespace, not one scraper")
                opened = [str(p.get("port")) for tp in rule.get("toPorts") or [{}] for p in (tp.get("ports") or [{}])]
                if opened != [port]:
                    bad.append(f"{name}: the rule for port {port} opens {opened}, not only {port}")
    return bad, ports


def api_ports(docs: list, ns: str, labels: dict, containers: list) -> dict[str, str]:
    """The ports that a Service publishes on this pod under a name that is not a metrics name."""
    named = {p.get("name"): str(p.get("containerPort")) for c in containers for p in c.get("ports") or []}
    out = {}
    for d in docs:
        if d.get("kind") != "Service" or ns_of(d) != ns:
            continue
        sel = (d.get("spec") or {}).get("selector") or {}
        if not sel or any(labels.get(k) != v for k, v in sel.items()):
            continue
        for p in (d.get("spec") or {}).get("ports") or []:
            target = p.get("targetPort", p.get("port"))
            target = named.get(target, str(target))
            pname = str(p.get("name") or "")
            if not any(m in pname for m in METRICS_NAMES):
                out[target] = f"Service {d['metadata']['name']} port {pname or p.get('port')}"
    return out


def judge(docs: list) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    bad, ports = judge_policy(docs)
    for name, ns, labels, containers in pods(docs):
        port = labels.get(LABEL)
        if port is None:
            continue
        port = str(port)
        if port not in ports:
            bad.append(f"{name}: has {LABEL}={port}, and no metrics rule opens that port")
        api = api_ports(docs, ns, labels, containers).get(port)
        if api:
            bad.append(f"{name}: {LABEL}={port} is an API port ({api}), never open it to the scraper")
    return bad


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = [d for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)]
        if not any(d.get("kind") == "CiliumNetworkPolicy" and ((d.get("metadata") or {}).get("labels") or {})
                   .get("app.kubernetes.io/component") == "network-metrics" for d in docs):
            continue
        seen += 1
        found = judge(docs)
        if not any(LABEL in labels for _, _, labels, _ in pods(docs)):
            found.append(f"no pod carries {LABEL}: this check is blind")
        bad += [f"{os.path.basename(f)}: {x}" for x in found]
    if not seen:
        bad.append("no golden render holds the metrics policy: this check is blind")
    return bad, seen


def selftest() -> int:
    scraper = {NS_LABEL: "monitoring", "app.kubernetes.io/name": "prometheus"}

    def spec(port, peer=None, opened=None, sel=None):
        return {"endpointSelector": sel or {"matchLabels": {LABEL: port}},
                "ingress": [{"fromEndpoints": [peer or {"matchLabels": scraper}],
                             "toPorts": [{"ports": [{"port": p, "protocol": "TCP"} for p in (opened or [port])]}]}]}

    def policy(*specs):
        return {"kind": "CiliumNetworkPolicy", "metadata": {
            "name": "m", "labels": {"app.kubernetes.io/component": "network-metrics"}}, "specs": list(specs)}

    def pod(name, port, cport=None, cname="metrics"):
        return {"kind": "Deployment", "metadata": {"name": name, "namespace": "gibson"}, "spec": {"template": {
            "metadata": {"labels": {"app": name, LABEL: port}},
            "spec": {"containers": [{"name": "c", "ports": [{"name": cname, "containerPort": int(cport or port)}]}]}}}}

    def svc(app, pname, port):
        return {"kind": "Service", "metadata": {"name": app, "namespace": "gibson"},
                "spec": {"selector": {"app": app}, "ports": [{"name": pname, "port": port, "targetPort": pname}]}}

    good = [policy(spec("9090"), spec("8080")), pod("daemon", "9090"), svc("daemon", "metrics", 9090),
            pod("op", "8080")]
    if judge(good):
        print(f"SELFTEST FAIL: a policy that opens only the metrics ports must pass, got {judge(good)}")
        return 1
    failing = (
        # The advisory shape: the OpenFGA HTTP API on 8080 open to the scraper.
        ("an API port labelled as the metrics port",
         [policy(spec("8080")), pod("openfga", "8080", cname="http"), svc("openfga", "http", 8080)]),
        ("a rule that opens a list of ports", [policy(spec("9090", opened=["9090", "8080"])), pod("d", "9090")]),
        ("a scraper from any namespace",
         [policy(spec("9090", peer={"matchLabels": {"app.kubernetes.io/name": "prometheus"},
                                    "matchExpressions": [{"key": NS_LABEL, "operator": "Exists"}]})),
          pod("d", "9090")]),
        ("each pod of the scraper namespace", [policy(spec("9090", peer={"matchLabels": {NS_LABEL: "monitoring"}})),
                                               pod("d", "9090")]),
        ("a spec that selects each pod with a role",
         [policy(spec("9090", sel={"matchExpressions": [{"key": "gibson.zeroroot.ai/net-role", "operator": "Exists"}]})),
          policy(spec("9091")), pod("d", "9091")]),
        ("a pod whose metrics port has no rule", [policy(spec("9090")), pod("d", "9091")]),
    )
    for what, docs in failing:
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: an API port, a port list, an open namespace, an open role selector and a missing rule fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("the metrics policy opens more than the metrics ports (D76):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ metrics-policy: the scraper reaches only metrics ports in {seen} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
