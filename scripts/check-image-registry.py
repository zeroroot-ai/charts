#!/usr/bin/env python3
"""check-image-registry.py — every image this repository names lives on ghcr.io.

Rebuilds two of the guards lost in the 2026-09-04 split (charts#17):
check-image-registry (no image reference outside ghcr.io, deploy#1170) and
check-no-docker-io (no docker.io reference, same origin). One rule covers
both: an image reference in this repository's own templates or values must
start with `ghcr.io/` — a first-party package or `ghcr.io/zeroroot-ai/mirror/`.
docker.io, quay.io, registry.k8s.io, cgr.dev and a bare `busybox:` all fail.

Scope is what THIS repository authors: helm/*/templates and helm/*/values*.yaml.
Vendored sub-charts (helm/*/charts/*.tgz) carry their upstream defaults; the
air-gap image list (bigbang/images) is where those are mirrored.

  check-image-registry.py             exit 1 on a reference off ghcr.io, 0 when clean
  check-image-registry.py --selftest  prove a planted docker.io reference fails and the tree passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# `image: <ref>` literals and `repository: <ref>` values. A ref is judged by
# its registry: anything before the first slash that contains a dot or a
# colon is a registry host; a ref with no host (busybox:1.37) is docker.io.
KEY_RX = re.compile(r'^\s*(?:-\s*)?(?:image|repository):\s*"?([A-Za-z0-9][A-Za-z0-9._/:@-]*)"?\s*(?:#.*)?$')
GOTMPL = re.compile(r"\{\{.*?\}\}", re.S)


def registry_of(ref: str) -> str:
    head = ref.split("/", 1)[0]
    if "/" not in ref or not ("." in head or ":" in head or head == "localhost"):
        return "docker.io"
    return head


def scan(root: str) -> list[str]:
    hits = []
    for dirpath, dirs, files in os.walk(os.path.join(root, "helm")):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        # vendored sub-charts, rendered snapshots and vendored CRD files are
        # not this repo's authorship; Chart.yaml names chart repositories.
        if "/charts" in rel or rel.startswith("helm/testdata") or "/files/crds" in rel:
            dirs[:] = []
            continue
        for f in files:
            in_templates = "/templates" in rel
            is_values = f.startswith("values") and f.endswith((".yaml", ".yml"))
            if not (in_templates and f.endswith((".yaml", ".yml", ".tpl"))) and not is_values:
                continue
            p = os.path.join(dirpath, f)
            lines = [GOTMPL.sub("", l) for l in open(p, encoding="utf-8", errors="replace")]
            for n, line in enumerate(lines, 1):
                m = KEY_RX.match(line)
                if not m:
                    continue
                ref = m.group(1)
                if not ref or ref.endswith("/") or ("/" not in ref and ":" not in ref):
                    continue  # a bare name is a values key, not an image
                reg = registry_of(ref)
                if reg == "docker.io" and line.lstrip().startswith("repository:"):
                    # the split form: `registry:` or `imageRegistry:` a few
                    # lines above `repository: zeroroot-ai/mirror/x` names the
                    # host there.
                    window = "".join(lines[max(0, n - 12):n - 1])
                    found = re.findall(r'^\s*(?:registry|imageRegistry):\s*"?([A-Za-z0-9.:-]+)"?', window, re.M)
                    if found:
                        reg = found[-1]
                if reg != "ghcr.io":
                    hits.append(f"{os.path.relpath(p, root)}:{n}: {ref} ({reg})")
    return hits


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "x", "templates")
        os.makedirs(t)
        open(os.path.join(t, "bad.yaml"), "w").write(
            "containers:\n  - image: docker.io/library/busybox:1.36\n  - image: busybox:1.37.0-uclibc\n"
            "  - image: quay.io/jetstack/cert-manager-controller:v1.21.1\n  - image: ghcr.io/zeroroot-ai/gibson:1.0\n"
            "image:\n  repository: registry.k8s.io/external-dns/external-dns\n"
            "ok:\n  registry: ghcr.io\n  repository: zeroroot-ai/mirror/redis-stack-server\n"
            "bad:\n  registry: docker.io\n  repository: library/redis\n"
        )
        hits = scan(d)
        if len(hits) != 5:
            print(f"SELFTEST FAIL: want 5 off-ghcr references in the fixture, got {hits}")
            return 1
        if not any("(docker.io)" in h and "busybox:1.37" in h for h in hits):
            print(f"SELFTEST FAIL: a bare busybox: must read as docker.io, got {hits}")
            return 1
    live = scan(ROOT)
    if live:
        print("SELFTEST FAIL: the tree names an image off ghcr.io:\n  " + "\n  ".join(live))
        return 1
    print("OK: docker.io, a bare name, quay.io and registry.k8s.io fail; the tree names only ghcr.io images")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    hits = scan(ROOT)
    if hits:
        print("❌ an image reference off ghcr.io (mirror it under ghcr.io/zeroroot-ai/mirror/ and reference the mirror):\n  " + "\n  ".join(hits))
        return 1
    print("✓ image-registry: every image this repository names is on ghcr.io")
    return 0


if __name__ == "__main__":
    sys.exit(main())
