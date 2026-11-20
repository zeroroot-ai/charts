#!/usr/bin/env python3
"""check-velero-reads-served-kinds.py: a Backup can list each namespaced kind that the charts serve.

A Backup lists every namespaced kind that the cluster serves and the Backup
does not exclude. Velero counts a forbidden list as a backup error, so the
Backup ends PartiallyFailed. The tenant-operator then refuses to delete a
tenant, because it requires a completed last backup.

charts#818 added 11 namespaced CRDs and did not change helm/gibson-velero. The
earlier version of this check held a hand list of 7 kinds, so it passed, and
no backup completed on chart 0.155.0.

The check derives the kinds. It holds no list of the kinds of a chart.

  Served kinds    each CustomResourceDefinition with scope Namespaced and a
                  served version, from three sources:
                    1. the committed render of each CRD release
                       (helm/testdata/golden/gibson-crds.yaml,
                       gibson-operator-crds.yaml and gibson-velero.yaml)
                    2. each vendored CRD file (helm/*/files/crds/*.yaml)
                    3. each crds/ file of a packed subchart
                       (helm/*/charts/*.tgz), which no render of the umbrella
                       contains
  The rule        the Schedule platform-hourly excludes the kind
                  (excludedNamespaceScopedResources), or the ClusterRole
                  velero-server can get and list it. Each Backup of the
                  platform comes from the template of that Schedule.

Two more rules:

  * Each kind in includedClusterScopedResources of the Schedule must be
    readable. A Backup with the scoped filters lists a cluster-scoped kind
    only when the Schedule names it.
  * Each kind of SERVED_WITHOUT_CRD_FILE must be readable. Kubernetes and
    Cilium serve these kinds, and no chart of this repository ships a CRD
    file for them, so no source above can show them. Each one was a measured
    listing error (charts#660, backup-restore run 38201546512).

  check-velero-reads-served-kinds.py             exit 1 on a finding
  check-velero-reads-served-kinds.py --selftest  prove each rule fails
"""
import copy
import fnmatch
import glob
import os
import sys
import tarfile

import yaml

try:
    from yaml import CSafeLoader as Loader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as Loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(ROOT, "helm", "testdata", "golden")
VELERO_GOLDEN = os.path.join(GOLDEN_DIR, "gibson-velero.yaml")
CRD_GOLDENS = ["gibson-crds.yaml", "gibson-operator-crds.yaml", "gibson-velero.yaml"]
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "velero-served-kinds", "unread-kind-crd.yaml")
ROLE = "velero-server"
SCHEDULE = "platform-hourly"

# (apiGroup, resource) of each namespaced kind that Kubernetes or Cilium
# serves. No chart here ships a CRD file for them.
SERVED_WITHOUT_CRD_FILE = [
    ("resource.k8s.io", "resourceclaims"),
    ("resource.k8s.io", "resourceclaimtemplates"),
    ("certificates.k8s.io", "podcertificaterequests"),
    ("cilium.io", "ciliumnetworkpolicies"),
    ("cilium.io", "ciliumendpoints"),
    ("cilium.io", "ciliumnodeconfigs"),
    ("events.k8s.io", "events"),
]


def crds_of(docs) -> set[tuple[str, str, str]]:
    """Return (scope, group, plural) of each CRD with a served version."""
    out = set()
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") != "CustomResourceDefinition":
            continue
        spec = d.get("spec") or {}
        if not any(v.get("served") for v in spec.get("versions") or []):
            continue
        out.add((spec.get("scope"), spec.get("group"), (spec.get("names") or {}).get("plural")))
    return out


def load_docs(stream) -> list:
    return [d for d in yaml.load_all(stream, Loader=Loader) if d]


def shipped_crds() -> tuple[set[tuple[str, str, str]], int]:
    """Return the CRDs of the three sources and the count of packed subcharts read."""
    found: set[tuple[str, str, str]] = set()
    for name in CRD_GOLDENS:
        with open(os.path.join(GOLDEN_DIR, name)) as f:
            found |= crds_of(load_docs(f))
    for path in sorted(glob.glob(os.path.join(ROOT, "helm", "*", "files", "crds", "*.yaml"))):
        with open(path) as f:
            found |= crds_of(load_docs(f))
    packed = sorted(glob.glob(os.path.join(ROOT, "helm", "*", "charts", "*.tgz")))
    for path in packed:
        with tarfile.open(path) as tar:
            for member in tar:
                parts = member.name.split("/")
                # Helm's crds/ directory holds plain YAML. A CRD under templates/
                # is a template, and the render of its CRD release shows it.
                if member.isfile() and "crds" in parts[:-1] and "templates" not in parts \
                        and parts[-1].endswith((".yaml", ".yml")):
                    found |= crds_of(load_docs(tar.extractfile(member).read()))
    return found, len(packed)


def find(docs, kind, name):
    for d in docs:
        if isinstance(d, dict) and d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name:
            return d
    return None


def can(role, group, resource, verb) -> bool:
    for rule in role.get("rules") or []:
        groups = rule.get("apiGroups") or []
        resources = rule.get("resources") or []
        verbs = rule.get("verbs") or []
        if (group in groups or "*" in groups) and (resource in resources or "*" in resources) \
                and (verb in verbs or "*" in verbs):
            return True
    return False


def excluded(patterns, group, resource) -> bool:
    """Velero matches a filter entry against `resource.group`, with globs."""
    full = f"{resource}.{group}" if group else resource
    return any(fnmatch.fnmatchcase(full, p) or p == resource for p in patterns)


def missing(verb: str, group: str, resource: str, why: str) -> str:
    return f"ClusterRole {ROLE} cannot {verb} {resource}.{group}, {why}: a Backup that lists it ends PartiallyFailed"


