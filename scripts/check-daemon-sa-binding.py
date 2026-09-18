#!/usr/bin/env python3
"""check-daemon-sa-binding.py — the operator binds the daemon's real ServiceAccount.

The tenant-operator gives the daemon its Secret access one tenant namespace
at a time: a RoleBinding from ClusterRole gibson-connector-creds to the
daemon's ServiceAccount (gibson#137). The operator learns that ServiceAccount
name from DAEMON_SERVICE_ACCOUNT_NAME. The daemon's StatefulSet runs as
gibson-workloads' gibson.serviceAccount.name. The two live in different
subcharts and nothing in Helm ties them together. A mismatch is silent at
install time and a 403 on every connector credential write at run time.

This guard renders the umbrella for every profile and fails when the env
value on the tenant-operator Deployment differs from the serviceAccountName
on the daemon StatefulSet, or when either is missing, or when the ClusterRole
the operator may bind is not the one the workloads chart renders.

  check-daemon-sa-binding.py             exit 1 on a mismatch, 0 when every profile agrees
  check-daemon-sa-binding.py --selftest  prove a renamed ServiceAccount fails
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


def judge(docs: list[dict]) -> list[str]:
    out = []
    ss = None
    for d in docs:
        if d.get("kind") == "StatefulSet" and d.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") == "daemon":
            ss = d
    if ss is None:
        return ["no StatefulSet with app.kubernetes.io/component=daemon in the render"]
    daemon_sa = ss["spec"]["template"]["spec"].get("serviceAccountName")
    dep = find(docs, "Deployment", "gibson-tenant-operator")
    if dep is None:
        return ["no Deployment gibson-tenant-operator in the render"]
    env_val = None
    for c in dep["spec"]["template"]["spec"]["containers"]:
        for e in c.get("env", []):
            if e.get("name") == "DAEMON_SERVICE_ACCOUNT_NAME":
                env_val = e.get("value")
    if env_val is None:
        out.append("tenant-operator Deployment has no DAEMON_SERVICE_ACCOUNT_NAME env")
    elif env_val != daemon_sa:
        out.append(f"DAEMON_SERVICE_ACCOUNT_NAME={env_val!r} but the daemon StatefulSet runs as {daemon_sa!r}")
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
    return out


def selftest() -> int:
    docs = render(PROFILES[0])
    if judge(docs):
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(judge(docs)))
        return 1
    # THE FIXTURE THIS EXISTS FOR: the daemon runs as another ServiceAccount.
    renamed = copy.deepcopy(docs)
    for d in renamed:
        if d.get("kind") == "StatefulSet" and d["metadata"].get("labels", {}).get("app.kubernetes.io/component") == "daemon":
            d["spec"]["template"]["spec"]["serviceAccountName"] = "gibson-renamed"
    got = judge(renamed)
    if len(got) != 1 or "runs as 'gibson-renamed'" not in got[0]:
        print(f"SELFTEST FAIL: a renamed daemon ServiceAccount must fail, got {got}")
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
    print("OK: a renamed daemon ServiceAccount, a cluster-wide binding and a lost bind permission fail; the baseline agrees")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for p in PROFILES:
        for m in judge(render(p)):
            bad.append(f"{p}: {m}")
    if bad:
        print("❌ the tenant-operator would bind the wrong ServiceAccount, or the daemon Secret grant is not per tenant:\n  " + "\n  ".join(bad))
        return 1
    print("✓ daemon-sa-binding: every profile binds the daemon's real ServiceAccount, per tenant namespace only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
