#!/usr/bin/env python3
"""check-entitlements-identity.py: the daemon pins the SPIFFE ID of the entitlements service.

With gibson.entitlementsEndpoint set, the daemon accepts only the SPIFFE ID in
ENTITLEMENTS_BILLING_SVID as the entitlements server, and it refuses to start
without it. The chart sets the env from gibson.entitlementsBillingSVID, and
validateEntitlementsCoherence fails the render when the value is empty or is
not a SPIFFE ID.

The guard renders the baseline profile four times:

  no endpoint                         renders, no ENTITLEMENTS_BILLING_SVID
  endpoint, required, SPIFFE ID       renders, the env holds the ID
  endpoint, required, no ID           must fail
  endpoint, required, ID not spiffe:  must fail

  check-entitlements-identity.py             exit 1 on a finding
  check-entitlements-identity.py --selftest  prove the env check fails on a wrong value
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAEMON = ("StatefulSet", "gibson-gibson-workloads")
ID = "spiffe://example.org/platform/entitlements-svc"
ON = ["gibson-workloads.gibson.entitlementsEndpoint=entitlements-svc:9091",
      "gibson-workloads.gibson.entitlementsRequired=true"]


def helm(settings: list[str]) -> subprocess.CompletedProcess:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"]
    for s in settings:
        args += ["--set", s]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def svid_values(docs) -> list[str] | None:
    """The ENTITLEMENTS_BILLING_SVID values of the daemon, or None with no daemon."""
    for d in docs:
        if isinstance(d, dict) and (d.get("kind"), (d.get("metadata") or {}).get("name")) == DAEMON:
            return [e.get("value") for c in d["spec"]["template"]["spec"]["containers"]
                    for e in c.get("env") or [] if e.get("name") == "ENTITLEMENTS_BILLING_SVID"]
    return None


def judge(case: str, got: list[str] | None, want: list[str]) -> list[str]:
    if got is None:
        return [f"{case}: the render has no daemon StatefulSet: this guard is blind"]
    if got != want:
        return [f"{case}: the daemon has ENTITLEMENTS_BILLING_SVID {got}, want {want}"]
    return []


def selftest() -> int:
    if judge("ok", [ID], [ID]) or judge("off", [], []):
        print("SELFTEST FAIL: a matching value must pass")
        return 1
    for what, got, want in (("a missing env", [], [ID]), ("a wrong ID", ["spiffe://x/y"], [ID]),
                            ("an env with no endpoint", [ID], []), ("no daemon", None, [])):
        if not judge(what, got, want):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    print("  ✓ selftest: a missing env, a wrong ID, an env with no endpoint and a blind render fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for case, settings, want in (("no endpoint", [], []),
                                 ("endpoint with an ID", ON + [f"gibson-workloads.gibson.entitlementsBillingSVID={ID}"], [ID])):
        r = helm(settings)
        if r.returncode != 0:
            bad.append(f"{case}: the render failed: {r.stderr.strip()[-300:]}")
            continue
        bad += judge(case, svid_values(yaml.safe_load_all(r.stdout)), want)
    for case, settings in (("endpoint with no ID", ON),
                           ("endpoint with an ID that is not spiffe", ON + ["gibson-workloads.gibson.entitlementsBillingSVID=entitlements-svc"])):
        r = helm(settings)
        if r.returncode == 0:
            bad.append(f"{case}: the render passed, and it must fail")
        elif "entitlementsBillingSVID" not in r.stderr:
            bad.append(f"{case}: the render failed for another reason: {r.stderr.strip()[-300:]}")
    if bad:
        print("the daemon does not pin the entitlements identity:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print("  ✓ entitlements-identity: the env follows the value, and an empty or non-SPIFFE value fails the render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
