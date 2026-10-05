#!/usr/bin/env python3
"""check-trust-domain-literal.py: no file holds the SaaS trust domain as a SPIFFE ID literal (ADR-0164).

Each install has its own SPIFFE trust domain. One value names it,
`global.spire.trustDomain`, and the chart builds each SPIFFE ID from it and a
path (gibson.spiffeID). Two checks:

1. Source. No tracked file holds the literal `spiffe://` + `zeroroot.ai`.
   CHANGELOG.md is history and the golden files are a render, so the scan
   skips them. This file builds the literal from two parts, so it does not
   match itself.

2. Render. The baseline profile, rendered with a test trust domain, holds
   SPIFFE IDs of that domain only, and at least MIN_IDS of them. A render with
   an empty trust domain fails.

  check-trust-domain-literal.py             exit 1 on a finding, 0 when clean
  check-trust-domain-literal.py --selftest  prove a literal fails and a clean tree passes
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LITERAL = "spiffe://" + "zeroroot.ai"
SKIP_FILES = {"CHANGELOG.md"}
SKIP_DIRS = ("helm/testdata/",)
TEST_DOMAIN = "trust-domain.example.test"
SPIFFE_ID = re.compile(r"spiffe://([a-z0-9._-]+)/")
# The baseline render holds 25 platform SPIFFE IDs today. A floor below that
# proves that the render check looked.
MIN_IDS = 20


def tracked(root: str) -> list[str]:
    out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True).stdout
    return [p for p in out.decode().split("\0") if p]


def scan(root: str, files: list[str]) -> list[str]:
    hits = []
    for rel in files:
        if os.path.basename(rel) in SKIP_FILES or rel.startswith(SKIP_DIRS):
            continue
        path = os.path.join(root, rel)
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if re.search(re.escape(LITERAL) + r"(?![a-z0-9.-])", line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    return hits


def helm(extra: list[str]) -> subprocess.CompletedProcess:
    args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "-f", "helm/gibson/values-baseline.yaml"] + extra
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True)


def judge_render(text: str, domain: str) -> list[str]:
    domains = SPIFFE_ID.findall(text)
    bad = sorted({d for d in domains if d != domain})
    out = [f"the render with trust domain {domain} holds a SPIFFE ID of {d}" for d in bad]
    mine = sum(1 for d in domains if d == domain)
    if mine < MIN_IDS:
        out.append(f"the render holds {mine} SPIFFE ID(s) of {domain}, fewer than {MIN_IDS}: this check is blind")
    return out


def render_check() -> list[str]:
    good = helm(["--set", f"global.spire.trustDomain={TEST_DOMAIN}"])
    if good.returncode != 0:
        return [f"the render with trust domain {TEST_DOMAIN} failed: {good.stderr.strip()[-300:]}"]
    out = judge_render(good.stdout, TEST_DOMAIN)
    empty = helm(["--set", "global.spire.trustDomain="])
    if empty.returncode == 0:
        out.append("the render with an empty global.spire.trustDomain passed; it must fail")
    return out


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["git", "init", "-q", d], check=True)
        os.makedirs(os.path.join(d, "helm", "testdata"))
        files = {
            "bad.yaml": f"id: {LITERAL}/platform/daemon\n",
            "comment.sh": f"# {LITERAL} in a comment is still a literal\n",
            "ok.yaml": 'id: "spiffe://{{ include "gibson.trustDomain" . }}/platform/daemon"\n'
                       "other: spiffe://localhost.zeroroot.ai/platform/daemon\n",
            "CHANGELOG.md": f"history: {LITERAL}/platform/daemon\n",
            "helm/testdata/golden.yaml": f"id: {LITERAL}/platform/daemon\n",
        }
        for name, text in files.items():
            open(os.path.join(d, name), "w").write(text)
        subprocess.run(["git", "-C", d, "add", "."], check=True)
        hits = scan(d, tracked(d))
        if sorted(h.split(":")[0] for h in hits) != ["bad.yaml", "comment.sh"]:
            print(f"SELFTEST FAIL: want bad.yaml and comment.sh flagged, got {hits}")
            return 1
    fixture = (f"a: spiffe://{TEST_DOMAIN}/platform/x\n" * MIN_IDS)
    if judge_render(fixture, TEST_DOMAIN):
        print("SELFTEST FAIL: a render with only the test domain must pass")
        return 1
    if not judge_render(fixture + f"b: {LITERAL}/platform/daemon\n", TEST_DOMAIN):
        print("SELFTEST FAIL: a render with a second trust domain must fail")
        return 1
    if not judge_render(f"a: spiffe://{TEST_DOMAIN}/platform/x\n", TEST_DOMAIN):
        print("SELFTEST FAIL: a render with too few SPIFFE IDs must fail as blind")
        return 1
    print("  ✓ selftest: a literal in a file and in a comment fails, history and golden files are skipped, "
          "a second trust domain in the render fails, a blind render fails")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = scan(ROOT, tracked(ROOT)) + render_check()
    if bad:
        print("the SaaS trust domain is a literal, or the render does not follow global.spire.trustDomain (ADR-0164):",
              file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ trust-domain-literal: no tracked file holds the literal, and a render with {TEST_DOMAIN} "
          "holds SPIFFE IDs of that domain only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
