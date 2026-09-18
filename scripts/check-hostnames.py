#!/usr/bin/env python3
"""check-hostnames.py — no hardcoded platform hostname that is not derived from global.domain.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#630
S5). `global.domain` is the single source of truth for every external
hostname the platform serves or claims; the helpers in gibson-common derive
app., api., auth., www. and docs. from it. A literal `<label>.zeroroot.ai`
in a template is a hostname that does not move with the domain: it points a
customer install at the vendor's estate. The rule is keyed by content: any
literal host under zeroroot.ai in a template fails, except the two things
that are names and not hosts, the `gibson.zeroroot.ai` API group and the
`spiffe://zeroroot.ai` trust domain. Comment lines are skipped.

  check-hostnames.py             exit 1 on a literal hostname, 0 when clean
  check-hostnames.py --selftest  prove a literal app host fails and the API group passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = re.compile(r"(?<![A-Za-z0-9-])((?:[a-z0-9-]+\.)+zeroroot\.ai)\b")
ALLOWED = ("gibson.zeroroot.ai", "setec.zeroroot.ai")  # API groups and label keys


def scan(root: str) -> list[str]:
    hits = []
    for dirpath, dirs, files in os.walk(os.path.join(root, "helm")):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if "/charts" in rel or rel.startswith("helm/testdata") or "/crds" in rel or "/files/crds" in rel:
            dirs[:] = []
            continue
        if "/templates" not in rel:
            continue
        for f in files:
            if not f.endswith((".yaml", ".yml", ".tpl")):
                continue
            p = os.path.join(dirpath, f)
            in_comment = False
            for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                s = line.strip()
                if "{{/*" in s or "{{- /*" in s:
                    in_comment = True
                if in_comment:
                    if "*/}}" in s:
                        in_comment = False
                    continue
                if s.startswith("#"):
                    continue
                for m in HOST.finditer(line.split(" #", 1)[0]):
                    host = m.group(1)
                    if host in ALLOWED or host.endswith(".gibson.zeroroot.ai") or "spiffe://" + host in line:
                        continue
                    if "spiffe://zeroroot.ai" in line and host == "zeroroot.ai":
                        continue
                    hits.append(f"{os.path.relpath(p, root)}:{n}: {host}")
    return hits


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "x", "templates")
        os.makedirs(t)
        open(os.path.join(t, "a.yaml"), "w").write(
            "{{/* a comment naming app.zeroroot.ai is fine */}}\n"
            "# so is a shell comment: api.zeroroot.ai\n"
            "group: gibson.zeroroot.ai\n"
            "kind: tenants.gibson.zeroroot.ai\n"
            "id: spiffe://zeroroot.ai/platform/daemon\n"
            "labels: {setec.zeroroot.ai/sandbox-namespace: \"true\"}\n"
            "url: https://app.zeroroot.ai/login\n"
            "issuer: {{ include \"gibson.oidcIssuer\" . }}\n"
        )
        hits = scan(d)
        if len(hits) != 1 or not hits[0].endswith("app.zeroroot.ai"):
            print(f"SELFTEST FAIL: want exactly the literal app host flagged, got {hits}")
            return 1
    live = scan(ROOT)
    if live:
        print("SELFTEST FAIL: the tree has a literal platform hostname:\n  " + "\n  ".join(live))
        return 1
    print("OK: a literal app host fails; the API group, the trust domain and comments pass; the tree is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    hits = scan(ROOT)
    if hits:
        print("❌ literal platform hostnames (derive them from global.domain through the gibson-common helpers):\n  " + "\n  ".join(hits))
        return 1
    print("✓ hostnames: no literal platform hostname; every host derives from global.domain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
