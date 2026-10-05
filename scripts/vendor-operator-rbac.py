#!/usr/bin/env python3
"""Vendor the RBAC of cert-manager and external-secrets into the umbrella, wave-annotated.

ADR-0083 and deploy#1728. Under Argo the umbrella is one Application applied
in sync-waves, and the ClusterSecretStore (-10), the ExternalSecrets (-8)
and the postgres Cluster (-7) are only Healthy once their operator
reconciles them. So every resource of those operators must sync earlier:
the chart puts them at wave -20. cert-manager and external-secrets expose an
annotation seam for their Deployments, ServiceAccounts, Services and webhook
configurations, but NOT for their RBAC, and a Deployment at -20 whose
ClusterRole lands at 0 never becomes Healthy. The subcharts therefore render
with RBAC off (values.yaml: global.rbac.create / rbac.create false) and this
script writes the same RBAC, from the exact chart version and values the
umbrella uses, with the wave annotation, to

    helm/gibson/templates/operators/<chart>-rbac.yaml

ONE SOURCE OF TRUTH. The RBAC is rendered from the tarball in
helm/gibson/charts/ with the subchart's values block from
helm/gibson/values.yaml and only the rbac toggles flipped on. Bump the chart
or change its values and --check fails until you rerun this script.

The rendered file is a Helm template: the release name and namespace are
rendered through placeholders and written back as {{ .Release.Name }} and
{{ .Release.Namespace }}, so a customer's release in another namespace binds
the right ServiceAccounts.

Modes:
    (default)    regenerate the two templates
    --check      exit 1 when a committed template differs from a fresh render
    --selftest   prove --check fails on a mutated copy (exit 2 if it cannot)

Needs helm on PATH and the umbrella's charts/ built (`make chart-deps`).
Exit: 0 ok · 1 drift or missing input · 2 self-test broken
"""

from __future__ import annotations

import copy
import pathlib
import subprocess
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
UMBRELLA = ROOT / "helm" / "gibson"
DEST = UMBRELLA / "templates" / "operators"
WAVE = {"argocd.argoproj.io/sync-wave": "-20"}
RBAC_KINDS = {"ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"}

# Placeholders survive `helm template` untouched and are swapped for the
# template expressions on the way out (and back on the way in for --check).
REL_NAME = "zzrelnamezz"
REL_NS = "zzrelnszz"
SUBST = ((REL_NAME, "{{ .Release.Name }}"), (REL_NS, "{{ .Release.Namespace }}"))

# chart name -> the values that turn the upstream RBAC back on.
OPERATORS: dict[str, list[str]] = {
    "cert-manager": ["global.rbac.create=true"],
    "external-secrets": ["rbac.create=true", "certController.rbac.create=true"],
}

# Upstream rules the umbrella removes from a vendored ClusterRole, with the
# reason, as (chart, ClusterRole, resource, verb). A removed rule that the
# operator still needs comes back as a namespaced, name-scoped Role in a
# hand-written template, which the reason names.
#
# external-secrets-controller: `create serviceaccounts/token` on every
# ServiceAccount in the cluster. The controller mints a token only for the
# ServiceAccount a store names for its own auth. The umbrella's stores name
# two: its own ServiceAccount (the OpenBao ClusterSecretStore) and
# gibson-setec-cert-reader (the setec mirror store). Minting a token for any
# other ServiceAccount would let the controller act as that workload, which
# is the same as becoming it. The two grants it needs are
# templates/operators/external-secrets-token-rbac.yaml and the Role beside
# the setec store (gibson-workloads templates/setec/daemon-client-secret.yaml).
REMOVED_RULES: list[tuple[str, str, str, str]] = [
    ("external-secrets", "external-secrets-controller", "serviceaccounts/token", "create"),
]


def remove_rules(name: str, docs: list[dict]) -> None:
    for chart, role, resource, verb in REMOVED_RULES:
        if chart != name:
            continue
        hit = False
        for d in docs:
            if d["kind"] != "ClusterRole" or d["metadata"]["name"] != role:
                continue
            kept = []
            for r in d.get("rules") or []:
                if resource in (r.get("resources") or []) and verb in (r.get("verbs") or []):
                    hit = True
                    r = copy.deepcopy(r)
                    r["resources"] = [x for x in r["resources"] if x != resource]
                    if not r["resources"]:
                        continue
                kept.append(r)
            d["rules"] = kept
        if not hit:
            sys.exit(f"ERROR: {chart} no longer renders `{verb} {resource}` in ClusterRole {role}; "
                     "REMOVED_RULES is stale, so drop that entry")


