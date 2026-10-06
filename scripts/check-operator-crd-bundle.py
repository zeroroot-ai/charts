#!/usr/bin/env python3
"""check-operator-crd-bundle.py: the operator CRD bundle matches the operators and the kinds of the chart.

helm/gibson-operator-crd-files/files/crds/<operator>.yaml holds the full CRD
set of each operator that the umbrella installs (charts#370, option 1). An
operator needs more of its own CRDs than the chart renders: cert-manager makes
a CertificateRequest for each Certificate, for example. So the bundle is not
trimmed to the rendered kinds. Two rules hold it to the chart instead:

  1. Each bundle file names an operator that helm/gibson/Chart.yaml installs:
     its file name is the name of an umbrella dependency. A bundle for an
     operator the chart does not install fails.
  2. Each object that a template renders in an API group of a bundled operator
     (the domain of its groups, for example cert-manager.io) has a kind that
     the bundle holds, in that group. A kind the bundle lacks fails.

The renders are the committed golden files (helm/testdata/golden/), which
`make golden` keeps equal to the chart. The guard fails as blind when the
renders hold no object of a bundled operator.

  check-operator-crd-bundle.py             exit 1 on a finding, 0 when clean
  check-operator-crd-bundle.py --selftest  prove each kind of finding fails
"""
import glob
import os
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUNDLE = os.path.join("helm", "gibson-operator-crd-files", "files", "crds")
UMBRELLA = os.path.join("helm", "gibson", "Chart.yaml")
GOLDEN = os.path.join("helm", "testdata", "golden")


def domain(group: str) -> str:
    return ".".join(group.split(".")[-2:])


def bundle(root: str) -> dict[str, set[tuple[str, str]]]:
    """operator -> {(group, kind)} of its bundled CRDs."""
    out = {}
    for f in sorted(glob.glob(os.path.join(root, BUNDLE, "*.yaml"))):
        op = os.path.basename(f)[:-len(".yaml")]
        kinds = set()
        for d in yaml.safe_load_all(open(f)):
            if d and d.get("kind") == "CustomResourceDefinition":
                kinds.add((d["spec"]["group"], d["spec"]["names"]["kind"]))
        out[op] = kinds
    return out


def installed(root: str) -> set[str]:
    deps = (yaml.safe_load(open(os.path.join(root, UMBRELLA))) or {}).get("dependencies") or []
    return {d["name"] for d in deps}


def rendered(root: str) -> set[tuple[str, str, str]]:
    """{(group, kind, golden file)} of each object of a non-core group."""
    out = set()
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        for d in yaml.safe_load_all(open(f)):
            if not d or "/" not in str(d.get("apiVersion", "")):
                continue
            if d.get("kind") == "CustomResourceDefinition":
                continue
            out.add((d["apiVersion"].split("/")[0], d["kind"], os.path.basename(f)))
    return out


def audit(root: str) -> tuple[list[str], int]:
    b = bundle(root)
    if not b:
        return ["the bundle holds no file: this guard is blind"], 0
    bad = []
    for op in sorted(set(b) - installed(root)):
        bad.append(f"{BUNDLE}/{op}.yaml bundles the CRDs of {op}, and {UMBRELLA} does not install {op}")
    held = {gk for kinds in b.values() for gk in kinds}
    domains = {domain(g) for g, _ in held}
    used = set()
    for group, kind, where in sorted(rendered(root)):
        if domain(group) not in domains:
            continue
        used.add((group, kind))
        if (group, kind) not in held:
            bad.append(f"{where}: a template renders {group}/{kind}, and the bundle holds no CRD for it")
    if not used:
        bad.append("the renders hold no object of a bundled operator: this guard is blind")
    return sorted(set(bad)), len(used)


def selftest() -> int:
    crd = lambda g, k: {"apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
                        "spec": {"group": g, "names": {"kind": k}}}
    obj = lambda g, k: {"apiVersion": f"{g}/v1", "kind": k, "metadata": {"name": "x"}}
    cases = [
        ("a clean tree", ["opa"], [crd("opa.example.io", "Thing")], [obj("opa.example.io", "Thing")], 0),
        ("a rendered kind that the bundle lacks", ["opa"], [crd("opa.example.io", "Thing")],
         [obj("opa.example.io", "Thing"), obj("opa.example.io", "Other")], 1),
        ("a kind in a second group of a bundled operator", ["opa"], [crd("opa.example.io", "Thing")],
         [obj("opa.example.io", "Thing"), obj("acme.opa.example.io", "Order")], 1),
        ("a bundle for an operator that is not installed", [], [crd("opa.example.io", "Thing")],
         [obj("opa.example.io", "Thing")], 1),
        ("renders with no object of a bundled operator", ["opa"], [crd("opa.example.io", "Thing")],
         [obj("other.dev", "Thing")], 1),
    ]
    for what, deps, crds, objs, want in cases:
        with tempfile.TemporaryDirectory() as d:
            for sub in (BUNDLE, GOLDEN, os.path.dirname(UMBRELLA)):
                os.makedirs(os.path.join(d, sub), exist_ok=True)
            yaml.safe_dump_all(crds, open(os.path.join(d, BUNDLE, "opa.yaml"), "w"))
            yaml.safe_dump_all(objs, open(os.path.join(d, GOLDEN, "r.yaml"), "w"))
            yaml.safe_dump({"dependencies": [{"name": n} for n in deps]}, open(os.path.join(d, UMBRELLA), "w"))
            got, _ = audit(d)
            if len(got) != want:
                print(f"SELFTEST FAIL: {what}: want {want} finding(s), got {got}")
                return 1
    print("  ✓ selftest: a kind the bundle lacks, a kind in a second group, a bundle for an operator that is not "
          "installed and a blind render fail; the clean tree passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, used = audit(ROOT)
    if bad:
        print("the operator CRD bundle does not match the operators or the kinds of the chart (charts#370):",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    b = bundle(ROOT)
    print(f"  ✓ operator-crd-bundle: {len(b)} bundled operators, each installed by the umbrella; "
          f"{used} rendered kinds of them, each held by the bundle ({sum(len(v) for v in b.values())} CRDs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