NOT_EXCLUDED = f"and the Schedule {SCHEDULE} does not exclude it"
NAMED = f"and the Schedule {SCHEDULE} names it in includedClusterScopedResources"
NO_CRD_FILE = "a kind that Kubernetes or Cilium serves"


def judge(velero_docs, crds) -> list[str]:
    role = find(velero_docs, "ClusterRole", ROLE)
    if role is None:
        return [f"no ClusterRole {ROLE} in the render: the check would measure nothing"]
    schedule = find(velero_docs, "Schedule", SCHEDULE)
    if schedule is None:
        return [f"no Schedule {SCHEDULE} in the render: the check would measure nothing"]
    template = (schedule.get("spec") or {}).get("template") or {}
    patterns = template.get("excludedNamespaceScopedResources") or []
    namespaced = sorted((g, r) for scope, g, r in crds if scope == "Namespaced")
    if not namespaced:
        return ["no namespaced CustomResourceDefinition found: the check would measure nothing"]
    bad = []
    for group, resource in namespaced:
        if excluded(patterns, group, resource):
            continue
        bad += [missing(v, group, resource, NOT_EXCLUDED) for v in ("get", "list") if not can(role, group, resource, v)]
    for entry in template.get("includedClusterScopedResources") or []:
        resource, _, group = entry.partition(".")
        bad += [missing(v, group, resource, NAMED) for v in ("get", "list") if not can(role, group, resource, v)]
    for group, resource in SERVED_WITHOUT_CRD_FILE:
        bad += [missing(v, group, resource, NO_CRD_FILE) for v in ("get", "list") if not can(role, group, resource, v)]
    return bad


def load_velero():
    with open(VELERO_GOLDEN) as f:
        return load_docs(f)


def selftest() -> int:
    docs = load_velero()
    crds, _ = shipped_crds()
    if judge(docs, crds):
        print("GUARD BROKEN: the committed render already fails; fix it before the self-test", file=sys.stderr)
        return 2

    def both(group, resource, why):
        return {missing(v, group, resource, why) for v in ("get", "list")}

    def drop_rule(group, resource):
        broken = copy.deepcopy(docs)
        for rule in find(broken, "ClusterRole", ROLE).get("rules") or []:
            if group in (rule.get("apiGroups") or []):
                rule["resources"] = [r for r in rule.get("resources") or [] if r != resource]
        return broken

    def drop_exclusion(pattern):
        broken = copy.deepcopy(docs)
        template = find(broken, "Schedule", SCHEDULE)["spec"]["template"]
        template["excludedNamespaceScopedResources"].remove(pattern)
        return broken

    # THE FIXTURE THIS CHECK EXISTS FOR: a new CRD file, and no change in
    # helm/gibson-velero (charts#818).
    with open(FIXTURE) as f:
        fixture = crds_of(load_docs(f))
    cases = [
        ("a new namespaced CRD that the role cannot read and the Schedule does not exclude",
         set(judge(docs, crds | fixture)), both("fixture.zeroroot.test", "widgets", NOT_EXCLUDED)),
        ("a role with no read of httproutes",
         set(judge(drop_rule("gateway.networking.k8s.io", "httproutes"), crds)),
         both("gateway.networking.k8s.io", "httproutes", NOT_EXCLUDED)),
        ("a Schedule that does not exclude the agentgateway kinds",
         {b.split(",")[0] for b in judge(drop_exclusion("*.agentgateway.dev"), crds)},
         {missing(v, "agentgateway.dev", r, "").split(",")[0] for v in ("get", "list") for r in (
             "agentgatewaybackends", "agentgatewaymodels", "agentgatewayparameters", "agentgatewaypolicies")}),
        ("a role with no read of tenants, which the Schedule names",
         set(judge(drop_rule("gibson.zeroroot.ai", "tenants"), crds)),
         both("gibson.zeroroot.ai", "tenants", NAMED)),
        ("a role with no read of ciliumendpoints",
         set(judge(drop_rule("cilium.io", "ciliumendpoints"), crds)),
         both("cilium.io", "ciliumendpoints", NO_CRD_FILE)),
    ]
    failed = [desc for desc, found, want in cases if found != want]
    for kind in ("ClusterRole", "Schedule"):
        if not judge([d for d in docs if d.get("kind") != kind], crds):
            failed.append(f"a render with no {kind}")
    if not judge(docs, {c for c in crds if c[0] != "Namespaced"}):
        failed.append("a tree with no namespaced CRD")
    if failed:
        print("GUARD BROKEN: these broken inputs did not give the expected finding: " + "; ".join(failed),
              file=sys.stderr)
        return 2
    print(f"✅ self-test: {len(cases) + 3} broken inputs each fail, and the committed render passes")
    return 0


def main(argv) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    crds, packed = shipped_crds()
    if not packed:
        print("❌ no packed subchart under helm/*/charts: the CRDs of the subcharts are not measured. "
              "Run `make chart-deps` first.")
        return 1
    bad = judge(load_velero(), crds)
    if bad:
        print("❌ a Backup cannot list a served kind (charts#660, charts#684, charts#818):")
        for b in bad:
            print("   ", b)
        print("    Decide for each kind (helm/gibson-velero/templates/rbac.yaml states the rule): grant "
              "get, list and watch in the ClusterRole, or exclude it in templates/schedule.yaml when it "
              "can hold secret material.")
        return 1
    count = sum(1 for scope, _, _ in crds if scope == "Namespaced")
    print(f"✅ each of {count} namespaced kinds from the shipped CRDs ({packed} packed subcharts read) is readable "
          f"by ClusterRole {ROLE} or excluded by Schedule {SCHEDULE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
