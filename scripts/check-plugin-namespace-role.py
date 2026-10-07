#!/usr/bin/env python3
"""check-plugin-namespace-role.py: the tenant-operator may write each object of a plugin instance.

The tenant-operator binds the ClusterRole gibson-tenant-operator-plugin-namespace
in each namespace tenant-<t>-plugins (gibson#815). For each plugin instance it
writes a Deployment, a ServiceAccount, the ConfigMap gibson-envoy-ca, a
NetworkPolicy with no egress rule, and one CiliumNetworkPolicy that allows
DNS, the hosts of the catalog entry and the edge host. Without the
CiliumNetworkPolicy grant the instance has no egress allow, or keeps an
allow-all egress.

The check reads each golden render and fails when the ClusterRole is missing,
when it grants a Secret, or when it lacks get, list, create, update or delete
on one of the kinds in KINDS.

  check-plugin-namespace-role.py             exit 1 on a finding
  check-plugin-namespace-role.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
ROLE = "gibson-tenant-operator-plugin-namespace"
VERBS = {"get", "list", "create", "update", "delete"}
KINDS = {("apps", "deployments"), ("", "serviceaccounts"), ("", "configmaps"),
         ("networking.k8s.io", "networkpolicies"), ("cilium.io", "ciliumnetworkpolicies")}


def judge(role: dict | None) -> list[str]:
    if role is None:
        return [f"no ClusterRole {ROLE}"]
    granted: dict[tuple[str, str], set[str]] = {}
    for r in role.get("rules") or []:
        for g in r.get("apiGroups") or []:
            for res in r.get("resources") or []:
                granted.setdefault((g, res), set()).update(r.get("verbs") or [])
    out = []
    for g, res in sorted(KINDS):
        miss = VERBS - granted.get((g, res), set())
        if miss:
            out.append(f"{ROLE} lacks {sorted(miss)} on {res} ({g or 'core'})")
    if any(res == "secrets" for (_, res) in granted):
        out.append(f"{ROLE} grants Secrets; a plugin namespace holds none")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "values-*.yaml"))):
        seen += 1
        role = next((d for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)
                     and d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == ROLE), None)
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(role)]
    if not seen:
        bad.append("no golden render: this check is blind")
    return bad, seen


def selftest() -> int:
    def role(*rules):
        return {"rules": list(rules)}
    full = [{"apiGroups": [g], "resources": [r], "verbs": sorted(VERBS)} for g, r in sorted(KINDS)]
    if judge(role(*full)):
        print(f"SELFTEST FAIL: a full role must pass, got {judge(role(*full))}")
        return 1
    no_cnp = [r for r in full if r["apiGroups"] != ["cilium.io"]]
    no_delete = [dict(r, verbs=["get", "list", "create", "update"]) if r["apiGroups"] == ["cilium.io"] else r for r in full]
    secret = full + [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]}]
    for what, r in (("no CiliumNetworkPolicy rule", role(*no_cnp)), ("no delete verb", role(*no_delete)),
                    ("a Secret grant", role(*secret)), ("no role", None)):
        if len(judge(r)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(r)}")
            return 1
    print("  ✓ selftest: no CiliumNetworkPolicy rule, a missing verb, a Secret grant and no role each fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("the tenant-operator cannot write each object of a plugin instance:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ plugin-namespace-role: {seen} renders grant each object of a plugin instance and no Secret")
    return 0


if __name__ == "__main__":
    sys.exit(main())
