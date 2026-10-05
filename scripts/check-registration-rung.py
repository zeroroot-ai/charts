#!/usr/bin/env python3
"""check-registration-rung.py: one value selects the registration rung, and both readers get it (ADR-0074).

`registration` in gibson-workloads names the rung: closed, approval or open.
The chart renders SIGNUP_SELF_SERVE from it on the daemon and on the
dashboard. The guard renders the baseline profile once for each rung and
checks both readers:

  closed    no SIGNUP_SELF_SERVE on either
  approval  SIGNUP_SELF_SERVE=approval on both
  open      SIGNUP_SELF_SERVE=true on both

A render with any other name, or with a deleted boolean key, must fail.

  check-registration-rung.py             exit 1 on a finding, 0 when clean
  check-registration-rung.py --selftest  prove the reader check fails on a wrong value
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
READERS = (("StatefulSet", "gibson-gibson-workloads"), ("Deployment", "gibson-dashboard"))
WANT = {"closed": [], "approval": ["approval"], "open": ["true"]}
MUST_FAIL = ("gibson-workloads.registration=yes", "gibson-workloads.registration=Open",
             "gibson-workloads.gibson.signupSelfServe=false", "gibson-workloads.dashboard.signup.selfServe=true")


def helm(setting: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
         "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml",
         "--set", setting], cwd=ROOT, capture_output=True, text=True)


def values(docs: list[dict]) -> dict:
    """reader -> the SIGNUP_SELF_SERVE values of its containers."""
    out = {}
    for d in docs:
        key = (d.get("kind"), (d.get("metadata") or {}).get("name"))
        if key in READERS:
            out[key] = [e.get("value") for c in d["spec"]["template"]["spec"]["containers"]
                        for e in c.get("env") or [] if e.get("name") == "SIGNUP_SELF_SERVE"]
    return out


def judge(rung: str, got: dict) -> list[str]:
    bad = []
    for reader in READERS:
        if reader not in got:
            bad.append(f"{rung}: the render has no {reader[0]}/{reader[1]}: this guard is blind")
        elif got[reader] != WANT[rung]:
            bad.append(f"{rung}: {reader[0]}/{reader[1]} has SIGNUP_SELF_SERVE {got[reader]}, want {WANT[rung]}")
    return bad


def selftest() -> int:
    good = {READERS[0]: ["approval"], READERS[1]: ["approval"]}
    if judge("approval", good):
        print("SELFTEST FAIL: two readers with the approval value must pass")
        return 1
    cases = {
        "one reader with the open value": {READERS[0]: ["approval"], READERS[1]: ["true"]},
        "one reader with no value": {READERS[0]: ["approval"], READERS[1]: []},
        "a missing reader": {READERS[0]: ["approval"]},
    }
    for what, got in cases.items():
        if not judge("approval", got):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    print("  ✓ selftest: a reader with a different value, with no value and a missing reader fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for rung in WANT:
        r = helm(f"gibson-workloads.registration={rung}")
        if r.returncode != 0:
            bad.append(f"{rung}: the render failed: {r.stderr.strip()[-200:]}")
            continue
        bad += judge(rung, values([d for d in yaml.safe_load_all(r.stdout) if d]))
    for setting in MUST_FAIL:
        if helm(setting).returncode == 0:
            bad.append(f"the render with {setting} passed; it must fail")
    if bad:
        print("the registration rung does not reach the daemon and the dashboard as one value (ADR-0074):",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ registration-rung: {len(WANT)} rungs reach the daemon and the dashboard, "
          f"{len(MUST_FAIL)} wrong settings fail the render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
