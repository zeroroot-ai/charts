#!/usr/bin/env python3
"""check-no-literal-tool-image.py — no template may name the alpine-k8s tool image by hand.

Every first-party Job, init container and gate renders `gibson.toolImage`,
which reads the umbrella's `global.toolImage` (zeroroot-ai/.github#20, the
`alpine-k8s` version link). Before this guard, thirteen templates carried
the literal `ghcr.io/zeroroot-ai/mirror/alpine-k8s:1.31.0` while the values
pinned 1.33.0, and nothing compared them. Keyed by content: any
`mirror/alpine-k8s:` under helm/*/templates fails, whatever the line.

  check-no-literal-tool-image.py             exit 1 on a literal, 0 when clean
  check-no-literal-tool-image.py --selftest  prove a literal fixture fails and the tree passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LITERAL = re.compile(r"mirror/alpine-k8s:")


def scan(root: str) -> list[str]:
    hits = []
    for dirpath, _, files in os.walk(os.path.join(root, "helm")):
        if "/templates" not in dirpath.replace(os.sep, "/"):
            continue
        for f in files:
            if not f.endswith((".yaml", ".yml", ".tpl")):
                continue
            p = os.path.join(dirpath, f)
            for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if LITERAL.search(line):
                    hits.append(f"{os.path.relpath(p, root)}:{n}: {line.strip()}")
    return hits


def main() -> int:
    if "--selftest" in sys.argv:
        with tempfile.TemporaryDirectory() as t:
            d = os.path.join(t, "helm", "x", "templates"); os.makedirs(d)
            open(os.path.join(d, "job.yaml"), "w").write('image: ghcr.io/zeroroot-ai/mirror/alpine-k8s:1.31.0\n')
            open(os.path.join(d, "ok.yaml"), "w").write('image: {{ include "gibson.toolImage" . | quote }}\n')
            open(os.path.join(t, "helm", "x", "values.yaml"), "w").write("toolImage: ghcr.io/zeroroot-ai/mirror/alpine-k8s:1.33.0\n")
            hits = scan(t)
            if len(hits) != 1 or "job.yaml" not in hits[0]:
                print(f"GUARD BROKEN: expected the one literal fixture to fail, got {hits}", file=sys.stderr); return 2
        if scan(ROOT):
            print("GUARD BROKEN: the tree carries a literal, see the plain run", file=sys.stderr); return 2
        print("✅ self-test: a literal tool image in a template is rejected, values files are not scanned, the tree is clean")
        return 0
    hits = scan(ROOT)
    if hits:
        print("❌ templates name the alpine-k8s tool image by hand; render `gibson.toolImage` instead (global.toolImage):")
        for h in hits:
            print("   " + h)
        return 1
    print("✅ no template names the alpine-k8s tool image by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
