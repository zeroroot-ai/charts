#!/usr/bin/env python3
"""check-reloader-names.py — every Reloader annotation names an object that exists.

A workload asks Reloader for a restart with
`secret.reloader.stakater.com/reload: "<names>"` or
`configmap.reloader.stakater.com/reload: "<names>"`. Reloader matches the
names as text. A name that no object carries is not an error anywhere: the
annotation is accepted, Reloader watches for an object that never comes, and
the workload keeps a stale credential after the real Secret changes.

Three names were wrong on main (charts#411). The umbrella prefixes a subchart
object with the release name, and three annotations spelled the name without
it: the daemon named `gibson-workloads-db-secrets`, and the dashboard named
`gibson-workloads-dashboard-secrets` and `gibson-workloads-redis-stack`. The
last one named a Secret that the dashboard stopped reading.

This check renders the baseline and every shipped values file. For each
workload it reads both annotations, on the object and on its pod template, and
fails on a name that is neither rendered (a Secret, an ExternalSecret target,
a Certificate secretName, a ConfigMap) nor produced at runtime
(`producers` and `configMapProducers` in helm/gibson/secret-producers.yaml).

  check-reloader-names.py             exit 1 on a name that matches nothing
  check-reloader-names.py --selftest  prove a misspelt name fails
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE = "helm/gibson/values-baseline.yaml"
PRODUCERS = os.path.join(ROOT, "helm", "gibson", "secret-producers.yaml")
KEYS = {"secret.reloader.stakater.com/reload": "Secret",
        "configmap.reloader.stakater.com/reload": "ConfigMap"}


def helm_template(extra: list[str]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", BASELINE,
            "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"] + extra
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def rendered(docs: list[dict]) -> dict[str, set[str]]:
    have: dict[str, set[str]] = {"Secret": set(), "ConfigMap": set()}
    for d in docs:
        kind, name = d.get("kind"), (d.get("metadata") or {}).get("name")
        if kind in have:
            have[kind].add(name)
        elif kind == "ExternalSecret":
            have["Secret"].add(((d.get("spec") or {}).get("target") or {}).get("name") or name)
        elif kind == "Certificate":
            have["Secret"].add((d.get("spec") or {}).get("secretName"))
    return have


def judge(docs: list[dict], runtime: dict[str, set[str]]) -> tuple[list[str], int]:
    """Findings, and how many names were read."""
    have = rendered(docs)
    out: list[str] = []
    seen = 0
    for d in docs:
        meta = d.get("metadata") or {}
        pod = (((d.get("spec") or {}).get("template") or {}).get("metadata") or {})
        for ann in (meta.get("annotations") or {}, pod.get("annotations") or {}):
            for key, kind in KEYS.items():
                for name in [n.strip() for n in str(ann.get(key, "")).split(",") if n.strip()]:
                    seen += 1
                    if name not in have[kind] and name not in runtime[kind]:
                        out.append(f"{d.get('kind')}/{meta.get('name')}: the Reloader annotation names "
                                   f"{kind} {name}, and no rendered object and no runtime producer has that name")
    return sorted(set(out)), seen


def runtime_producers() -> dict[str, set[str]]:
    data = yaml.safe_load(open(PRODUCERS)) or {}
    return {"Secret": set(data.get("producers") or {}), "ConfigMap": set(data.get("configMapProducers") or {})}


def selftest() -> int:
    def workload(names: str, key: str = "secret.reloader.stakater.com/reload") -> dict:
        return {"kind": "Deployment", "metadata": {"name": "w"},
                "spec": {"template": {"metadata": {"annotations": {key: names}}}}}
    objs = [{"kind": "Secret", "metadata": {"name": "gibson-gibson-workloads-db-secrets"}},
            {"kind": "ExternalSecret", "metadata": {"name": "es"}, "spec": {"target": {"name": "from-es"}}},
            {"kind": "ConfigMap", "metadata": {"name": "cm"}}]
    runtime = {"Secret": {"minted-at-runtime"}, "ConfigMap": {"written-by-a-job"}}
    good = objs + [workload("gibson-gibson-workloads-db-secrets,from-es,minted-at-runtime"),
                   workload("cm,written-by-a-job", "configmap.reloader.stakater.com/reload")]
    found, seen = judge(good, runtime)
    if found or seen != 5:
        print(f"selftest: rendered and runtime names must pass: {found} {seen}", file=sys.stderr)
        return 1
    cases = [("the name without the release prefix", workload("gibson-workloads-db-secrets")),
             ("a ConfigMap name given to the Secret annotation", workload("cm")),
             ("a ConfigMap that nothing writes", workload("gone", "configmap.reloader.stakater.com/reload"))]
    for what, w in cases:
        found, _ = judge(objs + [w], runtime)
        if len(found) != 1:
            print(f"selftest: {what} must give one finding, gave {found}", file=sys.stderr)
            return 1
    print("check-reloader-names selftest PASSED (a name without the release prefix, a name of "
          "the wrong kind and a name nothing writes each fail)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    runtime = runtime_producers()
    profiles = sorted(os.path.relpath(f, ROOT) for f in glob.glob(os.path.join(ROOT, "helm/gibson/values-*.yaml"))
                      if os.path.relpath(f, ROOT) != BASELINE)
    problems: list[str] = []
    total = 0
    for label, extra in [(BASELINE, [])] + [(p, ["-f", p]) for p in profiles]:
        found, seen = judge(helm_template(extra), runtime)
        total += seen
        if seen == 0:
            problems.append(f"{label}: the render holds no Reloader annotation, so this check read nothing")
        problems += [f"{label}: {f}" for f in found]
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-reloader-names PASSED ({total} names over {len(profiles) + 1} renders: each is rendered or produced at runtime)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
