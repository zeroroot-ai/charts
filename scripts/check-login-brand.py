#!/usr/bin/env python3
"""check-login-brand.py: the chart hands the platform-operator the declared login brand.

WHY THIS GUARD EXISTS
---------------------
On 2026-09-14 the staging login page still served the retired violet brand:
primaryColor #894fee on #0e0f15, THEME_MODE_DARK, and the violet CRT mark.
ADR-0064 had replaced it months earlier. The palette in files/branding/ was
stale, and the mark upload was keyed by presence, so an instance that had been
branded once could never be re-branded.

The brand is now applied by the platform-operator as a bootstrap step
(PlatformBootstrap spec.zitadel.loginBranding, condition LoginBrandingReady).
Writing the instance label policy needs IAM_OWNER, so no Job reads the owner
PAT for it. The operator compares by content, and its tests in gibson prove
that a stale mark is replaced and a current one is left alone. This guard
proves the chart half, on the baseline render:

  1. The zitadel-login-branding ConfigMap carries label-policy.json, logo.svg
     and icon.svg byte-for-byte from files/branding/, plus a branding-hash
     over the three.
  2. The PlatformBootstrap names that ConfigMap in spec.zitadel.loginBranding.
  3. The login Deployment reloads on that ConfigMap, so a rebrand rolls the
     pod that caches the policy.
  4. The declared palette is the brand's: a hex the brand does not define
     fails here, not on a screenshot.
  5. No workload renders that reads the owner PAT to brand (the old Job).

Usage: scripts/check-login-brand.py            # the guard
       scripts/check-login-brand.py --selftest # prove it can fail
Exit:  0 the chart hands over the brand, 1 it does not
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BRANDING = ROOT / "helm/gibson/files/branding"
GOLDEN = ROOT / "helm/testdata/golden/values-baseline.bare.yaml"
CONFIGMAP = "zitadel-login-branding"
FILES = ("label-policy.json", "logo.svg", "icon.svg")

# @zeroroot-ai/brand, the one light brand (ADR-0064). Kept here so a policy
# that drifts back to a colour the brand does not define fails the guard.
# See files/branding/README.md for the oklch each hex was converted from.
BRAND_HEX = {
    "primaryColor": "#346000",      # --highlight, the green that carries text
    "backgroundColor": "#e3e3df",   # --background, the concrete ground
    "fontColor": "#0e0d09",         # --foreground, ink
    "warnColor": "#c70009",         # --destructive
}


def load(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def files(branding: Path) -> dict[str, str]:
    return {f: (branding / f).read_text() for f in FILES}


def audit(docs: list[dict], declared: dict[str, str]) -> list[str]:
    out = []
    cms = [d for d in docs if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == CONFIGMAP]
    if len(cms) != 1:
        return [f"want one ConfigMap {CONFIGMAP}, the render has {len(cms)}"]
    data = cms[0].get("data") or {}
    annotations = cms[0]["metadata"].get("annotations") or {}
    if any(k.startswith("helm.sh/hook") or k.startswith("argocd.argoproj.io/hook") for k in annotations):
        out.append(f"{CONFIGMAP} is a hook; the operator and Reloader watch it, so it must be a standing resource")
    for f, want in declared.items():
        # `|` block scalars add one trailing newline to content that lacks one.
        if data.get(f, "").rstrip("\n") != want.rstrip("\n"):
            out.append(f"{CONFIGMAP}.{f} is not files/branding/{f}")
    want_hash = hashlib.sha256("".join(declared[f] for f in FILES).encode()).hexdigest()
    if data.get("branding-hash") != want_hash:
        out.append(f"{CONFIGMAP}.branding-hash is not the sha256 of the three brand files")

    pbs = [d for d in docs if d.get("kind") == "PlatformBootstrap"]
    names = [((pb.get("spec") or {}).get("zitadel") or {}).get("loginBranding", {}).get("configMap") for pb in pbs]
    if names != [CONFIGMAP]:
        out.append(f"PlatformBootstrap spec.zitadel.loginBranding.configMap = {names}, want [{CONFIGMAP!r}]")

    logins = [d for d in docs if d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("zitadel-login")]
    for dep in logins:
        ann = dep["metadata"].get("annotations") or {}
        reload = ann.get("configmap.reloader.stakater.com/reload", "")
        if CONFIGMAP not in [x.strip() for x in reload.split(",")]:
            out.append(f"Deployment {dep['metadata']['name']} does not reload on {CONFIGMAP} (got {reload!r})")
    if not logins:
        out.append("no zitadel-login Deployment in the render")

    try:
        policy = json.loads(declared["label-policy.json"])
    except json.JSONDecodeError as e:
        return out + [f"files/branding/label-policy.json is not JSON: {e}"]
    for field, hexv in BRAND_HEX.items():
        for f in (field, field + "Dark"):
            if str(policy.get(f, "")).lower() != hexv:
                out.append(f"label-policy.json {f} = {policy.get(f)!r}, the brand's is {hexv}")
    if policy.get("themeMode") != "THEME_MODE_LIGHT":
        out.append(f"label-policy.json themeMode = {policy.get('themeMode')!r}, the brand is THEME_MODE_LIGHT")

    for d in docs:
        if d.get("kind") in ("Job", "CronJob") and "branding" in d["metadata"]["name"]:
            out.append(f"{d['kind']} {d['metadata']['name']} renders; the platform-operator applies the brand")
    return out


def selftest() -> int:
    docs = load(GOLDEN)
    declared = files(BRANDING)
    if got := audit(docs, declared):
        print("SELFTEST FAIL: the committed render must pass:\n  " + "\n  ".join(got))
        return 1

    def mutate(fn) -> list[str]:
        d2, f2 = copy.deepcopy(docs), dict(declared)
        fn(d2, f2)
        return audit(d2, f2)

    def cm(d2):
        return next(d for d in d2 if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == CONFIGMAP)

    def pb(d2):
        return next(d for d in d2 if d.get("kind") == "PlatformBootstrap")

    cases = {
        "the retired violet palette": lambda d2, f2: f2.update(
            {"label-policy.json": declared["label-policy.json"].replace("#346000", "#894fee")}),
        "a stale logo in the ConfigMap": lambda d2, f2: cm(d2)["data"].update({"logo.svg": "<svg>old</svg>"}),
        "a branding-hash that ignores a file": lambda d2, f2: cm(d2)["data"].update({"branding-hash": "0" * 64}),
        "a PlatformBootstrap with no loginBranding": lambda d2, f2: pb(d2)["spec"]["zitadel"].pop("loginBranding"),
        "the ConfigMap as a hook": lambda d2, f2: cm(d2)["metadata"].setdefault("annotations", {}).update(
            {"helm.sh/hook": "post-install"}),
        "the old branding Job": lambda d2, f2: d2.append(
            {"kind": "Job", "metadata": {"name": "zitadel-login-branding"}}),
    }
    for what, fn in cases.items():
        if not mutate(fn):
            print(f"SELFTEST FAIL: {what} passed the guard")
            return 1
    print(f"OK: {len(cases)} broken hand-overs fail; the committed render hands the brand to the operator")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    got = audit(load(GOLDEN), files(BRANDING))
    if got:
        print("FAIL: the chart does not hand the declared login brand to the platform-operator:\n  " + "\n  ".join(got))
        return 1
    print("OK: the platform-operator gets the declared brand, and the login pod reloads on it")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
