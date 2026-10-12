#!/usr/bin/env python3
"""check-velero-home.py: the Velero server writes its repository config to a writable path (charts#659).

The Velero server runs with a read-only root (ADR-0165). Velero writes the
kopia repository config to $HOME/udmrepo and its cache to $HOME/.cache. With
no HOME the path is /udmrepo, on the read-only root, and every volume backup
fails ("mkdir /udmrepo: read-only file system"), so each backup ends
PartiallyFailed.

The check reads the committed golden render of the release
(helm/testdata/golden/gibson-velero.yaml, kept equal to the chart by `make
golden`) and fails on each Velero container with a read-only root whose HOME
is unset or is not under a mounted emptyDir. It fails as blind when the render
holds no Velero container with a read-only root.

  check-velero-home.py             exit 1 on a finding
  check-velero-home.py --selftest  prove each finding fails
"""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(ROOT, "helm", "testdata", "golden", "gibson-velero.yaml")


def workloads(docs):
    for d in docs:
        if isinstance(d, dict) and d.get("kind") in ("Deployment", "DaemonSet"):
            yield d


def judge(docs) -> tuple[list[str], int]:
    bad, seen = [], 0
    for d in workloads(docs):
        spec = d["spec"]["template"]["spec"]
        empty = {v["name"] for v in spec.get("volumes") or [] if "emptyDir" in v}
        for c in spec.get("containers") or []:
            if not (c.get("securityContext") or {}).get("readOnlyRootFilesystem"):
                continue
            if "velero" not in (c.get("image") or ""):
                continue
            seen += 1
            name = f'{d["kind"]}/{d["metadata"]["name"]}/{c["name"]}'
            home = next((e.get("value") for e in c.get("env") or [] if e.get("name") == "HOME"), None)
            if not home:
                bad.append(f"{name}: read-only root and no HOME, so Velero writes /udmrepo on the root")
                continue
            mounts = [m["mountPath"].rstrip("/") for m in c.get("volumeMounts") or [] if m.get("name") in empty]
            if not any(home.rstrip("/") == m or home.startswith(m + "/") for m in mounts):
                bad.append(f"{name}: HOME={home} is not under a mounted emptyDir ({mounts or 'none'})")
    return bad, seen


def selftest() -> int:
    def dep(env, mounts=(("scratch", "/scratch"),), volumes=("scratch",)):
        return [{"kind": "Deployment", "metadata": {"name": "velero"}, "spec": {"template": {"spec": {
            "volumes": [{"name": v, "emptyDir": {}} for v in volumes],
            "containers": [{"name": "velero", "image": "ghcr.io/zeroroot-ai/mirror/velero:v1",
                            "securityContext": {"readOnlyRootFilesystem": True},
                            "env": env,
                            "volumeMounts": [{"name": n, "mountPath": p} for n, p in mounts]}]}}}}]
    home = [{"name": "HOME", "value": "/scratch"}]
    if judge(dep(home))[0]:
        print("SELFTEST FAIL: HOME on the scratch emptyDir must pass")
        return 1
    for what, docs in (("no HOME", dep([])),
                       ("HOME on the root", dep([{"name": "HOME", "value": "/"}])),
                       ("HOME on an unmounted path", dep([{"name": "HOME", "value": "/home/velero"}])),
                       ("HOME on a mount that is not an emptyDir", dep(home, volumes=()))):
        if len(judge(docs)[0]) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)[0]}")
            return 1
    if judge([])[1] != 0:
        print("SELFTEST FAIL: an empty render must see no container")
        return 1
    print("  ✓ selftest: no HOME, HOME on the root, HOME on an unmounted path and HOME on a non-emptyDir mount each fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    docs = list(yaml.safe_load_all(open(GOLDEN)))
    bad, seen = judge(docs)
    if not seen:
        bad.append("the golden render holds no Velero container with a read-only root: this check is blind")
    if bad:
        print("the Velero server cannot write its repository config (charts#659):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ velero-home: {seen} Velero container(s) with a read-only root keep HOME on an emptyDir")
    return 0


if __name__ == "__main__":
    sys.exit(main())
