#!/usr/bin/env python3
"""check-secret-plumbing.py — no dangling Secret reference in the render.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1348).
Every secretKeyRef, secretRef and secretName a rendered workload names must
be produced: by a Secret the chart renders, by an ExternalSecret's target,
by a Certificate's secretName, or by a runtime actor recorded with its
producer in helm/gibson/secret-producers.yaml. A reference nothing produces
is a CreateContainerConfigError or an empty mount at runtime, which is how a
mistyped *SecretName in values used to reach a cluster. A listed producer
whose Secret the chart does render is a stale entry and fails too, so the
list stays honest.

A Secret that a script of a rendered workload WRITES (`kubectl create secret`,
`kubectl patch secret`, `kubectl replace secret`, `kubectl apply` of a
`kind: Secret`) is a runtime producer too (ADR-0014). It must have an entry in
secret-producers.yaml, and the entry must name the workload that writes it.
The guard resolves a name held in a shell variable from its assignment in the
same script or from the container env (charts#367).

  check-secret-plumbing.py             exit 1 on a dangling or stale entry, 0 when clean
  check-secret-plumbing.py --selftest  prove a dangling reference and a stale entry fail
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRODUCERS = os.path.join(ROOT, "helm", "gibson", "secret-producers.yaml")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def references(o, owner: str, into: dict) -> None:
    if isinstance(o, dict):
        for k, v in o.items():
            if k in ("secretKeyRef", "secretRef") and isinstance(v, dict) and v.get("name"):
                into.setdefault(v["name"], set()).add(owner)
            if k == "secretName" and isinstance(v, str):
                into.setdefault(v, set()).add(owner)
            references(v, owner, into)
    elif isinstance(o, list):
        for i in o:
            references(i, owner, into)


WRITE = re.compile(
    r"kubectl\s+(?:create\s+secret\s+[a-z-]+|patch\s+secret|replace\s+secret)\s+((?:\"[^\"]+\")|(?:'[^']+')|\S+)")
ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)\s*$", re.M)


def pod_spec(d: dict) -> dict:
    if d["kind"] == "CronJob":
        return d["spec"]["jobTemplate"]["spec"]["template"]["spec"]
    if d["kind"] == "Pod":
        return d.get("spec") or {}
    return d["spec"]["template"]["spec"]


def written_secrets(d: dict) -> tuple[set[str], list[str]]:
    """(Secret names that a container script of d writes, names it could not resolve)."""
    names, unresolved = set(), []
    spec = pod_spec(d)
    for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
        script = "\n".join(str(x) for x in (c.get("command") or []) + (c.get("args") or []))
        env = {e["name"]: e.get("value") for e in c.get("env") or [] if "value" in e}
        local = {m.group(1): m.group(2).strip("\"'") for m in ASSIGN.finditer(script)}
        for m in WRITE.finditer(script):
            raw = m.group(1).strip("\"'")
            var = re.fullmatch(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", raw)
            if var:
                value = local.get(var.group(1)) or env.get(var.group(1))
                if value and "$" not in value:
                    names.add(value)
                else:
                    unresolved.append(f"{c.get('name')}: {raw}")
            elif "$" in raw:
                unresolved.append(f"{c.get('name')}: {raw}")
            else:
                names.add(raw)
    return names, unresolved


def judge_writers(docs: list[dict], producers: dict[str, str]) -> list[str]:
    out = []
    for d in docs:
        if d.get("kind") not in WORKLOADS:
            continue
        owner = f"{d['kind']}/{d['metadata']['name']}"
        names, unresolved = written_secrets(d)
        for raw in unresolved:
            out.append(f"unresolved writer: {owner} writes a Secret named {raw}, and the guard cannot resolve the name")
        for name in sorted(names):
            entry = producers.get(name)
            if entry is None:
                out.append(f"unrecorded writer: {owner} writes Secret {name}, which has no entry in secret-producers.yaml")
            elif d["metadata"]["name"] not in str(entry):
                out.append(f"unnamed writer: {owner} writes Secret {name}, and its secret-producers.yaml entry does not name it")
    return out


def judge(docs: list[dict], producers: dict[str, str]) -> list[str]:
    rendered: dict[str, str] = {}
    for d in docs:
        k, n = d.get("kind"), (d.get("metadata") or {}).get("name")
        if k == "Secret":
            rendered[n] = "Secret"
        elif k == "ExternalSecret":
            rendered[((d.get("spec") or {}).get("target") or {}).get("name") or n] = "ExternalSecret"
        elif k == "Certificate":
            rendered[(d.get("spec") or {}).get("secretName")] = "Certificate"
    refs: dict[str, set] = {}
    for d in docs:
        if d.get("kind") in WORKLOADS:
            references(d, f"{d['kind']}/{d['metadata']['name']}", refs)
    out = []
    for name in sorted(refs):
        if name not in rendered and name not in producers:
            out.append(f"dangling: {name} (referenced by {', '.join(sorted(refs[name]))}) — nothing renders it and no producer is recorded")
    for name in sorted(producers):
        if name in rendered:
            out.append(f"stale producer entry: {name} is rendered by a {rendered[name]}; delete it from secret-producers.yaml")
    return out + judge_writers(docs, producers)


FIXTURE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: gibson}
spec:
  template:
    spec:
      containers:
        - name: c
          env:
            - name: A
              valueFrom: {secretKeyRef: {name: rendered-secret, key: k}}
            - name: B
              valueFrom: {secretKeyRef: {name: minted-at-runtime, key: k}}
            - name: C
              valueFrom: {secretKeyRef: {name: typo-secret, key: k}}
      volumes:
        - name: tls
          secret: {secretName: cert-secret}
---
apiVersion: v1
kind: Secret
metadata: {name: rendered-secret}
---
apiVersion: cert-manager.io/v1
kind: Certificate
metadata: {name: cert}
spec: {secretName: cert-secret}
"""


