#!/usr/bin/env python3
"""check-no-closed-images.py: the chart render names no closed image (ADR-0074).

A self-hosted install must not need an image that the org does not publish.
check-image-registry.py proves that each image is on ghcr.io. It does not read
the package name, so a template could name a closed package and stay green.

This guard renders each published profile and the gibson-velero release, and
collects each `ghcr.io/zeroroot-ai/<package>` reference in the whole render:
container images, and images that a custom resource or a config carries. It
fails when a package is in helm/gibson/closed-packages.yaml.

  check-no-closed-images.py             exit 1 on a closed image, 0 when clean
  check-no-closed-images.py --selftest  prove a closed reference fails
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLOSED = os.path.join(ROOT, "helm", "gibson", "closed-packages.yaml")
# The same profiles that scripts/golden.sh renders.
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
REF = re.compile(r"ghcr\.io/zeroroot-ai/([a-z0-9][a-z0-9._/-]*?)(?=[:@\s\"']|$)", re.M)
# A floor: the baseline render names more first-party packages than this.
MIN_PACKAGES = 5


def renders() -> dict[str, str]:
    out = {}
    for profile in PROFILES:
        args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
                "-f", "helm/testdata/render-inputs/gibson.yaml"]
        for f in profile:
            args += ["-f", f"helm/gibson/{f}"]
        out["+".join(profile)] = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    out["gibson-velero"] = subprocess.run(
        ["helm", "template", "gibson-velero", "helm/gibson-velero", "--namespace", "gibson",
         "-f", "helm/testdata/render-inputs/gibson-velero.yaml"],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return out


def packages(text: str) -> set[str]:
    return set(REF.findall(text))


def audit(rendered: dict[str, str], closed: set[str]) -> tuple[list[str], set[str]]:
    bad, seen = [], set()
    for name, text in rendered.items():
        found = packages(text)
        seen |= found
        for pkg in sorted(found & closed):
            bad.append(f"{name}: the render names ghcr.io/zeroroot-ai/{pkg}, a closed package")
    if len(seen) < MIN_PACKAGES:
        bad.append(f"the renders name {len(seen)} first-party package(s), fewer than {MIN_PACKAGES}: this guard is blind")
    return bad, seen


def load_closed(path: str = CLOSED) -> set[str]:
    data = yaml.safe_load(open(path)) or {}
    pkgs = data.get("packages") or {}
    for k, v in pkgs.items():
        if not str(v or "").strip():
            raise SystemExit(f"{path}: package {k!r} must say what it is")
    if not pkgs:
        raise SystemExit(f"{path}: the list is empty, so the guard checks nothing")
    return set(pkgs)


def selftest() -> int:
    base = "\n".join(f"image: ghcr.io/zeroroot-ai/pkg{i}:v1" for i in range(MIN_PACKAGES))
    cases = [
        ("a clean render", {"p": base}, 0),
        ("a container image of a closed package", {"p": base + "\nimage: ghcr.io/zeroroot-ai/secret:v1.2.3"}, 1),
        ("a digest-pinned closed image in a custom resource",
         {"p": base + '\n  runnerImage: "ghcr.io/zeroroot-ai/secret@sha256:' + "a" * 64 + '"'}, 1),
        ("a public package whose name starts with a closed name", {"p": base + "\nimage: ghcr.io/zeroroot-ai/secret-docs:v1"}, 0),
        ("a closed name under mirror/", {"p": base + "\nimage: ghcr.io/zeroroot-ai/mirror/secret:v1"}, 0),
        ("a blind render", {"p": "image: ghcr.io/zeroroot-ai/pkg0:v1"}, 1),
    ]
    for what, rendered, want in cases:
        got, _ = audit(rendered, {"secret"})
        if len(got) != want:
            print(f"SELFTEST FAIL: {what}: want {want} finding(s), got {got}")
            return 1
    print("  ✓ selftest: a closed image in a container and in a custom resource fails, a blind render fails; "
          "a near name and a mirror pass")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(renders(), load_closed())
    if bad:
        print("the chart render names a closed image (ADR-0074):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ no-closed-images: {len(PROFILES) + 1} renders name {len(seen)} first-party packages, none closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
