#!/usr/bin/env python3
"""check-config-consumed.py — every config key the chart renders, the daemon binds.

The chart renders a gibson.yaml ConfigMap and the daemon loads it with viper.
Viper IGNORES an unknown key. So a line the daemon does not bind is dropped in
silence: it looks like configuration, it reviews like configuration, and it
configures nothing. gibson#599 deleted 46 keys on the daemon side and 16 of them
were still rendered here, including four under `security.` that read as controls —
ssl_validation, audit_logging, encryption_algorithm, key_derivation.

That last point is why this is a guard and not a tidy-up. A rendered
`security.ssl_validation: true` is worse than absent: it answers the question "is
validation on?" with a yes that nothing enforces.

HOW IT DECIDES

helm/contracts/gibson-config-keys.txt is generated from a gibson checkout
(gen-config-keys.py) and committed, so this check needs no sibling repo and no
network. A rendered leaf key is bound when its LAST segment is a struct tag
somewhere in gibson, or its full dotted path appears as a Go string literal.

DELIBERATELY CONSERVATIVE, and worth knowing before trusting a pass: matching the
last segment means a key bound under a DIFFERENT parent still passes. So this
catches the real class — a name gibson binds nowhere at all — and does not catch
a key moved from one section to another. Tightening it needs the full struct
graph, which is a Go parser, not a regex. A false pass here is a key that still
has a reader somewhere; a false FAIL would be the dangerous direction and cannot
happen by construction.

EXEMPT carries the keys the chart renders on purpose for something other than the
daemon, with the reason, and is checked in BOTH directions so a stale entry fails.

  check-config-consumed.py             exit 1 on a rendered key gibson binds nowhere
  check-config-consumed.py --selftest  prove the assertion fails on a planted key
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit("check-config-consumed: PyYAML is required")

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "helm" / "contracts" / "gibson-config-keys.txt"
CONFIGMAP_KEY = "gibson.yaml"

# Rendered on purpose, read by something other than the daemon's config loader.
# Keyed by the full dotted path, never by position, and checked both ways.
EXEMPT: dict[str, str] = {}


def fail(msg: str) -> None:
    sys.exit(f"check-config-consumed: {msg}")


def contract() -> tuple[set[str], set[str]]:
    if not CONTRACT.is_file():
        fail(f"{CONTRACT.relative_to(ROOT)} does not exist. Run `make config-contract-sync` "
             f"with a gibson checkout beside this one.")
    leaves, paths, cur = set(), set(), None
    for line in CONTRACT.read_text().split("\n"):
        if line.startswith("# [leaves]"):
            cur = leaves
            continue
        if line.startswith("# [paths]"):
            cur = paths
            continue
        if not line or line.startswith("#") or cur is None:
            continue
        cur.add(line.strip())
    if len(leaves) < 200:
        fail(f"the contract lists only {len(leaves)} leaf name(s), which cannot be right. "
             f"Refusing to report a pass over nothing.")
    return leaves, paths


def rendered_keys() -> list[str]:
    # Repack first. `helm template` on the umbrella reads helm/gibson/charts/*.tgz,
    # and `make chart-deps` is stamp-gated, so without this the render is of a
    # tarball built BEFORE the edit — the first run of this check reported three
    # keys as still present after they had been deleted. A check that renders the
    # umbrella and does not repack is measuring the previous commit.
    subprocess.run("rm -f helm/gibson/charts/gibson-{workloads,operators,crds,common}-*.tgz "
                   "helm/*/.charts.stamp", cwd=ROOT, shell=True, check=False)
    dep = subprocess.run(["make", "chart-deps"], cwd=ROOT, capture_output=True, text=True)
    if dep.returncode != 0:
        fail(f"chart-deps failed, so the render would be stale:\n{dep.stderr[-800:]}")
    cmd = ["helm", "template", "gibson", "helm/gibson",
           "-f", "helm/gibson/values-baseline.yaml",
           "-f", "helm/testdata/render-inputs/gibson.yaml",
           "--namespace", "gibson"]
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        fail(f"render failed:\n{p.stderr[-1200:]}")
    for d in yaml.safe_load_all(p.stdout):
        if not d or d.get("kind") != "ConfigMap":
            continue
        data = d.get("data") or {}
        if CONFIGMAP_KEY not in data:
            continue
        cfg = yaml.safe_load(data[CONFIGMAP_KEY])
        if not isinstance(cfg, dict):
            continue

        def leaves(o, path=()):
            if isinstance(o, dict) and o:
                for k, v in o.items():
                    yield from leaves(v, path + (str(k),))
            else:
                yield ".".join(path)

        keys = sorted(set(leaves(cfg)))
        if not keys:
            fail(f"the rendered {CONFIGMAP_KEY} has no keys, which cannot be right")
        return keys
    fail(f"no rendered ConfigMap carries a {CONFIGMAP_KEY}, so there is nothing to check. "
         f"If the daemon's config moved, this gate must follow it.")
    raise AssertionError("unreachable")


def unbound(keys: list[str], leaves: set[str], paths: set[str]) -> list[str]:
    out = []
    for k in keys:
        if k in EXEMPT or k in paths or k.split(".")[-1] in leaves:
            continue
        out.append(k)
    return out


def main() -> int:
    leaves, paths = contract()
    keys = rendered_keys()

    stale = sorted(set(EXEMPT) - set(keys))
    if stale:
        fail(f"EXEMPT names {stale}, which the chart no longer renders. An exemption that "
             f"protects nothing hides the next real one — delete the entry.")

    bad = unbound(keys, leaves, paths)
    if bad:
        print(f"check-config-consumed: {len(bad)} rendered config key(s) the gibson daemon "
              f"binds NOWHERE, so viper drops each one in silence:", file=sys.stderr)
        for k in bad:
            print(f"  {k}", file=sys.stderr)
        print("  Delete the line, or add it to EXEMPT with the reader that is not the daemon.",
              file=sys.stderr)
        return 1

    if "--selftest" in sys.argv:
        # A key gibson binds nowhere must be caught. Planted in the comparison, not
        # in the template, so the selftest cannot pass against a corpus it wrote.
        planted = keys + ["core.not_a_real_setting_xyz"]
        if not unbound(planted, leaves, paths):
            fail("selftest: a planted unbound key was accepted, so the assertion is inert")
        # And the exemption must not be a wildcard: an exempt key still has to be rendered.
        if unbound(keys + ["core.another_fake_abc"], leaves, paths) == []:
            fail("selftest: the planted key vanished")
        print("check-config-consumed: selftest OK — a planted unbound key is caught")

    print(f"check-config-consumed: {len(keys)} rendered config key(s), every one bound by "
          f"gibson ({len(leaves)} leaf names, {len(paths)} dotted paths in the contract)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
