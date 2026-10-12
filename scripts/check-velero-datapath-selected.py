#!/usr/bin/env python3
"""check-velero-datapath-selected.py: the Velero policy selects each data path pod (charts#671, drill sweep row 17).

Velero 1.18 runs each PodVolumeBackup and PodVolumeRestore in a hosting pod of
the velero namespace. The pod carries one label, velero.io/pod-volume-backup or
velero.io/pod-volume-restore, and no label of the Velero server or node-agent
(velero v1.18.1, pkg/controller/pod_volume_backup_controller.go). The namespace
default deny selects it. On staging the egress allow selected only the server
and the maintenance Jobs, so every volume backup of platform-hourly failed on
`dial tcp 172.20.0.1:443: i/o timeout`.

The check reads each pod fixture under tests/fixtures/velero-datapath/, a copy
of the hosting pod shape from the Velero source at the pinned version, and the
committed Velero render (helm/testdata/golden/gibson-velero.yaml). For each
fixture pod it fails when no CiliumNetworkPolicy spec of the pod's namespace
selects the pod, and when a selecting spec does not allow egress to the
kube-apiserver entity and to the bucket on one port. The golden gate keeps
the render equal to the chart.

  check-velero-datapath-selected.py             exit 1 on a finding
  check-velero-datapath-selected.py --selftest  prove each finding fails
"""
import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden", "gibson-velero.yaml")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "velero-datapath")
NS_KEY = "io.kubernetes.pod.namespace"


def norm(key: str) -> str:
    """Cilium writes a source prefix (k8s:) on a selector key. Drop it."""
    return key.split(":", 1)[1] if ":" in key else key


def selects(selector: dict, pod: dict) -> bool:
    labels = dict(pod["metadata"].get("labels") or {})
    labels[NS_KEY] = pod["metadata"]["namespace"]
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(norm(k)) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        k, op, vals = norm(e["key"]), e["operator"], e.get("values") or []
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and labels.get(k) in vals:
            return False
    return True


def egress_problems(spec: dict) -> list[str]:
    rules = spec.get("egress") or []
    if not any("kube-apiserver" in (r.get("toEntities") or []) for r in rules):
        return ["no egress to the kube-apiserver entity"]
    bucket = [r for r in rules if r.get("toFQDNs") or r.get("toCIDR")]
    if len(bucket) != 1:
        return [f"{len(bucket)} bucket rules (toFQDNs or toCIDR), want one"]
    ports = [p for tp in (bucket[0].get("toPorts") or []) for p in (tp.get("ports") or [])]
    if len(ports) != 1:
        return [f"the bucket rule names {len(ports)} ports, want one"]
    return []


def policy_specs(docs: list) -> list[tuple[str, str, dict]]:
    """(policy name, namespace, spec) for each CiliumNetworkPolicy spec."""
    out = []
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") != "CiliumNetworkPolicy":
            continue
        name = d["metadata"]["name"]
        ns = d["metadata"].get("namespace", "")
        for spec in d.get("specs") or ([d["spec"]] if d.get("spec") else []):
            out.append((name, ns, spec))
    return out


def check(docs: list, pods: list[dict]) -> list[str]:
    findings = []
    specs = policy_specs(docs)
    if not specs:
        return ["the render holds no CiliumNetworkPolicy spec: the check is blind"]
    if not pods:
        return [f"no pod fixture under {os.path.relpath(FIXTURES, ROOT)}: the check is blind"]
    for pod in pods:
        pname, pns = pod["metadata"]["name"], pod["metadata"]["namespace"]
        hits = [(n, s) for n, ns, s in specs if ns == pns and selects(s.get("endpointSelector") or {}, pod)]
        if not hits:
            findings.append(f"pod {pns}/{pname} (labels {pod['metadata'].get('labels')}): "
                            f"no CiliumNetworkPolicy spec of the namespace selects it, so the default deny "
                            f"closes the API server and the bucket")
            continue
        for n, s in hits:
            for p in egress_problems(s):
                findings.append(f"pod {pns}/{pname}: policy {n} selects it, and the spec has {p}")
    return findings


def load_pods() -> list[dict]:
    pods = []
    for f in sorted(glob.glob(os.path.join(FIXTURES, "*.yaml"))):
        with open(f, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        if not isinstance(d, dict) or d.get("kind") != "Pod":
            raise SystemExit(f"{os.path.relpath(f, ROOT)} is not a Pod")
        pods.append(d)
    return pods


def load_docs() -> list:
    with open(GOLDEN, encoding="utf-8") as fh:
        return [d for d in yaml.safe_load_all(fh) if d]


def selftest(docs: list, pods: list[dict]) -> int:
    def strip(docs_in: list, key: str) -> list:
        out = copy.deepcopy(docs_in)
        for d in out:
            if isinstance(d, dict) and d.get("kind") == "CiliumNetworkPolicy":
                d["specs"] = [s for s in d["specs"] if not any(
                    e.get("key") == key for e in (s.get("endpointSelector") or {}).get("matchExpressions") or [])]
        return out

    def without_api(docs_in: list) -> list:
        out = copy.deepcopy(docs_in)
        for d in out:
            if isinstance(d, dict) and d.get("kind") == "CiliumNetworkPolicy":
                for s in d["specs"]:
                    s["egress"] = [r for r in s["egress"] if "toEntities" not in r]
        return out

    if check(docs, pods):
        print("selftest: the committed render must pass", file=sys.stderr)
        return 1
    cases = {
        "the policy before charts#672 (no pod-volume-backup selector) fails":
            (strip(docs, "velero.io/pod-volume-backup"), pods),
        "a policy with no pod-volume-restore selector fails":
            (strip(docs, "velero.io/pod-volume-restore"), pods),
        "a pod with a label the policy does not select fails":
            (docs, [{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "du", "namespace": "gibson",
                                                                    "labels": {"velero.io/data-upload": "du"}}}]),
        "a pod of another namespace with the right label fails":
            (docs, [{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "pvb", "namespace": "other",
                                                                    "labels": {"velero.io/pod-volume-backup": "pvb"}}}]),
        "a selecting spec without the API server entity fails": (without_api(docs), pods),
        "a render with no policy is blind and fails": ([{"kind": "ConfigMap"}], pods),
        "no fixture is blind and fails": (docs, []),
    }
    bad = [name for name, (d, p) in cases.items() if not check(d, p)]
    if bad:
        print("selftest: these cases did not fail: " + "; ".join(bad), file=sys.stderr)
        return 1
    print(f"selftest ok: {len(cases)} broken inputs each fail, and the committed render passes")
    return 0


def main() -> int:
    docs, pods = load_docs(), load_pods()
    if "--selftest" in sys.argv[1:]:
        return selftest(docs, pods)
    findings = check(docs, pods)
    for f in findings:
        print(f"::error::velero-datapath-selected: {f}")
    if findings:
        return 1
    print(f"  ✓ velero-datapath-selected: {len(pods)} data path pod fixtures, each selected by a policy spec "
          f"that allows the API server and the bucket")
    return 0


if __name__ == "__main__":
    sys.exit(main())
