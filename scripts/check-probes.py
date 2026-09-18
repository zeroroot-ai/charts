#!/usr/bin/env python3
"""check-probes.py — no smoke or verify probe whose failure is swallowed.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1398).
A probe that ends in `|| true` cannot fail, and a test that cannot fail is
worse than no test: it reports green on a service that is down. The rule is
keyed by content: a line in a helm-test or hook template that CALLS a probe
(probe_http, probe_tcp, curl, wget, nc, psql, redis-cli, kubectl get/wait) as
its statement and ends in `|| true` fails. A line that CAPTURES the result
(`out=$(curl ... || true)`) and judges it, or a diagnostic (ls, cat, echo), is
not a probe and passes.

  check-probes.py             exit 1 on a swallowed probe, 0 when clean
  check-probes.py --selftest  prove a swallowed probe fails and a judged one passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SWALLOWED = re.compile(
    r"^\s*(?:probe_\w+|curl|wget|nc|psql|redis-cli|kubectl\s+(?:get|wait|rollout))\b[^=\n]*\|\|\s*true\s*$"
)


def scan(root: str) -> list[str]:
    hits = []
    for dirpath, dirs, files in os.walk(os.path.join(root, "helm")):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if "/charts" in rel or rel.startswith("helm/testdata"):
            dirs[:] = []
            continue
        if "/templates" not in rel:
            continue
        for f in files:
            if not f.endswith((".yaml", ".yml")):
                continue
            p = os.path.join(dirpath, f)
            for n, line in enumerate(open(p, encoding="utf-8", errors="replace"), 1):
                if SWALLOWED.match(line):
                    hits.append(f"{os.path.relpath(p, root)}:{n}: {line.strip()}")
    return hits


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "x", "templates", "tests")
        os.makedirs(t)
        open(os.path.join(t, "t.yaml"), "w").write(
            "args:\n  - |\n"
            '    probe_http "zitadel" "http://z:8080/debug/ready" "200" || true\n'
            "    curl -fsS http://a/healthz || true\n"
            "    out=$(curl -fsS http://a/healthz || true)\n"
            "    ls -la /tmp || true\n"
            '    probe_http "ok" "http://a" "200"\n'
        )
        hits = scan(d)
        if len(hits) != 2:
            print(f"SELFTEST FAIL: want the two swallowed probes and nothing else, got {hits}")
            return 1
    live = scan(ROOT)
    if live:
        print("SELFTEST FAIL: the tree swallows a probe:\n  " + "\n  ".join(live))
        return 1
    print("OK: a swallowed probe fails, a judged capture and a diagnostic pass, the tree is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    hits = scan(ROOT)
    if hits:
        print("❌ a probe whose failure is swallowed (a test that cannot fail):\n  " + "\n  ".join(hits))
        return 1
    print("✓ probes: no smoke or verify probe swallows its failure")
    return 0


if __name__ == "__main__":
    sys.exit(main())
