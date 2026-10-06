#!/usr/bin/env python3
"""check-alert-runbooks.py: each alert rule names a runbook section that exists.

Owner decision of 2026-10-05: each alert rule names a runbook page, and a
guard fails on a rule with no runbook. The pages are in docs/runbooks/alerts/
of this repository, so a public reader of the chart can open them.

The guard reads each alert rule of each published profile, rendered with the
Prometheus Operator API present (the rules render only then), and each rule
file under helm/*/files/alerts/. For each rule it fails when:

  1. the rule has no runbook_url annotation;
  2. the URL does not start with BASE (a page of this repository);
  3. the page that the URL names does not exist;
  4. the page has no heading whose anchor is the URL fragment.

It reports how many rules it read, and it fails when that count is zero.

  check-alert-runbooks.py             exit 1 on a finding, 0 when clean
  check-alert-runbooks.py --selftest  prove each kind of finding fails
"""
import glob
import os
import re
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "https://github.com/zeroroot-ai/charts/blob/main/"
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)
CAPS = "monitoring.coreos.com/v1"


def anchor(heading: str) -> str:
    """The GitHub anchor of a Markdown heading."""
    a = heading.strip().lower()
    a = re.sub(r"[^\w\- ]", "", a)
    return a.replace(" ", "-")


def anchors(path: str) -> set[str]:
    return {anchor(m.group(1)) for m in re.finditer(r"^#{1,6}\s+(.+)$", open(path, encoding="utf-8").read(), re.M)}


def rules_of(docs: list[dict], where: str) -> list[tuple[str, dict]]:
    out = []
    for d in docs:
        if not d or d.get("kind") != "PrometheusRule":
            continue
        for g in (d.get("spec") or {}).get("groups") or []:
            for r in g.get("rules") or []:
                if "alert" in r:
                    out.append((f"{where}: {r['alert']}", r))
    return out


def render_rules() -> list[tuple[str, dict]]:
    out = []
    for profile in PROFILES:
        args = ["helm", "template", "gibson", "helm/gibson", "--namespace", "gibson", "--api-versions", CAPS,
                "-f", "helm/testdata/render-inputs/gibson.yaml"]
        for f in profile:
            args += ["-f", f"helm/gibson/{f}"]
        text = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
        out += rules_of(list(yaml.safe_load_all(text)), "+".join(profile))
    for f in sorted(glob.glob(os.path.join(ROOT, "helm", "*", "files", "alerts", "*.yaml"))):
        out += rules_of(list(yaml.safe_load_all(open(f))), os.path.relpath(f, ROOT))
    return out


def audit(rules: list[tuple[str, dict]], root: str = ROOT) -> list[str]:
    if not rules:
        return ["read 0 alert rules: this guard is blind"]
    bad = []
    for where, r in rules:
        url = str(((r.get("annotations") or {}).get("runbook_url")) or "")
        if not url:
            bad.append(f"{where}: no runbook_url annotation")
            continue
        if not url.startswith(BASE) or "#" not in url:
            bad.append(f"{where}: runbook_url {url!r} is not a section of a page under {BASE}")
            continue
        path, frag = url[len(BASE):].split("#", 1)
        full = os.path.join(root, path)
        if not os.path.isfile(full):
            bad.append(f"{where}: runbook page {path} does not exist")
        elif frag not in anchors(full):
            bad.append(f"{where}: runbook page {path} has no section #{frag}")
    return sorted(set(bad))


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        page = os.path.join("docs", "runbooks", "alerts", "x.md")
        os.makedirs(os.path.join(d, os.path.dirname(page)))
        open(os.path.join(d, page), "w").write("# X\n\n## GoodAlert\n\nSteps.\n")
        good = BASE + page.replace(os.sep, "/")

        def rule(name, url=None):
            ann = {"summary": "s"}
            if url is not None:
                ann["runbook_url"] = url
            return (f"fixture: {name}", {"alert": name, "annotations": ann})

        cases = [
            ("a rule with its section", [rule("GoodAlert", good + "#goodalert")], 0),
            ("a rule with no runbook_url", [rule("NoRunbook")], 1),
            ("a URL outside the repository", [rule("Elsewhere", "https://example.com/runbook#a")], 1),
            ("a page that does not exist", [rule("NoPage", BASE + "/".join(("docs", "runbooks", "alerts", "gone.md")) + "#nopage")], 1),
            ("a section that does not exist", [rule("NoSection", good + "#nosection")], 1),
            ("zero rules", [], 1),
        ]
        for what, rules, want in cases:
            got = audit(rules, d)
            if len(got) != want:
                print(f"SELFTEST FAIL: {what}: want {want} finding(s), got {got}")
                return 1
    print("  ✓ selftest: no runbook_url, a URL outside the repo, a missing page, a missing section and zero rules fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    rules = render_rules()
    bad = audit(rules)
    if bad:
        print("an alert rule names no runbook section that exists:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    names = {w.split(": ", 1)[1] for w, _ in rules}
    print(f"  ✓ alert-runbooks: {len(rules)} alert rules read ({len(names)} distinct), each names a runbook section that exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
