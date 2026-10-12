#!/usr/bin/env python3
"""check-tenant-namespace-kinds.py — the chart grants the tenant-operator each
kind that it writes in a tenant or plugin namespace (charts#640).

The tenant-operator writes some kinds in the namespaces of a tenant through
the manager client, with a per-namespace Role. The chart renders those grants
as two ClusterRoles that the operator binds in each namespace:
gibson-tenant-operator-tenant-namespace and
gibson-tenant-operator-plugin-namespace. These kinds have no kubebuilder
marker, so check-operator-rbac-covers.py does not see them. gibson#1089 was
this gap: the belief trainer CronJob had no grant, and the step that wrote it
failed in every tenant.

helm/contracts/gibson-tenant-namespace-kinds.yaml lists the kinds, from
perTenantNamespaceCacheDisableTypes() at the pinned gibson tag
(scripts/gen-operator-rbac.py). This check renders the chart and fails on each
kind that neither ClusterRole grants with each verb of VERBS. The list does not
say which kinds go to a plugin namespace only, so a kind passes when either
ClusterRole grants it.

  check-tenant-namespace-kinds.py             exit 1 on a kind with no grant
  check-tenant-namespace-kinds.py --selftest  prove a missing kind, a missing
                                              verb and a blind render fail
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, "helm", "contracts", "gibson-tenant-namespace-kinds.yaml")
ROLES = ("gibson-tenant-operator-tenant-namespace", "gibson-tenant-operator-plugin-namespace")
# The verbs that a create-or-update of the operator needs on each kind.
VERBS = ("get", "list", "create", "update", "delete")


def helm_template() -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/gibson/values-baseline.yaml",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def kinds() -> list[tuple[str, str]]:
    data = yaml.safe_load(open(CONTRACT)) or {}
    return [(k["group"], k["resource"]) for k in data.get("kinds") or []]


def rules(docs: list[dict]) -> list[dict]:
    out: list[dict] = []
    for d in docs:
        if d.get("kind") == "ClusterRole" and d["metadata"]["name"] in ROLES:
            out += d.get("rules") or []
    return out


def has(rule: dict, key: str, value: str) -> bool:
    values = rule.get(key) or []
    return value in values or "*" in values


def missing(want: list[tuple[str, str]], got: list[dict]) -> list[str]:
    if not got:
        return [f"no ClusterRole named {' or '.join(ROLES)} renders: the check looked at nothing"]
    out = []
    for group, res in want:
        for verb in VERBS:
            if not any(has(r, "apiGroups", group) and has(r, "resources", res) and has(r, "verbs", verb) for r in got):
                out.append(f"{group or 'core'}/{res}: {verb}")
    return out


def selftest() -> int:
    want = [("batch", "cronjobs"), ("", "secrets")]
    good = [{"apiGroups": ["batch"], "resources": ["cronjobs"], "verbs": list(VERBS)},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": list(VERBS)}]
    if missing(want, good):
        print("selftest: a role that grants each kind must pass", file=sys.stderr)
        return 1
    cases = {
        "a missing kind (the gibson#1089 shape)": ([good[1]], len(VERBS)),
        "a missing verb": ([good[0], {**good[1], "verbs": ["get", "list", "create", "update"]}], 1),
        "a kind in another API group": ([{**good[0], "apiGroups": ["apps"]}, good[1]], len(VERBS)),
        "no per-namespace ClusterRole": ([], 1),
    }
    for what, (got, n) in cases.items():
        found = missing(want, copy.deepcopy(got))
        if len(found) != n:
            print(f"selftest: {what} must give {n} finding(s), gave {found}", file=sys.stderr)
            return 1
    print("check-tenant-namespace-kinds selftest PASSED (a missing kind, a missing verb, another API "
          "group and a blind render fail; a full grant passes)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    want = kinds()
    if not want:
        print("::error::helm/contracts/gibson-tenant-namespace-kinds.yaml lists no kind: run `make operator-rbac-sync`")
        return 1
    found = missing(want, rules(helm_template()))
    for f in found:
        print(f"::error::the tenant-operator writes {f} in a tenant namespace, and no per-namespace "
              f"ClusterRole ({', '.join(ROLES)}) grants it")
    if found:
        return 1
    print(f"ok: the per-namespace ClusterRoles grant each of the {len(want)} kinds that the tenant-operator writes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
