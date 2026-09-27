#!/usr/bin/env python3
"""check-reloader-namespaced.py — Reloader reads Secrets in its own namespace only,
and may roll exactly the workloads that ask it to.

Reloader scopes its Secret and ConfigMap informers to KUBERNETES_NAMESPACE;
unset, it watches every namespace and was handed a ClusterRole over every
Secret in the cluster to do so. This guard keeps the two halves together:
the Deployment sets the variable, and no cluster-scoped grant on secrets
exists for the reloader ServiceAccount.

It also renders the umbrella for each profile and compares two lists: the
Deployments and StatefulSets in the release namespace that carry a
`*.reloader.stakater.com/reload` annotation (on the object or on its pod
template), and the names the Reloader Role may patch. A patch of a pod
template can change the image and the ServiceAccount, so Reloader may patch
the annotated workloads and nothing else. An annotated workload the Role
does not name would never restart, and a named workload with no annotation
is a write grant with no use.

  check-reloader-namespaced.py             exit 1 on a violation, 0 when clean
  check-reloader-namespaced.py --selftest  prove the cluster-scoped fixture fails and the tree passes
"""
import os
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET = os.path.join(ROOT, "helm", "gibson-operators", "templates", "reloader", "reloader.yaml")
GOTMPL = re.compile(r"\{\{.*?\}\}", re.S)


def violations(path: str) -> list[str]:
    text = GOTMPL.sub("", open(path, encoding="utf-8").read())
    out = []
    docs = [d for d in yaml.safe_load_all(text) if d]
    for d in docs:
        kind = d.get("kind")
        name = (d.get("metadata") or {}).get("name")
        if kind == "ClusterRole":
            for r in d.get("rules") or []:
                if "secrets" in (r.get("resources") or []):
                    out.append(f"ClusterRole/{name} grants secrets cluster-wide")
        if kind == "ClusterRoleBinding":
            out.append(f"ClusterRoleBinding/{name}: the reloader needs no cluster-scoped binding")
        if kind == "Deployment":
            envs = []
            for c in ((d.get("spec") or {}).get("template", {}).get("spec", {}).get("containers") or []):
                envs += [e.get("name") for e in (c.get("env") or [])]
            if "KUBERNETES_NAMESPACE" not in envs:
                out.append(f"Deployment/{name} does not set KUBERNETES_NAMESPACE, so Reloader watches every namespace")
    return out


BAD = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: reloader}
rules:
- apiGroups: ['']
  resources: [secrets, configmaps]
  verbs: [list, get, watch]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: reloader}
spec:
  template:
    spec:
      containers:
      - name: reloader
        args: ["--reload-strategy=annotations"]
"""


NS = "gibson"
PROFILES = [[], ["values-eks.yaml"], ["values-guest.yaml"]]
KINDS = {"Deployment": "deployments", "StatefulSet": "statefulsets"}


def render(extra: list[str]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", NS,
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"]
    for f in extra:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def annotated(d: dict) -> bool:
    meta = d.get("metadata") or {}
    tmpl = (((d.get("spec") or {}).get("template") or {}).get("metadata") or {})
    keys = list((meta.get("annotations") or {})) + list((tmpl.get("annotations") or {}))
    return any(k.endswith("reloader.stakater.com/reload") or k.endswith("reloader.stakater.com/auto") for k in keys)


def rollout_mismatch(docs: list[dict]) -> list[str]:
    """Annotated workloads versus the names the reloader Role may patch."""
    want = {(d["kind"], d["metadata"]["name"]) for d in docs
            if d.get("kind") in KINDS and (d["metadata"].get("namespace") or NS) == NS and annotated(d)}
    have = set()
    for d in docs:
        if d.get("kind") != "Role" or d["metadata"]["name"] != "reloader":
            continue
        for r in d.get("rules") or []:
            if "patch" in (r.get("verbs") or []) or "update" in (r.get("verbs") or []) or "*" in (r.get("verbs") or []):
                names = r.get("resourceNames") or []
                for kind, res in KINDS.items():
                    if res in (r.get("resources") or []):
                        if not names:
                            return [f"the reloader Role may patch every {res}; name them (reloader.rollouts)"]
                        have |= {(kind, n) for n in names}
    out = [f"{k}/{n} carries a reload annotation, and the reloader Role may not patch it "
           "(add it to reloader.rollouts)" for k, n in sorted(want - have)]
    out += [f"the reloader Role may patch {k}/{n}, which carries no reload annotation "
            "(remove it from reloader.rollouts)" for k, n in sorted(have - want)]
    return out


ROLLOUT_FIXTURE_BASE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: web, namespace: gibson, annotations: {secret.reloader.stakater.com/reload: web-tls}}
spec: {template: {spec: {containers: [{name: c}]}}}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: reloader, namespace: gibson}
rules:
- {apiGroups: [apps], resources: [deployments, statefulsets], verbs: [list, get, watch]}
- {apiGroups: [apps], resources: [deployments], resourceNames: [web], verbs: [patch]}
"""

