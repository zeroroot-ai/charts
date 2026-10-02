#!/usr/bin/env python3
"""check-cg-rotation-window.py — the CG signing-key rotation window renders.

WHAT A ROTATION WINDOW IS

The daemon's Capability-Grant JWT signing key is a key SET, not a key:
`internal/platform/capabilitygrant/signingkey.go`, `LoadSigningKeySetFromDir`.
It reads four files from the mount —

    current.kid   the kid minted tokens are stamped with   (required)
    current.key   its ed25519 seed                         (required)
    previous.kid  a kid that still VERIFIES, never signs   (rotation only)
    previous.key  its ed25519 seed                         (rotation only)

— and an absent `previous.kid` is "no rotation in progress", so a steady-state
install is correct without it. A CG-JWT lives up to 30 minutes, so without the
previous key every token signed before a rotation fails verification the moment
the new key lands. The symptom is an authorization failure on component
dispatch, not a secret-sync error, so it does not look like a rotation problem,
and the safe-looking response is to stop rotating.

WHY A GATE AND NOT A GOLDEN

The window is OFF in every profile, so every committed golden shows two files
and no previous remoteRef. A reviewer comparing this Secret with its two
siblings — the ext-authz grant key and the impersonation key, which both
project `previous` unconditionally because their Go loaders tolerate an empty
value — reads the asymmetry as a missing half. It is not: this loader treats a
`previous.kid` that exists but is EMPTY as a broken mount, errors the Minter
constructor, and disables capability grants outright. Projecting the pair
unconditionally would be the defect.

So the shape only exists in a render nothing commits, which is exactly the
shape that rots. This renders both states and asserts both.

  check-cg-rotation-window.py             exit 1 when either state is wrong
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
ROTATION_VALUE = "gibson-workloads.gibson.cgSigningKey.rotationWindow"


def render(rotating: bool) -> list[dict]:
    cmd = ["helm", "template", "gibson", "helm/gibson",
           "-f", "helm/gibson/values-baseline.yaml",
           "-f", "helm/testdata/render-inputs/gibson.yaml",
           "--namespace", "gibson"]
    if rotating:
        cmd += ["--set", f"{ROTATION_VALUE}=true"]
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


def judge(docs: list[dict], rotating: bool) -> list[str]:
    """Every way this render disagrees with the loader's contract."""
    bad = []
    state = "rotation window OPEN" if rotating else "steady state"
    es = external_secret(docs)
    if es is None:
        return [f"{state}: no ExternalSecret/{SECRET} in the render"]

    files = set((es["spec"]["target"]["template"]["data"] or {}).keys())
    refs = {(e["remoteRef"]["key"], e["remoteRef"]["property"]) for e in es["spec"].get("data") or []}

    want_files = {"current.kid", "current.key"}
    want_refs = {(BACKEND, "kid"), (BACKEND, "key")}
    if rotating:
        want_files |= {"previous.kid", "previous.key"}
        want_refs |= {(PREVIOUS_BACKEND, "kid"), (PREVIOUS_BACKEND, "key")}

    if files != want_files:
        bad.append(f"{state}: the Secret projects {sorted(files)}, want {sorted(want_files)}")
    if refs != want_refs:
        bad.append(f"{state}: the ExternalSecret reads {sorted(refs)}, want {sorted(want_refs)}")

    # A projected file with no remoteRef behind it renders as the empty string,
    # which is the one value this loader refuses.
    keys = {e["secretKey"] for e in es["spec"].get("data") or []}
    for f in sorted(files):
        placeholder = f.replace(".", "_")
        if placeholder not in keys:
            bad.append(f"{state}: {f} is projected but no data entry provides {placeholder}, "
                       f"so it renders empty and the loader reads a broken mount")

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
PREV = {"previous.kid": (PREVIOUS_BACKEND, "kid"), "previous.key": (PREVIOUS_BACKEND, "key")}


def selftest() -> int:
    cases = [
        ("steady state, correct", fixture(CURRENT, CURRENT), False, 0),
        ("rotating, correct", fixture(CURRENT | PREV, CURRENT | PREV), True, 0),
        # THE ISSUE'S PROPOSAL: project previous unconditionally. In steady
        # state that is an empty previous.kid, which disables capability grants.
        ("steady state projecting previous", fixture(CURRENT | PREV, CURRENT | PREV), False, 2),
        # THE REGRESSION THIS GATE EXISTS FOR: the window is open and the pair
        # is gone.
        ("rotating without the previous pair", fixture(CURRENT, CURRENT), True, 2),
        # A projected file with no data entry renders as the empty string.
        ("previous projected with no remoteRef", fixture(CURRENT | PREV, CURRENT), True, 3),
        # The fallback contract.
        ("volume not optional", fixture(CURRENT, CURRENT, optional=False), False, 1),
        ("env and mount disagree", fixture(CURRENT, CURRENT, env="/elsewhere"), False, 1),
    ]
    for name, docs, rotating, want in cases:
        got = judge(docs, rotating)
        if len(got) != want:
            print(f"SELFTEST FAIL: {name} must yield {want} finding(s), got {len(got)}: {got}")
            return 1
    for rotating in (False, True):
        live = judge(render(rotating), rotating)
        if live:
            state = "rotating" if rotating else "steady"
            print(f"SELFTEST FAIL: the {state} render is not clean:\n  " + "\n  ".join(live))
            return 1
    print("OK: an unconditional previous pair, a missing one, a projected file with no source, "
          "a non-optional volume and a mismatched mount all fail; both live renders pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = []
    for rotating in (False, True):
        bad += judge(render(rotating), rotating)
    if bad:
        print("❌ the CG signing-key rotation window does not render correctly:\n  " + "\n  ".join(bad))
        return 1
    print("✓ cg-rotation-window: steady state projects the current key only, and "
          f"{ROTATION_VALUE}=true adds the previous pair from {PREVIOUS_BACKEND}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