def pinned_versions() -> dict[str, str]:
    chart = yaml.safe_load((UMBRELLA / "Chart.yaml").read_text())
    return {d["name"]: d["version"] for d in chart.get("dependencies", [])}


def umbrella_values(name: str) -> dict:
    values = yaml.safe_load((UMBRELLA / "values.yaml").read_text()) or {}
    block = values.get(name)
    if not isinstance(block, dict):
        sys.exit(f"ERROR: helm/gibson/values.yaml has no `{name}:` block; the umbrella must configure it")
    return block


def render_rbac(name: str, version: str) -> list[dict]:
    tgz = UMBRELLA / "charts" / f"{name}-{version}.tgz"
    if not tgz.exists():
        sys.exit(f"ERROR: {tgz.relative_to(ROOT)} is missing. Run `make chart-deps` first.")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as vf:
        yaml.safe_dump(umbrella_values(name), vf)
        vpath = vf.name
    cmd = ["helm", "template", REL_NAME, str(tgz), "--namespace", REL_NS, "-f", vpath]
    for kv in OPERATORS[name]:
        cmd += ["--set", kv]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    finally:
        pathlib.Path(vpath).unlink(missing_ok=True)
    docs = []
    for d in yaml.safe_load_all(out):
        if not isinstance(d, dict) or d.get("kind") not in RBAC_KINDS:
            continue
        d = copy.deepcopy(d)
        md = d.setdefault("metadata", {})
        ann = md.get("annotations") or {}
        ann.update(WAVE)
        md["annotations"] = ann
        docs.append(d)
    remove_rules(name, docs)
    docs.sort(key=lambda d: (d["kind"], d["metadata"].get("namespace", ""), d["metadata"]["name"]))
    if not docs:
        sys.exit(f"ERROR: {name} {version} rendered no RBAC with {OPERATORS[name]}")
    return docs


# chart name -> the umbrella value that selects whose operator runs
# ([[0087]]). The RBAC belongs to the operator the chart installs; a cluster
# that owns its own already has it, and applying ours collides with the live
# ClusterRole (found by a real guest-cluster install, which a render test
# cannot see). So the generated file renders only when the seam is on.
SEAMS = {"cert-manager": "certManager", "external-secrets": "externalSecrets"}
SEAM_OPEN = (
    "{{{{- /* Gated on the {seam} seam. This RBAC belongs to the {pretty} the chart\n"
    "     installs; a cluster that owns its own already has it, and applying ours\n"
    "     collides with the live ClusterRole. Found by a real guest-cluster\n"
    "     install, which a render test cannot see. */}}}}\n"
    "{{{{- if (.Values.{seam}).enabled }}}}\n"
)
SEAM_CLOSE = "{{- end }}\n"
PRETTY = {"cert-manager": "cert-manager", "external-secrets": "External Secrets"}


def dump(name: str, version: str, docs: list[dict]) -> str:
    header = SEAM_OPEN.format(seam=SEAMS[name], pretty=PRETTY[name]) + (
        "{{- /*\n"
        f"GENERATED by scripts/vendor-operator-rbac.py from {name}-{version}.tgz with the\n"
        f"`{name}:` block of values.yaml and its rbac toggles flipped on, less the rules\n"
        "REMOVED_RULES in that script names with its reason. Do not edit:\n"
        "run `make vendor-operators` and commit the result. Every object carries\n"
        "argocd.argoproj.io/sync-wave -20, the wave the operator's Deployment syncs\n"
        "at (deploy#1728); the upstream chart has no seam to put it there itself.\n"
        "*/}}\n"
    )
    body = yaml.safe_dump_all(docs, sort_keys=False, width=1 << 20, allow_unicode=True,
                              explicit_start=True)
    for placeholder, expr in SUBST:
        body = body.replace(placeholder, expr)
    return header + body + SEAM_CLOSE


