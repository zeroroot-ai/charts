#!/usr/bin/env python3
"""check-operator-rbac-covers.py — the chart grants each gibson operator at
least the role its own controllers declare.

helm/contracts/gibson-operator-rbac-<name>.yaml is the operator's generated
ClusterRole at the pinned gibson tag (scripts/gen-operator-rbac.py). This check
renders the chart and reads every ClusterRole bound to the operator's
ServiceAccount through a ClusterRoleBinding. It fails on each (group, resource,
verb) that the operator's role holds and the chart does not grant at cluster
scope.

Only cluster scope counts. The operator's informers list at cluster scope, so a
namespaced Role does not let one sync: the apiserver answers "forbidden ... at
the cluster scope", the cache never syncs, and the controllers that share it
never start. The pod stays Running and Ready the whole time.

The chart may grant MORE than the operator's role. That direction is the
business of the RBAC scope guards, not of this one.

  check-operator-rbac-covers.py             exit 1 on a missing grant
  check-operator-rbac-covers.py --selftest  prove a missing verb, a missing
                                            resource and a namespaced grant fail
"""
from __future__ import annotations

import itertools
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPERATORS = {"tenant": "gibson-tenant-operator", "connector": "gibson-connector-operator",
             "platform": "gibson-platform-operator"}


def helm_template() -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/gibson/values-baseline.yaml",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def cluster_rules(docs: list[dict], sa: str) -> list[dict]:
    """Every rule the ServiceAccount holds through a ClusterRoleBinding."""
    roles = {d["metadata"]["name"]: d.get("rules") or [] for d in docs if d.get("kind") == "ClusterRole"}
    out: list[dict] = []
    for d in docs:
        if d.get("kind") != "ClusterRoleBinding":
            continue
        if any(s.get("kind") == "ServiceAccount" and s.get("name") == sa for s in d.get("subjects") or []):
            out += roles.get(d["roleRef"]["name"], [])
    return out


def has(rule: dict, key: str, value: str) -> bool:
    values = rule.get(key) or []
    return value in values or "*" in values


def missing(want_rules: list[dict], got_rules: list[dict]) -> list[tuple[str, str, str]]:
    out = []
    for r in want_rules:
        for group, res, verb in itertools.product(r.get("apiGroups") or [""], r.get("resources") or [], r.get("verbs") or []):
            if not any(has(g, "apiGroups", group) and has(g, "resources", res) and has(g, "verbs", verb)
                       for g in got_rules):
                out.append((group or "core", res, verb))
    return out


def want(op: str) -> list[dict]:
    path = os.path.join(ROOT, "helm", "contracts", f"gibson-operator-rbac-{op}.yaml")
    rules: list[dict] = []
    for d in yaml.safe_load_all(open(path)):
        if d and d.get("kind") == "ClusterRole":
            rules += d.get("rules") or []
    return rules


def selftest() -> int:
    want_rules = [{"apiGroups": ["gibson.zeroroot.ai"], "resources": ["oidcclients"], "verbs": ["get", "list", "watch"]}]
    sa = "gibson-tenant-operator"

    def render(rules, binding="ClusterRoleBinding", role="ClusterRole"):
        return [{"kind": role, "metadata": {"name": "r", "namespace": "gibson"}, "rules": rules},
                {"kind": binding, "metadata": {"name": "b", "namespace": "gibson"},
                 "roleRef": {"kind": role, "name": "r"}, "subjects": [{"kind": "ServiceAccount", "name": sa}]}]

    full = [{"apiGroups": ["gibson.zeroroot.ai"], "resources": ["oidcclients"], "verbs": ["get", "list", "watch", "create"]}]
    if missing(want_rules, cluster_rules(render(full), sa)):
        print("selftest: a chart that grants more than the operator role must pass", file=sys.stderr)
        return 1
    cases = [
        ("a missing verb", render([{"apiGroups": ["gibson.zeroroot.ai"], "resources": ["oidcclients"], "verbs": ["get", "list"]}]), 1),
        ("a missing resource", render([{"apiGroups": ["gibson.zeroroot.ai"], "resources": ["tenants"], "verbs": ["get", "list", "watch"]}]), 3),
        ("a grant through a namespaced RoleBinding", render(full, binding="RoleBinding", role="Role"), 3),
    ]
    for what, docs, n in cases:
        got = missing(want_rules, cluster_rules(docs, sa))
        if len(got) != n:
            print(f"selftest: {what} must leave {n} missing grant(s), left {got}", file=sys.stderr)
            return 1
    print("check-operator-rbac-covers selftest PASSED (a missing verb, a missing resource and a "
          "namespaced grant each fail, and a wider chart role passes)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    docs = helm_template()
    problems = []
    total = 0
    for op, sa in OPERATORS.items():
        want_rules = want(op)
        got_rules = cluster_rules(docs, sa)
        if not want_rules:
            problems.append(f"{op}-operator: the vendored role holds no rule, so this check read nothing")
        if not got_rules:
            problems.append(f"{op}-operator: the render binds no ClusterRole to ServiceAccount {sa}")
        gaps = missing(want_rules, got_rules)
        total += sum(len(r.get("resources") or []) for r in want_rules)
        by: dict[tuple[str, str], list[str]] = {}
        for group, res, verb in gaps:
            by.setdefault((group, res), []).append(verb)
        for (group, res), verbs in sorted(by.items()):
            problems.append(f"{op}-operator: its own role holds {','.join(verbs)} on {res}.{group}, "
                            f"and the chart does not grant that to {sa} at cluster scope")
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-operator-rbac-covers PASSED ({len(OPERATORS)} operators, {total} resources: "
          f"the chart grants each operator at least its own role)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
