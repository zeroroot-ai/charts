#!/usr/bin/env python3
"""check-connector-creds-binding.py — the Secret grant goes to the connector operator, and the daemon holds none.

The tenant-operator gives the connector operator its Secret access one tenant
namespace at a time: a RoleBinding from ClusterRole gibson-connector-creds to
the ServiceAccount that CONNECTOR_OPERATOR_SERVICE_ACCOUNT_NAME names
(gibson#664). The daemon holds no Kubernetes client (ADR-0023), so no binding
may give its ServiceAccount Tenant, ConnectorInstance or Secret rules.

This guard renders the umbrella for every profile and fails when the env value
on the tenant-operator Deployment differs from the serviceAccountName of the
connector-operator Deployment, when the ClusterRole is missing or bound
cluster-wide, when the tenant operator may not bind it, and when a binding
gives the daemon one of those rules.

  check-connector-creds-binding.py             exit 1 on a finding
  check-connector-creds-binding.py --selftest  prove each finding fails
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = ["values-baseline.yaml", "values-baseline.yaml -f helm/gibson/values-eks.yaml", "values-baseline.yaml -f helm/gibson/values-guest.yaml"]
CLUSTER_ROLE = "gibson-connector-creds"


def render(profile: str) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", f"helm/gibson/{profile.split()[0]}"]
    if "-f" in profile:
        args += ["-f", profile.split()[-1]]
    args += ["-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def find(docs, kind, name):
    for d in docs:
        if d.get("kind") == kind and d.get("metadata", {}).get("name") == name:
            return d
    return None


DAEMON_KINDS = {"tenants", "connectorinstances", "secrets"}


def judge(docs: list[dict]) -> list[str]:
    out = []
    ss = None
    for d in docs:
        if d.get("kind") == "StatefulSet" and d.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "daemon":
            ss = d
    if ss is None:
        return ["no StatefulSet with app.kubernetes.io/component=daemon in the render"]
    daemon_sa = ss["spec"]["template"]["spec"].get("serviceAccountName")
    co = find(docs, "Deployment", "gibson-connector-operator")
    if co is None:
        return ["no Deployment gibson-connector-operator in the render"]
    co_sa = co["spec"]["template"]["spec"].get("serviceAccountName")
    dep = find(docs, "Deployment", "gibson-tenant-operator")
    if dep is None:
        return ["no Deployment gibson-tenant-operator in the render"]
    env_val = None
    for c in dep["spec"]["template"]["spec"]["containers"]:
        for e in c.get("env", []):
            if e.get("name") == "CONNECTOR_OPERATOR_SERVICE_ACCOUNT_NAME":
                env_val = e.get("value")
    if env_val is None:
        out.append("tenant-operator Deployment has no CONNECTOR_OPERATOR_SERVICE_ACCOUNT_NAME env")
    elif env_val != co_sa:
        out.append(f"CONNECTOR_OPERATOR_SERVICE_ACCOUNT_NAME={env_val!r} but the connector operator runs as {co_sa!r}")
    if find(docs, "ClusterRole", CLUSTER_ROLE) is None:
        out.append(f"ClusterRole {CLUSTER_ROLE} is not rendered; the operator's per-tenant RoleBinding would dangle")
    for d in docs:
        if d.get("kind") == "ClusterRoleBinding" and d.get("roleRef", {}).get("name") == CLUSTER_ROLE:
            out.append(f"ClusterRoleBinding {d['metadata']['name']} binds {CLUSTER_ROLE} cluster-wide; the grant is per tenant namespace only")
    cr = find(docs, "ClusterRole", "gibson-tenant-operator")
    can_bind = False
    for r in (cr or {}).get("rules", []):
        if "clusterroles" in r.get("resources", []) and "bind" in r.get("verbs", []) and CLUSTER_ROLE in r.get("resourceNames", []):
            can_bind = True
    if not can_bind:
        out.append(f"ClusterRole gibson-tenant-operator may not bind {CLUSTER_ROLE} (clusterroles/bind resourceNames)")
    # The daemon holds no Kubernetes client (ADR-0023, gibson#664): no binding
    # may give its ServiceAccount Tenant, ConnectorInstance or Secret rules.
    roles = {(d["kind"], d["metadata"]["name"]): d for d in docs if d.get("kind") in ("Role", "ClusterRole")}
    for b in docs:
        if b.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        if not any(s.get("kind") == "ServiceAccount" and s.get("name") == daemon_sa for s in b.get("subjects") or []):
            continue
        role = roles.get((b.get("roleRef", {}).get("kind"), b.get("roleRef", {}).get("name")))
        for r in (role or {}).get("rules", []):
            hit = DAEMON_KINDS & set(r.get("resources") or [])
            if hit:
                out.append(f"{b['kind']} {b['metadata']['name']} gives the daemon ServiceAccount {daemon_sa!r} "
                           f"rules on {sorted(hit)}; the daemon holds no Kubernetes client (gibson#664)")
    return out


def selftest() -> int:
    docs = render(PROFILES[0])
    if judge(docs):
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(judge(docs)))
        return 1
    # The connector operator runs as another ServiceAccount.
    renamed = copy.deepcopy(docs)
    for d in renamed:
        if d.get("kind") == "Deployment" and d["metadata"]["name"] == "gibson-connector-operator":
            d["spec"]["template"]["spec"]["serviceAccountName"] = "gibson-renamed"
    got = judge(renamed)
    if len(got) != 1 or "runs as 'gibson-renamed'" not in got[0]:
        print(f"SELFTEST FAIL: a renamed connector operator ServiceAccount must fail, got {got}")
        return 1
    # A grant to the daemon comes back (gibson#664).
    daemon_sa = next(d for d in docs if d.get("kind") == "StatefulSet" and d["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "daemon")["spec"]["template"]["spec"]["serviceAccountName"]
    back = copy.deepcopy(docs) + [
        {"kind": "ClusterRole", "metadata": {"name": "tenants-read"}, "rules": [{"apiGroups": ["gibson.zeroroot.ai"], "resources": ["tenants"], "verbs": ["list"]}]},
        {"kind": "ClusterRoleBinding", "metadata": {"name": "tenants-read"}, "roleRef": {"kind": "ClusterRole", "name": "tenants-read"},
         "subjects": [{"kind": "ServiceAccount", "name": daemon_sa, "namespace": "gibson"}]}]
    got = judge(back)
    if len(got) != 1 or "holds no Kubernetes client" not in got[0]:
        print(f"SELFTEST FAIL: a Tenant grant to the daemon must fail, got {got}")
        return 1
    # A cluster-wide binding sneaking back fails.
    crb = copy.deepcopy(docs) + [{"kind": "ClusterRoleBinding", "metadata": {"name": "sneak"}, "roleRef": {"name": CLUSTER_ROLE}}]
    got = judge(crb)
    if len(got) != 1 or "cluster-wide" not in got[0]:
        print(f"SELFTEST FAIL: a ClusterRoleBinding on {CLUSTER_ROLE} must fail, got {got}")
        return 1
    # The operator losing its bind permission fails.
    nobind = [d for d in copy.deepcopy(docs)]
    for d in nobind:
        if d.get("kind") == "ClusterRole" and d["metadata"]["name"] == "gibson-tenant-operator":
            for r in d["rules"]:
                if "clusterroles" in r.get("resources", []):
                    r["resourceNames"] = [n for n in r.get("resourceNames", []) if n != CLUSTER_ROLE]
    got = judge(nobind)
    if len(got) != 1 or "may not bind" not in got[0]:
        print(f"SELFTEST FAIL: losing clusterroles/bind on {CLUSTER_ROLE} must fail, got {got}")
        return 1
    print("OK: a renamed connector operator ServiceAccount, a grant to the daemon, a cluster-wide binding and a lost bind permission fail; the baseline agrees")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for p in PROFILES:
        for m in judge(render(p)):
            bad.append(f"{p}: {m}")
    if bad:
        print("❌ the tenant-operator would bind the wrong ServiceAccount, the Secret grant is not per tenant, or the daemon holds a grant:\n  " + "\n  ".join(bad))
        return 1
    print("✓ connector-creds-binding: every profile binds the connector operator's real ServiceAccount, per tenant namespace only, and grants the daemon nothing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
