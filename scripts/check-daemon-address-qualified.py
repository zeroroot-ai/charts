#!/usr/bin/env python3
"""check-daemon-address-qualified.py: each operator hands out a daemon address that resolves in any namespace (charts#526).

The tenant operator passes GIBSON_DAEMON_GRPC_ADDRESS to the belief trainer
of each tenant namespace. A short Service name resolves only in the namespace
of the Service, so the trainer could not find the daemon.

The check reads each golden render. For each container env
GIBSON_DAEMON_GRPC_ADDRESS it fails when the value is not
"<service>.<namespace>.svc:<port>", or when that Service does not exist in the
render, or when the Service has no such port.

  check-daemon-address-qualified.py             exit 1 on a finding
  check-daemon-address-qualified.py --selftest  prove each finding fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden", "values-*.yaml")
ENV = "GIBSON_DAEMON_GRPC_ADDRESS"
SHAPE = re.compile(r"^([a-z0-9-]+)\.([a-z0-9-]+)\.svc:([0-9]+)$")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")


def pod_spec(d: dict) -> dict:
    spec = d.get("spec") or {}
    if d.get("kind") == "CronJob":
        spec = ((spec.get("jobTemplate") or {}).get("spec")) or {}
    return ((spec.get("template") or {}).get("spec")) or {}


def judge(docs: list[dict], namespace: str = "gibson") -> list[str]:
    services = {}
    for d in docs:
        if d.get("kind") == "Service":
            md = d["metadata"]
            ports = {int(p["port"]) for p in (d.get("spec") or {}).get("ports") or []}
            services[(md["name"], md.get("namespace") or namespace)] = ports
    bad, seen = [], 0
    for d in docs:
        if d.get("kind") not in WORKLOADS:
            continue
        spec = pod_spec(d)
        for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
            for e in c.get("env") or []:
                if e.get("name") != ENV:
                    continue
                seen += 1
                where = f"{d['kind']}/{d['metadata']['name']} {c['name']}"
                m = SHAPE.match(str(e.get("value") or ""))
                if not m:
                    bad.append(f"{where}: {ENV}={e.get('value')!r} is not <service>.<namespace>.svc:<port>. "
                               "A pod in a tenant namespace cannot resolve a short name")
                    continue
                svc, ns, port = m.group(1), m.group(2), int(m.group(3))
                if (svc, ns) not in services:
                    bad.append(f"{where}: {ENV} names the Service {svc} in {ns}, which the render does not hold")
                elif port not in services[(svc, ns)]:
                    bad.append(f"{where}: {ENV} names port {port}, the Service {svc} has {sorted(services[(svc, ns)])}")
    if not seen:
        bad.append(f"no container sets {ENV}: this check is blind")
    return bad


def load(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(open(path)) if isinstance(d, dict) and d.get("metadata")]


def selftest() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN)))
    base = load(files[0])
    if judge(base):
        print(f"selftest: the golden {files[0]} is not clean: {judge(base)}")
        return 1

    def with_value(v: str) -> list[dict]:
        docs = yaml.safe_load(yaml.safe_dump(base))
        for d in docs:
            if d.get("kind") in WORKLOADS:
                for c in pod_spec(d).get("containers") or []:
                    for e in c.get("env") or []:
                        if e.get("name") == ENV:
                            e["value"] = v
        return docs

    cases = {
        "short name": with_value("gibson-gibson-workloads:50051"),
        "no such Service": with_value("gibson-daemon.gibson.svc:50051"),
        "no such port": with_value("gibson-gibson-workloads.gibson.svc:50999"),
        "no setter": [d for d in base if d.get("kind") not in WORKLOADS],
    }
    bad = [name for name, docs in cases.items() if not judge(docs)]
    if bad:
        print(f"selftest: these fixtures passed and must fail: {bad}")
        return 1
    print(f"selftest: {len(cases)} fixtures fail as they must")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN)))
    fail = []
    for f in files:
        fail += [f"{os.path.relpath(f, ROOT)}: {m}" for m in judge(load(f))]
    for m in fail:
        print(f"::error::{m}")
    if fail or not files:
        return 1
    print(f"OK: each {ENV} names a Service with its namespace and port in {len(files)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
