#!/usr/bin/env python3
"""check-setec-federation.py: the SPIFFE registration of the setec frontend
lists each foreign trust domain that the setec chart federates (setec#251).

WHY. The setec chart writes one ClusterFederatedTrustDomain for each enrolled
frontend client in a foreign trust domain. SPIRE then fetches the bundle of
that domain, but it gives the bundle to a workload only when the
ClusterSPIFFEID of the workload lists the domain under federatesWith. With no
federatesWith, the frontend cannot verify the SVID of a client cluster in
another trust domain, and each call of that cluster fails.

HOW. It renders the umbrella twice:
  1. the baseline profile, where every client is in the install trust domain:
     the ClusterSPIFFEID gibson-platform-setec-frontend has no federatesWith
     field, and the render holds no ClusterFederatedTrustDomain;
  2. the baseline profile plus tests/fixtures/setec-federation/values.yaml
     (two foreign domains, one of them twice): federatesWith equals the set
     of spec.trustDomain of the ClusterFederatedTrustDomain objects, sorted,
     each domain once.
--selftest proves that a registration with a missing domain, an extra
domain, a duplicate, or a field on the baseline fails.

Usage:
  check-setec-federation.py [--selftest]
"""
import argparse
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "gibson-platform-setec-frontend"


def render(extra=None):
    args = ["helm", "template", "gibson", os.path.join(ROOT, "helm/gibson"), "--namespace", "gibson",
            "-f", os.path.join(ROOT, "helm/gibson/values-baseline.yaml"),
            "-f", os.path.join(ROOT, "helm/testdata/render-inputs/gibson.yaml")]
    if extra:
        args += ["-f", extra]
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"FAIL: helm template failed:\n{out.stderr}")
    return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)]


def check(docs, want_field):
    """Findings for one render. want_field: the render must list domains."""
    reg = [d for d in docs if d.get("kind") == "ClusterSPIFFEID" and d["metadata"]["name"] == NAME]
    if len(reg) != 1:
        return [f"the render holds {len(reg)} ClusterSPIFFEID {NAME}, want 1"]
    spec = reg[0]["spec"]
    feds = sorted({d["spec"]["trustDomain"] for d in docs if d.get("kind") == "ClusterFederatedTrustDomain"})
    out = []
    if not want_field:
        if "federatesWith" in spec:
            out.append(f"{NAME} has federatesWith, and no client is in a foreign trust domain")
        if feds:
            out.append(f"the baseline render federates {feds}, and no client is in a foreign trust domain")
        return out
    if not feds:
        return ["the fixture render holds no ClusterFederatedTrustDomain: the fixture tests nothing"]
    got = spec.get("federatesWith")
    if got is None:
        return [f"{NAME} has no federatesWith, and the setec chart federates {feds}"]
    if len(got) != len(set(got)):
        out.append(f"{NAME} federatesWith lists a domain twice: {got}")
    if sorted(set(got)) != feds:
        out.append(f"{NAME} federatesWith is {got}, and the setec chart federates {feds}")
    if got != sorted(got):
        out.append(f"{NAME} federatesWith is not sorted: {got}")
    return out


def frontend(docs):
    return next(d for d in docs if d.get("kind") == "ClusterSPIFFEID" and d["metadata"]["name"] == NAME)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    base = render()
    fixture = render(os.path.join(ROOT, "tests/fixtures/setec-federation/values.yaml"))
    if a.selftest:
        bad = 0
        cases = []
        import copy
        d = copy.deepcopy(fixture); del frontend(d)["spec"]["federatesWith"]
        cases.append(("FAILING FIXTURE: no federatesWith with foreign clients", d, True))
        d = copy.deepcopy(fixture); frontend(d)["spec"]["federatesWith"] = frontend(d)["spec"]["federatesWith"][:1]
        cases.append(("FAILING FIXTURE: a missing domain", d, True))
        d = copy.deepcopy(fixture); frontend(d)["spec"]["federatesWith"].append("zz.example.com")
        cases.append(("FAILING FIXTURE: an extra domain", d, True))
        d = copy.deepcopy(fixture); frontend(d)["spec"]["federatesWith"].append(frontend(d)["spec"]["federatesWith"][0])
        cases.append(("FAILING FIXTURE: a domain listed twice", d, True))
        d = copy.deepcopy(base); frontend(d)["spec"]["federatesWith"] = ["east.example.org"]
        cases.append(("FAILING FIXTURE: a field on the baseline", d, False))
        for label, docs, field in cases:
            ok = bool(check(docs, field))
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
            bad += 0 if ok else 1
        for label, docs, field in (("the baseline render", base, False), ("the fixture render", fixture, True)):
            ok = not check(docs, field)
            print(f"  {'ok  ' if ok else 'FAIL'} {label} passes")
            bad += 0 if ok else 1
        if bad:
            print(f"check-setec-federation selftest: {bad} case(s) failed", file=sys.stderr)
            return 1
        print(f"check-setec-federation selftest: {len(cases) + 2} cases passed")
        return 0
    findings = check(base, False) + check(fixture, True)
    for f in findings:
        print(f"FAIL: {f}", file=sys.stderr)
    if findings:
        return 1
    print(f"setec-federation: the baseline has no federatesWith, and the fixture lists {frontend(fixture)['spec']['federatesWith']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
