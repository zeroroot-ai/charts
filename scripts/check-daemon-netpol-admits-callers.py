#!/usr/bin/env python3
"""check-daemon-netpol-admits-callers.py — the network policy lets every in-cluster caller reach the daemon.

The Cilium policies of the release select pods by label (D76,
helm/gibson/templates/network-policies.yaml). A workload that dials the
daemon but has no label that the policies allow is rejected at the CNI layer,
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
`<daemon-service>:<port>`. This guard reads those, then proves that the
egress rules of the caller and the ingress rules of the daemon both allow the
flow on that port (scripts/lib/cilium_policy.py).

  check-daemon-netpol-admits-callers.py             exit 1 on an unadmitted caller, 0 when clean
  check-daemon-netpol-admits-callers.py --selftest  prove an unadmitted caller fails and an admitted one passes
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts", "lib"))
import cilium_policy as cp  # noqa: E402
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


def daemon_service_names(docs: list[dict]) -> set[str]:
    names = set()
    for d in docs:
        if d.get("kind") != "Service":
            continue
        sel = d["spec"].get("selector") or {}
        if sel.get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            names.add(d["metadata"]["name"])
    return names


def daemon_pod(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") in WORKLOADS and pod_labels(d).get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            return cp.endpoint(d["metadata"].get("namespace") or NAMESPACE, pod_labels(d))
    return None


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


def audit(docs: list[dict]) -> list[str]:
    svc = daemon_service_names(docs)
    if not svc:
        return ["no daemon Service in the render: this guard cannot see its callers"]
    daemon = daemon_pod(docs)
    if daemon is None:
        return ["no daemon pod in the render: this guard is blind"]
    rules = cp.rules(docs, NAMESPACE)
    if not any(cp.rule_selects(r, ns, daemon) for _, r, ns in rules):
        return ["no Cilium policy selects the daemon: it is unprotected, or this guard is blind"]
    bad = []
    for name, labels, port in callers(docs, svc):
        if not cp.reaches(rules, cp.endpoint(NAMESPACE, labels), daemon, port):
            shown = {k: v for k, v in labels.items() if k.startswith(("app.kubernetes.io/component", "gibson.zeroroot.ai/"))}
            bad.append(f"{name} dials the daemon on :{port} but the network policy does not allow {shown}")
    return bad


def selftest() -> int:
    platform = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
                "metadata": {"name": "platform", "namespace": NAMESPACE},
                "spec": {"endpointSelector": {"matchLabels": {"gibson.zeroroot.ai/net-role": "platform"}},
                         "ingress": [{"fromEndpoints": [{"matchLabels": {"gibson.zeroroot.ai/net-role": "platform"}}]}],
                         "egress": [{"toEndpoints": [{"matchLabels": {"gibson.zeroroot.ai/net-role": "platform"}}]}]}}
    service = {"kind": "Service", "metadata": {"name": "gibson-daemon"},
               "spec": {"selector": {"app.kubernetes.io/component": "daemon"}}}

    def workload(component, role, env=True):
        labels = {"app.kubernetes.io/component": component}
        if role:
            labels["gibson.zeroroot.ai/net-role"] = role
        container = {"name": "manager"}
        if env:
            container["env"] = [{"name": "GIBSON_DAEMON_GRPC_ADDRESS", "value": "gibson-daemon:50051"}]
        return {"kind": "Deployment", "metadata": {"name": f"gibson-{component}", "namespace": NAMESPACE},
                "spec": {"template": {"metadata": {"labels": labels}, "spec": {"containers": [container]}}}}

    daemon = workload("daemon", "platform", env=False)
    if not audit([service, platform, daemon, workload("tenant-operator", "system")]):
        print("SELFTEST FAIL: a caller with no platform label was not reported", file=sys.stderr)
        return 1
    if not audit([service, daemon, workload("tenant-operator", "platform")]):
        print("SELFTEST FAIL: a daemon that no policy selects was not reported", file=sys.stderr)
        return 1
    admitted = audit([service, platform, daemon, workload("tenant-operator", "platform")])
    if admitted:
        print(f"SELFTEST FAIL: an admitted caller was reported: {admitted}", file=sys.stderr)
        return 1
    print("  ✓ selftest: a caller with no platform label fails, an unselected daemon fails, a platform caller passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = audit(render())
    if bad:
        print("the network policy does not let every in-cluster caller reach the daemon:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        print("\nGive the caller the platform role with gibson.netLabels (gibson-common).", file=sys.stderr)
        return 1
    print("  ✓ daemon-netpol-admits-callers: the network policy lets every in-cluster caller reach the daemon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
