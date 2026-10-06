#!/usr/bin/env python3
"""check-no-pinned-addressing.py: the IdP addressing workarounds stay deleted (charts#163, ADR-0092).

Three rules:

  1. No rendered pod spec, in any profile, holds `hostAliases`. A pod reaches
     Envoy by its Service name, or by api.<domain> through the CoreDNS record
     that the bringup writes.
  2. No values file pins a Service `clusterIP`, a `discoveryURL` or a
     `sourcePrefixRanges` (the deleted in-cluster Envoy chain).
  3. No rendered environment variable holds a public origin (api., app., www.
     or docs. of a domain) unless scripts/.public-origin-env.yaml names it with
     its meaning: a link, a browser redirect, an issuer string, or an
     outside-style client through the edge. A name listed there that no render
     holds fails as stale.

The renders are the committed golden files, which `make golden` keeps equal to
the chart. The check fails as blind when they hold no pod.

  check-no-pinned-addressing.py             exit 1 on a finding
  check-no-pinned-addressing.py --selftest  prove each rule fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
ALLOW = os.path.join("scripts", ".public-origin-env.yaml")
VALUES_GLOBS = ("helm/*/values*.yaml", "helm/testdata/render-inputs/*.yaml")
PINNED_KEYS = ("discoveryURL", "sourcePrefixRanges")
PUBLIC = re.compile(r"https?://(api|app|www|docs)\.[a-z0-9.-]+")
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")


def pod_specs(d: dict):
    k = d.get("kind")
    if k == "Pod":
        yield d.get("spec") or {}
    elif k == "CronJob":
        yield ((((d.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
    elif k in WORKLOADS:
        yield ((d.get("spec") or {}).get("template") or {}).get("spec") or {}


def judge_render(docs: list, allowed: set) -> tuple[list[str], set, int]:
    bad, used, pods = [], set(), 0
    for d in docs:
        if not isinstance(d, dict):
            continue
        for spec in pod_specs(d):
            pods += 1
            where = f"{d['kind']}/{d['metadata']['name']}"
            if spec.get("hostAliases"):
                bad.append(f"{where}: the pod spec holds hostAliases")
            for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
                for e in c.get("env") or []:
                    v = str(e.get("value", ""))
                    if PUBLIC.search(v):
                        if e.get("name") in allowed:
                            used.add(e["name"])
                        else:
                            bad.append(f"{where}: env {e.get('name')}={v} holds a public origin; a dial target must name "
                                       f"a Service, or {ALLOW} must name its meaning")
    return bad, used, pods


def walk_values(node, path, out):
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if k in PINNED_KEYS and v not in (None, "", [], {}):
                out.append(p)
            if k == "clusterIP" and v not in (None, "") and path.endswith("service"):
                out.append(p)
            walk_values(v, p, out)
    elif isinstance(node, list):
        for v in node:
            walk_values(v, path, out)


def judge_values(files: dict) -> list[str]:
    bad = []
    for f, data in sorted(files.items()):
        hits = []
        walk_values(data, "", hits)
        bad += [f"{f}: {h} pins an address the chart no longer reads" for h in hits]
    return bad


def selftest() -> int:
    pod = lambda spec: {"kind": "Deployment", "metadata": {"name": "a"}, "spec": {"template": {"spec": spec}}}
    env = lambda n, v: {"containers": [{"name": "c", "env": [{"name": n, "value": v}]}]}
    if judge_render([pod(env("AUTH_URL", "https://app.example.test"))], {"AUTH_URL"})[0]:
        print("SELFTEST FAIL: a listed public origin must pass")
        return 1
    cases = [
        ("hostAliases", [pod({"hostAliases": [{"ip": "10.96.0.250", "hostnames": ["api.example.test"]}]})]),
        ("an unlisted public origin as a dial target", [pod(env("ZITADEL_URL", "https://app.example.test"))]),
    ]
    for what, docs in cases:
        if len(judge_render(docs, {"AUTH_URL"})[0]) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding")
            return 1
    for what, data in (("a pinned Service clusterIP", {"envoy": {"service": {"clusterIP": "10.96.0.250"}}}),
                       ("a discoveryURL", {"x": {"discoveryURL": "https://gibson-envoy.gibson.svc/.well-known"}}),
                       ("sourcePrefixRanges", {"envoy": {"internalChain": {"sourcePrefixRanges": [{"a": 1}]}}})):
        if len(judge_values({"v.yaml": data})) != 1:
            print(f"SELFTEST FAIL: {what} in a values file must give one finding")
            return 1
    if judge_values({"v.yaml": {"envoy": {"service": {"clusterIP": ""}}, "pg": {"clusterIP": "None-is-not-a-service-key"}}}):
        print("SELFTEST FAIL: an empty clusterIP and a clusterIP outside a service block must pass")
        return 1
    print("  ✓ selftest: hostAliases, an unlisted public origin, a pinned clusterIP, a discoveryURL and "
          "sourcePrefixRanges each fail; a listed origin passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    allowed = set((yaml.safe_load(open(os.path.join(ROOT, ALLOW))) or {}).get("names") or {})
    bad, used, pods = [], set(), 0
    for f in sorted(glob.glob(os.path.join(ROOT, GOLDEN, "*.yaml"))):
        b, u, p = judge_render(list(yaml.safe_load_all(open(f))), allowed)
        bad += [f"{os.path.basename(f)}: {x}" for x in b]
        used |= u
        pods += p
    if not pods:
        bad.append("the golden renders hold no pod: this check is blind")
    bad += [f"{ALLOW}: {n} is stale; no render holds it" for n in sorted(allowed - used)]
    files = {}
    for g in VALUES_GLOBS:
        for f in glob.glob(os.path.join(ROOT, g)):
            files[os.path.relpath(f, ROOT)] = yaml.safe_load(open(f)) or {}
    bad += judge_values(files)
    if bad:
        print("the IdP addressing workarounds came back (charts#163, ADR-0092):", file=sys.stderr)
        for b in sorted(set(bad)):
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ no-pinned-addressing: {pods} rendered pods hold no hostAliases, {len(files)} values files pin "
          f"no address, and {len(used)} public-origin env names each have a stated meaning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
