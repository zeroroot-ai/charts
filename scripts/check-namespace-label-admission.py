#!/usr/bin/env python3
"""check-namespace-label-admission.py: each namespace label that a network rule trusts has one writer (charts#527).

A Cilium rule can select the source namespace by a namespace label
(`io.cilium.k8s.namespace.labels.<key>`). The daemon rule for the belief
trainer of each tenant namespace does that with
gibson.zeroroot.ai/managed-by=tenant-operator. A label is only as strong as
the set of principals that can write it: any principal that can create or
patch a Namespace could set it, and so get the rule.

The check reads each golden render. For each namespace label key that a
Cilium rule selects on (except kubernetes.io/metadata.name, which the API
server sets from the name), it fails unless the render holds a
ValidatingAdmissionPolicy that:

  1. matches namespaces on CREATE and UPDATE,
  2. has failurePolicy Fail,
  3. names the key in a validation, and
  4. has a ValidatingAdmissionPolicyBinding with the action Deny.

  check-namespace-label-admission.py             exit 1 on a finding
  check-namespace-label-admission.py --selftest  prove each finding fails
"""
import copy
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden", "values-*.yaml")
NS_LABEL = re.compile(r"io\.cilium\.k8s\.namespace\.labels\.([A-Za-z0-9./_-]+)")
SERVER_SET = {"kubernetes.io/metadata.name"}
POLICIES = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")


def keys_of(node, out: set) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            m = NS_LABEL.search(str(k))
            if m:
                out.add(m.group(1))
            keys_of(v, out)
    elif isinstance(node, list):
        for v in node:
            keys_of(v, out)


def trusted_keys(docs: list[dict]) -> set:
    out: set = set()
    for d in docs:
        if d.get("kind") in POLICIES:
            keys_of(d.get("spec"), out)
            keys_of(d.get("specs"), out)
    return out - SERVER_SET


def guards(docs: list[dict], key: str) -> list[str]:
    """The names of the policies that hold the key, with a Deny binding."""
    deny = {(d.get("spec") or {}).get("policyName") for d in docs
            if d.get("kind") == "ValidatingAdmissionPolicyBinding"
            and "Deny" in ((d.get("spec") or {}).get("validationActions") or [])}
    out = []
    for d in docs:
        if d.get("kind") != "ValidatingAdmissionPolicy":
            continue
        spec = d.get("spec") or {}
        ops = set()
        for r in (spec.get("matchConstraints") or {}).get("resourceRules") or []:
            if "namespaces" in (r.get("resources") or []) and "" in (r.get("apiGroups") or []):
                ops |= set(r.get("operations") or [])
        text = " ".join(str(e.get("expression") or "")
                        for e in (spec.get("validations") or []) + (spec.get("variables") or []))
        if ({"CREATE", "UPDATE"} <= ops and spec.get("failurePolicy") == "Fail"
                and f'"{key}"' in text and d["metadata"]["name"] in deny):
            out.append(d["metadata"]["name"])
    return out


def judge(docs: list[dict]) -> list[str]:
    keys = trusted_keys(docs)
    if not keys:
        return ["no Cilium rule selects a namespace label: this check is blind"]
    return [f"the namespace label {key} selects peers in a network rule, and no ValidatingAdmissionPolicy "
            "with failurePolicy Fail and a Deny binding limits who sets it on a Namespace (CREATE and UPDATE)"
            for key in sorted(keys) if not guards(docs, key)]


def load(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(open(path)) if isinstance(d, dict) and d.get("metadata")]


def selftest() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN)))
    base = load(files[0])
    if judge(base):
        print(f"selftest: the golden {files[0]} is not clean: {judge(base)}")
        return 1

    def edit(fn) -> list[dict]:
        docs = copy.deepcopy(base)
        for d in docs:
            if d["metadata"]["name"] == "gibson-tenant-namespace-label":
                fn(d)
        return [d for d in docs if d.get("_drop") is None]

    def drop(d):
        d["_drop"] = True

    def warn(d):
        if d["kind"] == "ValidatingAdmissionPolicyBinding":
            d["spec"]["validationActions"] = ["Warn"]

    def ignore(d):
        if d["kind"] == "ValidatingAdmissionPolicy":
            d["spec"]["failurePolicy"] = "Ignore"

    def create_only(d):
        if d["kind"] == "ValidatingAdmissionPolicy":
            d["spec"]["matchConstraints"]["resourceRules"][0]["operations"] = ["CREATE"]

    def other_key(d):
        if d["kind"] == "ValidatingAdmissionPolicy":
            d["spec"]["variables"] = yaml.safe_load(
                yaml.safe_dump(d["spec"]["variables"]).replace("gibson.zeroroot.ai/managed-by", "example.com/other"))
            d["spec"]["validations"] = yaml.safe_load(
                yaml.safe_dump(d["spec"]["validations"]).replace("gibson.zeroroot.ai/managed-by", "example.com/other"))

    cases = {"no policy": edit(drop), "a Warn binding": edit(warn), "failurePolicy Ignore": edit(ignore),
             "CREATE only": edit(create_only), "another label key": edit(other_key),
             "no rule selects a namespace label": [d for d in base if d.get("kind") not in POLICIES]}
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
    print(f"OK: each namespace label that a network rule trusts has an admission policy, in {len(files)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
