#!/usr/bin/env python3
"""check-cronjob-pods-excluded-from-backup.py: each CronJob pod of the chart stays out of a Velero backup (C66).

The Velero backups back up every pod volume (defaultVolumesToFsBackup). A Job
pod of a CronJob that ends while a backup runs leaves no volume path on the
host, so its PodVolumeBackup fails and the whole backup ends PartiallyFailed
(hosted backup-restore run 38517042392: gibson-redis-rotation, volume tmp).
A Job pod has nothing to restore, so its pod template carries
velero.io/exclude-from-backup: "true". The CronJob and its Job objects stay
in the backup.

The check reads each committed golden render and fails on a CronJob whose
pod template lacks the label, and on a render set that holds no CronJob at
all, because then the check looked at nothing. A CronJob that no golden
profile renders (gibson-redis-backup renders only under redis.backup.enabled)
is still checked in its template: each template file that holds a CronJob
must hold the label.

  check-cronjob-pods-excluded-from-backup.py             exit 1 on a finding
  check-cronjob-pods-excluded-from-backup.py --selftest  prove each shape fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden", "*.yaml")
LABEL = "velero.io/exclude-from-backup"


def pod_labels(cronjob: dict) -> dict:
    spec = ((cronjob.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}
    return (((spec.get("template") or {}).get("metadata") or {}).get("labels")) or {}


def judge(docs) -> tuple[list[str], int]:
    bad, seen = [], 0
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") != "CronJob":
            continue
        seen += 1
        name = (d.get("metadata") or {}).get("name", "?")
        if str(pod_labels(d).get(LABEL, "")) != "true":
            bad.append(f"CronJob {name}: the pod template has no {LABEL}: \"true\", so a backup that "
                       "runs while its Job pod ends fails on that pod's volumes")
    return bad, seen


TEMPLATES = os.path.join(ROOT, "helm", "*", "templates", "**", "*.yaml")


LABEL_LINE = re.compile(r'^\s*' + re.escape(LABEL) + r':\s*"true"\s*$', re.M)


def template_findings(paths) -> list[str]:
    """Each template file with a CronJob has the label as a YAML line of its
    own: a mention inside a comment does not count."""
    bad = []
    for f in paths:
        text = open(f).read()
        if re.search(r"^kind: CronJob\s*$", text, re.M) and not LABEL_LINE.search(text):
            bad.append(f"{os.path.relpath(f, ROOT)}: a CronJob template with no {LABEL} label line")
    return bad


def audit() -> int:
    bad, seen = [], 0
    bad += template_findings(sorted(glob.glob(TEMPLATES, recursive=True)))
    for f in sorted(glob.glob(GOLDEN)):
        docs = list(yaml.safe_load_all(open(f)))
        b, s = judge(docs)
        seen += s
        bad += [f"{os.path.basename(f)}: {x}" for x in b]
    if seen == 0:
        bad.append("no golden render holds a CronJob: this check is blind")
    if bad:
        print("a CronJob pod is not excluded from a Velero backup (C66):")
        for b in sorted(set(bad)):
            print(f"  {b}")
        return 1
    print(f"  ✓ cronjob-pods-excluded-from-backup: {seen} CronJob(s) in the renders keep their pods out of a backup")
    return 0


def selftest() -> int:
    def cj(labels):
        return {"kind": "CronJob", "metadata": {"name": "c"},
                "spec": {"jobTemplate": {"spec": {"template": {"metadata": {"labels": labels}}}}}}
    good = cj({"app": "x", LABEL: "true"})
    for what, doc in (("no label", cj({"app": "x"})), ("label false", cj({LABEL: "false"})),
                      ("no pod template", {"kind": "CronJob", "metadata": {"name": "c"}, "spec": {}})):
        if not judge([doc])[0]:
            print(f"SELFTEST FAIL: {what} passed")
            return 1
    if judge([good])[0]:
        print("SELFTEST FAIL: an excluded CronJob pod failed")
        return 1
    if judge([{"kind": "Deployment"}])[1] != 0:
        print("SELFTEST FAIL: a Deployment was read as a CronJob")
        return 1
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        bare = os.path.join(d, "bare.yaml")
        open(bare, "w").write("kind: CronJob\nspec: {}\n")
        ok = os.path.join(d, "ok.yaml")
        open(ok, "w").write(f"kind: CronJob\nspec:\n  labels:\n    {LABEL}: \"true\"\n")
        comment = os.path.join(d, "comment.yaml")
        open(comment, "w").write(f"kind: CronJob\n# the pods carry {LABEL}: \"true\" somewhere\n")
        if not template_findings([bare]) or template_findings([ok]) or not template_findings([comment]):
            print("SELFTEST FAIL: the template check did not tell a bare CronJob template from a labelled one")
            return 1
    print("  ✓ selftest: a CronJob pod with no label, a false label or no template fails; an excluded one passes;"
          " a CronJob template file without the label fails")
    return 0


if __name__ == "__main__":
    sys.exit(selftest() if "--selftest" in sys.argv else audit())
