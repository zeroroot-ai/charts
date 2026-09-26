#!/usr/bin/env python3
"""check-signin-policy.py: the first Zitadel instance starts with the sign-in policy.

WHY THIS EXISTS
Zitadel applies DefaultInstance.* only when it creates the first instance. The
platform-operator then reconciles the sign-in policy on every install and
repairs drift. The chart values close the window between the first Zitadel
start and the first operator reconcile. In that window a person must not be
able to sign in without MFA, through an external identity provider, or by
self-registration.

WHAT IT CHECKS
  helm/gibson/values.yaml, zitadel.zitadel.configmapConfig.DefaultInstance:
    LoginPolicy.ForceMFA true, LoginPolicy.AllowExternalIDP false,
    LoginPolicy.AllowRegister false, LoginPolicy.AllowDomainDiscovery false,
    DomainPolicy.UserLoginMustBeDomain false (usernames unique install-wide).
  No other values file under helm/ or ci/ sets DefaultInstance, so an overlay
  cannot weaken the policy.

USAGE
  scripts/check-signin-policy.py             check the tree
  scripts/check-signin-policy.py --selftest  prove a weak policy fails
Exit: 0 pass · 1 a rule fails · 2 self-test broke
"""
from __future__ import annotations

import glob
import os
import sys
import tempfile

import yaml

REQUIRED = {
    ("LoginPolicy", "ForceMFA"): True,
    ("LoginPolicy", "AllowExternalIDP"): False,
    ("LoginPolicy", "AllowRegister"): False,
    ("LoginPolicy", "AllowDomainDiscovery"): False,
    ("DomainPolicy", "UserLoginMustBeDomain"): False,
}
BASE = "helm/gibson/values.yaml"


def default_instance(values: dict) -> dict | None:
    node = values
    for key in ("zitadel", "zitadel", "configmapConfig", "DefaultInstance"):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node if isinstance(node, dict) else None


def check_base(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        di = default_instance(yaml.safe_load(f) or {})
    if di is None:
        return [f"{path}: zitadel.zitadel.configmapConfig.DefaultInstance is missing"]
    problems = []
    for (section, key), want in REQUIRED.items():
        got = (di.get(section) or {}).get(key)
        if got is not want:
            problems.append(f"{path}: DefaultInstance.{section}.{key} is {got!r}, must be {want!r}")
    return problems


def check_overlays(root: str) -> list[str]:
    problems = []
    files = glob.glob(os.path.join(root, "helm", "**", "values*.yaml"), recursive=True)
    files += glob.glob(os.path.join(root, "ci", "**", "*.yaml"), recursive=True)
    base = os.path.normpath(os.path.join(root, BASE))
    for p in sorted(files):
        norm = os.path.normpath(p)
        if norm == base or "/testdata/" in norm or "/charts/" in norm.replace(root, ""):
            continue
        with open(p, encoding="utf-8") as f:
            for doc in yaml.safe_load_all(f):
                if isinstance(doc, dict) and default_instance(doc) is not None:
                    problems.append(f"{p}: sets DefaultInstance; only {BASE} may set it")
    return problems


def check(root: str) -> list[str]:
    return check_base(os.path.join(root, BASE)) + check_overlays(root)


def _write(root: str, rel: str, login: dict, domain: dict) -> None:
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    doc = {"zitadel": {"zitadel": {"configmapConfig": {"DefaultInstance": {
        "LoginPolicy": login, "DomainPolicy": domain}}}}}
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)


def selftest() -> int:
    good = {"ForceMFA": True, "AllowExternalIDP": False, "AllowRegister": False,
            "AllowDomainDiscovery": False}
    domain = {"UserLoginMustBeDomain": False}
    cases = {
        "good": (lambda r: _write(r, BASE, good, domain), True),
        "mfa_off": (lambda r: _write(r, BASE, {**good, "ForceMFA": False}, domain), False),
        "external_idp_on": (lambda r: _write(r, BASE, {**good, "AllowExternalIDP": True}, domain), False),
        "domain_scoped_usernames": (
            lambda r: _write(r, BASE, good, {"UserLoginMustBeDomain": True}), False),
        "overlay_sets_policy": (
            lambda r: (_write(r, BASE, good, domain),
                       _write(r, "ci/values-staging.yaml", {**good, "ForceMFA": False}, domain)),
            False),
    }
    for name, (build, want_ok) in cases.items():
        with tempfile.TemporaryDirectory() as d:
            build(d)
            got_ok = not check(d)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2
    print("✅ self-test: a weak or overridden first-instance sign-in policy is rejected")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    problems = check(".")
    if problems:
        print("❌ the first-instance sign-in policy is not enforced")
        for p in problems:
            print("   " + p)
        return 1
    print("✅ the first-instance sign-in policy forces MFA and blocks external IdPs and self-registration")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
