#!/usr/bin/env python3
"""check-login-brand.py — the login-branding Job applies the brand it declares,
and re-applies it when the brand changes.

WHY THIS GUARD EXISTS
---------------------
On 2026-09-14 the staging login page was still serving the retired violet
brand: primaryColor #894fee on #0e0f15, THEME_MODE_DARK, and the violet CRT
mark. ADR-0064 replaced all of it with one light "acid concrete" brand months
earlier. Two separate reasons it never arrived, and the second is the one that
would have kept arriving:

  1. files/branding/ still held the old palette. A value edit fixes that once.
  2. The Job uploaded marks only when the policy's dark-logo slot was EMPTY.
     Any instance branded once could never be re-branded: editing an SVG
     changed nothing, and the documented remedy was a hand-run DELETE against
     the admin API. A declarative platform cannot have a rebrand that needs a
     human to run a DELETE first.

So the marks are keyed by CONTENT now, and this proves it: a stale mark is
replaced, an identical one is left alone.

WHAT IT MEASURES
----------------
The Job script, EXACTLY as the chart renders it, driven against a stub Zitadel
and a stub kubectl. Three runs, one assertion each:

  1. bare instance      — every slot uploaded, policy applied, marks verified
  2. stale marks        — served bytes differ, so every slot is replaced
  3. steady state       — served bytes match, so nothing is uploaded and the
                          Job reports no change (the property that keeps a
                          no-op Sync from rolling the login pod)

It also asserts the declared palette is the brand's: a policy that drifts back
to a hex the brand does not define fails here, not on a screenshot.

Usage: scripts/check-login-brand.py            # the guard
       scripts/check-login-brand.py --selftest # prove it can fail
Exit:  0 the Job applies the brand · 1 it does not · 2 could not run
"""
from __future__ import annotations

import http.server
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from hashlib import sha256
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BRANDING = ROOT / "helm/gibson/files/branding"
GOLDEN = ROOT / "helm/testdata/golden/values-vanilla.bare.yaml"
JOB_NAME = "zitadel-login-branding"

# @zeroroot-ai/brand, the one light brand (ADR-0064). Kept here so a policy
# that drifts back to a colour the brand does not define fails the guard.
# See files/branding/README.md for the oklch each hex was converted from.
BRAND_HEX = {
    "primaryColor": "#346000",      # --highlight, the green that carries text
    "backgroundColor": "#e3e3df",   # --background, the concrete ground
    "fontColor": "#0e0d09",         # --foreground, ink
    "warnColor": "#c70009",         # --destructive
}


