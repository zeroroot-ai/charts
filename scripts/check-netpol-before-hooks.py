#!/usr/bin/env python3
"""check-netpol-before-hooks.py: a hook Job's NetworkPolicy applies before the Job.

WHY THIS EXISTS
The namespace has a default-deny policy. A bringup Job reaches its peers only
through an allow policy that selects its pod labels. Argo runs these Jobs as
Sync hooks in early waves; a resource with no sync-wave is wave 0. On an
upgrade that changes a Job's labels, the Job then runs under the OLD allow
policy. On staging (2026-09-27) the postgres-setup Jobs waited forever for
Postgres this way. kind enforces no NetworkPolicy, so it could not show it.

WHAT IT CHECKS, on every rendered golden (helm/testdata/golden/*.yaml)
  For every Job with an argocd.argoproj.io/hook annotation, every
  NetworkPolicy in the same namespace whose non-empty podSelector selects the
  Job's pod labels has a sync-wave lower than the Job's sync-wave.

USAGE
  scripts/check-netpol-before-hooks.py             check the goldens
  scripts/check-netpol-before-hooks.py --selftest  prove a late policy fails
Exit: 0 pass, 1 a policy applies too late, 2 self-test broke
"""
from __future__ import annotations

import glob
import os
import sys
import tempfile

import yaml


def wave(obj: dict) -> int:
    ann = (obj.get("metadata") or {}).get("annotations") or {}
    try:
        return int(str(ann.get("argocd.argoproj.io/sync-wave", "0")).strip('"'))
    except ValueError:
        return 0


def selects(selector: dict, labels: dict) -> bool:
    if not selector:
        return False  # the namespace-wide default deny; ordering does not matter for it
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for e in selector.get("matchExpressions") or []:
        k, op, vals = e.get("key"), e.get("operator"), e.get("values") or []
        if op == "In" and labels.get(k) not in vals:
            return False
        if op == "NotIn" and labels.get(k) in vals:
            return False
        if op == "Exists" and k not in labels:
            return False
        if op == "DoesNotExist" and k in labels:
            return False
    return True


def check(paths: list[str]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            docs = [d for d in yaml.safe_load_all(f) if isinstance(d, dict)]
        pols = [d for d in docs if d.get("kind") == "NetworkPolicy"]
        for job in (d for d in docs if d.get("kind") == "Job"):
            ann = (job.get("metadata") or {}).get("annotations") or {}
            if "argocd.argoproj.io/hook" not in ann:
                continue
            ns = (job.get("metadata") or {}).get("namespace")
            labels = ((((job.get("spec") or {}).get("template") or {}).get("metadata") or {}).get("labels")) or {}
            jw = wave(job)
            for p in pols:
                if (p.get("metadata") or {}).get("namespace") != ns:
                    continue
                if selects((p.get("spec") or {}).get("podSelector") or {}, labels) and wave(p) >= jw:
                    problems.append(
                        f"{os.path.basename(path)}: NetworkPolicy/{p['metadata']['name']} (wave {wave(p)}) "
                        f"selects hook Job/{job['metadata']['name']} (wave {jw}); the policy must apply first")
    return sorted(set(problems))


def _doc(kind, name, wave_, extra):
    d = {"kind": kind, "metadata": {"name": name, "namespace": "gibson", "annotations": {}}}
    if wave_ is not None:
        d["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] = str(wave_)
    d.update(extra)
    return d


def selftest() -> int:
    job = _doc("Job", "zitadel-postgres-setup", -6, {"spec": {"template": {"metadata": {"labels": {"app.kubernetes.io/component": "postgres-setup"}}}}})
    job["metadata"]["annotations"]["argocd.argoproj.io/hook"] = "Sync"
    def pol(w, comp="postgres-setup"):
        return _doc("NetworkPolicy", "gibson-bringup-jobs", w, {"spec": {"podSelector": {"matchExpressions": [
            {"key": "app.kubernetes.io/component", "operator": "In", "values": [comp]}]}}})
    deny = _doc("NetworkPolicy", "default-deny", None, {"spec": {"podSelector": {}}})
    cases = {"early_policy": ([job, pol(-20), deny], True), "policy_without_wave": ([job, pol(None)], False),
             "policy_same_wave": ([job, pol(-6)], False), "unrelated_policy": ([job, pol(None, "other")], True)}
    for name, (docs, want_ok) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump_all(docs, f)
            if (not check([p])) != want_ok:
                print(f"GUARD BROKEN: fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2
    print("✅ self-test: a hook Job's allow policy that applies at or after the Job is rejected")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    paths = sorted(glob.glob("helm/testdata/golden/*.yaml"))
    problems = check(paths)
    if problems:
        print("❌ a hook Job would run before the NetworkPolicy that lets it reach its peers")
        for p in problems:
            print("   " + p)
        return 1
    print(f"✅ every hook Job's NetworkPolicy applies in an earlier wave ({len(paths)} renders)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
