#!/usr/bin/env python3
"""check-zitadel-lockstep.py — the ZITADEL server and the login fork move together.

WHY THIS EXISTS
The login app (zeroroot-ai/zitadel-login, a thin fork of upstream apps/login)
speaks the server's API version, so `zitadel.image.tag` and the login image
pin must name the same upstream release. Until 2026-09-07 a README checklist
was the only thing that said so, and the fork moved to v4.17.3 while the chart
stayed on v4.14.0 with the login pinned at a main-build ref
(zeroroot-ai/charts#13, zeroroot-ai/.github#20).

WHAT IT CHECKS, on helm/gibson/values.yaml
  zitadel.image.tag        must be  v<major>.<minor>.<patch>
  zitadel.login.image.tag  must be  v<major>.<minor>.<patch>@sha256:<64 hex>
  and the login tag before `@` must equal the server tag.

The pin keeps the tag next to the digest on purpose: the digest is what runs
(deploy#789 digest-pin-check), the tag is what this guard and the org drift
detector read, and `bump-image-digest.py --resolve-all` re-resolves the digest
from that tag at chart publish.

USAGE
  scripts/check-zitadel-lockstep.py            check helm/gibson/values.yaml
  scripts/check-zitadel-lockstep.py FILE       check another values file
  scripts/check-zitadel-lockstep.py --selftest prove it fails on a one-line move
Exit: 0 in lockstep · 1 out of lockstep · 2 self-test broke
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import yaml

SERVER = re.compile(r"^v\d+\.\d+\.\d+$")
LOGIN = re.compile(r"^(v\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}$")


def check(path: str) -> list[str]:
    with open(path, encoding="utf-8") as f:
        values = yaml.safe_load(f)
    z = values.get("zitadel") or {}
    server = str(((z.get("image") or {}).get("tag")) or "")
    login = str((((z.get("login") or {}).get("image") or {}).get("tag")) or "")
    problems: list[str] = []
    if not SERVER.match(server):
        problems.append(f"zitadel.image.tag is {server!r}; expected v<major>.<minor>.<patch>")
    m = LOGIN.match(login)
    if not m:
        problems.append(
            f"zitadel.login.image.tag is {login!r}; expected v<major>.<minor>.<patch>@sha256:<digest> "
            "(the fork publishes v<upstream> on every main build)"
        )
    elif SERVER.match(server) and m.group(1) != server:
        problems.append(
            f"zitadel.login.image.tag names {m.group(1)} but zitadel.image.tag is {server}; "
            "the login fork and the server move together (rebase the fork first, see its README)"
        )
    return problems


def selftest() -> int:
    digest = "@sha256:" + "0" * 64
    cases = {
        "ok": ("v4.17.3", "v4.17.3" + digest, True),
        "server_moved_alone": ("v4.18.0", "v4.17.3" + digest, False),
        "login_moved_alone": ("v4.17.3", "v4.18.0" + digest, False),
        "login_without_digest": ("v4.17.3", "v4.17.3", False),
        "login_main_build_ref": ("v4.17.3", "sha-f41ce75" + digest, False),
    }
    with tempfile.TemporaryDirectory() as d:
        for name, (server, login, want_ok) in cases.items():
            p = os.path.join(d, name + ".yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump({"zitadel": {"image": {"tag": server}, "login": {"image": {"tag": login}}}}, f)
            got_ok = not check(p)
            if got_ok != want_ok:
                print(f"GUARD BROKEN: fixture {name} expected {'pass' if want_ok else 'fail'}", file=sys.stderr)
                return 2
    print("✅ self-test: a one-line move of either ZITADEL tag is rejected")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    path = argv[1] if len(argv) > 1 else "helm/gibson/values.yaml"
    problems = check(path)
    if problems:
        print(f"❌ {path}: ZITADEL server and login are out of lockstep")
        for p in problems:
            print("   " + p)
        return 1
    print(f"✅ {path}: ZITADEL server and login pin name the same release")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
