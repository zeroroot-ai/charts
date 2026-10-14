#!/usr/bin/env python3
"""check-final-backup-reads-schedule.py: the tenant-operator may read the Schedule it builds the last backup from (C67).

The tenant-operator builds the last Backup of a deleted tenant from the
template of the Schedule that the chart ships (gibson#1177), so the last
backup excludes the same kinds as the hourly backups (secrets, the ESO
generators, the k3s kinds, CiliumEndpoints). The Role
gibson-tenant-operator-final-backup must grant get on that Schedule by name,
and get and create on backups. Without the Schedule read, the operator takes
no last backup, and a tenant delete stops.

The check reads the committed render helm/testdata/golden/gibson-velero.yaml.
It fails when the render holds no Schedule, when the Role is missing, when it
cannot get the Schedule by its rendered name, or when it cannot get and create
backups.

  check-final-backup-reads-schedule.py             exit 1 on a finding
  check-final-backup-reads-schedule.py --selftest  prove each shape fails
"""
import copy
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden", "gibson-velero.yaml")
ROLE = "gibson-tenant-operator-final-backup"


def grants(role, resource, verb, name=None) -> bool:
    for rule in role.get("rules") or []:
        if "velero.io" not in (rule.get("apiGroups") or []) or resource not in (rule.get("resources") or []):
            continue
        if verb not in (rule.get("verbs") or []):
            continue
        names = rule.get("resourceNames")
        if names and name not in names:
            continue
        return True
    return False


def judge(docs) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    schedules = [d["metadata"]["name"] for d in docs if d.get("kind") == "Schedule"]
    roles = [d for d in docs if d.get("kind") == "Role" and d.get("metadata", {}).get("name") == ROLE]
    bad = []
    if not schedules:
        bad.append("the render holds no Schedule: the last backup has no template, and this check looked at nothing")
    if not roles:
        bad.append(f"the render holds no Role {ROLE}")
        return bad
    role = roles[0]
    for s in schedules:
        if not grants(role, "schedules", "get", s):
            bad.append(f"Role {ROLE} cannot get the Schedule {s}, the template of the last backup")
    for verb in ("get", "create"):
        if not grants(role, "backups", verb):
            bad.append(f"Role {ROLE} cannot {verb} backups")
    return bad


def selftest() -> int:
    good = [
        {"kind": "Schedule", "metadata": {"name": "platform-hourly"}},
        {"kind": "Role", "metadata": {"name": ROLE}, "rules": [
            {"apiGroups": ["velero.io"], "resources": ["backups"], "verbs": ["get", "create"]},
            {"apiGroups": ["velero.io"], "resources": ["schedules"], "resourceNames": ["platform-hourly"],
             "verbs": ["get"]},
        ]},
    ]
    if judge(good):
        print(f"SELFTEST FAIL: the good render failed: {judge(good)}")
        return 1
    cases = []
    no_sched = copy.deepcopy(good); no_sched[1]["rules"].pop(1); cases.append(("no Schedule read", no_sched))
    other = copy.deepcopy(good); other[1]["rules"][1]["resourceNames"] = ["other"]; cases.append(("another Schedule name", other))
    no_create = copy.deepcopy(good); no_create[1]["rules"][0]["verbs"] = ["get"]; cases.append(("no backup create", no_create))
    cases.append(("no Role", good[:1]))
    cases.append(("no Schedule", good[1:]))
    for what, docs in cases:
        if not judge(docs):
            print(f"SELFTEST FAIL: {what} passed")
            return 1
    print("  ✓ selftest: no Schedule read, another Schedule name, no backup create, no Role and no Schedule each fail")
    return 0


def main() -> int:
    bad = judge(list(yaml.safe_load_all(open(GOLDEN))))
    if bad:
        print("the tenant-operator cannot build the last backup from the Schedule (C67):")
        for b in bad:
            print(f"  {b}")
        return 1
    print("  ✓ final-backup-reads-schedule: the tenant-operator may read the Schedule and get and create backups")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else main())
