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

The tenant and connector operators start callers in tenant namespaces that
the render does not hold (charts#505): the belief trainer dials the daemon
gRPC port (GIBSON_DAEMON_GRPC_ADDRESS of the tenant-operator), and a
connector runner dials the harness callback port (the daemon container port
`callback`). The operators write the egress side in each tenant namespace,
so the guard proves the ingress side of the daemon for a modeled pod of each.
A tenant namespace carries gibson.zeroroot.ai/managed-by: tenant-operator
(gibson operators/tenant/internal/controller/tenant_namespace.go).

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
TENANT_NS = "tenant-example"
TENANT_NS_LABELS = {"io.cilium.k8s.namespace.labels.gibson.zeroroot.ai/managed-by": "tenant-operator"}


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


def daemon_container_port(docs: list[dict], name: str) -> int | None:
    for d in docs:
        if d.get("kind") in WORKLOADS and pod_labels(d).get("app.kubernetes.io/component") == DAEMON_COMPONENT:
            for c in pod_template(d)["spec"].get("containers") or []:
                for p in c.get("ports") or []:
                    if p.get("name") == name:
                        return int(p["containerPort"])
    return None


def operator_env_port(docs: list[dict], component: str, env: str) -> int | None:
    for d in docs:
        if d.get("kind") in WORKLOADS and pod_labels(d).get("app.kubernetes.io/component") == component:
            for c in pod_template(d)["spec"].get("containers") or []:
                for e in c.get("env") or []:
                    if e.get("name") == env and isinstance(e.get("value"), str) and ":" in e["value"]:
                        return int(e["value"].rsplit(":", 1)[1])
    return None


def tenant_callers(docs: list[dict]) -> tuple[list[tuple[str, dict, int]], list[str]]:
    """The modeled callers that the operators start in a tenant namespace."""
    bad = []
    grpc = operator_env_port(docs, "tenant-operator", "GIBSON_DAEMON_GRPC_ADDRESS")
    callback = daemon_container_port(docs, "callback")
    if grpc is None:
        bad.append("no tenant-operator GIBSON_DAEMON_GRPC_ADDRESS in the render: the trainer port is unknown")
    if callback is None:
        bad.append("no daemon container port named callback in the render: the callback port is unknown")
    out = []
    if grpc is not None:
        out.append(("belief trainer (tenant namespace)",
                    {**TENANT_NS_LABELS, "app.kubernetes.io/component": "belief-trainer"}, grpc))
    if callback is not None:
        out.append(("connector runner (tenant namespace)", {**TENANT_NS_LABELS, "toolhive-name": "example"}, callback))
    return out, bad


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
    modeled, missing = tenant_callers(docs)
    bad += missing
    for name, labels, port in modeled:
        src = cp.endpoint(TENANT_NS, labels)
        if not any(cp.rule_selects(r, ns, daemon) and cp.ingress_admits(r, ns, src, port) for _, r, ns in rules):
            bad.append(f"{name} dials the daemon on :{port} but no ingress rule of the daemon admits a tenant namespace")
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
    daemon["spec"]["template"]["spec"]["containers"][0]["ports"] = [{"name": "callback", "containerPort": 50001}]
    tenant = {"apiVersion": "cilium.io/v2", "kind": "CiliumNetworkPolicy",
              "metadata": {"name": "daemon-tenant-callers", "namespace": NAMESPACE},
              "spec": {"endpointSelector": {"matchLabels": {"app.kubernetes.io/component": "daemon"}},
                       "ingress": [{"fromEndpoints": [{
                           "matchLabels": {"k8s:" + next(iter(TENANT_NS_LABELS)): "tenant-operator"},
                           "matchExpressions": [{"key": "k8s:io.kubernetes.pod.namespace", "operator": "Exists"}]}],
                           "toPorts": [{"ports": [{"port": "50001", "protocol": "TCP"},
                                                  {"port": "50051", "protocol": "TCP"}]}]}]}}
    # charts#505: without the tenant rule, the operator-started callers fail.
    unadmitted = audit([service, platform, daemon, workload("tenant-operator", "platform")])
    if len(unadmitted) != 2 or not all("tenant namespace" in b for b in unadmitted):
        print(f"SELFTEST FAIL: the tenant-namespace callers must fail with no tenant rule, got {unadmitted}",
              file=sys.stderr)
        return 1
    narrow = dict(tenant, spec=dict(tenant["spec"], ingress=[dict(tenant["spec"]["ingress"][0], toPorts=[
        {"ports": [{"port": "50051", "protocol": "TCP"}]}])]))
    if len(audit([service, platform, narrow, daemon, workload("tenant-operator", "platform")])) != 1:
        print("SELFTEST FAIL: a tenant rule without the callback port must fail once", file=sys.stderr)
        return 1
    if not audit([service, platform, tenant, daemon, workload("tenant-operator", "system")]):
        print("SELFTEST FAIL: a caller with no platform label was not reported", file=sys.stderr)
        return 1
    if not audit([service, daemon, workload("tenant-operator", "platform")]):
        print("SELFTEST FAIL: a daemon that no policy selects was not reported", file=sys.stderr)
        return 1
    admitted = audit([service, platform, tenant, daemon, workload("tenant-operator", "platform")])
    if admitted:
        print(f"SELFTEST FAIL: an admitted caller was reported: {admitted}", file=sys.stderr)
        return 1
    print("  ✓ selftest: a caller with no platform label fails, an unselected daemon fails, tenant callers with no or a narrow tenant rule fail, a platform caller passes")
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
