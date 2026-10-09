#!/usr/bin/env python3
"""check-dns-names.py: each pod resolves only the names it needs (D76, charts#518).

The chart permits DNS names per pod through the DNS rules of its Cilium
policies:

  - the default deny of the release namespace: the names in the cluster
    (under cluster.local) and the hosts of the install (the domain and one
    label under it),
  - an egress-fqdn policy: the names of its host group,
  - the egress-internet policy: each name.

The check reads each golden render and fails when:

  1. a DNS rule permits each name (matchPattern "*") in a policy that does not
     select the egress-internet label,
  2. a default deny of the release namespace permits a name outside the
     cluster and the install domain, or has no DNS rule,
  3. an egress-fqdn policy opens a host by name with no DNS rule in the same
     policy that permits that name, so the pod could not resolve it.

The namespaces of other charts (setec-system) keep their own policies.

  check-dns-names.py             exit 1 on a finding
  check-dns-names.py --selftest  prove each finding fails
"""
import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden", "values-*.yaml")
RELEASE_NS = "gibson"
KINDS = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")
INTERNET = "gibson.zeroroot.ai/egress-internet"
FQDN = "gibson.zeroroot.ai/egress-fqdn"
CLUSTER = ".cluster.local"


def rules(doc: dict) -> list[dict]:
    return ([doc["spec"]] if doc.get("spec") else []) + list(doc.get("specs") or [])


def dns_names(rule: dict) -> list[dict]:
    out = []
    for e in rule.get("egress") or []:
        for tp in e.get("toPorts") or []:
            out += ((tp.get("rules") or {}).get("dns") or [])
    return out


def selector(rule: dict) -> dict:
    return {k.split(":", 1)[-1]: v for k, v in ((rule.get("endpointSelector") or {}).get("matchLabels") or {}).items()}


def name_of(sel: dict) -> str:
    return sel.get("matchName") or sel.get("matchPattern") or ""


def is_default_deny(doc: dict) -> bool:
    sel = selector(doc.get("spec") or {})
    return (doc.get("kind") == "CiliumClusterwideNetworkPolicy"
            and sel == {"io.kubernetes.pod.namespace": RELEASE_NS})


def judge(docs: list[dict]) -> list[str]:
    bad, seen_deny = [], False
    for d in docs:
        if d.get("kind") not in KINDS:
            continue
        name = d["metadata"]["name"]
        ns = d["metadata"].get("namespace")
        if d.get("kind") == "CiliumNetworkPolicy" and ns not in (None, RELEASE_NS):
            continue
        deny = is_default_deny(d)
        if d.get("kind") == "CiliumClusterwideNetworkPolicy" and not deny:
            continue
        for r in rules(d):
            names = dns_names(r)
            sel = selector(r)
            for n in names:
                if n.get("matchPattern") == "*" and sel.get(INTERNET) != "true":
                    bad.append(f"{name}: a DNS rule permits each name, and the policy does not select {INTERNET}")
            if deny:
                seen_deny = True
                if not names:
                    bad.append(f"{name}: the default deny has no DNS rule, so no pod resolves a Service")
                domains = {n["matchName"] for n in names if n.get("matchName") and not n["matchName"].endswith(CLUSTER)}
                for n in names:
                    v = name_of(n)
                    inside = v.endswith(CLUSTER) or v in domains or any(v == f"*.{dom}" for dom in domains)
                    if not inside:
                        bad.append(f"{name}: the default deny permits {v!r}, which is outside the cluster and the domain")
            if sel.get(FQDN):
                allowed = {(k, n.get(k)) for n in names for k in ("matchName", "matchPattern") if n.get(k)}
                for e in r.get("egress") or []:
                    for f in e.get("toFQDNs") or []:
                        key = next(((k, f[k]) for k in ("matchName", "matchPattern") if f.get(k)), None)
                        if key and key not in allowed:
                            bad.append(f"{name}: opens {key[1]} with no DNS rule that permits it, so the pod cannot resolve it")
    if not seen_deny:
        bad.append("no default deny of the release namespace in the render: this check is blind")
    return bad


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
            if d.get("kind") in KINDS:
                fn(d)
        return docs

    def deny_star(d):
        if is_default_deny(d):
            dns_names(d["spec"])[0].clear()
            dns_names(d["spec"])[0]["matchPattern"] = "*"

    def deny_outside(d):
        if is_default_deny(d):
            for e in d["spec"]["egress"]:
                for tp in e.get("toPorts") or []:
                    if (tp.get("rules") or {}).get("dns"):
                        tp["rules"]["dns"].append({"matchPattern": "*.example.org"})

    def deny_none(d):
        if is_default_deny(d):
            for e in d["spec"]["egress"]:
                for tp in e.get("toPorts") or []:
                    tp.pop("rules", None)

    def group_no_dns(d):
        if FQDN in str((d.get("spec") or {}).get("endpointSelector")):
            d["spec"]["egress"] = [e for e in d["spec"]["egress"] if "toEndpoints" not in e]

    def group_star(d):
        if FQDN in str((d.get("spec") or {}).get("endpointSelector")):
            for e in d["spec"]["egress"]:
                for tp in e.get("toPorts") or []:
                    if (tp.get("rules") or {}).get("dns"):
                        tp["rules"]["dns"] = [{"matchPattern": "*"}]

    cases = {"a default deny that permits each name": edit(deny_star),
             "a default deny that permits an outside name": edit(deny_outside),
             "a default deny with no DNS rule": edit(deny_none),
             "an egress group with no DNS rule": edit(group_no_dns),
             "an egress group that permits each name": edit(group_star),
             "no default deny": [d for d in base if not is_default_deny(d)]}
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
    print(f"OK: each pod resolves only the names it needs, in {len(files)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