ROLLOUT_FIXTURES_THAT_MUST_FAIL = {
    "an annotated StatefulSet the Role does not name": """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: db, namespace: gibson}
spec: {template: {metadata: {annotations: {secret.reloader.stakater.com/reload: db-pw}}, spec: {containers: [{name: c}]}}}
""",
    "a Role that patches every Deployment": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: reloader, namespace: gibson}
rules:
- {apiGroups: [apps], resources: [deployments], verbs: [patch]}
""",
    "a Role that names a workload with no annotation": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: reloader, namespace: gibson}
rules:
- {apiGroups: [apps], resources: [deployments], resourceNames: [web, operator], verbs: [patch]}
""",
}


def selftest_rollouts() -> int:
    base = [d for d in yaml.safe_load_all(ROLLOUT_FIXTURE_BASE) if d]
    if rollout_mismatch(base):
        print(f"SELFTEST FAIL: the matched rollout fixture must pass, got {rollout_mismatch(base)}")
        return 1
    for what, text in ROLLOUT_FIXTURES_THAT_MUST_FAIL.items():
        extra = [d for d in yaml.safe_load_all(text) if d]
        docs = [d for d in base if not (d["kind"] == "Role" and any(e["kind"] == "Role" for e in extra))] + extra
        if not rollout_mismatch(docs):
            print(f"SELFTEST FAIL: {what}: the rollout check passed it")
            return 1
    for prof in PROFILES:
        got = rollout_mismatch(render(prof))
        if got:
            print(f"SELFTEST FAIL: the render ({prof or 'baseline'}) disagrees:\n  " + "\n  ".join(got))
            return 1
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.yaml")
        open(p, "w").write(BAD)
        v = violations(p)
        if not (any("cluster-wide" in x for x in v) and any("KUBERNETES_NAMESPACE" in x for x in v)):
            print(f"SELFTEST FAIL: the cluster-scoped fixture must fail on both counts, got {v}")
            return 1
    live = violations(TARGET)
    if live:
        print("SELFTEST FAIL: the shipped template violates the rule:\n  " + "\n  ".join(live))
        return 1
    if selftest_rollouts():
        return 1
    print(f"OK: a cluster-scoped reloader fails, and the shipped one is namespaced; "
          f"{len(ROLLOUT_FIXTURES_THAT_MUST_FAIL)} rollout fixtures fail and the render matches")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    v = violations(TARGET)
    for prof in PROFILES:
        v += [f"({prof[0] if prof else 'baseline'}) {x}" for x in rollout_mismatch(render(prof))]
    if v:
        print("❌ reloader is not namespaced:\n  " + "\n  ".join(v))
        return 1
    print("✓ reloader: KUBERNETES_NAMESPACE set, secrets read through a Role in its namespace")
    return 0


if __name__ == "__main__":
    sys.exit(main())