class Zitadel(http.server.BaseHTTPRequestHandler):
    """Enough of the admin + assets API for the Job to run end to end."""

    # Per-server state, set by serve().
    assets: dict[str, bytes]
    policy: dict
    uploads: list[str]
    deletes: list[str]
    seq: int

    def log_message(self, *_args):  # keep the harness quiet
        pass

    def _send(self, code: int, body: bytes = b"{}", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        if self.path == "/debug/ready":
            return self._send(200)
        if self.path == "/admin/v1/policies/label":
            return self._send(200, json.dumps({"policy": self.policy}).encode())
        if self.path.startswith("/assets/v1/"):
            blob = self.assets.get(self.path)
            if blob is None:
                return self._send(404, b'{"error":"no such asset"}')
            return self._send(200, blob, "image/svg+xml")
        return self._send(404, b'{"error":"unrouted"}')

    def do_PUT(self):
        if self.path != "/admin/v1/policies/label":
            return self._send(404, b'{"error":"unrouted"}')
        want = json.loads(self._read() or b"{}")
        if all(self.policy.get(k) == v for k, v in want.items()):
            # Zitadel answers 400 "Object Details has not been changed" on a
            # second write with no diff. The Job reads that as its no-op.
            return self._send(400, b'{"message":"Object Details has not been changed"}')
        self.policy.update(want)
        return self._send(200)

    def do_DELETE(self):
        slot = {
            "/admin/v1/policies/label/logo": "logoUrl",
            "/admin/v1/policies/label/logo_dark": "logoUrlDark",
            "/admin/v1/policies/label/icon": "iconUrl",
            "/admin/v1/policies/label/icon_dark": "iconUrlDark",
        }.get(self.path)
        if slot is None:
            return self._send(404, b'{"error":"unrouted"}')
        self.deletes.append(self.path)
        url = self.policy.pop(slot, "")
        self.assets.pop("/" + url.split("/", 3)[-1] if url else "", None)
        return self._send(200)

    def do_POST(self):
        if self.path == "/admin/v1/policies/label/_activate":
            self._read()
            return self._send(200)
        slot = {
            "/assets/v1/instance/policy/label/logo": ("logoUrl", "logo"),
            "/assets/v1/instance/policy/label/logo/dark": ("logoUrlDark", "logo-dark"),
            "/assets/v1/instance/policy/label/icon": ("iconUrl", "icon"),
            "/assets/v1/instance/policy/label/icon/dark": ("iconUrlDark", "icon-dark"),
        }.get(self.path)
        if slot is None:
            return self._send(404, b'{"error":"unrouted"}')
        field, stem = slot
        body = self._read()
        # Zitadel stores the uploaded bytes verbatim and serves them back
        # verbatim (internal/api/assets/asset.go); the harness does the same.
        blob = multipart_file(body)
        # Every upload mints a fresh object name, so a URL never identifies
        # content — which is exactly why the Job compares bytes.
        type(self).seq += 1
        obj = f"/assets/v1/inst/policy/label/{stem}-{type(self).seq}"
        self.assets[obj] = blob
        self.policy[field] = f"https://app.example.test{obj}"
        self.uploads.append(self.path)
        return self._send(200)


def multipart_file(body: bytes) -> bytes:
    """The one part's payload, without re-implementing a MIME parser."""
    start = body.find(b"\r\n\r\n")
    if start < 0:
        raise ValueError("upload carried no multipart body")
    rest = body[start + 4 :]
    end = rest.rfind(b"\r\n--")
    return rest[:end] if end >= 0 else rest


def job_script(golden: Path) -> str:
    """The Job's script exactly as the chart renders it."""
    for doc in yaml.safe_load_all(golden.read_text()):
        if doc and doc.get("kind") == "Job" and doc["metadata"]["name"] == JOB_NAME:
            return doc["spec"]["template"]["spec"]["containers"][0]["args"][0]
    raise LookupError(f"no {JOB_NAME} Job in {golden}")


def run_job(script: str, policy: dict, assets: dict[str, bytes], branding: Path):
    """Run the Job against a fresh stub. Returns (result, uploads, deletes)."""
    uploads: list[str] = []
    deletes: list[str] = []
    handler = type("Stub", (Zitadel,), {})
    handler.assets, handler.policy = assets, policy
    handler.uploads, handler.deletes, handler.seq = uploads, deletes, 0
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "bin"
            bin_dir.mkdir()
            # The Job reads the IAM PAT through kubectl; stub it.
            kubectl = bin_dir / "kubectl"
            kubectl.write_text("#!/bin/sh\necho c3R1Yi1wYXQ=\n")
            kubectl.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            env["ZITADEL_EXTERNAL_DOMAIN"] = "app.example.test"
            env["ZITADEL_API_URL"] = f"http://127.0.0.1:{server.server_port}"
            # The Job reads its brand from /branding; bind it by symlink.
            link = Path(tmp) / "branding"
            link.symlink_to(branding)
            script_here = script.replace("/branding/", f"{link}/")
            result = subprocess.run(
                ["bash", "-c", script_here],
                env=env, capture_output=True, text=True, timeout=120,
            )
    finally:
        server.shutdown()
        server.server_close()
    return result, uploads, deletes


def bare_policy() -> dict:
    return {"themeMode": "THEME_MODE_UNSPECIFIED"}


def declared() -> dict:
    return json.loads((BRANDING / "label-policy.json").read_text())


def stale_state(branding: Path) -> tuple[dict, dict[str, bytes]]:
    """A branded instance whose marks are NOT the declared ones."""
    policy = declared() | {
        "logoUrl": "https://app.example.test/assets/v1/inst/policy/label/logo-1",
        "logoUrlDark": "https://app.example.test/assets/v1/inst/policy/label/logo-dark-2",
        "iconUrl": "https://app.example.test/assets/v1/inst/policy/label/icon-3",
        "iconUrlDark": "https://app.example.test/assets/v1/inst/policy/label/icon-dark-4",
    }
    retired = (branding / "logo.svg").read_bytes().replace(b"#0e0d09", b"#894fee")
    assets = {
        "/assets/v1/inst/policy/label/logo-1": retired,
        "/assets/v1/inst/policy/label/logo-dark-2": retired,
        "/assets/v1/inst/policy/label/icon-3": retired,
        "/assets/v1/inst/policy/label/icon-dark-4": retired,
    }
    return policy, assets


def current_state(branding: Path) -> tuple[dict, dict[str, bytes]]:
    """A branded instance already serving exactly the declared marks."""
    policy = declared() | {
        "logoUrl": "https://app.example.test/assets/v1/inst/policy/label/logo-1",
        "logoUrlDark": "https://app.example.test/assets/v1/inst/policy/label/logo-dark-2",
        "iconUrl": "https://app.example.test/assets/v1/inst/policy/label/icon-3",
        "iconUrlDark": "https://app.example.test/assets/v1/inst/policy/label/icon-dark-4",
    }
    logo = (branding / "logo.svg").read_bytes()
    icon = (branding / "icon.svg").read_bytes()
    assets = {
        "/assets/v1/inst/policy/label/logo-1": logo,
        "/assets/v1/inst/policy/label/logo-dark-2": logo,
        "/assets/v1/inst/policy/label/icon-3": icon,
        "/assets/v1/inst/policy/label/icon-dark-4": icon,
    }
    return policy, assets


def audit(branding: Path, golden: Path) -> list[str]:
    failures: list[str] = []
    policy = json.loads((branding / "label-policy.json").read_text())

    for field, want in BRAND_HEX.items():
        for key in (field, field.replace("Color", "ColorDark")):
            got = policy.get(key)
            if got != want:
                failures.append(
                    f"label-policy.json: {key}={got!r}, expected {want!r} "
                    f"(@zeroroot-ai/brand, ADR-0064)"
                )
    if policy.get("themeMode") != "THEME_MODE_LIGHT":
        failures.append(
            f"label-policy.json: themeMode={policy.get('themeMode')!r}, "
            f"expected 'THEME_MODE_LIGHT' (ADR-0064: one light brand)"
        )

    script = job_script(golden)
    all_slots = 4

    # 1. A bare instance takes the whole brand.
    result, uploads, _ = run_job(script, bare_policy(), {}, branding)
    if result.returncode != 0:
        failures.append(f"bare instance: Job failed\n{tail(result)}")
    elif len(uploads) != all_slots:
        failures.append(f"bare instance: uploaded {len(uploads)} slots, expected {all_slots}")

    # 2. A branded instance whose marks are stale takes the new ones.
    policy2, assets2 = stale_state(branding)
    result, uploads, deletes = run_job(script, policy2, assets2, branding)
    if result.returncode != 0:
        failures.append(f"stale marks: Job failed\n{tail(result)}")
    elif len(uploads) != all_slots or len(deletes) != all_slots:
        failures.append(
            f"stale marks: {len(uploads)} uploads / {len(deletes)} deletes, "
            f"expected {all_slots} of each — a rebrand did not reach the instance"
        )

    # 3. An instance already serving them is left alone.
    policy3, assets3 = current_state(branding)
    result, uploads, deletes = run_job(script, policy3, assets3, branding)
    if result.returncode != 0:
        failures.append(f"steady state: Job failed\n{tail(result)}")
    elif uploads or deletes:
        failures.append(
            f"steady state: {len(uploads)} uploads / {len(deletes)} deletes, "
            f"expected none — a no-op Sync would roll the login pod"
        )
    elif "policy unchanged; nothing to roll" not in result.stdout:
        failures.append("steady state: Job did not report an unchanged policy")

    return failures


def tail(result: subprocess.CompletedProcess) -> str:
    out = (result.stdout + result.stderr).strip().splitlines()
    return "\n".join(f"    {line}" for line in out[-15:])


def selftest() -> int:
    """Prove the guard fails on the brand that reached staging: the retired
    violet palette, and the presence-keyed upload that could not replace it."""
    script = job_script(GOLDEN)
    with tempfile.TemporaryDirectory() as tmp:
        branding = Path(tmp) / "branding"
        branding.mkdir()
        for name in ("logo.svg", "icon.svg"):
            branding.joinpath(name).write_bytes(
                (BRANDING / name).read_bytes().replace(b"#0e0d09", b"#894fee")
            )
        branding.joinpath("label-policy.json").write_text(json.dumps({
            "primaryColor": "#894fee",
            "warnColor": "#cc1331",
            "backgroundColor": "#0e0f15",
            "fontColor": "#f0f1f7",
            "primaryColorDark": "#894fee",
            "warnColorDark": "#cc1331",
            "backgroundColorDark": "#0e0f15",
            "fontColorDark": "#f0f1f7",
            "hideLoginNameSuffix": True,
            "disableWatermark": True,
            "themeMode": "THEME_MODE_DARK",
        }, indent=2))
        failures = audit(branding, GOLDEN)
    if not failures:
        print("SELFTEST BROKEN: the retired brand was accepted", file=sys.stderr)
        return 2
    if not any("themeMode" in f for f in failures):
        print("SELFTEST BROKEN: a dark themeMode was accepted", file=sys.stderr)
        return 2
    print("OK: check-login-brand self-test: the retired violet brand is rejected")
    return 0


def main(argv: list[str]) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    if argv[1:]:
        print(f"usage: {argv[0]} [--selftest]", file=sys.stderr)
        return 2
    if not GOLDEN.is_file():
        print(f"could not run: no {GOLDEN} (run `make golden-update`)", file=sys.stderr)
        return 2
    failures = audit(BRANDING, GOLDEN)
    for line in failures:
        print(f"FAIL: {line}", file=sys.stderr)
    if failures:
        return 1
    print("OK: the Job applies the declared brand and re-applies a changed one")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
