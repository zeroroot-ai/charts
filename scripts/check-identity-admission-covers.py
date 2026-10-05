#!/usr/bin/env python3
"""check-identity-admission-covers.py — every identity the chart registers is
one the admission policy protects.

clusterspiffeids.yaml registers identities: a pod with the right component
label and ServiceAccount gets the SVID. platform-identity-admission.yaml lets
only a workload controller create such a pod. The policy keys on two lists, a
component list and a ServiceAccount list, and a comment used to say that the
lists mirror the identities. Nothing held the two together, so an identity
could be registered with no admission rule in front of it.

This check renders both and fails when a first-party ClusterSPIFFEID (a
`platform/...` or `plugin/...` SPIFFE ID) has a podSelector component, or a
`k8s:sa:` selector, that the policy's lists do not hold. The vendored spire
chart renders identities of its own under other paths. This check holds those
to a closed list: the two that have a workload in this chart. Any other
identity from the subchart is a registration with no workload, and it fails.
The spire chart turns on four SPIKE identities by default, and this chart
deploys no SPIKE. It renders the baseline twice: as shipped, and with
one plugin enabled, because the per-plugin identities exist only then.

Two identities are outside the lists on purpose. Each is named here with its
reason, by SPIFFE ID path, and a name that matches no rendered identity fails
the check, so the list cannot grow stale:

  platform/helm-test   an SVID issuance probe that appears in no peer list.
                       Protecting it would put the installer in the creator
                       allow-list (clusterspiffeids.yaml).
  platform/e2e-runner  rendered only under gibson.e2eRunner.enabled, which no
                       shipped profile sets, and a production daemon holds no
                       policy for it.

  check-identity-admission-covers.py             exit 1 on an uncovered identity
  check-identity-admission-covers.py --selftest  prove each shape fails
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNPROTECTED_ON_PURPOSE = {"platform/helm-test", "platform/e2e-runner"}
FIRST_PARTY = ("platform/", "plugin/")
# The subchart identities that have a workload here, by ClusterSPIFFEID name
# suffix: the generic per-ServiceAccount fallback for other namespaces, and the
# OIDC discovery provider. test-keys belongs to the spire chart's own helm test.
SUBCHART_WITH_WORKLOAD = ("-default", "-oidc-discovery-provider", "-test-keys")
POLICY_NAME_PART = "platform-identity"
LISTS = re.compile(r"variables\.component in (\[[^\]]*\])\s*\|\|\s*variables\.sa in (\[[^\]]*\])")


def helm_template(extra: list[str]) -> list[dict]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/gibson/values-baseline.yaml",
            "-f", "helm/testdata/render-inputs/gibson.yaml", "--namespace", "gibson"] + extra
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def policy_lists(docs: list[dict]) -> list[tuple[str, set[str], set[str]]]:
    """(policy name, components, service accounts) for each admission policy
    that decides on an identity claim."""
    out = []
    for d in docs:
        if d.get("kind") != "ValidatingAdmissionPolicy":
            continue
        for var in (d.get("spec") or {}).get("variables") or []:
            m = LISTS.search(str(var.get("expression", "")))
            if m:
                out.append((d["metadata"]["name"], set(json.loads(m.group(1))), set(json.loads(m.group(2)))))
    return out


def unowned(docs: list[dict]) -> list[str]:
    """Each ClusterSPIFFEID that is neither first-party nor a subchart identity
    with a workload in this chart."""
    out = []
    for d in docs:
        if d.get("kind") != "ClusterSPIFFEID":
            continue
        path = str((d.get("spec") or {}).get("spiffeIDTemplate", "")).split("/", 3)[-1]
        name = d["metadata"]["name"]
        if path.startswith(FIRST_PARTY) or name.endswith(SUBCHART_WITH_WORKLOAD):
            continue
        out.append(f"{name} registers {path}, and this chart deploys no workload for it")
    return out


def identities(docs: list[dict]) -> list[tuple[str, str, list[str]]]:
    """(spiffe path, podSelector component, k8s:sa names) for each ClusterSPIFFEID."""
    out = []
    for d in docs:
        if d.get("kind") != "ClusterSPIFFEID":
            continue
        spec = d.get("spec") or {}
        path = str(spec.get("spiffeIDTemplate", "")).split("/", 3)[-1]
        comp = (((spec.get("podSelector") or {}).get("matchLabels")) or {}).get("app.kubernetes.io/component", "")
        sas = [t.split("k8s:sa:", 1)[1] for t in spec.get("workloadSelectorTemplates") or [] if "k8s:sa:" in t]
        out.append((path, comp, sas))
    return out


def judge(docs: list[dict]) -> tuple[list[str], int]:
    """Findings, and how many identities were checked against the lists."""
    policies = policy_lists(docs)
    ids = identities(docs)
    out: list[str] = []
    if not policies:
        return ["the render holds no admission policy with the two protected lists: this check read nothing"], 0
    if not any(p.startswith(FIRST_PARTY) for p, _, _ in ids):
        return ["the render holds no first-party ClusterSPIFFEID: this check read nothing"], 0
    out += unowned(docs)
    checked = 0
    for path, comp, sas in ids:
        if not path.startswith(FIRST_PARTY) or path in UNPROTECTED_ON_PURPOSE:
            continue
        checked += 1
        for name, comps, protected_sas in policies:
            if comp not in comps:
                out.append(f"{path}: component {comp!r} is not in the protected components of {name}")
            for sa in sas:
                if sa not in protected_sas:
                    out.append(f"{path}: ServiceAccount {sa!r} is not in the protected ServiceAccounts of {name}")
            if not sas:
                out.append(f"{path}: the identity has no k8s:sa: selector, so no ServiceAccount bounds it")
    return out, checked


POLICY_FIXTURE = {
    "kind": "ValidatingAdmissionPolicy", "metadata": {"name": "fixture-platform-identity"},
    "spec": {"variables": [{"name": "claimsPlatformIdentity", "expression":
             'variables.component in ["daemon","plugin"] || variables.sa in ["gibson","gibson-plugin-acme"]'}]},
}


def csid(path: str, comp: str, sa: str | None) -> dict:
    sel = [f"k8s:ns:gibson"] + ([f"k8s:sa:{sa}"] if sa else [])
    return {"kind": "ClusterSPIFFEID", "metadata": {"name": path.replace("/", "-")},
            "spec": {"spiffeIDTemplate": f"spiffe://zeroroot.ai/{path}",
                     "podSelector": {"matchLabels": {"app.kubernetes.io/component": comp}},
                     "workloadSelectorTemplates": sel}}


def selftest() -> int:
    good = [POLICY_FIXTURE, csid("platform/daemon", "daemon", "gibson"),
            csid("plugin/acme", "plugin", "gibson-plugin-acme"),
            csid("platform/helm-test", "helm-test", "gibson-helm-test")]
    found, checked = judge(good)
    if found or checked != 2:
        print(f"selftest: covered identities must pass, and the probe is not counted: {found} {checked}", file=sys.stderr)
        return 1
    cases = [
        ("an identity whose component is outside the list", csid("platform/new", "new", "gibson")),
        ("a plugin whose ServiceAccount is outside the list", csid("plugin/other", "plugin", "gibson-plugin-other")),
        ("an identity with no ServiceAccount selector", csid("platform/daemon2", "daemon", None)),
    ]
    for what, doc in cases:
        found, _ = judge([POLICY_FIXTURE, doc])
        if len(found) != 1:
            print(f"selftest: {what} must give one finding, gave {found}", file=sys.stderr)
            return 1
    spike = {"kind": "ClusterSPIFFEID", "metadata": {"name": "gibson-gibson-spike-pilot"},
             "spec": {"spiffeIDTemplate": "spiffe://zeroroot.ai/spike/pilot/role/superuser"}}
    kept = {"kind": "ClusterSPIFFEID", "metadata": {"name": "gibson-gibson-default"},
            "spec": {"spiffeIDTemplate": "spiffe://zeroroot.ai/ns/x/sa/y"}}
    if len(judge(good + [spike])[0]) != 1 or judge(good + [kept])[0]:
        print("selftest: a subchart identity with no workload must fail, and the fallback must pass", file=sys.stderr)
        return 1
    if not judge([csid("platform/daemon", "daemon", "gibson")])[0]:
        print("selftest: a render with no policy must fail", file=sys.stderr)
        return 1
    print("check-identity-admission-covers selftest PASSED (an uncovered component, an uncovered "
          "ServiceAccount, an identity with no ServiceAccount, a registration with no workload "
          "and a missing policy each fail)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    plugin = ["--set", "gibson-workloads.plugins.guardfixture.enabled=true",
              "--set", "gibson-workloads.plugins.guardfixture.image.repository=ghcr.io/zeroroot-ai/integrations/guardfixture",
              "--set", "gibson-workloads.plugins.guardfixture.image.tag=0.0.0"]
    problems: list[str] = []
    total = 0
    seen_paths: set[str] = set()
    for label, extra in (("the baseline", []), ("the baseline with one plugin enabled", plugin)):
        docs = helm_template(extra)
        found, checked = judge(docs)
        total += checked
        seen_paths |= {p for p, _, _ in identities(docs)}
        problems += [f"{label}: {f}" for f in found]
        if extra and not any(p.startswith("plugin/") for p, _, _ in identities(docs)):
            problems.append(f"{label}: the render holds no plugin identity, so the plugin half read nothing")
    # A named exception that no profile can render is checked only when its
    # gate is on. helm-test renders on every profile, so it must be present.
    if "platform/helm-test" not in seen_paths:
        problems.append("platform/helm-test is named as unprotected on purpose, and no render holds it")
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-identity-admission-covers PASSED ({total} identity checks over two renders, plugin identities included)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
