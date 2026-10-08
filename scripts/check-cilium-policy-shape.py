#!/usr/bin/env python3
"""check-cilium-policy-shape.py: each Cilium policy of each render has the shape the CRD accepts (charts#494).

The Cilium CRD refuses a field that its schema does not declare. charts#492
nested `specs` under `spec` in the velero policy, and the kind install could
not sync it (`.spec.specs: field not declared in schema`). No offline gate saw
it: kubeconform skips the Cilium kinds, and the network guards read
`spec` and `specs` at the top level only, so they did not see the nested rules.

The check reads each golden render and fails when a CiliumNetworkPolicy or a
CiliumClusterwideNetworkPolicy:

  1. has both `spec` and `specs`, or neither,
  2. has a rule with a key that a Cilium rule does not declare,
  3. has a rule with no endpointSelector and no nodeSelector.

  check-cilium-policy-shape.py             exit 1 on a finding
  check-cilium-policy-shape.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
KINDS = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")
# The fields of a Cilium rule (cilium.io/v2 Rule).
RULE_KEYS = {"description", "endpointSelector", "nodeSelector", "ingress", "ingressDeny",
             "egress", "egressDeny", "labels", "enableDefaultDeny", "log"}


def judge(docs: list) -> list[str]:
    out = []
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") not in KINDS:
            continue
        name = f"{d['kind']}/{(d.get('metadata') or {}).get('name')}"
        has_spec, has_specs = "spec" in d, "specs" in d
        if has_spec == has_specs:
            out.append(f"{name}: has {'both spec and specs' if has_spec else 'neither spec nor specs'}")
            continue
        rules = [d["spec"]] if has_spec else list(d.get("specs") or [])
        if not rules:
            out.append(f"{name}: specs is empty")
        for i, r in enumerate(rules):
            where = "spec" if has_spec else f"specs[{i}]"
            if not isinstance(r, dict):
                out.append(f"{name}: {where} is not a rule")
                continue
            extra = sorted(set(r) - RULE_KEYS)
            if extra:
                out.append(f"{name}: {where} has keys that a Cilium rule does not declare: {extra}")
            if "endpointSelector" not in r and "nodeSelector" not in r:
                out.append(f"{name}: {where} selects no endpoint and no node")
    return out


def selftest() -> int:
    sel = {"matchLabels": {"app": "x"}}
    good = [{"kind": "CiliumNetworkPolicy", "metadata": {"name": "a"}, "spec": {"endpointSelector": sel, "egress": []}},
            {"kind": "CiliumNetworkPolicy", "metadata": {"name": "b"},
             "specs": [{"description": "d", "endpointSelector": sel, "egress": []}]}]
    if judge(good):
        print(f"SELFTEST FAIL: a good policy must pass, got {judge(good)}")
        return 1
    failing = (
        ("specs nested under spec (charts#494)",
         {"kind": "CiliumNetworkPolicy", "metadata": {"name": "v"},
          "spec": {"description": "d", "specs": [{"endpointSelector": sel}]}}),
        ("both spec and specs",
         {"kind": "CiliumNetworkPolicy", "metadata": {"name": "v"}, "spec": {"endpointSelector": sel},
          "specs": [{"endpointSelector": sel}]}),
        ("neither spec nor specs", {"kind": "CiliumClusterwideNetworkPolicy", "metadata": {"name": "v"}}),
        ("a rule with no selector",
         {"kind": "CiliumNetworkPolicy", "metadata": {"name": "v"}, "specs": [{"egress": []}]}),
    )
    for what, doc in failing:
        if not judge([doc]):
            print(f"SELFTEST FAIL: {what} must give a finding")
            return 1
    print("  ✓ selftest: specs under spec, both, neither, an unknown key and a rule with no selector fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN, "*.yaml")))
    if not files:
        print("::error::no golden render to read; the check would see nothing", file=sys.stderr)
        return 1
    bad, seen = [], 0
    for f in files:
        with open(f) as fh:
            docs = [d for d in yaml.safe_load_all(fh) if d]
        seen += sum(1 for d in docs if isinstance(d, dict) and d.get("kind") in KINDS)
        bad += [f"{os.path.basename(f)}: {b}" for b in judge(docs)]
    if not seen:
        print("::error::the golden renders hold no Cilium policy; the check would see nothing", file=sys.stderr)
        return 1
    if bad:
        print("a Cilium policy has a shape that the CRD refuses (charts#494):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ cilium-policy-shape: {seen} Cilium policies in {len(files)} renders have the CRD shape")
    return 0


if __name__ == "__main__":
    sys.exit(main())
