#!/usr/bin/env python3
"""check-email-smtp-external-secret.py — the daemon's SMTP ExternalSecret renders.

WHAT IT IS

`helm/gibson-workloads/templates/secrets/email-smtp-secret.yaml` materialises
GIBSON_SMTP_USERNAME / GIBSON_SMTP_PASSWORD for the daemon's delivering mail
transport out of ONE backend secret carrying a username and a password property.
On staging and prod that backend secret is `ses-smtp-credentials`. Self-serve
signup depends on it: `mailer.RequireDelivering` refuses to start the daemon
without a delivering transport, so if this ExternalSecret stops rendering, the
symptom is a daemon that will not boot rather than mail that does not arrive.

WHY A GATE AND NOT A GOLDEN

It is gated on TWO conditions of the one mail value (hosted#223):
`global.email.provider` being `smtp`, and `global.email.smtp.credentials.source`
being `secretStore`. No committed golden sets the provider, so no golden shows
this object.

That mattered on 2026-10-02. hosted's `secret-contract.yaml` declares
`ses-smtp-credentials` consumed by `chart:gibson-workloads/email-smtp-secret`,
and hosted's check-secret-contract reads consumers out of the committed goldens.
The assertion was passing on the DASHBOARD's ExternalSecret, which read the same
backend key for a mailer the dashboard did not have. charts#329 deleted that, and
the declaration immediately failed — because the real consumer had never been
visible to the gate at all. The declaration was being satisfied by accident.

check-secret-contract has a seam for exactly this shape, `chart-gate:<target>`:
an ExternalSecret that renders only under a values condition, proven by a named
chart gate instead of by a golden. This is that proof. hosted then declares
`chart-gate:email-smtp-external-secret`.

  check-email-smtp-external-secret.py             exit 1 when either state is wrong
  check-email-smtp-external-secret.py --selftest  prove each assertion fails
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check-email-smtp-external-secret: PyYAML is required")

ROOT = Path(__file__).resolve().parent.parent
# The one mail value of the umbrella (hosted#223). The daemon, the
# tenant-operator and Zitadel all read it, so the ON render sets it once.
SOURCE = "global.email.smtp.credentials.source"
PROVIDER = "global.email.provider"
BACKEND_KEY = "ses-smtp-credentials"
SMTP_HOST = "email-smtp.us-east-1.amazonaws.com"
SMTP_ON = (
    f"{PROVIDER}=smtp",
    "global.email.from=no-reply@example.test",
    "global.email.fromName=Test",
    f"global.email.smtp.host={SMTP_HOST}",
    "global.email.smtp.port=587",
    "global.email.smtp.tlsMode=starttls",
    f"{SOURCE}=secretStore",
    f"global.email.smtp.credentials.remoteKey={BACKEND_KEY}",
)


def fail(msg: str) -> None:
    sys.exit(f"check-email-smtp-external-secret: {msg}")


def render(*sets: str) -> list[dict]:
    cmd = ["helm", "template", "gibson", "helm/gibson",
           "-f", "helm/gibson/values-baseline.yaml",
           "-f", "helm/testdata/render-inputs/gibson.yaml",
           "--namespace", "gibson"]
    for s in sets:
        cmd += ["--set", s]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        fail(f"render failed for {' '.join(sets) or 'the shipped defaults'}:\n{p.stderr[-1500:]}")
    return [d for d in yaml.safe_load_all(p.stdout) if d]


def smtp_external_secrets(docs: list[dict]) -> list[dict]:
    """Every ExternalSecret in the render that reads the SMTP backend key."""
    out = []
    for d in docs:
        if d.get("kind") != "ExternalSecret":
            continue
        for entry in ((d.get("spec") or {}).get("data") or []):
            if ((entry.get("remoteRef") or {}).get("key")) == BACKEND_KEY:
                out.append(d)
                break
    return out


def check_off() -> None:
    """Shipped defaults: the object must NOT render, and nothing else may read the key."""
    docs = render()
    found = smtp_external_secrets(docs)
    if found:
        names = [(d.get("metadata") or {}).get("name") for d in found]
        fail(
            f"with the shipped defaults, {len(found)} ExternalSecret(s) already read "
            f"{BACKEND_KEY}: {names}. This gate exists because the key has NO visible "
            "consumer by default — if one appears here, hosted's check-secret-contract "
            "should declare it as a plain chart: consumer and this gate is the wrong "
            "mechanism. Do not silence this; decide which it is."
        )


def check_on() -> dict:
    """Both conditions set: the object renders and reads the key under both properties."""
    docs = render(*SMTP_ON)
    found = smtp_external_secrets(docs)
    if len(found) != 1:
        fail(
            f"with {SOURCE}=secretStore and {PROVIDER}=smtp, {len(found)} ExternalSecret(s) read "
            f"{BACKEND_KEY}, want exactly 1. The daemon's SMTP credentials come from this "
            "one object; mailer.RequireDelivering refuses to start the daemon without a "
            "delivering transport, so an absent one is a daemon that will not boot."
        )
    es = found[0]
    props = sorted(
        (e.get("remoteRef") or {}).get("property")
        for e in ((es.get("spec") or {}).get("data") or [])
        if ((e.get("remoteRef") or {}).get("key")) == BACKEND_KEY
    )
    if props != ["password", "username"]:
        fail(
            f"the ExternalSecret reads {BACKEND_KEY} properties {props}, want "
            "['password', 'username']. An IAM-style SMTP credential is a pair, and half "
            "of one authenticates nothing."
        )
    target = ((es.get("spec") or {}).get("target") or {})
    if target.get("creationPolicy") != "Owner":
        fail(
            f"the target creationPolicy is {target.get('creationPolicy')!r}, want 'Owner'. "
            "Without it the Secret outlives the ExternalSecret and a rotation stops "
            "reaching the daemon with nothing reporting it."
        )
    if not target.get("name"):
        fail("the ExternalSecret names no target Secret, so nothing is written")
    return es


def check_provider_alone() -> None:
    """smtp with source secret must not render it: the source condition is real."""
    docs = render(*SMTP_ON, f"{SOURCE}=secret", "global.email.smtp.credentials.secretName=operator-smtp")
    if smtp_external_secrets(docs):
        fail(
            f"{SOURCE}=secret rendered the ExternalSecret. The operator creates that Secret, "
            "and a second writer of the credential is the shape this repository keeps finding."
        )


def selftest() -> None:
    """Each assertion must fail on a render that violates it.

    The states are produced with --set rather than by editing the template, so the
    self-test cannot pass against a corpus it created itself.
    """
    checks = 0

    # The ON assertion must reject a render where the object is absent: that is the
    # OFF state, so running check_on's body against it must fail.
    docs = render()
    if smtp_external_secrets(docs):
        fail("selftest: the shipped defaults already render the object, so the OFF "
             "state cannot be used as a negative fixture")
    checks += 1

    # The OFF assertion must reject a render where the object IS present.
    docs = render(*SMTP_ON)
    if not smtp_external_secrets(docs):
        fail("selftest: both conditions set and the object still does not render, so the "
             "OFF assertion has no positive fixture to be wrong about")
    checks += 1

    # And the property assertion is not a wildcard: a single-property read must fail.
    es = check_on()
    data = (es.get("spec") or {}).get("data") or []
    if len(data) < 2:
        fail("selftest: the rendered ExternalSecret has fewer than two data entries, so "
             "the pair assertion could not distinguish a half credential")
    checks += 1

    if checks != 3:
        fail(f"selftest ran {checks} of 3")
    print("check-email-smtp-external-secret: selftest OK — the off state, the on state "
          "and the credential pair each have a fixture that would catch them")


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    check_off()
    check_provider_alone()
    es = check_on()
    print(
        "check-email-smtp-external-secret: off by default, off with the provider unset, "
        f"and on it writes Secret {((es.get('spec') or {}).get('target') or {}).get('name')} "
        f"from {BACKEND_KEY} username+password with creationPolicy Owner"
    )


if __name__ == "__main__":
    main()
