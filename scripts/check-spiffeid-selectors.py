#!/usr/bin/env python3
"""check-spiffeid-selectors.py: each ClusterSPIFFEID names the workload selectors of its pod.

A ClusterSPIFFEID with `workloadSelectorTemplates` gives the identity only to
a workload that attests with those selectors (namespace and ServiceAccount).
An object with no such key gives the identity on the pod selector alone, with
the default selectors of the SPIRE controller manager.

Each ClusterSPIFFEID that the chart renders, in each published profile, must
have the key. MAY_OMIT names the objects that can omit it, by object name,
with a reason. An entry that matches no rendered object fails, and so does an
entry for an object that has the key.

  check-spiffeid-selectors.py             exit 1 on a finding, 0 when clean
  check-spiffeid-selectors.py --selftest  prove each kind of finding fails
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The same profiles that scripts/golden.sh renders.
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
# Object name -> why the object can omit workloadSelectorTemplates. The
# vendored SPIRE chart renders these three, and it has no value for the key.
MAY_OMIT = {
    "gibson-gibson-default":
        "The default identity of the SPIRE chart for a namespace outside the platform. "
        "Its ID holds the namespace and the ServiceAccount of the pod.",
    "gibson-gibson-oidc-discovery-provider":
        "The identity of the OIDC discovery provider, from the SPIRE chart. The pod selector names the component.",
    "gibson-gibson-test-keys":
        "The identity of the test-keys helm test pod, from the SPIRE chart. The pod selector names the component.",
}


def render(profile: tuple[str, ...]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/testdata/render-inputs/gibson.yaml",
            "--namespace", "gibson"]
    for f in profile:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def audit(renders: list[list[dict]], may_omit: dict) -> list[str]:
    bad, without, total = [], set(), 0
    for docs in renders:
        for d in docs:
            if d.get("kind") != "ClusterSPIFFEID":
                continue
            total += 1
            name = d["metadata"]["name"]
            if (d.get("spec") or {}).get("workloadSelectorTemplates"):
                continue
            without.add(name)
            if name not in may_omit:
                bad.append(f"ClusterSPIFFEID/{name} has no workloadSelectorTemplates and no entry in MAY_OMIT")
    if not total:
        return ["no ClusterSPIFFEID in the render: this guard is blind"]
    for name in sorted(may_omit):
        if name not in without:
            bad.append(f"stale entry in MAY_OMIT: no render has a ClusterSPIFFEID/{name} without workloadSelectorTemplates")
    return sorted(set(bad))


def _obj(name: str, selectors: bool) -> dict:
    spec = {"spiffeIDTemplate": "spiffe://example/x", "podSelector": {"matchLabels": {"app": name}}}
    if selectors:
        spec["workloadSelectorTemplates"] = ["k8s:ns:gibson", f"k8s:sa:{name}"]
    return {"apiVersion": "spire.spiffe.io/v1alpha1", "kind": "ClusterSPIFFEID", "metadata": {"name": name}, "spec": spec}


def selftest() -> int:
    listed = {"vendored": "fixture"}
    cases = [
        ("an object with the key and a listed object without it", [[_obj("ours", True), _obj("vendored", False)]], listed, 0),
        ("an object without the key and without an entry", [[_obj("ours", False), _obj("vendored", False)]], listed, 1),
        ("an entry for an object that the render does not contain", [[_obj("ours", True)]], listed, 1),
        ("an entry for an object that has the key", [[_obj("ours", True), _obj("vendored", True)]], listed, 1),
        ("an empty selector list", [[dict(_obj("ours", True), spec={"workloadSelectorTemplates": []}), _obj("vendored", False)]], listed, 1),
        ("a render with no ClusterSPIFFEID", [[]], {}, 1),
    ]
    for what, renders, may_omit, want in cases:
        got = audit(renders, may_omit)
        if len(got) != want:
            print(f"SELFTEST FAIL: {what}: want {want} finding(s), got {got}")
            return 1
    print("  ✓ selftest: an object with no selectors, a stale entry, an entry for an object with selectors, "
          "an empty list and a blind render fail; the clean render passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    renders = [render(p) for p in PROFILES]
    bad = audit(renders, MAY_OMIT)
    if bad:
        print("a ClusterSPIFFEID gives an identity without workload selectors:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    names = {d["metadata"]["name"] for docs in renders for d in docs if d.get("kind") == "ClusterSPIFFEID"}
    print(f"  ✓ spiffeid-selectors: {len(names)} ClusterSPIFFEID objects in {len(PROFILES)} profiles, "
          f"{len(names) - len(MAY_OMIT)} with workload selectors and {len(MAY_OMIT)} listed in MAY_OMIT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