def load_committed(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    text = path.read_text()
    for placeholder, expr in SUBST:
        text = text.replace(expr, placeholder)
    # Drop the leading seam gate and {{- /* ... */}} comment: everything
    # before the first '---'. Then the seam's closing `end`.
    text = text[text.index("\n---") + 1:] if "\n---" in text else text
    if text.rstrip().endswith(SEAM_CLOSE.strip()):
        text = text.rstrip()[: -len(SEAM_CLOSE.strip())]
    return [d for d in yaml.safe_load_all(text) if isinstance(d, dict)]


def check(versions: dict[str, str]) -> list[str]:
    problems = []
    for name in OPERATORS:
        fresh = render_rbac(name, versions[name])
        path = DEST / f"{name}-rbac.yaml"
        if load_committed(path) != fresh:
            problems.append(f"{path.relative_to(ROOT)} differs from {name}-{versions[name]}.tgz")
    return problems


def selftest(versions: dict[str, str]) -> int:
    name = "external-secrets"
    fresh = render_rbac(name, versions[name])
    # 1. Round trip: a freshly dumped file must load back equal (the substitution is lossless).
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as tf:
        tf.write(dump(name, versions[name], fresh))
        tpath = pathlib.Path(tf.name)
    try:
        if load_committed(tpath) != fresh:
            print("GUARD BROKEN: a freshly written template does not round-trip through --check", file=sys.stderr)
            return 2
    finally:
        tpath.unlink(missing_ok=True)
    # 2. A dropped rule and a dropped wave annotation must both count as drift.
    mutated = copy.deepcopy(fresh)
    mutated[0]["rules" if "rules" in mutated[0] else "subjects"].pop()
    if mutated == fresh:
        print("GUARD BROKEN: removing a rule went unnoticed", file=sys.stderr)
        return 2
    mutated = copy.deepcopy(fresh)
    mutated[0]["metadata"]["annotations"].pop("argocd.argoproj.io/sync-wave")
    if mutated == fresh:
        print("GUARD BROKEN: removing the sync-wave annotation went unnoticed", file=sys.stderr)
        return 2
    # 3. A removed upstream rule stays removed, and one put back is drift.
    for chart, role, resource, verb in REMOVED_RULES:
        docs = fresh if chart == name else render_rbac(chart, versions[chart])
        cr = next(d for d in docs if d["kind"] == "ClusterRole" and d["metadata"]["name"] == role)
        if any(resource in (r.get("resources") or []) and verb in (r.get("verbs") or []) for r in cr["rules"]):
            print(f"GUARD BROKEN: {role} still grants {verb} {resource}", file=sys.stderr)
            return 2
        restored = copy.deepcopy(docs)
        next(d for d in restored if d["kind"] == "ClusterRole" and d["metadata"]["name"] == role)["rules"].append(
            {"apiGroups": [""], "resources": [resource], "verbs": [verb]})
        if restored == docs:
            print(f"GUARD BROKEN: putting back {verb} {resource} on {role} went unnoticed", file=sys.stderr)
            return 2
    print("✅ selftest passed: --check round-trips, and detects a dropped rule, a dropped wave "
          "and a removed upstream rule put back")
    return 0


def main(argv: list[str]) -> int:
    versions = pinned_versions()
    missing = [n for n in OPERATORS if n not in versions]
    if missing:
        print(f"ERROR: helm/gibson/Chart.yaml no longer lists {missing}; the umbrella must own them (deploy#1728)")
        return 1
    if "--selftest" in argv:
        return selftest(versions)
    if "--check" in argv:
        problems = check(versions)
        if problems:
            print("❌ vendored operator RBAC is stale:")
            for p in problems:
                print(f"   {p}")
            print("   Run `make vendor-operators` and commit helm/gibson/templates/operators/.")
            return 1
        print("✅ vendored operator RBAC matches the pinned subcharts and values")
        return 0
    DEST.mkdir(parents=True, exist_ok=True)
    for name in OPERATORS:
        docs = render_rbac(name, versions[name])
        path = DEST / f"{name}-rbac.yaml"
        path.write_text(dump(name, versions[name], docs))
        print(f"  wrote {path.relative_to(ROOT)} ({len(docs)} objects from {name}-{versions[name]})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
