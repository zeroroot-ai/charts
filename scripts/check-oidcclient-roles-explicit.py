#!/usr/bin/env python3
"""check-oidcclient-roles-explicit.py — every OIDCClient declares its roles (hosted#191).

WHY THIS EXISTS
gibson's platform-operator used to grant a machine user ["IAM_OWNER"] — full
ownership of the Zitadel instance — whenever an OIDCClient's `roles` field was
left empty. Three service accounts inherited that default this way, including
the internet-facing dashboard's own identity. gibson's reconciler no longer has
that default (an empty list means no role, full stop), but the chart's
authoring surface still lets someone add a new `oidcClients` entry, or delete
a `roles:` line from an existing one, without noticing the field is unset —
that mistake now ships with silently NO role rather than a silently WRONG one,
which is safer but still not what anyone meant to write. This guard makes
"I forgot to think about this identity's roles" a build failure instead of a
silent gap, on every entry, every time.

WHAT IT CHECKS, on helm/gibson/values-baseline.yaml
  platformBootstrap.oidcClients[] — every entry must have a literal `roles`
  key present (any value, including an empty list). A missing key is the
  failure; an empty list is a legitimate, deliberate answer ("this identity
  gets no Zitadel role") and passes.

USAGE
  scripts/check-oidcclient-roles-explicit.py            check helm/gibson/values-baseline.yaml
  scripts/check-oidcclient-roles-explicit.py FILE        check another values file
  scripts/check-oidcclient-roles-explicit.py --selftest  prove it fails on a missing key
Exit: 0 every client declares roles · 1 a client is missing the key · 2 self-test broke
"""
from __future__ import annotations

import os
import sys
import tempfile

import yaml


def check(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        values = yaml.safe_load(f)
    clients = (((values.get("platformBootstrap") or {}).get("oidcClients")) or [])
    problems: list[str] = []
    for i, client in enumerate(clients):
        name = client.get("name") if isinstance(client, dict) else None
        label = name or f"entry #{i}"
        if not isinstance(client, dict) or "roles" not in client:
            problems.append(
                f"oidcClients[{label}] has no `roles` key — declare it explicitly "
                "(use `roles: []` for none, never omit the field)"
            )
    return problems


def _fixture(clients: list[dict]) -> dict:
    return {"platformBootstrap": {"oidcClients": clients}}


def selftest() -> int:
    cases = {
        "all_declared": (
            [
                {"name": "gibson-daemon", "applicationType": "MACHINE_USER", "roles": ["IAM_OWNER"]},
                {"name": "gibson-dashboard-service", "applicationType": "MACHINE_USER", "roles": []},
                {"name": "gibson-dashboard", "roles": []},
            ],
            True,
        ),
        "one_missing_roles_key": (
            [
                {"name": "gibson-daemon", "applicationType": "MACHINE_USER", "roles": ["IAM_OWNER"]},
                {"name": "gibson-dashboard-service", "applicationType": "MACHINE_USER"},
            ],
            False,
        ),
        "empty_list_is_fine": (
            [{"name": "gibson-native-login", "applicationType": "NATIVE", "roles": []}],
            True,
        ),
        "no_clients_declared": ([], True),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (clients, want_ok) in cases.items():
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump(_fixture(clients), f)
            got_ok = not check(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2
    print("✅ self-test: an OIDCClient missing `roles` is rejected")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    path = argv[1] if len(argv) > 1 else "helm/gibson/values-baseline.yaml"
    problems = check(path)
    if problems:
        print(f"❌ {path}: not every OIDCClient declares its roles")
        for p in problems:
            print("   " + p)
        return 1
    print(f"✅ {path}: every OIDCClient declares roles explicitly")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
