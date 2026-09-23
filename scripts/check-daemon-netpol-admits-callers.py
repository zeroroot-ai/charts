#!/usr/bin/env python3
"""check-daemon-netpol-admits-callers.py — every in-cluster caller of the daemon is admitted by its NetworkPolicy.

The daemon's NetworkPolicy lists the only allowed ingress sources. A workload
that dials the daemon but appears in no rule is rejected at the CNI layer,
and the failure surfaces far from its cause: the caller reports a transport
error against a Service IP, while the daemon looks healthy and its Service
has endpoints.

Measured on staging 2026-09-23. The tenant-operator dials the daemon gRPC
port to drain the pending-provisioning, admin-tenant-op and first-tenant-seed
queues, and no rule admitted it. The `primary` tenant was never created and
the first-admin Job waited out its activeDeadlineSeconds, so the cluster had
no admin login. kind's kindnet implements no NetworkPolicy, so the whole
policy set is inert there and the exit tests could not see it.

A caller declares itself in the render: a container env value of the form
`<daemon-service>:<port>`. This guard reads those, then proves the daemon's
policy admits that workload's pod labels on that port.

  check-daemon-netpol-admits-callers.py             exit 1 on an unadmitted caller, 0 when clean
  check-daemon-netpol-admits-callers.py --selftest  prove an unadmitted caller fails and an admitted one passes
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
NAMESPACE = "gibson"
DAEMON_COMPONENT = "daemon"


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_template(d: dict) -> dict:
    return d["spec"]["jobTemplate"]["spec"]["template"] if d["kind"] == "CronJob" else d["spec"]["template"]


def pod_labels(d: dict) -> dict:
    return (pod_template(d).get("metadata") or {}).get("labels") or {}


def subset(sel: dict, labels: dict) -> bool:
    """A podSelector with no matchLabels selects everything in its scope."""
    return all(labels.get(k) == v for k, v in (sel.get("matchLabels") or {}).items())


def daemon_service_names(docs: list[dict]) -> set[str]:
    names = set()
    for d in docs:
        if d.get("kind") != "Service":
            continue
        sel = d["spec"].get("selector") or {}
        if sel.get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            names.add(d["metadata"]["name"])
    return names


def daemon_policies(docs: list[dict]) -> list[dict]:
    out = []
    for d in docs:
        if d.get("kind") != "NetworkPolicy":
            continue
        sel = (d["spec"].get("podSelector") or {}).get("matchLabels") or {}
        if sel.get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            out.append(d)
    return out


def callers(docs: list[dict], svc_names: set[str]) -> list[tuple[str, dict, int]]:
    """(workload name, its pod labels, the daemon port it names) for every in-cluster caller."""
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(svc_names)) + r")\b:(\d+)")
    found = []
    for d in docs:
        if d.get("kind") not in WORKLOADS:
            continue
        spec = pod_template(d)["spec"]
        labels = pod_labels(d)
        if labels.get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            continue  # the daemon's own pods are not ingress peers
        seen = set()
        for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
            for e in c.get("env") or []:
                v = e.get("value")
                if not isinstance(v, str):
                    continue
                for _, port in pattern.findall(v):
                    seen.add(int(port))
        for port in sorted(seen):
            found.append((d["metadata"]["name"], labels, port))
    return found


def admits(policies: list[dict], labels: dict, port: int) -> bool:
    for np in policies:
        for rule in np["spec"].get("ingress") or []:
            froms = rule.get("from")
            if froms is None:
                peer_ok = True  # no `from` means every source
            else:
                peer_ok = False
                for f in froms:
                    if "ipBlock" in f:
                        continue
                    if f.get("namespaceSelector") not in (None, {}):
                        continue  # a cross-namespace peer is not this workload
                    if subset(f.get("podSelector") or {}, labels):
                        peer_ok = True
                        break
            if not peer_ok:
                continue
            ports = rule.get("ports")
            if ports is None or any(p.get("port") == port for p in ports):
                return True
    return False


def audit(docs: list[dict]) -> list[str]:
    svc = daemon_service_names(docs)
    if not svc:
        return ["no daemon Service in the render: this guard cannot see its callers"]
    policies = daemon_policies(docs)
    if not policies:
        return ["no NetworkPolicy selects the daemon: it is unprotected, or this guard is blind"]
    bad = []
    for name, labels, port in callers(docs, svc):
        if not admits(policies, labels, port):
            shown = {k: v for k, v in labels.items() if k.startswith("app.kubernetes.io/")}
            bad.append(f"{name} dials the daemon on :{port} but no ingress rule admits {shown}")
    return bad


def selftest() -> int:
    policy = {
        "kind": "NetworkPolicy",
        "metadata": {"name": "daemon"},
        "spec": {
            "podSelector": {"matchLabels": {"app.kubernetes.io/component": "daemon"}},
            "ingress": [{
                "from": [{"podSelector": {"matchLabels": {"app.kubernetes.io/component": "envoy"}}}],
                "ports": [{"protocol": "TCP", "port": 50051}],
            }],
        },
    }
    service = {
        "kind": "Service",
        "metadata": {"name": "gibson-daemon"},
        "spec": {"selector": {"app.kubernetes.io/component": "daemon"}},
    }

    def caller(component):
        return {
            "kind": "Deployment",
            "metadata": {"name": f"gibson-{component}"},
            "spec": {"template": {
                "metadata": {"labels": {"app.kubernetes.io/component": component}},
                "spec": {"containers": [{
                    "name": "manager",
                    "env": [{"name": "GIBSON_DAEMON_GRPC_ADDRESS", "value": "gibson-daemon:50051"}],
                }]},
            }},
        }

    unadmitted = audit([service, policy, caller("tenant-operator")])
    if not unadmitted:
        print("SELFTEST FAIL: an unadmitted caller was not reported", file=sys.stderr)
        return 1
    admitted = audit([service, policy, caller("envoy")])
    if admitted:
        print(f"SELFTEST FAIL: an admitted caller was reported: {admitted}", file=sys.stderr)
        return 1
    print("  ✓ selftest: an unadmitted caller fails, an admitted one passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = audit(render())
    if bad:
        print("daemon NetworkPolicy does not admit every in-cluster caller:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        print("\nAdd an ingress rule to helm/gibson-workloads/templates/gibson/networkpolicy.yaml", file=sys.stderr)
        print("selecting the labels the caller's pods carry.", file=sys.stderr)
        return 1
    print("  ✓ daemon-netpol-admits-callers: every in-cluster caller is admitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
