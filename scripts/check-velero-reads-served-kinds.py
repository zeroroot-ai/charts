#!/usr/bin/env python3
"""check-velero-reads-served-kinds.py: the Velero server can list each served kind a Backup lists (charts#660, charts#684).

A Backup lists every namespaced kind that the cluster serves and the Backup
does not exclude, and Velero counts a forbidden list as a backup error, so
the Backup ends PartiallyFailed. The Schedule excludes some kinds (secrets,
the ESO generators, CiliumEndpoints), but a Backup that is not built from the
Schedule excludes none of them: the hosted rotation exit test makes one and
requires Completed. So the role must read each served kind that carries no
credential, even when the Schedule leaves it out.

REQUIRED lists the kinds measured as listing errors (charts#660,
backup-restore run 38201546512) plus events.k8s.io. The check reads the
committed golden render (helm/testdata/golden/gibson-velero.yaml) and fails on
each REQUIRED kind that the ClusterRole velero-server cannot get and list.

  check-velero-reads-served-kinds.py             exit 1 on a finding
  check-velero-reads-served-kinds.py --selftest  prove a dropped kind fails
"""
import copy
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden", "gibson-velero.yaml")
ROLE = "velero-server"

# (apiGroup, resource) of each served namespaced kind with no credential.
REQUIRED = [
    ("resource.k8s.io", "resourceclaims"),
    ("resource.k8s.io", "resourceclaimtemplates"),
    ("certificates.k8s.io", "podcertificaterequests"),
    ("cilium.io", "ciliumnetworkpolicies"),
    ("cilium.io", "ciliumendpoints"),
    ("cilium.io", "ciliumnodeconfigs"),
    ("events.k8s.io", "events"),
]


def cluster_role(docs):
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == "ClusterRole" and (d.get("metadata") or {}).get("name") == ROLE:
            return d
    return None


def can(role, group, resource, verb) -> bool:
    for rule in role.get("rules") or []:
        groups = rule.get("apiGroups") or []
        resources = rule.get("resources") or []
        verbs = rule.get("verbs") or []
        if (group in groups or "*" in groups) and (resource in resources or "*" in resources) \
                and (verb in verbs or "*" in verbs):
            return True
    return False


def missing(role: str, verb: str, group: str, resource: str) -> str:
    return f"ClusterRole {role} cannot {verb} {resource}.{group}: a Backup that lists it ends PartiallyFailed"


def judge(docs) -> list[str]:
    role = cluster_role(docs)
    if role is None:
        return [f"no ClusterRole {ROLE} in the render: the check would measure nothing"]
    bad = []
    for group, resource in REQUIRED:
        for verb in ("get", "list"):
            if not can(role, group, resource, verb):
                bad.append(missing(ROLE, verb, group, resource))
    return bad


def load():
    with open(GOLDEN) as f:
        return [d for d in yaml.safe_load_all(f) if d]


def selftest() -> int:
    docs = load()
    if judge(docs):
        print("GUARD BROKEN: the committed render already fails; fix it before the self-test", file=sys.stderr)
        return 2
    broken = copy.deepcopy(docs)
    role = cluster_role(broken)
    for rule in role.get("rules") or []:
        if any(g == "cilium.io" for g in rule.get("apiGroups") or []):
            rule["resources"] = [r for r in rule.get("resources") or [] if r != "ciliumendpoints"]
    found = set(judge(broken))
    want = {missing(ROLE, verb, "cilium.io", "ciliumendpoints") for verb in ("get", "list")}
    if found != want:
        print("GUARD BROKEN: a role with no read of ciliumendpoints passed", file=sys.stderr)
        return 2
    if not judge([d for d in docs if d.get("kind") != "ClusterRole"]):
        print("GUARD BROKEN: a render with no velero-server ClusterRole passed", file=sys.stderr)
        return 2
    print("✅ self-test: a role that drops ciliumendpoints fails, and a render with no role fails")
    return 0


def main(argv) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    bad = judge(load())
    if bad:
        print("❌ the Velero server cannot list a served kind (charts#660, charts#684):")
        for b in bad:
            print("   ", b)
        return 1
    print(f"✅ ClusterRole {ROLE} can get and list each of {len(REQUIRED)} served kinds")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
