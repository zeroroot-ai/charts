#!/usr/bin/env python3
"""check-egress-policy-type.py: one policy type for egress by host name (ADR-0165 rule 4, charts#395).

The chart renders CiliumNetworkPolicy, through the one helper
gibson.ciliumEgressPolicy (gibson-common), for egress by host name. Every
cluster runs Cilium (ADR-0087, hosted#436). Two things fail, over the
committed golden renders (helm/testdata/golden/):

  1. A second policy type that can permit egress by host or by mesh: Calico
     NetworkPolicy and GlobalNetworkPolicy, an Istio ServiceEntry or Sidecar,
     a CiliumClusterwideNetworkPolicy, a GKE FQDNNetworkPolicy, an
     AdminNetworkPolicy. The chart ships no egress proxy and no mesh.
  2. Two egress policies that disagree: a pod that a CiliumNetworkPolicy with
     toFQDNs selects, and that a Kubernetes NetworkPolicy also gives an
     allow-all egress rule. Cilium unions allow rules, so the allow-all rule
     would win and the host list would mean nothing.

The self-test also renders the helper in a throw-away chart. It proves that the
policy permits DNS to kube-dns with a DNS rule, names each host with its
ports, and that an empty host list or a host with no port fails the render.

  check-egress-policy-type.py             exit 1 on a finding
  check-egress-policy-type.py --selftest  prove each finding fails, and the helper
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
OTHER_TYPES = {
    ("crd.projectcalico.org", "NetworkPolicy"), ("crd.projectcalico.org", "GlobalNetworkPolicy"),
    ("projectcalico.org", "NetworkPolicy"), ("projectcalico.org", "GlobalNetworkPolicy"),
    ("networking.istio.io", "ServiceEntry"), ("networking.istio.io", "Sidecar"),
    ("cilium.io", "CiliumClusterwideNetworkPolicy"),
    ("networking.gke.io", "FQDNNetworkPolicy"),
    ("policy.networking.k8s.io", "AdminNetworkPolicy"),
}


def group(d: dict) -> str:
    av = str(d.get("apiVersion", ""))
    return av.split("/")[0] if "/" in av else ""


def selects(selector: dict, labels: dict) -> bool:
    """A Kubernetes label selector against the full labels of one pod."""
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        k, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and k in labels and labels[k] in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return True


WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "Pod")


def pods(docs: list) -> list[tuple[str, str, dict]]:
    """(namespace, workload, pod labels) of each rendered pod template."""
    out = []
    for d in docs:
        if d.get("kind") == "CronJob":
            t = (((d.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}
        elif d.get("kind") == "Pod":
            t = d
        elif d.get("kind") in WORKLOADS:
            t = (d.get("spec") or {}).get("template") or {}
        else:
            continue
        labels = (t.get("metadata") or {}).get("labels") or {}
        out.append((d["metadata"].get("namespace"), f"{d['kind']}/{d['metadata']['name']}", labels))
    return out


def judge(docs: list) -> list[str]:
    bad = []
    docs = [d for d in docs if isinstance(d, dict)]
    for d in docs:
        if (group(d), d.get("kind")) in OTHER_TYPES:
            bad.append(f"{d['kind']}/{d['metadata']['name']}: a second policy type for egress "
                       f"({group(d)}); the chart renders CiliumNetworkPolicy only")
    rendered = pods(docs)
    for c in docs:
        if c.get("kind") != "CiliumNetworkPolicy":
            continue
        if not any(r.get("toFQDNs") for r in (c.get("spec") or {}).get("egress") or []):
            continue
        ns = c["metadata"].get("namespace")
        sel = (c["spec"].get("endpointSelector") or {})
        limited = [(w, l) for n, w, l in rendered if n == ns and selects(sel, l)]
        if not limited:
            bad.append(f"CiliumNetworkPolicy/{c['metadata']['name']} selects no rendered pod")
        for w, labels in limited:
            for n in docs:
                if n.get("kind") != "NetworkPolicy" or n["metadata"].get("namespace") != ns:
                    continue
                spec = n.get("spec") or {}
                if not selects(spec.get("podSelector") or {}, labels):
                    continue
                if any(not r.get("to") for r in spec.get("egress") or []):
                    bad.append(f"NetworkPolicy/{n['metadata']['name']} gives an allow-all egress rule to {w}, "
                               f"which CiliumNetworkPolicy/{c['metadata']['name']} limits by host name")
    return sorted(set(bad))


def golden_docs(root: str) -> list:
    out = []
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        out += [d for d in yaml.safe_load_all(open(f)) if d]
    return out


def render_helper(values: dict) -> tuple[int, str]:
    """Render gibson.ciliumEgressPolicy in a throw-away chart that depends on gibson-common."""
    with tempfile.TemporaryDirectory() as d:
        chart = os.path.join(d, "fixture")
        os.makedirs(os.path.join(chart, "templates"))
        os.makedirs(os.path.join(chart, "charts"))
        shutil.copytree(os.path.join(ROOT, "helm", "gibson-common"), os.path.join(chart, "charts", "gibson-common"))
        with open(os.path.join(chart, "Chart.yaml"), "w") as f:
            f.write("apiVersion: v2\nname: fixture\nversion: 0.0.1\nappVersion: \"0.0.1\"\n"
                    "dependencies:\n  - name: gibson-common\n    version: \"*\"\n")
        with open(os.path.join(chart, "templates", "p.yaml"), "w") as f:
            f.write('{{ include "gibson.ciliumEgressPolicy" (dict "ctx" $ "name" "fx" '
                    '"selector" .Values.selector "hosts" .Values.hosts) }}\n')
        with open(os.path.join(chart, "values.yaml"), "w") as f:
            yaml.safe_dump(values, f)
        p = subprocess.run(["helm", "template", "fx", chart, "--namespace", "fx"], capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


def selftest() -> int:
    pod = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "a", "namespace": "g"},
           "spec": {"template": {"metadata": {"labels": {"app": "a", "tier": "web"}}}}}
    np_all = {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
              "metadata": {"name": "np", "namespace": "g"},
              "spec": {"podSelector": {"matchLabels": {"app": "a"}}, "egress": [{}]}}
    cnp = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy", "metadata": {"name": "cnp", "namespace": "g"},
           "spec": {"endpointSelector": {"matchLabels": {"app": "a"}},
                    "egress": [{"toFQDNs": [{"matchName": "api.example.com"}]}]}}
    np_peer = dict(np_all, spec={"podSelector": {"matchLabels": {"app": "a"}},
                                 "egress": [{"to": [{"podSelector": {"matchLabels": {"app": "db"}}}]}]})
    se = {"apiVersion": "networking.istio.io/v1", "kind": "ServiceEntry", "metadata": {"name": "se"}}
    ccnp = {"apiVersion": "cilium.io/v2", "kind": "CiliumClusterwideNetworkPolicy", "metadata": {"name": "c"}}
    np_other = dict(np_all, metadata={"name": "jobs", "namespace": "g"},
                    spec={"podSelector": {"matchExpressions": [{"key": "app", "operator": "In", "values": ["job"]}]},
                          "egress": [{}]})
    if judge([cnp, np_other, pod]):
        print("SELFTEST FAIL: an allow-all policy for other pods (an In expression) must pass")
        return 1
    if judge([cnp, np_peer, pod]):
        print("SELFTEST FAIL: a host policy with a peer-only NetworkPolicy must pass")
        return 1
    for what, docs in (("an Istio ServiceEntry", [se]), ("a clusterwide Cilium policy", [ccnp]),
                       ("an allow-all NetworkPolicy on a host-limited pod", [cnp, np_all, pod]),
                       ("a host policy that selects no pod", [cnp])):
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    rc, out = render_helper({"selector": {"app": "a"},
                             "hosts": [{"host": "api.example.com", "ports": [443]},
                                       {"pattern": "*.example.org", "ports": [443, 8443]}]})
    if rc != 0:
        print(f"SELFTEST FAIL: the helper did not render:\n{out}")
        return 1
    p = next(d for d in yaml.safe_load_all(out) if d)
    eg = p["spec"]["egress"]
    dns = eg[0]["toPorts"][0]
    if p["kind"] != "CiliumNetworkPolicy" or dns["ports"][0]["port"] != "53" or not dns["rules"]["dns"]:
        print(f"SELFTEST FAIL: the helper must permit DNS with a DNS rule first: {eg[0]}")
        return 1
    if (eg[1]["toFQDNs"] != [{"matchName": "api.example.com"}] or eg[2]["toFQDNs"] != [{"matchPattern": "*.example.org"}]
            or [x["port"] for x in eg[2]["toPorts"][0]["ports"]] != ["443", "8443"]):
        print(f"SELFTEST FAIL: the helper must name each host with its ports: {eg[1:]}")
        return 1
    for what, vals in (("an empty host list", {"selector": {"app": "a"}, "hosts": []}),
                       ("a host with no port", {"selector": {"app": "a"}, "hosts": [{"host": "x.example.com"}]})):
        if render_helper(vals)[0] == 0:
            print(f"SELFTEST FAIL: {what} must fail the render")
            return 1
    print("  ✓ selftest: a second policy type and an allow-all rule on a host-limited pod fail; the helper "
          "renders DNS and each host with its ports, and refuses an empty list and a host with no port")
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
        print("egress by host name uses one policy type, with no disagreeing rule (ADR-0165, charts#395):",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    hosts = sum(1 for d in docs if isinstance(d, dict) and d.get("kind") == "CiliumNetworkPolicy")
    print(f"  ✓ egress-policy-type: {len(docs)} rendered objects, no second policy type for egress, "
          f"{hosts} CiliumNetworkPolicy objects with no disagreeing NetworkPolicy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
