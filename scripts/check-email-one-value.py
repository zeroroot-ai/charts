#!/usr/bin/env python3
"""check-email-one-value.py: the install has one mail value, global.email (hosted#223).

The daemon, the tenant-operator and Zitadel (the PlatformBootstrap) read
global.email. The three old blocks were deleted with no fallback (ADR-0027):

  gibson-workloads.gibson.email
  gibson-operators.tenantOperator.smtp
  gibson-operators.platformBootstrap.zitadel.smtp

The guard renders the baseline profile and fails when:

  - a render that sets any old key, even to an empty value, does not fail
    with a message that names global.email,
  - an smtp render gives the three consumers a different host, port, sender
    or credential Secret.

  check-email-one-value.py             exit 1 on a finding
  check-email-one-value.py --selftest  prove the agreement check fails on a drift
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD = (
    "gibson-workloads.gibson.email.provider=smtp",
    "gibson-workloads.gibson.email.from=",
    "gibson-workloads.gibson.email.smtp.host=smtp.example.test",
    "gibson-operators.tenantOperator.smtp.host=smtp.example.test",
    "gibson-operators.tenantOperator.smtp.tlsMode=starttls",
    "gibson-operators.platformBootstrap.zitadel.smtp.host=smtp.example.test",
    "gibson-operators.platformBootstrap.zitadel.smtp.fromName=",
)
SMTP = ("global.email.provider=smtp", "global.email.from=no-reply@example.test",
        "global.email.fromName=Test", "global.email.smtp.host=smtp.example.test",
        "global.email.smtp.port=2587", "global.email.smtp.tlsMode=starttls",
        "global.email.smtp.credentials.source=secretStore",
        "global.email.smtp.credentials.remoteKey=ses-smtp-credentials")


def helm(sets) -> subprocess.CompletedProcess:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"]
    for s in sets:
        args += ["--set", s]
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def env_of(docs, kind, name_suffix, var):
    for d in docs:
        if d.get("kind") == kind and d["metadata"]["name"].endswith(name_suffix):
            for c in d["spec"]["template"]["spec"]["containers"]:
                for e in c.get("env") or []:
                    if e.get("name") == var:
                        if "value" in e:
                            return str(e["value"])
                        return "secret:" + e["valueFrom"]["secretKeyRef"]["name"]
    return None


def consumers(docs) -> dict:
    """What each consumer sends with: host, port, from, credential Secret."""
    pb = next((d for d in docs if d.get("kind") == "PlatformBootstrap"), {})
    z = ((pb.get("spec") or {}).get("zitadel") or {}).get("smtp") or {}
    return {
        "daemon": (env_of(docs, "StatefulSet", "gibson-workloads", "GIBSON_SMTP_HOST"),
                   env_of(docs, "StatefulSet", "gibson-workloads", "GIBSON_SMTP_PORT"),
                   env_of(docs, "StatefulSet", "gibson-workloads", "GIBSON_EMAIL_FROM"),
                   env_of(docs, "StatefulSet", "gibson-workloads", "GIBSON_SMTP_PASSWORD")),
        "tenant-operator": (env_of(docs, "Deployment", "tenant-operator", "SMTP_HOST"),
                            env_of(docs, "Deployment", "tenant-operator", "SMTP_PORT"),
                            env_of(docs, "Deployment", "tenant-operator", "SMTP_FROM"),
                            env_of(docs, "Deployment", "tenant-operator", "SMTP_PASSWORD")),
        "zitadel": (z.get("host"), None if z.get("port") is None else str(z.get("port")),
                    z.get("fromAddress"),
                    "secret:" + z["passwordSecretRef"]["name"] if z.get("passwordSecretRef") else None),
    }


def judge(got: dict) -> list[str]:
    vals = set(got.values())
    if len(vals) != 1 or None in next(iter(vals)):
        return [f"the three consumers do not send with one transport: {got}"]
    return []


def selftest() -> int:
    t = ("h", "587", "f", "secret:s")
    if judge({"daemon": t, "tenant-operator": t, "zitadel": t}):
        print("SELFTEST FAIL: three equal consumers must pass")
        return 1
    for what, got in (("another host", {"daemon": t, "tenant-operator": ("x",) + t[1:], "zitadel": t}),
                      ("another credential", {"daemon": t, "tenant-operator": t, "zitadel": t[:3] + ("secret:o",)}),
                      ("a missing consumer", {"daemon": t, "tenant-operator": t, "zitadel": (None, None, None, None)})):
        if not judge(got):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    print("  ✓ selftest: another host, another credential and a missing consumer fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for key in OLD:
        r = helm([key])
        if r.returncode == 0:
            bad.append(f"{key}: the render passed, and an old mail key must fail it")
        elif "global.email" not in r.stderr:
            bad.append(f"{key}: the render failed without naming global.email: {r.stderr.strip()[-200:]}")
    r = helm(list(SMTP) + ["global.platformOwner.offlineSetup=false"])
    if r.returncode != 0:
        bad.append(f"the smtp render failed: {r.stderr.strip()[-300:]}")
    else:
        bad += judge(consumers([d for d in yaml.safe_load_all(r.stdout) if isinstance(d, dict)]))
    if bad:
        print("the install does not have one mail value (hosted#223):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ email-one-value: {len(OLD)} old mail keys each fail the render, and the daemon, the tenant-operator and Zitadel send with one transport")
    return 0


if __name__ == "__main__":
    sys.exit(main())