WRITER_FIXTURE = """
apiVersion: batch/v1
kind: CronJob
metadata: {name: rotator, namespace: gibson}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: c
              command: [sh, -c]
              args:
                - |
                  STATE="rotation-state"
                  kubectl create secret generic "$STATE" -n gibson --from-literal=v=1 --dry-run=client -o yaml \\
                    | kubectl apply -f -
"""


def selftest() -> int:
    docs = [d for d in yaml.safe_load_all(FIXTURE) if d]
    got = judge(docs, {"minted-at-runtime": "an operator mints it", "rendered-secret": "stale"})
    want_dangling = any(x.startswith("dangling: typo-secret") for x in got)
    want_stale = any(x.startswith("stale producer entry: rendered-secret") for x in got)
    if not (want_dangling and want_stale and len(got) == 2):
        print(f"SELFTEST FAIL: want exactly the typo flagged and the stale entry flagged, got {got}")
        return 1
    live = judge(render(), load_producers())
    if live:
        print("SELFTEST FAIL: the baseline render has a plumbing gap:\n  " + "\n  ".join(live))
        return 1
    writer = yaml.safe_load(WRITER_FIXTURE)
    cases = [
        ("a script that writes a Secret with no entry", {}, "unrecorded writer"),
        ("an entry that does not name the writer", {"rotation-state": "some other job writes it"}, "unnamed writer"),
    ]
    for what, prods, want in cases:
        got = judge_writers([writer], prods)
        if not any(x.startswith(want) for x in got):
            print(f"SELFTEST FAIL: {what}: want '{want}', got {got}")
            return 1
    if judge_writers([writer], {"rotation-state": "the CronJob rotator writes it"}):
        print("SELFTEST FAIL: an entry that names the writer must pass")
        return 1
    blind = yaml.safe_load(WRITER_FIXTURE.replace('STATE="rotation-state"', 'STATE="$(pick)"'))
    if not any(x.startswith("unresolved writer") for x in judge_writers([blind], {"rotation-state": "rotator"})):
        print("SELFTEST FAIL: a Secret name the guard cannot resolve must fail")
        return 1
    print("OK: a dangling reference, a stale producer entry, an unrecorded writer, an unnamed writer and an "
          "unresolved name fail; the render is fully plumbed")
    return 0


def load_producers() -> dict[str, str]:
    return (yaml.safe_load(open(PRODUCERS)) or {}).get("producers") or {}


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(render(), load_producers())
    if got:
        print("❌ secret plumbing:\n  " + "\n  ".join(got))
        return 1
    print("✓ secret-plumbing: every Secret a workload references is rendered or has a recorded producer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
