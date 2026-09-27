#!/usr/bin/env python3
"""check-instance-admin-roles-scoped.py — least privilege stays the default (hosted#207).

WHY THIS EXISTS
hosted#199 and hosted#200 narrowed the daemon's and the tenant-operator's
OIDCClient to IAM_ORG_MANAGER, dropping IAM_OWNER. Nothing before this guard
stopped a later change from quietly widening one of them back, or handing an
instance-administrator role to a NEW OIDCClient, a new SystemAPIUsers entry,
or the platform_operator FGA seed list. Zitadel's own defaults do the widening
silently (an OIDCClient with no `roles` used to default to IAM_OWNER, fixed in
gibson#229) — the whole point of this guard is that "an admin role landed on
a service that never asked for one" is a build failure, not something anyone
has to notice in a diff.

WHAT IT CHECKS
  1. helm/gibson/values-baseline.yaml, platformBootstrap.oidcClients[]: no
     entry's `roles` contains an instance-administrator role
     (IAM_OWNER, IAM_OWNER_VIEWER, IAM_ADMIN_IMPERSONATOR,
     IAM_END_USER_IMPERSONATOR, SYSTEM_OWNER), unless the entry's `name` is
     in BOOTSTRAP_IDENTITIES. That allowlist is empty today: no OIDCClient
     in this chart is the bootstrap identity — the bootstrap identity
     (gibson-system-bot) is a SystemAPIUsers entry, not an OIDCClient, and
     is checked separately below.
  2. helm/testdata/golden/*.yaml, every rendered PlatformBootstrap: the same
     rule against spec.oidcClients[], so a template that hard-codes a role
     the values file does not carries the same failure.
  3. helm/gibson/values.yaml, zitadel...SystemAPIUsers: exactly one entry,
     named gibson-system-bot, holding exactly ["SYSTEM_OWNER"]. This is the
     one bootstrap identity ADR-0093 decision 5 allows to hold instance
     ownership. A second entry, a renamed entry, or an added role fails.
  4. helm/gibson-workloads/templates/fga-init/job.yaml: the hard-coded
     REQUIRED= allowlist that the fga-init Job seeds the `platform_operator`
     FGA relation for is exactly {gibson-iam-admin, gibson-tenant-operator}.
     This list is not templated from values (by design: it must survive a
     values-only edit), so it is checked as source text.

USAGE
  scripts/check-instance-admin-roles-scoped.py             check the tree
  scripts/check-instance-admin-roles-scoped.py --selftest  prove each rule can fail
Exit: 0 pass · 1 a rule failed · 2 self-test broke
"""
from __future__ import annotations

import glob
import os
import re
import sys
import tempfile

import yaml

INSTANCE_ADMIN_ROLES = {
    "IAM_OWNER",
    "IAM_OWNER_VIEWER",
    "IAM_ADMIN_IMPERSONATOR",
    "IAM_END_USER_IMPERSONATOR",
    "SYSTEM_OWNER",
}

# OIDCClient names allowed to hold an instance-administrator role. Empty
# today: the one bootstrap identity (gibson-system-bot) is a SystemAPIUsers
# entry, never an OIDCClient. Add a name here only for a new, deliberate
# bootstrap identity, never to silence this guard for an operational service.
BOOTSTRAP_OIDC_CLIENT_NAMES: set[str] = set()

EXPECTED_SYSTEM_API_USER = "gibson-system-bot"
EXPECTED_SYSTEM_API_ROLES = ["SYSTEM_OWNER"]

EXPECTED_PLATFORM_OPERATOR_SAS = {"gibson-iam-admin", "gibson-tenant-operator"}

FGA_JOB_PATH = "helm/gibson-workloads/templates/fga-init/job.yaml"
REQUIRED_LINE_RE = re.compile(r"""^\s*REQUIRED=(['"])(.*?)\1\s*$""", re.MULTILINE)


