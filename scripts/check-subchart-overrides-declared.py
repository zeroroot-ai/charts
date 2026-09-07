#!/usr/bin/env python3
"""Every hand-overridden subchart image tag is declared as a version link (charts#14).

The umbrella values override images that its Helm dependencies ship (the
ZITADEL server tag, the OpenFGA tag, the tool images the zitadel subchart runs).
Each override is one copy of an upstream version that nobody compares against
the subchart's own appVersion or against upstream. zeroroot-ai/.github keeps
`version-links.yaml`, the one manifest of such links, and `version-drift.yml`
reports every declared link daily (epic zeroroot-ai/.github#20).

This guard closes the gap between the two: an override that is NOT declared is
invisible to the detector, so this fails `make check` for every `tag:` scalar
and every scalar `image:` string that sits under a top-level values key which
is a dependency (alias or name) in Chart.yaml, unless the manifest names that
exact key as a source or a consumer for this file.

Manifest location, first match wins:
  --manifest PATH             explicit file
  $VERSION_LINKS              path (CI checks out zeroroot-ai/.github and sets it)
  gh api on zeroroot-ai/.github (workstation, needs `gh auth`)

Usage:
  check-subchart-overrides-declared.py [--manifest PATH] [--values PATH] [--chart PATH]
  check-subchart-overrides-declared.py --selftest

Exit codes: 0 every override declared, 1 an override is undeclared, 2 the guard
itself cannot run (no manifest, yq missing).
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
VALUES = REPO_ROOT / "helm" / "gibson" / "values.yaml"
CHART = REPO_ROOT / "helm" / "gibson" / "Chart.yaml"
MANIFEST_REPO = "zeroroot-ai/.github"
MANIFEST_PATH = "version-links.yaml"
THIS_REPO = "zeroroot-ai/charts"
THIS_FILE = "helm/gibson/values.yaml"


def yaml_to_json(text: str) -> dict:
    r = subprocess.run(["yq", "-o=json", "-I=0", "."], input=text, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"guard broken: yq: {r.stderr.strip()[:200]}", file=sys.stderr)
        sys.exit(2)
    return json.loads(r.stdout or "{}")


def dependency_keys(chart: dict) -> set[str]:
    keys = set()
    for d in chart.get("dependencies", []):
        for k in (d.get("alias"), d.get("name")):
            if k:
                keys.add(str(k))
    return keys


def overrides(values: dict, dep_keys: set[str]) -> list[str]:
    """Dotted paths of every `tag:` scalar and scalar `image:` under a dependency key."""
    found: list[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("tag", "image") and isinstance(v, (str, int, float)) and str(v) != "":
                    found.append(".".join(path + [k]))
                walk(v, path + [k])
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, path + [str(i)])

    for top, node in values.items():
        if top in dep_keys:
            walk(node, [top])
    return sorted(found)


def declared_keys(manifest: dict) -> set[str]:
    keys = set()
    for link in manifest.get("links", []):
        src = link.get("source", {})
        if src.get("repo") == THIS_REPO and src.get("file") == THIS_FILE:
            keys.add(src["key"])
        for c in link.get("consumers", []):
            if c.get("repo") == THIS_REPO and c.get("file") == THIS_FILE:
                keys.add(c["key"])
    return keys


def load_manifest(explicit: str | None) -> dict:
    path = explicit or os.environ.get("VERSION_LINKS")
    if path:
        if not os.path.isfile(path):
            print(f"guard broken: manifest {path} does not exist", file=sys.stderr)
            sys.exit(2)
        return yaml_to_json(open(path).read())
    r = subprocess.run(["gh", "api", f"repos/{MANIFEST_REPO}/contents/{MANIFEST_PATH}", "--jq", ".content"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("guard broken: no --manifest, no $VERSION_LINKS, and `gh api` cannot read "
              f"{MANIFEST_REPO}:{MANIFEST_PATH}: {r.stderr.strip()[:200]}", file=sys.stderr)
        sys.exit(2)
    return yaml_to_json(base64.b64decode(r.stdout).decode())


def check(values: dict, chart: dict, manifest: dict) -> list[str]:
    return [k for k in overrides(values, dependency_keys(chart)) if k not in declared_keys(manifest)]


def selftest() -> int:
    chart = {"dependencies": [{"name": "zitadel", "alias": "zitadel", "version": "9.34.1"},
                              {"name": "openfga", "version": "0.3.4"},
                              {"name": "gibson-workloads", "version": "0.2.0"}]}
    values = {
        "zitadel": {"image": {"tag": "v4.14.0"},
                    "tools": {"kubectl": {"image": {"repository": "x", "tag": "1.33.0"}}},
                    "initContainers": [{"name": "w", "image": "ghcr.io/o/alpine-k8s:1.31.0"}],
                    "login": {"image": {"repository": "ghcr.io/o/login", "tag": "v4.17.3@sha256:abc"}}},
        "openfga": {"image": {"repository": "m/openfga", "tag": "v1.15.1"}},
        "gibson-workloads": {"daemon": {"image": {"tag": "0.1.0"}}},
        "global": {"registry": "ghcr.io"},
    }
    manifest = {"links": [
        {"name": "zitadel", "source": {"repo": "o/fork", "file": "UPSTREAM_REF", "key": "TAG"},
         "consumers": [{"repo": THIS_REPO, "file": THIS_FILE, "key": "zitadel.image.tag"},
                       {"repo": THIS_REPO, "file": THIS_FILE, "key": "zitadel.login.image.tag"}]},
        {"name": "openfga", "source": {"repo": THIS_REPO, "file": THIS_FILE, "key": "openfga.image.tag"}},
        {"name": "alpine-k8s", "source": {"repo": THIS_REPO, "file": THIS_FILE, "key": "zitadel.tools.kubectl.image.tag"},
         "consumers": [{"repo": THIS_REPO, "file": THIS_FILE, "key": "zitadel.initContainers.0.image"}]},
        {"name": "other-repo", "source": {"repo": "o/other", "file": THIS_FILE, "key": "gibson-workloads.daemon.image.tag"}},
    ]}
    passed = failed = 0

    def ok(cond, name):
        nonlocal passed, failed
        print(("PASS: " if cond else "FAIL: ") + name)
        passed += cond
        failed += not cond

    found = overrides(values, dependency_keys(chart))
    ok(found == ["gibson-workloads.daemon.image.tag", "openfga.image.tag", "zitadel.image.tag",
                 "zitadel.initContainers.0.image", "zitadel.login.image.tag", "zitadel.tools.kubectl.image.tag"],
       "every tag: and inline image: under a dependency key is found, list indices included, non-dependency keys skipped")
    ok(check(values, chart, manifest) == ["gibson-workloads.daemon.image.tag"],
       "an override declared only for another repo counts as undeclared")
    manifest["links"].append({"name": "wl", "source": {"repo": THIS_REPO, "file": THIS_FILE, "key": "gibson-workloads.daemon.image.tag"}})
    ok(check(values, chart, manifest) == [], "fully declared values pass")
    values["zitadel"]["tools"]["wait4x"] = {"image": {"tag": "3.6"}}
    ok(check(values, chart, manifest) == ["zitadel.tools.wait4x.image.tag"], "a NEW undeclared override fails (the fixture that must fail)")
    ok(check({"zitadel": {"image": {"tag": ""}}}, chart, {"links": []}) == [], "an empty tag is not an override")
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest")
    ap.add_argument("--values", default=str(VALUES))
    ap.add_argument("--chart", default=str(CHART))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    values = yaml_to_json(open(a.values).read())
    chart = yaml_to_json(open(a.chart).read())
    manifest = load_manifest(a.manifest)
    missing = check(values, chart, manifest)
    if missing:
        print("❌ subchart image overrides not declared in "
              f"{MANIFEST_REPO}:{MANIFEST_PATH} (source or consumer for {THIS_REPO}:{THIS_FILE}):")
        for k in missing:
            print(f"     {k}")
        print("   Add a link for each (charts#14, zeroroot-ai/.github#20) so version-drift.yml reports it.")
        return 1
    print(f"✅ every subchart image override in {os.path.relpath(a.values, REPO_ROOT)} is declared as a version link")
    return 0


if __name__ == "__main__":
    sys.exit(main())
