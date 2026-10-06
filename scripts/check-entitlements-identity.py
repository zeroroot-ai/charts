#!/usr/bin/env python3
"""check-entitlements-identity.py: the daemon pins the SPIFFE ID of the entitlements service.

With gibson.entitlementsEndpoint set, the daemon accepts only the SPIFFE ID in
ENTITLEMENTS_BILLING_SVID as the entitlements server, and it refuses to start
without it. The chart builds the ID from global.spire.trustDomain and the
fixed path of the billing component (ADR-0164). No values file writes it.

BILLING_PATH is the path that the billing chart issues to its pods: hosted
gitops/saas-overlay/entitlements-svc/templates/clusterspiffeid.yaml,
spiffeIDTemplate spiffe://<trust domain>/platform/entitlements-svc. A change
of either side must change the other.

The guard renders the baseline profile and fails when:
  - with no endpoint, the daemon gets ENTITLEMENTS_BILLING_SVID,
  - with the endpoint, the env is not spiffe://<global.spire.trustDomain>/<BILLING_PATH>,
    for the baseline trust domain and for another one,
  - the deleted value gibson.entitlementsBillingSVID renders.

  check-entitlements-identity.py             exit 1 on a finding
  check-entitlements-identity.py --selftest  prove the env check fails on a wrong ID
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = ("StatefulSet", "gibson-gibson-workloads")
BILLING_PATH = "platform/entitlements-svc"
ON = ["gibson-workloads.gibson.entitlementsEndpoint=entitlements-svc:9091",
      "gibson-workloads.gibson.entitlementsRequired=true"]


def helm(settings):
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"]
    for s in settings:
        args += ["--set", s]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def rendered(docs):
    """The ENTITLEMENTS_BILLING_SVID values of the daemon, or None with no daemon."""
    for d in docs:
        if isinstance(d, dict) and (d.get("kind"), (d.get("metadata") or {}).get("name")) == DAEMON:
            return [e.get("value") for c in d["spec"]["template"]["spec"]["containers"]
                    for e in c.get("env") or [] if e.get("name") == "ENTITLEMENTS_BILLING_SVID"]
    return None


def judge(case, got, want):
    if got is None:
        return [f"{case}: the render has no daemon StatefulSet: this guard is blind"]
    if got != want:
        return [f"{case}: the daemon has ENTITLEMENTS_BILLING_SVID {got}, want {want}"]
    return []


def selftest():
    want = [f"spiffe://example.org/{BILLING_PATH}"]
    if judge("ok", want, want) or judge("off", [], []):
        print("SELFTEST FAIL: a matching value must pass")
        return 1
    for what, got, w in (("a missing env", [], want), ("another path", ["spiffe://example.org/platform/billing"], want),
                         ("another trust domain", [f"spiffe://zeroroot.ai/{BILLING_PATH}"], want),
                         ("an env with no endpoint", want, []), ("no daemon", None, [])):
        if not judge(what, got, w):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    print("  ✓ selftest: a missing env, another path, another trust domain, an env with no endpoint and a blind render fail")
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    base_td = yaml.safe_load(open(os.path.join(ROOT, "helm/gibson/values-baseline.yaml")))["global"]["spire"]["trustDomain"]
    bad = []
    for case, settings, want in (
            ("no endpoint", [], []),
            ("endpoint, baseline trust domain", ON, [f"spiffe://{base_td}/{BILLING_PATH}"]),
            ("endpoint, another trust domain", ON + ["global.spire.trustDomain=example.org"], [f"spiffe://example.org/{BILLING_PATH}"])):
        r = helm(settings)
        if r.returncode != 0:
            bad.append(f"{case}: the render failed: {r.stderr.strip()[-300:]}")
            continue
        bad += judge(case, rendered(yaml.safe_load_all(r.stdout)), want)
    r = helm(ON + ["gibson-workloads.gibson.entitlementsBillingSVID=spiffe://x/y"])
    if r.returncode == 0:
        bad.append("the deleted value gibson.entitlementsBillingSVID rendered, and it must fail the render")
    if bad:
        print("the daemon does not pin the entitlements identity:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ entitlements-identity: the env is spiffe://<trust domain>/{BILLING_PATH} for each trust domain, and the deleted value fails the render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
