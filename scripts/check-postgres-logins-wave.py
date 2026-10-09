#!/usr/bin/env python3
"""check-postgres-logins-wave.py: a database login ExternalSecret is not ahead of the platform operator.

WHY THIS EXISTS
The login of each platform database comes from the OpenBao database engine
(ADR-0171, charts#583). The openbao-auto-init sidecar writes the engine only
after the CNPG superuser Secret exists, and the platform operator makes the
Postgres cluster. The umbrella is one Argo Application: Argo applies a wave
only when every earlier wave is Healthy. An ExternalSecret at wave -8 whose
source is a postgres-login generator waits for a cluster that the operator
(wave 0) makes after it. A from-zero install never ends: the kind exit tests
died on it on 2026-10-09 with `unable to get dynamic secret: empty response
from Vault` and no operator Pod.

WHAT IT CHECKS, on every rendered golden (helm/testdata/golden/values-*.yaml)
  Each ExternalSecret that reads a VaultDynamicSecret generator named
  postgres-login-* has a sync-wave at or above the sync-wave of the platform
  operator Deployment.

USAGE
  scripts/check-postgres-logins-wave.py             check the goldens
  scripts/check-postgres-logins-wave.py --selftest  prove an early login fails
Exit: 0 pass, 1 an ExternalSecret is too early, 2 self-test broke
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


def judge(docs: list[dict]) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    ops = [d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-platform-operator")]
    logins = []
    for d in docs:
        if d.get("kind") != "ExternalSecret":
            continue
        for src in (d.get("spec") or {}).get("dataFrom") or []:
            gen = ((src.get("sourceRef") or {}).get("generatorRef") or {}).get("name", "")
            if gen.startswith("postgres-login-"):
                logins.append(d)
    out = []
    if not ops:
        return ["no platform operator Deployment in the render"]
    if not logins:
        return ["no ExternalSecret reads a postgres-login generator in the render"]
    op_wave = min(wave(o) for o in ops)
    for d in logins:
        if wave(d) < op_wave:
            out.append(f"ExternalSecret/{d['metadata']['name']} is at wave {wave(d)}, ahead of the platform operator at wave {op_wave}")
    return out


def goldens() -> list[str]:
    return sorted(glob.glob(os.path.join(ROOT, "helm/testdata/golden/values-baseline.bare.yaml")))


def selftest() -> int:
    path = goldens()[0]
    docs = [d for d in yaml.safe_load_all(open(path)) if d]
    if judge(docs):
        print("SELFTEST FAIL: the golden must pass:\n  " + "\n  ".join(judge(docs)))
        return 2
    bad = copy.deepcopy(docs)
    hit = 0
    for d in bad:
        if isinstance(d, dict) and d.get("kind") == "ExternalSecret" and d["metadata"]["name"].endswith("postgres-credentials") and not hit:
            d["metadata"].setdefault("annotations", {})[WAVE] = "-8"
            hit = 1
    got = judge(bad)
    if not hit or len(got) != 1:
        print(f"SELFTEST FAIL: an ExternalSecret at wave -8 must fail once, got {got}")
        return 2
    print("selftest ok: a login ExternalSecret ahead of the operator fails")
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
