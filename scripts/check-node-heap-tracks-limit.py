#!/usr/bin/env python3
"""check-node-heap-tracks-limit.py: every Node server bounds its heap inside its memory limit.

Node sizes its old-space heap from the HOST's memory and never learns the cgroup
it runs in. On a 32GiB node a 1GiB container gets a heap ceiling several times
its own limit, so a heavy render grows past the cgroup and the process dies with
no V8 heap error to read. That is dashboard#150: every signed-in page returned
503, the pod restarted, and nothing in the pod named memory as the cause.
Measured the same day, the zitadel-login container had `resources: {}` — the
worse form of the same defect, with no bound at all.

A comment cannot keep the heap ceiling and the limit in step. This guard can.

The rule, for every container running a first-party Node server:

1. It declares a memory limit. Without one there is nothing for the heap to fit
   inside, and one render can take the node.
2. It sets NODE_OPTIONS with --max-old-space-size.
3. That heap sits between 50% and 90% of the limit. Below 50% wastes most of the
   bound; above 90% leaves no room for the Node binary, buffers and native
   allocations, so the kernel kills the container before V8 reports anything.

The image repositories are listed below rather than sniffed, because a Node
server is not detectable from a rendered manifest. Add a new one when the chart
grows one; a repository that is in the chart and not in this list is simply
unguarded, which is the failure this list makes visible.

  check-node-heap-tracks-limit.py             exit 1 on a finding, 0 when clean
  check-node-heap-tracks-limit.py --selftest  prove each kind of finding fails
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAMESPACE = "gibson"

# Image repositories whose containers run a Node server.
NODE_IMAGES = (
    "ghcr.io/zeroroot-ai/dashboard",
    "ghcr.io/zeroroot-ai/zitadel-login",
)

MIN_FRACTION = 0.50
MAX_FRACTION = 0.90

HEAP_FLAG = re.compile(r"--max-old-space-size[= ](\d+)")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")


# ------------------------------------------------------------------ render

def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_spec(doc: dict) -> dict:
    """The PodSpec of a workload, whatever shape wraps it."""
    spec = doc.get("spec") or {}
    if "jobTemplate" in spec:
        spec = (spec["jobTemplate"].get("spec") or {})
    return ((spec.get("template") or {}).get("spec")) or {}


def node_containers(docs: list[dict]) -> list[tuple[str, dict]]:
    """(where, container) for every container running one of NODE_IMAGES."""
    out = []
    for d in docs:
        if d.get("kind") not in WORKLOADS:
            continue
        name = (d.get("metadata") or {}).get("name", "?")
        for c in pod_spec(d).get("containers") or []:
            image = c.get("image") or ""
            if any(image.startswith(repo + ":") or image == repo for repo in NODE_IMAGES):
                out.append((f"{d['kind']}/{name} container {c.get('name')}", c))
    return out


# ------------------------------------------------------------------ parsing

SUFFIXES = {"Gi": 1024.0, "Mi": 1.0, "Ki": 1 / 1024, "G": 1000 ** 3 / 1048576,
            "M": 1000 ** 2 / 1048576, "K": 1000 / 1048576}


def mib(quantity: str) -> float | None:
    """A Kubernetes memory quantity in MiB, or None when it carries no unit we know."""
    q = str(quantity).strip()
    for suffix, factor in SUFFIXES.items():
        if q.endswith(suffix):
            try:
                return float(q[: -len(suffix)]) * factor
            except ValueError:
                return None
    try:                       # a bare quantity is bytes
        return float(q) / 1048576
    except ValueError:
        return None


def heap_mib(container: dict) -> int | None:
    for e in container.get("env") or []:
        if e.get("name") != "NODE_OPTIONS":
            continue
        m = HEAP_FLAG.search(str(e.get("value") or ""))
        if m:
            return int(m.group(1))
    return None


# -------------------------------------------------------------------- audit

def audit(docs: list[dict]) -> list[str]:
    found = node_containers(docs)
    if not found:
        return [f"no container in the render runs any of {', '.join(NODE_IMAGES)}: this guard is blind"]
    bad = []
    for where, c in found:
        limit = ((c.get("resources") or {}).get("limits") or {}).get("memory")
        if not limit:
            bad.append(f"{where}: runs Node with no memory limit; one render can take the node, "
                       f"and there is no bound for the heap to fit inside")
            continue
        limit_mib = mib(limit)
        if limit_mib is None:
            bad.append(f"{where}: memory limit {limit!r} carries no unit this guard reads")
            continue
        heap = heap_mib(c)
        if heap is None:
            bad.append(f"{where}: no --max-old-space-size in NODE_OPTIONS; Node will size its heap from "
                       f"the node's memory, not from this container's {limit} limit")
            continue
        low, high = MIN_FRACTION * limit_mib, MAX_FRACTION * limit_mib
        if heap > high:
            bad.append(f"{where}: heap ceiling {heap}MiB is over {MAX_FRACTION:.0%} of the {limit} limit "
                       f"({high:.0f}MiB); the kernel kills the container before V8 reports anything")
        elif heap < low:
            bad.append(f"{where}: heap ceiling {heap}MiB is under {MIN_FRACTION:.0%} of the {limit} limit "
                       f"({low:.0f}MiB); most of the bound is unreachable")
    return bad


# ---------------------------------------------------------------- self-test

def _edit(docs: list[dict], fn) -> list[dict]:
    """A copy of the render with fn applied to every Node container."""
    out = copy.deepcopy(docs)
    for _, c in node_containers(out):
        fn(c)
    return out


def _drop_limit(c: dict) -> None:
    ((c.get("resources") or {}).get("limits") or {}).pop("memory", None)


def _drop_node_options(c: dict) -> None:
    c["env"] = [e for e in (c.get("env") or []) if e.get("name") != "NODE_OPTIONS"]


def _set_heap(value: str):
    def fn(c: dict) -> None:
        _drop_node_options(c)
        c.setdefault("env", []).insert(0, {"name": "NODE_OPTIONS", "value": value})
    return fn


def _set_limit(value: str):
    def fn(c: dict) -> None:
        c.setdefault("resources", {}).setdefault("limits", {})["memory"] = value
    return fn


def selftest(docs: list[dict]) -> int:
    rc = 0
    clean = audit(docs)
    if clean:
        print("SELFTEST FAIL: the render itself has findings:\n  " + "\n  ".join(clean), file=sys.stderr)
        rc = 1

    fails = [
        ("no memory limit", _drop_limit, "no memory limit"),
        ("no NODE_OPTIONS", _drop_node_options, "no --max-old-space-size"),
        ("NODE_OPTIONS without the flag", _set_heap("--enable-source-maps"), "no --max-old-space-size"),
        ("heap above the limit", _set_heap("--max-old-space-size=4096"), "over 90%"),
        ("heap at the limit", _set_heap("--max-old-space-size=1024"), "over 90%"),
        ("heap far under the limit", _set_heap("--max-old-space-size=128"), "under 50%"),
        ("limit raised, heap left behind", _set_limit("8Gi"), "under 50%"),
        ("limit lowered, heap left behind", _set_limit("512Mi"), "over 90%"),
    ]
    passes = [
        ("the flag written with a space", _set_heap("--max-old-space-size 768")),
        ("another option alongside it", _set_heap("--enable-source-maps --max-old-space-size=768")),
        ("limit and heap raised together",
         lambda c: (_set_limit("2Gi")(c), _set_heap("--max-old-space-size=1536")(c)) and None),
    ]
    for name, fn, needle in fails:
        findings = audit(_edit(docs, fn))
        if any(needle in f for f in findings):
            print(f"  ok   {name} fails")
        else:
            print(f"  FAIL {name}: expected a finding containing {needle!r}, got {findings}", file=sys.stderr)
            rc = 1
    for name, fn in passes:
        findings = audit(_edit(docs, fn))
        if not findings:
            print(f"  ok   {name} passes")
        else:
            print(f"  FAIL {name}: expected no finding, got {findings}", file=sys.stderr)
            rc = 1
    print("SELFTEST " + ("PASS" if rc == 0 else "FAIL"))
    return rc


def main() -> int:
    docs = render()
    if "--selftest" in sys.argv:
        return selftest(docs)
    bad = audit(docs)
    if bad:
        print("node-heap-tracks-limit: FAIL\n  " + "\n  ".join(bad))
        return 1
    print(f"node-heap-tracks-limit: OK ({len(node_containers(docs))} Node containers bound inside their limit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
