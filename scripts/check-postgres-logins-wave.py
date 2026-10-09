#!/usr/bin/env python3
"""check-postgres-logins-wave.py: a database login ExternalSecret sits between the setup Jobs and its consumers.

WHY THIS EXISTS
The login of each platform database comes from the OpenBao database engine
(ADR-0171, charts#583). A login is a member of its owner role, and the
postgres-setup Jobs make the owner roles in the Postgres cluster. The
openbao-auto-init sidecar writes the engine only after the CNPG superuser
Secret exists, which is after the cluster. The umbrella is one Argo
Application: Argo applies a wave only when every earlier wave is Healthy.
  - At wave -8 the ExternalSecrets waited for a cluster that wave -7 makes.
    A from-zero install never ended (the kind exit tests, 2026-10-09).
  - At wave 0 the Jobs at wave -4 that mount the login (zitadel-init) waited
    for a Secret that wave 0 makes, and ran out of their deadline.
So each login ExternalSecret is after the Postgres cluster and the setup
Jobs, and not after any hook Job that mounts the Secret it writes.

WHAT IT CHECKS, on a rendered golden (helm/testdata/golden/values-baseline.bare.yaml)
  For each ExternalSecret that reads a postgres-login-* generator:
    1. its sync-wave is above the wave of the Postgres Cluster and of each
       Job named *-postgres-setup, and
    2. its sync-wave is at or below the wave of each hook Job whose pod
       reads the Secret it writes.

USAGE
  scripts/check-postgres-logins-wave.py             check the golden
  scripts/check-postgres-logins-wave.py --selftest  prove an early and a late login fail
Exit: 0 pass, 1 an ExternalSecret is out of order, 2 self-test broke
"""
from __future__ import annotations

import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAVE = "argocd.argoproj.io/sync-wave"


def wave(obj: dict) -> int:
    ann = (obj.get("metadata") or {}).get("annotations") or {}
    try:
        return int(str(ann.get(WAVE, "0")).strip('"'))
    except ValueError:
        return 0


def secret_names(job: dict) -> set[str]:
    names = set()
    spec = ((job.get("spec") or {}).get("template") or {}).get("spec") or {}
    for v in spec.get("volumes") or []:
        n = (v.get("secret") or {}).get("secretName")
        if n:
            names.add(n)
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for e in c.get("env") or []:
            n = (((e.get("valueFrom") or {}).get("secretKeyRef")) or {}).get("name")
            if n:
                names.add(n)
        for ef in c.get("envFrom") or []:
            n = (ef.get("secretRef") or {}).get("name")
            if n:
                names.add(n)
    return names


def judge(docs: list[dict]) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    logins = []
    for d in docs:
        if d.get("kind") != "ExternalSecret":
            continue
        for src in (d.get("spec") or {}).get("dataFrom") or []:
            gen = ((src.get("sourceRef") or {}).get("generatorRef") or {}).get("name", "")
            if gen.startswith("postgres-login-"):
                logins.append(d)
    if not logins:
        return ["no ExternalSecret reads a postgres-login generator in the render"]
    before = [d for d in docs if (d.get("kind") == "Cluster" and str(d.get("apiVersion", "")).startswith("postgresql.cnpg.io"))
              or (d.get("kind") == "Job" and d["metadata"]["name"].endswith("-postgres-setup"))]
    if not before:
        return ["no Postgres Cluster and no postgres-setup Job in the render"]
    floor = max(wave(d) for d in before)
    jobs = [d for d in docs if d.get("kind") == "Job" and "argocd.argoproj.io/hook" in ((d.get("metadata") or {}).get("annotations") or {})]
    out = []
    for es in logins:
        w = wave(es)
        name = es["metadata"]["name"]
        target = ((es.get("spec") or {}).get("target") or {}).get("name", name)
        if w <= floor:
            out.append(f"ExternalSecret/{name} is at wave {w}, not after the Postgres cluster and the setup Jobs (wave {floor}): "
                       "the engine or the owner role does not exist yet")
        for j in jobs:
            if target in secret_names(j) and wave(j) < w:
                out.append(f"ExternalSecret/{name} is at wave {w}, after Job/{j['metadata']['name']} (wave {wave(j)}) that mounts {target}: "
                           "the Job waits for a Secret that a later wave makes")
    return out


def goldens() -> list[str]:
    return sorted(glob.glob(os.path.join(ROOT, "helm/testdata/golden/values-baseline.bare.yaml")))


def selftest() -> int:
    path = goldens()[0]
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    got = judge(docs)
    if got:
        print("SELFTEST FAIL: the golden must pass:\n  " + "\n  ".join(got))
        return 2
    for label, wv, want in (("too early", "-8", "not after the Postgres cluster"), ("too late", "0", "waits for a Secret")):
        bad = copy.deepcopy(docs)
        hit = 0
        for d in bad:
            if (isinstance(d, dict) and d.get("kind") == "ExternalSecret" and d["metadata"]["name"] == "zitadel-postgres-credentials"):
                d["metadata"].setdefault("annotations", {})[WAVE] = wv
                hit = 1
        got = judge(bad)
        if not hit or not got or any(want not in g for g in got):
            print(f"SELFTEST FAIL: a login ExternalSecret {label} must fail with '{want}', got {got}")
            return 2
    print("selftest ok: a login ExternalSecret too early and one too late fail")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    bad = []
    for p in goldens():
        for b in judge([d for d in yaml.safe_load_all(open(p)) if d]):
            bad.append(f"{os.path.basename(p)}: {b}")
    for b in bad:
        print(b)
    if not bad:
        print("postgres logins wave: ok")
    sys.exit(1 if bad else 0)
