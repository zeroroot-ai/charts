#!/usr/bin/env python3
"""check-no-owner-password-secret.py — no template may ask a bootstrap binary
to write a password anywhere, for the Platform owner or the first tenant's
Owner (ADR-0093 decisions 6/8, hosted#201/#202).

Before this guard, the first-admin Job's rendered command invoked
`bootstrap-tenant-owner -generate-password -credential-secret ...`, which
generated a password and wrote it, in plain text, to the `gibson-first-admin`
Secret — readable by anyone who can read that one Secret, forever, until an
operator rotated it by hand. Both the Platform owner and the first tenant's
Owner are now created with NO password: Zitadel's own invite-code flow
delivers a one-time setup link instead (emailed, or written to an offline
Secret that carries a LINK, never a credential).

This guard is keyed by content, never by line number: any of the literal
CLI flags or field names below, anywhere under helm/*/templates, fails the
build. A workload that legitimately needs to mint or store an unrelated
INFRASTRUCTURE password (Postgres, Redis, the OpenBao seeder) does not use
any of these names and is untouched by this guard.

  check-no-owner-password-secret.py             exit 1 on a hit, 0 when clean
  check-no-owner-password-secret.py --selftest  prove a fixture fails and the tree passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Every one of these strings is specific to the owner-bootstrap password path
# this guard closes. None of them has a legitimate reason to appear in any
# chart template ever again (ADR-0027: hard cutover, no back-compat flag).
LITERALS = [
    re.compile(r"-generate-password\b"),
    re.compile(r"-credential-secret\b"),
    re.compile(r"-credential-namespace\b"),
    re.compile(r"\binitialPassword\b"),
]


def scan(root: str) -> list[str]:
    hits = []
    for dirpath, _, files in os.walk(os.path.join(root, "helm")):
        if "/templates" not in dirpath.replace(os.sep, "/"):
            continue
        for f in files:
            if not f.endswith((".yaml", ".yml", ".tpl")):
                continue
            p = os.path.join(dirpath, f)
            for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                for pat in LITERALS:
                    if pat.search(line):
                        hits.append(f"{os.path.relpath(p, root)}:{n}: {line.strip()}")
    return hits


def main() -> int:
    if "--selftest" in sys.argv:
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "helm", "x", "templates")
            os.makedirs(d)
            open(os.path.join(d, "job.yaml"), "w").write(
                "command: [bootstrap-tenant-owner, -generate-password, -credential-secret, s]\n"
            )
            open(os.path.join(d, "ok.yaml"), "w").write(
                "command: [bootstrap-tenant-owner, -offline-setup, -setup-secret, s]\n"
            )
            hits = scan(t)
            if len(hits) != 2 or any("job.yaml" not in h for h in hits):
                print(f"GUARD BROKEN: expected two hits in the one fixture file, got {hits}", file=sys.stderr)
                return 2
        if scan(ROOT):
            print("GUARD BROKEN: the tree carries a hit, see the plain run", file=sys.stderr)
            return 2
        print("✅ self-test: a password-generating flag in a template is rejected, the tree is clean")
        return 0
    hits = scan(ROOT)
    if hits:
        print("❌ a template asks a bootstrap binary to write a password (ADR-0093: no password is ever generated or stored — reuse the setup-link mechanism instead):")
        for h in hits:
            print("   " + h)
        return 1
    print("✅ no template asks a bootstrap binary to write a password; the Platform owner and the first tenant's Owner are both set up by a one-time setup link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
