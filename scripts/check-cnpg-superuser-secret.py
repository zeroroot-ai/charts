#!/usr/bin/env python3
"""check-cnpg-superuser-secret.py: one Secret sets the password of the role `postgres`.

CNPG's instance manager writes two Secrets into the database on every
reconcile of the primary (internal/management/controller/instance_controller.go,
refreshCredentialsFromSecret, v1.30.0), in this order:

  1. the superuser Secret, to the role `postgres`, when enableSuperuserAccess
     is true (spec.superuserSecret, default <cluster>-superuser);
  2. the owner Secret, to the owner of the application database
     (bootstrap.<initdb|recovery|pg_basebackup>.secret, default <cluster>-app).

When the owner is `postgres` and the two Secrets differ, the second write
wins and the superuser Secret no longer logs in. Every client that reads the
superuser Secret (the postgres-setup Jobs, the PlatformBootstrap
postgresBundle) then fails with "password authentication failed for user
postgres". This guard renders every profile, with and without a recovery
backup, and fails on a Cluster where two Secrets claim the role `postgres`.

  check-cnpg-superuser-secret.py             exit 1 on a violation
  check-cnpg-superuser-secret.py --selftest  prove the conflicting shape fails and the render passes
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILES = [[], ["values-eks.yaml"], ["values-guest.yaml"]]
# A render that takes the RESTORE-ON-BRINGUP branch (bootstrap.recovery).
RECOVERY = ["--set", "platformPostgres.recovery.backupID=20260101T000000",
            "--set", "platformPostgres.recovery.serverName=platform-postgres-old"]
BOOTSTRAP_KINDS = ("initdb", "recovery", "pg_basebackup")


def render(extra: list[str], sets: list[str]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"]
    for f in extra:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args + sets, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def violations(docs: list[dict]) -> list[str]:
    out = []
    for d in docs:
        if d.get("kind") != "Cluster" or not str(d.get("apiVersion", "")).startswith("postgresql.cnpg.io/"):
            continue
        name = d["metadata"]["name"]
        spec = d.get("spec") or {}
        if not spec.get("enableSuperuserAccess"):
            continue
        superuser = (spec.get("superuserSecret") or {}).get("name") or f"{name}-superuser"
        boot = spec.get("bootstrap") or {}
        for kind in BOOTSTRAP_KINDS:
            b = boot.get(kind)
            if b is None:
                continue
            owner = b.get("owner") or "app"
            if owner != "postgres":
                continue
            owner_secret = (b.get("secret") or {}).get("name") or f"{name}-app"
            if owner_secret != superuser:
                out.append(f"Cluster/{name}: bootstrap.{kind}.owner is postgres, and its owner Secret "
                           f"{owner_secret} differs from the superuser Secret {superuser}. CNPG writes both to "
                           "the role postgres, the owner Secret last, so the superuser Secret stops logging "
                           f"in. Set bootstrap.{kind}.secret.name to {superuser}.")
    return out


FIXTURE = """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: pg}
spec:
  enableSuperuserAccess: true
  bootstrap:
    initdb: {database: postgres, owner: postgres}
"""

FIXTURES_THAT_MUST_PASS = {
    "the owner Secret is the superuser Secret": """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: pg}
spec:
  enableSuperuserAccess: true
  bootstrap:
    initdb: {database: postgres, owner: postgres, secret: {name: pg-superuser}}
""",
    "the owner is another role": """
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: pg}
spec:
  enableSuperuserAccess: true
  bootstrap:
    recovery: {source: old}
""",
}


def renders() -> list[tuple[str, list[dict]]]:
    out = []
    for prof in PROFILES:
        label = prof[0] if prof else "baseline"
        out.append((label, render(prof, [])))
        out.append((f"{label}+recovery", render(prof, RECOVERY)))
    return out


def selftest() -> int:
    if not violations([yaml.safe_load(FIXTURE)]):
        print("SELFTEST FAIL: an initdb owner postgres with the default app Secret must fail")
        return 1
    for what, text in FIXTURES_THAT_MUST_PASS.items():
        got = violations([yaml.safe_load(text)])
        if got:
            print(f"SELFTEST FAIL: {what}: {got}")
            return 1
    seen = 0
    for label, docs in renders():
        seen += sum(1 for d in docs if d.get("kind") == "Cluster")
        got = violations(docs)
        if got:
            print(f"SELFTEST FAIL: the render ({label}) violates the rule:\n  " + "\n  ".join(got))
            return 1
    if seen == 0:
        print("SELFTEST FAIL: no render produced a CNPG Cluster, so the guard checked nothing")
        return 1
    print("OK: two Secrets for the role postgres fail, the near misses pass, and every render has one")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = []
    for label, docs in renders():
        got += [f"({label}) {x}" for x in violations(docs)]
    if got:
        print("❌ two Secrets set the password of the role postgres:\n  " + "\n  ".join(got))
        return 1
    print("✓ cnpg-superuser-secret: one Secret sets the password of the role postgres, in every render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