def _oidc_clients(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "oidcClients" and isinstance(value, list):
                yield value
            else:
                yield from _oidc_clients(value)
    elif isinstance(node, list):
        for item in node:
            yield from _oidc_clients(item)


def check_values(path: str) -> list[str]:
    problems: list[str] = []
    with open(path, encoding="utf-8") as f:
        values = yaml.safe_load(f)
    for clients in _oidc_clients(values):
        for i, client in enumerate(clients):
            if not isinstance(client, dict):
                continue
            name = client.get("name") or f"entry #{i}"
            if name in BOOTSTRAP_OIDC_CLIENT_NAMES:
                continue
            roles = client.get("roles") or []
            bad = sorted(set(roles) & INSTANCE_ADMIN_ROLES)
            if bad:
                problems.append(f"{path}: oidcClients[{name}] declares instance-administrator role(s) {bad}")
    return problems


def check_rendered(path: str) -> list[str]:
    problems: list[str] = []
    with open(path, encoding="utf-8") as f:
        for doc in yaml.safe_load_all(f):
            if not isinstance(doc, dict) or doc.get("kind") != "PlatformBootstrap":
                continue
            for i, client in enumerate(((doc.get("spec") or {}).get("oidcClients")) or []):
                if not isinstance(client, dict):
                    continue
                name = client.get("name") or f"entry #{i}"
                if name in BOOTSTRAP_OIDC_CLIENT_NAMES:
                    continue
                roles = client.get("roles") or []
                bad = sorted(set(roles) & INSTANCE_ADMIN_ROLES)
                if bad:
                    problems.append(f"{path}: rendered oidcClients[{name}] declares instance-administrator role(s) {bad}")
    return problems


def _system_api_users(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "SystemAPIUsers" and isinstance(value, list):
                yield value
            else:
                yield from _system_api_users(value)
    elif isinstance(node, list):
        for item in node:
            yield from _system_api_users(item)


def check_system_api_users(path: str) -> list[str]:
    problems: list[str] = []
    with open(path, encoding="utf-8") as f:
        values = yaml.safe_load(f)
    found_lists = list(_system_api_users(values))
    if not found_lists:
        problems.append(f"{path}: no SystemAPIUsers entry found (expected exactly one: {EXPECTED_SYSTEM_API_USER})")
        return problems
    for entries in found_lists:
        names = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            names.extend(entry.keys())
        if names != [EXPECTED_SYSTEM_API_USER]:
            problems.append(f"{path}: SystemAPIUsers names {names}, expected exactly [{EXPECTED_SYSTEM_API_USER}]")
            continue
        entry = entries[0][EXPECTED_SYSTEM_API_USER] or {}
        roles = ((entry.get("Memberships") or [{}])[0] or {}).get("Roles") or []
        if list(roles) != EXPECTED_SYSTEM_API_ROLES:
            problems.append(
                f"{path}: {EXPECTED_SYSTEM_API_USER} Memberships[0].Roles is {roles}, "
                f"expected exactly {EXPECTED_SYSTEM_API_ROLES}"
            )
    return problems


def check_fga_required_list(path: str) -> list[str]:
    if not os.path.exists(path):
        return [f"{path}: not found"]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = REQUIRED_LINE_RE.search(text)
    if not m:
        return [f"{path}: no REQUIRED='...' line found for the platform_operator seed allowlist"]
    got = set(m.group(2).split())
    if got != EXPECTED_PLATFORM_OPERATOR_SAS:
        extra = sorted(got - EXPECTED_PLATFORM_OPERATOR_SAS)
        missing = sorted(EXPECTED_PLATFORM_OPERATOR_SAS - got)
        detail = []
        if extra:
            detail.append(f"unexpected: {extra}")
        if missing:
            detail.append(f"missing: {missing}")
        return [f"{path}: platform_operator REQUIRED allowlist is {sorted(got)} ({', '.join(detail)})"]
    return []


def _values_fixture(clients=None, system_api_users=None) -> dict:
    d: dict = {"platformBootstrap": {}}
    if clients is not None:
        d["platformBootstrap"]["oidcClients"] = clients
    if system_api_users is not None:
        d["zitadel"] = {"config": {"SystemAPIUsers": system_api_users}}
    return d


def selftest() -> int:
    # Rule 1/2: oidcClients instance-admin roles.
    cases = {
        "clean": ([{"name": "gibson-daemon", "roles": ["IAM_ORG_MANAGER"]},
                   {"name": "gibson-dashboard", "roles": []}], True),
        "iam_owner_leaks_back_in": ([{"name": "gibson-daemon", "roles": ["IAM_OWNER"]}], False),
        "admin_impersonator": ([{"name": "gibson-tenant-operator", "roles": ["IAM_ADMIN_IMPERSONATOR"]}], False),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (clients, want_ok) in cases.items():
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(_values_fixture(clients=clients), f)
            got_ok = not check_values(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2

    rendered = {
        "rendered_clean": ([{"name": "gibson-daemon", "roles": ["IAM_ORG_MANAGER"]}], True),
        "rendered_iam_owner": ([{"name": "gibson-daemon", "roles": ["IAM_OWNER"]}], False),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (clients, want_ok) in rendered.items():
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump_all([{"kind": "ConfigMap"},
                                    {"kind": "PlatformBootstrap", "spec": {"oidcClients": clients}}], f)
            got_ok = not check_rendered(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: rendered fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2

    # Rule 3: SystemAPIUsers bootstrap identity.
    sysusers_cases = {
        "clean": ([{"gibson-system-bot": {"Memberships": [{"MemberType": "System", "Roles": ["SYSTEM_OWNER"]}]}}], True),
        "second_entry": ([
            {"gibson-system-bot": {"Memberships": [{"MemberType": "System", "Roles": ["SYSTEM_OWNER"]}]}},
            {"gibson-side-door": {"Memberships": [{"MemberType": "System", "Roles": ["SYSTEM_OWNER"]}]}},
        ], False),
        "extra_role": ([{"gibson-system-bot": {"Memberships": [{"MemberType": "System", "Roles": ["SYSTEM_OWNER", "IAM_OWNER"]}]}}], False),
        "renamed": ([{"gibson-renamed-bot": {"Memberships": [{"MemberType": "System", "Roles": ["SYSTEM_OWNER"]}]}}], False),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (entries, want_ok) in sysusers_cases.items():
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(_values_fixture(system_api_users=entries), f)
            got_ok = not check_system_api_users(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: SystemAPIUsers fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2

    # Rule 4: the fga-init Job's hard-coded platform_operator allowlist.
    fga_cases = {
        "clean": ("REQUIRED='gibson-iam-admin gibson-tenant-operator'\n", True),
        "widened": ("REQUIRED='gibson-iam-admin gibson-tenant-operator gibson-dashboard-service'\n", False),
        "renamed": ("REQUIRED='gibson-daemon gibson-tenant-operator'\n", False),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (text, want_ok) in fga_cases.items():
            p = os.path.join(d, name + ".sh")
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
            got_ok = not check_fga_required_list(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: fga-required fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2

    print("✅ self-test: an instance-administrator role outside the bootstrap identity is rejected, "
          "in values, in the render, in SystemAPIUsers, and in the platform_operator seed allowlist")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()

    problems: list[str] = []
    problems += check_values("helm/gibson/values-baseline.yaml")
    problems += check_system_api_users("helm/gibson/values.yaml")
    problems += check_fga_required_list(FGA_JOB_PATH)

    goldens = sorted(glob.glob("helm/testdata/golden/*.yaml"))
    if not goldens:
        problems.append("no rendered snapshots under helm/testdata/golden to check")
    for g in goldens:
        problems += check_rendered(g)

    if problems:
        print("❌ an instance-administrator role reached a service outside the bootstrap identity")
        for p in problems:
            print("   " + p)
        return 1
    print("✅ no OIDCClient outside the bootstrap identity declares an instance-administrator role; "
          "the bootstrap identity and the platform_operator seed allowlist are exactly as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
