#!/usr/bin/env python3
"""check-smtp-tls-mode.py — the tenant operator's SMTP transport is a named mode.

The mode is global.email.smtp.tlsMode, part of the one mail value (hosted#223).

WHY THE TYPE EXISTS

It used to be a boolean, `tenantOperator.smtp.tls` (now deleted), and the boolean read
backwards: `false` meant STARTTLS, not plaintext, because the operator's
`UseTLS=false` branch went through `net/smtp.SendMail`, which issues STARTTLS and
refuses PlainAuth over an unencrypted link. Both branches encrypted. Every
environment therefore set `tls: false` against a TLS-only relay, and setting it
to `true` on 587 produced

    WelcomeEmailFailed: smtp tls dial: tls: first record does not look like a
    TLS handshake

which is `tls.Dial` meeting a port that expects EHLO (measured on staging).
gibson#561 replaced it with three named modes and gibson#574 added the third.

    starttls   dial plaintext, then STARTTLS. Port 587. The default.
    implicit   TLS from the first byte. Port 465.
    plaintext  no encryption, ever. For a sink that offers no STARTTLS at all.

`plaintext` is not a weakening: `starttls` is MANDATORY STARTTLS and fails
against a sink that does not offer it, because an upgrade that silently does not
happen is the trap the type exists to remove. The in-cluster mailpit answers a
TLS open with "500 5.5.2 Syntax error" and offers no STARTTLS, so before #574
there was no mode that could reach it — the signup and identity exit tests could
not deliver mail, and a green run of either was green because nothing asserted
the delivery.

WHAT THIS ASSERTS

The render is where a typo must die, because the alternative is a pod that
crash-loops on an operator-side refusal. So: each of the three modes renders its
own SMTP_TLS_MODE, an unknown one fails the render naming all three, and
`plaintext` with a credential fails — that pair can never deliver, since net/smtp
refuses PlainAuth over a cleartext non-localhost link, and failing here says why
a deploy earlier than the operator would.

  check-smtp-tls-mode.py             exit 1 when any of the five is wrong
  check-smtp-tls-mode.py --selftest  prove each assertion has a fixture
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check-smtp-tls-mode: PyYAML is required")

ROOT = Path(__file__).resolve().parent.parent
KEY = "global.email.smtp.tlsMode"
SOURCE = "global.email.smtp.credentials.source"
# A relay of the one mail value. Each render below adds the mode and the
# credential source.
SMTP = ("global.email.provider=smtp", "global.email.from=no-reply@example.test",
        "global.email.fromName=Test", "global.email.smtp.host=smtp.example.test",
        "global.email.smtp.port=587")
CRED = (f"{SOURCE}=secretStore", "global.email.smtp.credentials.remoteKey=ses-smtp-credentials")
ANON = (f"{SOURCE}=none",)
MODES = ("starttls", "implicit", "plaintext")
ENV = "SMTP_TLS_MODE"


def fail(msg: str) -> None:
    sys.exit(f"check-smtp-tls-mode: {msg}")


def render(*sets: str, smtp: bool = True) -> tuple[int, str, str]:
    cmd = ["helm", "template", "gibson", "helm/gibson",
           "-f", "helm/gibson/values-baseline.yaml",
           "-f", "helm/testdata/render-inputs/gibson.yaml",
           "--namespace", "gibson"]
    for s in (SMTP if smtp else ()) + sets:
        cmd += ["--set", s]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def mode_env(stdout: str) -> list[str]:
    """Every value SMTP_TLS_MODE is given across the whole render."""
    out = []
    for doc in yaml.safe_load_all(stdout):
        if not doc:
            continue
        for container in _containers(doc):
            for e in container.get("env") or []:
                if e.get("name") == ENV:
                    out.append(e.get("value"))
    return out


def _containers(doc: dict) -> list[dict]:
    spec = (((doc.get("spec") or {}).get("template") or {}).get("spec") or {})
    return list(spec.get("containers") or []) + list(spec.get("initContainers") or [])


def check_mode(mode: str) -> None:
    rc, out, err = render(f"{KEY}={mode}", *ANON)
    if rc != 0:
        fail(f"{mode!r} is a valid mode and the render refused it:\n{err[-800:]}")
    got = mode_env(out)
    if got != [mode]:
        fail(
            f"with tlsMode={mode!r} the render sets {ENV} to {got}, want exactly [{mode!r}]. "
            f"One operator reads this; more than one value or none means the env block and "
            f"the mode have come apart."
        )


def check_unknown() -> None:
    rc, _, err = render(f"{KEY}=tls", *ANON)
    if rc == 0:
        fail(
            "tlsMode=tls rendered successfully. An unknown mode must fail the RENDER: the "
            "operator refuses it at startup, so letting it through turns a typo into a "
            "crash-looping pod instead of a failed deploy."
        )
    for m in MODES:
        if m not in err:
            fail(
                f"the refusal for an unknown mode does not name {m!r}. With three modes a "
                f"message that lists fewer is a coin toss that reads like a diagnosis."
            )


def check_plaintext_with_username() -> None:
    rc, _, err = render(f"{KEY}=plaintext", *CRED)
    if rc == 0:
        fail(
            "tlsMode=plaintext with a credential rendered successfully. The credential would "
            "cross the network in the clear, and net/smtp refuses PlainAuth over a cleartext "
            "non-localhost link, so the pair can never deliver mail — it must fail the render."
        )
    if "credential" not in err:
        fail(f"the refusal does not name the credential it is refusing: {err[-300:]}")
    if not any(m in err for m in ("starttls", "implicit")):
        fail("the refusal names no encrypted alternative, so it says what is wrong and not "
             f"what to do: {err[-300:]}")


def check_plaintext_without_username() -> None:
    """The refusal is about the PAIR, not about plaintext."""
    rc, out, err = render(f"{KEY}=plaintext", *ANON)
    if rc != 0:
        fail(
            "tlsMode=plaintext with no username was refused. plaintext is a supported mode "
            "for an anonymous sink; refusing it outright leaves the in-cluster mailpit with "
            f"no reachable transport, which is what gibson#574 fixed:\n{err[-500:]}"
        )
    if mode_env(out) != ["plaintext"]:
        fail(f"plaintext alone did not reach the pod: {ENV}={mode_env(out)}")


def main() -> None:
    selftest = "--selftest" in sys.argv
    for mode in MODES:
        check_mode(mode)
    check_unknown()
    check_plaintext_without_username()
    check_plaintext_with_username()

    if selftest:
        # Each assertion needs a render that would trip it, or it is decoration.
        # These are produced with --set, never by editing the template, so the
        # self-test cannot pass against a corpus it wrote itself.
        rc, _, _ = render(f"{KEY}=STARTTLS", *ANON)
        if rc == 0:
            fail("selftest: the enum is case-insensitive, so the unknown-mode assertion "
                 "cannot distinguish a typo")
        rc, out, _ = render(smtp=False)
        if mode_env(out) != ["plaintext"]:
            fail(f"selftest: the shipped default (provider log, the mailpit sink) is "
                 f"{mode_env(out)}, not ['plaintext'], so the per-mode assertion has no baseline")
        rc, _, err = render(f"{KEY}=implicit", *CRED)
        if rc != 0:
            fail(f"selftest: implicit WITH a username was refused, so the pair refusal is not "
                 f"specific to plaintext: {err[-300:]}")
        print("check-smtp-tls-mode: selftest OK — a cased variant, the shipped default and "
              "an encrypted mode with a username each behave differently")

    print("check-smtp-tls-mode: starttls, implicit and plaintext each reach the pod; an "
          "unknown mode fails the render naming all three; plaintext with a username fails "
          "and plaintext alone does not")


if __name__ == "__main__":
    main()
