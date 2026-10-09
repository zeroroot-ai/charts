#!/usr/bin/env python3
"""check-externalsecret-store-namespace.py: each ExternalSecret sits where its store serves it (charts#504).

External Secrets refuses an ExternalSecret in a namespace that its store does
not serve. The refusal shows only as a status condition on the live object,
so the target Secret is never written and nothing in the render says so.

The check reads each golden render and, for each ExternalSecret, finds the
store that `spec.secretStoreRef` names:

  - a SecretStore must be in the same namespace as the ExternalSecret,
  - a ClusterSecretStore must be in the render, and one entry of its
    `spec.conditions` must admit the namespace of the ExternalSecret, by
    `namespaces`, by `namespaceRegexes` (unanchored, as Go regexp.MatchString
    matches), or by `namespaceSelector.matchLabels`
    against a Namespace object of the same render.

An ExternalSecret whose every source is a generator (dataFrom
sourceRef.generatorRef, and no data) reads no store, so it needs none.

A ClusterSecretStore with no conditions serves every namespace. The platform
store holds the Zitadel owner credentials, so that fails too.

  check-externalsecret-store-namespace.py             exit 1 on a finding
  check-externalsecret-store-namespace.py --selftest  prove each finding fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
RELEASE_NS = "gibson"  # the --namespace of each golden render (scripts/golden.sh)


def ns_of(doc: dict) -> str:
    return (doc.get("metadata") or {}).get("namespace") or RELEASE_NS


def admits(cond: dict, ns: str, ns_labels: dict) -> bool:
    if ns in (cond.get("namespaces") or []):
        return True
    if any(re.search(r, ns) for r in (cond.get("namespaceRegexes") or [])):
        return True
    sel = (cond.get("namespaceSelector") or {}).get("matchLabels")
    if sel and ns in ns_labels and all(ns_labels[ns].get(k) == v for k, v in sel.items()):
        return True
    return False


def judge(docs: list) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    stores = {d["metadata"]["name"]: d for d in docs if d.get("kind") == "ClusterSecretStore"}
    local = {(ns_of(d), d["metadata"]["name"]) for d in docs if d.get("kind") == "SecretStore"}
    ns_labels = {d["metadata"]["name"]: (d["metadata"].get("labels") or {})
                 for d in docs if d.get("kind") == "Namespace"}
    bad = []
    for name, s in sorted(stores.items()):
        if not (s.get("spec") or {}).get("conditions"):
            bad.append(f"ClusterSecretStore {name} has no conditions, so it serves every namespace")
    for d in docs:
        if d.get("kind") != "ExternalSecret":
            continue
        ns, es = ns_of(d), f"ExternalSecret {ns_of(d)}/{d['metadata']['name']}"
        spec = d.get("spec") or {}
        if (not spec.get("secretStoreRef") and not spec.get("data") and spec.get("dataFrom")
                and all(((f.get("sourceRef") or {}).get("generatorRef")) for f in spec["dataFrom"])):
            continue
        ref = spec.get("secretStoreRef") or {}
        kind, store = ref.get("kind") or "SecretStore", ref.get("name")
        if kind == "SecretStore":
            if (ns, store) not in local:
                bad.append(f"{es} names SecretStore {store}, and no store of that name is in {ns}")
            continue
        if store not in stores:
            bad.append(f"{es} names ClusterSecretStore {store}, and the render has no such store")
            continue
        conds = (stores[store].get("spec") or {}).get("conditions") or []
        if conds and not any(admits(c, ns, ns_labels) for c in conds):
            bad.append(f"{es} is in namespace {ns}, and ClusterSecretStore {store} does not serve it")
    return bad


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = [d for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)]
        if not any(d.get("kind") == "ExternalSecret" for d in docs):
            continue
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(docs)]
    if not seen:
        bad.append("no golden render holds an ExternalSecret: this check is blind")
    return bad, seen


def selftest() -> int:
    def store(name="s", conds=None):
        spec = {} if conds is None else {"conditions": conds}
        return {"kind": "ClusterSecretStore", "metadata": {"name": name}, "spec": spec}

    def es(ns, ref="s", kind="ClusterSecretStore"):
        return {"kind": "ExternalSecret", "metadata": {"name": "e", "namespace": ns},
                "spec": {"secretStoreRef": {"kind": kind, "name": ref}}}

    gibson = [{"namespaces": ["gibson"]}]
    passing = (
        ("a store that lists the namespace", [store(conds=gibson), es("gibson")]),
        ("a store that lists two namespaces", [store(conds=[{"namespaces": ["gibson", "setec-system"]}]),
                                               es("gibson"), es("setec-system")]),
        ("a regex that matches", [store(conds=[{"namespaceRegexes": ["setec-.*"]}]), es("setec-system")]),
        # External Secrets matches a regex unanchored (Go regexp.MatchString).
        ("an unanchored regex that matches inside the name",
         [store(conds=[{"namespaceRegexes": ["setec"]}]), es("x-setec-system")]),
        ("a selector that matches a rendered Namespace",
         [store(conds=[{"namespaceSelector": {"matchLabels": {"a": "b"}}}]), es("x"),
          {"kind": "Namespace", "metadata": {"name": "x", "labels": {"a": "b"}}}]),
        ("an ExternalSecret fed by a generator only",
         [store(conds=gibson), {"kind": "ExternalSecret", "metadata": {"name": "g", "namespace": "gibson"},
                                "spec": {"dataFrom": [{"sourceRef": {"generatorRef": {"kind": "VaultDynamicSecret", "name": "v"}}}]}}]),
        ("a SecretStore in the same namespace",
         [{"kind": "SecretStore", "metadata": {"name": "l", "namespace": "x"}}, es("x", "l", "SecretStore")]),
    )
    for what, docs in passing:
        if judge(docs):
            print(f"SELFTEST FAIL: {what} must pass, got {judge(docs)}")
            return 1
    failing = (
        # The charts#504 shape: the setec ExternalSecret outside a store that serves gibson only.
        ("an ExternalSecret outside the store namespaces", [store(conds=gibson), es("setec-system")]),
        ("a regex that does not match", [store(conds=[{"namespaceRegexes": ["^gibson$"]}]), es("gibson-x")]),
        ("a selector with no matching Namespace",
         [store(conds=[{"namespaceSelector": {"matchLabels": {"a": "b"}}}]), es("x")]),
        ("a ClusterSecretStore with no conditions", [store(), es("gibson")]),
        ("a ClusterSecretStore that is not rendered", [store(conds=gibson), es("gibson", "other")]),
        ("a generator next to a data entry, with no store",
         [store(conds=gibson), {"kind": "ExternalSecret", "metadata": {"name": "g", "namespace": "gibson"},
                                "spec": {"data": [{"secretKey": "k", "remoteRef": {"key": "k"}}],
                                         "dataFrom": [{"sourceRef": {"generatorRef": {"kind": "VaultDynamicSecret", "name": "v"}}}]}}]),
        ("a SecretStore in another namespace",
         [{"kind": "SecretStore", "metadata": {"name": "l", "namespace": "y"}}, es("x", "l", "SecretStore")]),
    )
    for what, docs in failing:
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: an ExternalSecret outside its store, an open store, a missing store and a store-less data entry fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("an ExternalSecret sits where its store does not serve it (charts#504):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ externalsecret-store-namespace: each ExternalSecret of {seen} renders is served by its store")
    return 0


if __name__ == "__main__":
    sys.exit(main())
