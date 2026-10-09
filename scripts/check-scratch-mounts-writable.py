#!/usr/bin/env python3
"""check-scratch-mounts-writable.py: each emptyDir volume of a pod has a writable mount.

An emptyDir starts empty. When every mount of it in the pod is read-only,
nothing can write to it, so it holds nothing. That is the sign of a
misplaced `readOnly: true` line. (A volume that one container writes and
another reads read-only is fine: it has a writable mount.) The sidecar openbao-auto-init mounted its /tmp scratch volume read-only
after a merge (charts#492), `mktemp` failed, the login to the store wrote to
/login.json on a read-only root, and the store never got ready (hosted exit
tests, 2026-10-09). This guard renders the umbrella and fails on each such
mount.

  check-scratch-mounts-writable.py             exit 1 on a finding, 0 when clean
  check-scratch-mounts-writable.py --selftest  prove a read-only scratch mount fails
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", "-f", "helm/gibson/values-baseline.yaml",
         "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson",
         "--api-versions", "monitoring.coreos.com/v1"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_specs(doc: dict):
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        yield spec
    elif kind in ("Deployment", "StatefulSet", "DaemonSet", "Job", "ReplicaSet"):
        yield (spec.get("template") or {}).get("spec") or {}
    elif kind == "CronJob":
        yield (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}


def judge(docs: list[dict]) -> list[str]:
    out = []
    pods = 0
    for d in docs:
        for ps in pod_specs(d):
            pods += 1
            scratch = {v["name"] for v in ps.get("volumes") or [] if "emptyDir" in v}
            mounts: dict[str, list[tuple[str, dict]]] = {}
            for c in (ps.get("containers") or []) + (ps.get("initContainers") or []):
                for m in c.get("volumeMounts") or []:
                    if m.get("name") in scratch:
                        mounts.setdefault(m["name"], []).append((c["name"], m))
            for vol, ms in sorted(mounts.items()):
                if all(m.get("readOnly") for _, m in ms):
                    where = ", ".join(f"{cn} at {m['mountPath']}" for cn, m in ms)
                    out.append(f"{d['kind']}/{d['metadata']['name']}: the emptyDir volume {vol} has only read-only mounts ({where})")
    if pods == 0:
        out.append("no pod in the render")
    return out


def selftest() -> int:
    docs = render()
    got = judge(docs)
    if got:
        print("SELFTEST FAIL: the baseline render must pass:\n  " + "\n  ".join(got))
        return 1
    # THE FIXTURE THIS EXISTS FOR: the scratch mount of the openbao sidecar read-only.
    bad = copy.deepcopy(docs)
    hit = 0
    for d in bad:
        for ps in pod_specs(d):
            scratch = {v["name"] for v in ps.get("volumes") or [] if "emptyDir" in v}
            for c in ps.get("containers") or []:
                for m in c.get("volumeMounts") or []:
                    if c["name"] == "openbao-auto-init" and m["name"] in scratch and not hit:
                        m["readOnly"] = True
                        hit = 1
    got = judge(bad)
    if not hit or len(got) != 1 or "openbao-auto-init" not in got[0]:
        print(f"SELFTEST FAIL: a read-only scratch mount of openbao-auto-init must fail once, got {got}")
        return 1
    print("selftest ok: a read-only scratch mount fails")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    bad = judge(render())
    for b in bad:
        print(b)
    if not bad:
        print("scratch mounts writable: ok")
    sys.exit(1 if bad else 0)
