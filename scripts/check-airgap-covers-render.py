#!/usr/bin/env python3
"""check-airgap-covers-render.py: each image that the chart starts is on the air-gap list (charts#668).

An air-gapped install mirrors only the images of airgap/images.txt. The
generator of that list read `image:` lines and *_IMAGE env vars, and missed
the setec launcher and disk builder, which the setec operator starts from
its flags --launcher-image and --disk-builder-image. Such an install could
not start a sandbox.

This check does not reuse the generator. It reads every golden render of
the umbrella and of gibson-velero (helm/testdata/golden/), walks each container of each pod template, and
collects each image it names:

  - the image field of a container and of an init container,
  - the value of an env var named *_IMAGE,
  - the value of a flag --<name>-image=<ref> in command or args.

It fails on each first-party or mirror reference (ghcr.io/zeroroot-ai/...)
that airgap/images.txt does not list.

  check-airgap-covers-render.py             exit 1 on an image missing from the list
  check-airgap-covers-render.py --selftest  prove a dropped entry fails and the full list passes
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = os.path.join(ROOT, "airgap", "images.txt")
OURS = re.compile(r"^ghcr\.io/zeroroot-ai/")
ENV_IMAGE = re.compile(r"^[A-Z0-9_]+_IMAGE$")
ARG_IMAGE = re.compile(r"^--[a-z0-9-]+-image=(\S+)$")


def pod_specs(d):
    spec = d.get("spec") or {}
    tpl = (spec.get("template") or {}).get("spec")
    if tpl:
        yield tpl
    job = ((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec")
    if job:
        yield job
    if d.get("kind") == "Pod" and spec.get("containers"):
        yield spec


def images(docs) -> set[str]:
    out = set()
    for d in docs:
        if not isinstance(d, dict):
            continue
        for ps in pod_specs(d):
            for c in (ps.get("containers") or []) + (ps.get("initContainers") or []):
                if c.get("image"):
                    out.add(str(c["image"]))
                for e in c.get("env") or []:
                    if ENV_IMAGE.match(str(e.get("name", ""))) and e.get("value"):
                        out.add(str(e["value"]))
                for a in (c.get("command") or []) + (c.get("args") or []):
                    m = ARG_IMAGE.match(str(a))
                    if m:
                        out.add(m.group(1))
    return {i for i in out if OURS.match(i)}


def listed(path: str) -> set[str]:
    return {line.strip() for line in open(path) if line.strip() and not line.startswith("#")}


def missing(rendered: set[str], on_list: set[str]) -> list[str]:
    return sorted(rendered - on_list)


def goldens() -> set[str]:
    found = set()
    # The umbrella profiles and Velero, which every install runs as its own release.
    paths = sorted(glob.glob(os.path.join(ROOT, "helm", "testdata", "golden", "values-*.yaml")))
    paths.append(os.path.join(ROOT, "helm", "testdata", "golden", "gibson-velero.yaml"))
    for f in paths:
        found |= images(yaml.safe_load_all(open(f)))
    return found


def selftest() -> int:
    rendered = goldens()
    full = listed(LIST)
    if missing(rendered, full):
        print(f"SELFTEST FAIL: the committed list must cover the render: {missing(rendered, full)}")
        return 1
    launcher = next((i for i in rendered if "/setec-launcher:" in i), None)
    builder = next((i for i in rendered if "/setec-disk-builder:" in i), None)
    velero = next((i for i in rendered if "/mirror/velero:" in i), None)
    if not launcher or not builder or not velero:
        print("SELFTEST FAIL: the render names no setec launcher, disk builder or Velero image: the check is blind")
        return 1
    for drop in (launcher, builder, velero):
        if missing(rendered, full - {drop}) != [drop]:
            print(f"SELFTEST FAIL: a list without {drop} must fail on it")
            return 1
    print("  ✓ selftest: a list without the setec launcher, the disk builder or Velero fails, the committed list passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    rendered = goldens()
    if not rendered:
        print("no golden render names a first-party image: this check is blind", file=sys.stderr)
        return 1
    bad = missing(rendered, listed(LIST))
    if bad:
        print("an image that the chart starts is not on airgap/images.txt (charts#668); "
              "run ./airgap/generate-image-list.py:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ airgap-covers-render: {len(rendered)} first-party and mirror images of the render, each on the air-gap list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
