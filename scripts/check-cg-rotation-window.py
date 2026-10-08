#!/usr/bin/env python3
"""check-cg-rotation-window.py — the CG signing-key set renders all three slots.

THE KEY SET

The daemon's Capability-Grant JWT signing key is a key SET:
`internal/platform/capabilitygrant/signingkey.go`, `LoadSigningKeySetFromDir`.
It reads six files from the mount:

    current.kid / current.key     the key that signs              (required)
    next.kid / next.key           the incoming key, verify only   (empty unless rotating)
    previous.kid / previous.key   the outgoing key, verify only   (empty unless rotating)

An optional slot whose two files are empty is "no key" (gibson#1033), so the
chart projects every slot always and has no rotation flag. The
openbao-auto-init sidecar runs the rotation by writing the three OpenBao keys
(ADR-0171). A slot that the chart does not project is a rotation step the
daemon never sees: a missing `next` lets a replica sign with a kid that the
other replicas do not publish yet, and a missing `previous` refuses each token
of the outgoing key at once.

This guard renders the baseline and asserts the six files, their six
remoteRefs, and the fallback contract of the daemon volume. The make target
keeps its old name, cg-rotation-window, because the hosted secret contract
names it.

  check-cg-rotation-window.py             exit 1 when the render is wrong
  check-cg-rotation-window.py --selftest  prove each assertion fails on a fixture
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRET = "gibson-gibson-workloads-cg-signing-key"
BACKEND = "gibson-cg-signing-key"
PREVIOUS_BACKEND = "gibson-cg-signing-key-previous"
MOUNT = "/etc/gibson/cg-signing-key"
ENV_VAR = "GIBSON_CGJWT_SIGNING_KEY_DIR"
NEXT_BACKEND = "gibson-cg-signing-key-next"
VALUES = os.path.join("helm", "gibson-workloads", "values.yaml")


def render() -> list[dict]:
    cmd = ["helm", "template", "gibson", "helm/gibson",
           "-f", "helm/gibson/values-baseline.yaml",
           "-f", "helm/testdata/render-inputs/gibson.yaml",
           "--namespace", "gibson"]
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def external_secret(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") == "ExternalSecret" and (d.get("metadata") or {}).get("name") == SECRET:
            return d
    return None


def daemon(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") == "StatefulSet" and ENV_VAR in yaml.safe_dump(d):
            return d
    return None


def judge(docs: list[dict]) -> list[str]:
    """Every way this render disagrees with the loader's contract."""
    bad = []
    state = "render"
    es = external_secret(docs)
    if es is None:
        return [f"{state}: no ExternalSecret/{SECRET} in the render"]

    files = set((es["spec"]["target"]["template"]["data"] or {}).keys())
    refs = {(e["remoteRef"]["key"], e["remoteRef"]["property"]) for e in es["spec"].get("data") or []}

    want_files = {"current.kid", "current.key", "next.kid", "next.key", "previous.kid", "previous.key"}
    want_refs = {(b, p) for b in (BACKEND, NEXT_BACKEND, PREVIOUS_BACKEND) for p in ("kid", "key")}

    if files != want_files:
        bad.append(f"{state}: the Secret projects {sorted(files)}, want {sorted(want_files)}")
    if refs != want_refs:
        bad.append(f"{state}: the ExternalSecret reads {sorted(refs)}, want {sorted(want_refs)}")

    # A projected file with no remoteRef behind it renders as the empty string
    # in every state, so its slot can never hold a key.
    keys = {e["secretKey"] for e in es["spec"].get("data") or []}
    for f in sorted(files):
        placeholder = f.replace(".", "_")
        if placeholder not in keys:
            bad.append(f"{state}: {f} is projected but no data entry provides {placeholder}, "
                       f"so it renders empty in every state")

    # The fallback contract, in BOTH states: an install whose backend has no
    # such secret must mount an empty dir and degrade to the KEK derivation,
    # never fail to start.
    sts = daemon(docs)
    if sts is None:
        bad.append(f"{state}: no StatefulSet sets {ENV_VAR}")
        return bad
    spec = sts["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in spec.get("volumes") or []}
    v = vols.get("cg-signing-key")
    if v is None:
        bad.append(f"{state}: the daemon declares no cg-signing-key volume")
    elif (v.get("secret") or {}).get("secretName") != SECRET:
        bad.append(f"{state}: the cg-signing-key volume reads "
                   f"{(v.get('secret') or {}).get('secretName')!r}, want {SECRET!r}")
    elif not (v.get("secret") or {}).get("optional"):
        bad.append(f"{state}: the cg-signing-key volume is not optional, so an install whose "
                   f"backend has no such secret fails to start instead of degrading to the KEK")

    for c in spec.get("containers") or []:
        for e in c.get("env") or []:
            if e["name"] == ENV_VAR and e.get("value") != MOUNT:
                bad.append(f"{state}: {ENV_VAR}={e.get('value')!r}, want {MOUNT!r}")
    for c in spec.get("containers") or []:
        for m in c.get("volumeMounts") or []:
            if m["name"] == "cg-signing-key" and m.get("mountPath") != MOUNT:
                bad.append(f"{state}: the cg-signing-key mountPath is {m.get('mountPath')!r}, "
                           f"want {MOUNT!r} — the loader reads {ENV_VAR}")
    return bad


# Fixtures: the four ways this has a wrong answer. Each is the render reduced to
# the two documents the assertions read.
def fixture(files, refs, optional=True, env=MOUNT, mount=MOUNT):
    return [
        {"kind": "ExternalSecret", "metadata": {"name": SECRET},
         "spec": {"target": {"template": {"data": {f: "x" for f in files}}},
                  "data": [{"secretKey": k.replace(".", "_"), "remoteRef": {"key": b, "property": p}}
                           for k, (b, p) in refs.items()]}},
        {"kind": "StatefulSet", "metadata": {"name": "gibson"},
         "spec": {"template": {"spec": {
             "volumes": [{"name": "cg-signing-key",
                          "secret": {"secretName": SECRET, "optional": optional}}],
             "containers": [{"name": "gibson",
                             "env": [{"name": ENV_VAR, "value": env}],
                             "volumeMounts": [{"name": "cg-signing-key", "mountPath": mount}]}]}}}},
    ]


CURRENT = {"current.kid": (BACKEND, "kid"), "current.key": (BACKEND, "key")}
NEXT = {"next.kid": (NEXT_BACKEND, "kid"), "next.key": (NEXT_BACKEND, "key")}
PREV = {"previous.kid": (PREVIOUS_BACKEND, "kid"), "previous.key": (PREVIOUS_BACKEND, "key")}
ALL = CURRENT | NEXT | PREV


def flag_left(values_text: str) -> list[str]:
    """The old rotation flag is a second code path (ADR-0027)."""
    if "rotationWindow" in values_text:
        return ["values.yaml still declares gibson.cgSigningKey.rotationWindow: the slots are projected always"]
    return []


def selftest() -> int:
    cases = [
        ("correct", fixture(ALL, ALL), 0),
        ("no next slot", fixture(CURRENT | PREV, CURRENT | PREV), 2),
        ("no previous slot", fixture(CURRENT | NEXT, CURRENT | NEXT), 2),
        ("previous projected with no remoteRef", fixture(ALL, CURRENT | NEXT), 3),
        ("volume not optional", fixture(ALL, ALL, optional=False), 1),
        ("env and mount disagree", fixture(ALL, ALL, env="/elsewhere"), 1),
    ]
    for name, docs, want in cases:
        got = judge(docs)
        if len(got) != want:
            print(f"SELFTEST FAIL: {name} must yield {want} finding(s), got {len(got)}: {got}")
            return 1
    if not flag_left("  cgSigningKey:\n    rotationWindow: false\n"):
        print("SELFTEST FAIL: a leftover rotationWindow value was not detected")
        return 1
    live = judge(render()) + flag_left(open(os.path.join(ROOT, VALUES)).read())
    if live:
        print("SELFTEST FAIL: the live render is not clean:\n  " + "\n  ".join(live))
        return 1
    print("OK: a missing next or previous slot, a projected file with no source, a non-optional "
          "volume, a mismatched mount and a leftover flag all fail; the live render passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = judge(render()) + flag_left(open(os.path.join(ROOT, VALUES)).read())
    if bad:
        print("❌ the CG signing-key set does not render correctly:\n  " + "\n  ".join(bad))
        return 1
    print("✓ cg-rotation-window: the key set projects current, next and previous from "
          f"{BACKEND}, {NEXT_BACKEND} and {PREVIOUS_BACKEND}, with no rotation flag")
    return 0


if __name__ == "__main__":
    sys.exit(main())
