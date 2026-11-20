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

Four more rules:

  * Each kind in includedClusterScopedResources of the Schedule must be
    readable. A Backup with the scoped filters lists a cluster-scoped kind
    only when the Schedule names it.
  * Each kind of SERVED_WITHOUT_CRD_FILE must be readable. Kubernetes and
    Cilium serve these kinds, and no chart of this repository ships a CRD
    file for them, so no source above can show them. Each one was a measured
    listing error (charts#660, backup-restore run 38201546512).

  * Each dependency of each helm/*/Chart.yaml must have its packed subchart
    (charts/<name>-<version>.tgz). Source 3 with a tarball missing measures
    fewer kinds and says nothing. Run `make chart-deps` first.
  * No rendered HTTPRoute or GRPCRoute in a namespace that the Schedule
    includes may set or add a request header. The role reads these kinds, and
    a `requestHeaderModifier` value is a literal that can be a credential.

How the check matches an entry of excludedNamespaceScopedResources. Velero
v1.18.1 (pkg/util/collections/includes_excludes.go, scopeResourceMapFunc and
ScopeIncludesExcludes.ShouldInclude) asks discovery for each entry, and
discovery resolves a name to ONE resource. The entry then becomes that one
`resource.group`, and Velero compares it with the listed kind as a glob over
the whole string. An entry that discovery cannot resolve, such as a glob,
stays as written. The check has no discovery, so it reads an entry this way:

  a glob (`*`, `?`, `[`)    matches the whole `resource.group` string
  `resource.group`          matches that one kind
  a bare name (`secrets`)   matches the core kind of that name only.
                            Kubernetes discovery lists the core group first,
                            so a bare name never selects a kind of a CRD
                            while a core kind has the name. Write a kind of
                            a CRD as `resource.group`.

  check-velero-reads-served-kinds.py             exit 1 on a finding
  check-velero-reads-served-kinds.py --selftest  prove each rule fails
"""
import copy
import fnmatch
import glob
import os
import io
import sys
import tarfile
import tempfile

import yaml

try:
    from yaml import CSafeLoader as Loader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as Loader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN_DIR = os.path.join(ROOT, "helm", "testdata", "golden")
VELERO_GOLDEN = os.path.join(GOLDEN_DIR, "gibson-velero.yaml")
CRD_GOLDENS = ["gibson-crds.yaml", "gibson-operator-crds.yaml", "gibson-velero.yaml"]
UMBRELLA_GOLDENS = os.path.join(GOLDEN_DIR, "values-*.yaml")
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "velero-served-kinds", "unread-kind-crd.yaml")
ROUTE_FIXTURE = os.path.join(ROOT, "tests", "fixtures", "velero-served-kinds", "route-sets-request-header.yaml")
RELEASE_NAMESPACE = "gibson"  # scripts/golden.sh renders each chart with --namespace gibson
ROUTE_KINDS = ("HTTPRoute", "GRPCRoute")
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


def packed_crds(paths) -> set[tuple[str, str, str]]:
    """Return the CRDs of the crds/ directories of the packed subcharts."""
    found: set[tuple[str, str, str]] = set()
    for path in paths:
        with tarfile.open(path) as tar:
            for member in tar:
                parts = member.name.split("/")
                # Helm's crds/ directory holds plain YAML. A CRD under templates/
                # is a template, and the render of its CRD release shows it.
                if member.isfile() and "crds" in parts[:-1] and "templates" not in parts \
                        and parts[-1].endswith((".yaml", ".yml")):
                    found |= crds_of(load_docs(tar.extractfile(member).read()))
    return found


def packed_subcharts(root) -> tuple[list[str], list[str]]:
    """Return the packed subcharts under root, and a finding for each dependency with no tarball."""
    bad = []
    for chart in sorted(glob.glob(os.path.join(root, "helm", "*", "Chart.yaml"))):
        with open(chart) as f:
            deps = (yaml.load(f, Loader=Loader) or {}).get("dependencies") or []
        for dep in deps:
            name = f"{dep.get('name')}-{dep.get('version')}.tgz"
            if not os.path.isfile(os.path.join(os.path.dirname(chart), "charts", name)):
                rel = os.path.relpath(os.path.dirname(chart), root)
                bad.append(f"{rel}/charts/{name} is missing: the CRDs of that subchart are not measured. "
                           "Run `make chart-deps` first.")
    return sorted(glob.glob(os.path.join(root, "helm", "*", "charts", "*.tgz"))), bad


def committed_crds() -> set[tuple[str, str, str]]:
    """Return the CRDs of the committed renders and of the vendored CRD files."""
    found: set[tuple[str, str, str]] = set()
    for name in CRD_GOLDENS:
        with open(os.path.join(GOLDEN_DIR, name)) as f:
            found |= crds_of(load_docs(f))
    for path in sorted(glob.glob(os.path.join(ROOT, "helm", "*", "files", "crds", "*.yaml"))):
        with open(path) as f:
            found |= crds_of(load_docs(f))
    return found


def rendered_routes() -> list[dict]:
    """Return each HTTPRoute and GRPCRoute of the committed umbrella renders."""
    routes = []
    for path in sorted(glob.glob(UMBRELLA_GOLDENS)):
        with open(path) as f:
            # Parse only the documents of the route kinds: a render is large.
            for chunk in f.read().split("\n---\n"):
                if any(f"\nkind: {k}\n" in "\n" + chunk for k in ROUTE_KINDS):
                    routes += [d for d in load_docs(chunk) if d.get("kind") in ROUTE_KINDS]
    return routes


def request_header_values(route) -> list[str]:
    """Return the name of each request header that the route sets or adds."""
    names = []
    for rule in (route.get("spec") or {}).get("rules") or []:
        holders = [rule] + list(rule.get("backendRefs") or [])
        for holder in holders:
            for flt in holder.get("filters") or []:
                modifier = flt.get("requestHeaderModifier") or {}
                names += [h.get("name") for op in ("set", "add") for h in modifier.get(op) or []]
    return names


def judge_routes(velero_docs, routes) -> list[str]:
    schedule = find(velero_docs, "Schedule", SCHEDULE)
    if schedule is None:
        return [f"no Schedule {SCHEDULE} in the render: the check would measure nothing"]
    included = ((schedule.get("spec") or {}).get("template") or {}).get("includedNamespaces") or []
    bad = []
    for route in routes:
        meta = route.get("metadata") or {}
        namespace = meta.get("namespace") or RELEASE_NAMESPACE
        if not any(fnmatch.fnmatchcase(namespace, p) for p in included):
            continue
        for header in request_header_values(route):
            bad.append(f"{route.get('kind')} {namespace}/{meta.get('name')} sets the request header {header} to a "
                       f"literal value, and the Schedule {SCHEDULE} includes its namespace: a Backup would hold "
                       "the value. Move the route to the gateway namespace, or exclude the kind in the Schedule "
                       f"and remove its read from ClusterRole {ROLE}")
    return bad


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
    """Report whether an entry selects the kind. The module text states the Velero rule."""
    full = f"{resource}.{group}" if group else resource
    for p in patterns:
        if any(c in p for c in "*?["):
            if fnmatch.fnmatchcase(full, p):
                return True
        elif "." in p:
            if p == full:
                return True
        elif p == resource and not group:
            return True
    return False


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


def write_tar(path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def selftest_packed(fixture_bytes: bytes, fixture) -> list[str]:
    """Prove the packed-subchart source: it reads crds/, skips templates/, and a missing tarball is a finding."""
    failed = []
    with tempfile.TemporaryDirectory() as root:
        chart = os.path.join(root, "helm", "umbrella")
        os.makedirs(os.path.join(chart, "charts"))
        with open(os.path.join(chart, "Chart.yaml"), "w") as f:
            yaml.safe_dump({"name": "umbrella", "dependencies": [
                {"name": "widgets", "version": "1.0.0"}, {"name": "gadgets", "version": "v2.0.0"}]}, f)
        # No tarball: each dependency is a finding, and none is read.
        paths, bad = packed_subcharts(root)
        if paths or len(bad) != 2:
            failed.append("a tree with no packed subchart")
        write_tar(os.path.join(chart, "charts", "widgets-1.0.0.tgz"), {
            "widgets/crds/widget.yaml": fixture_bytes,
            # A template is not YAML. The reader must skip it.
            "widgets/templates/crds/widget.yaml": b"{{- if .Values.crds }}\nkind: CustomResourceDefinition\n{{- end }}\n",
        })
        # One of two tarballs: the other one is a finding.
        paths, bad = packed_subcharts(root)
        if len(paths) != 1 or len(bad) != 1 or "gadgets-v2.0.0.tgz" not in bad[0]:
            failed.append("a tree with one of two packed subcharts")
        if packed_crds(paths) != fixture:
            failed.append("a packed subchart with a crds/ file and a CRD template")
        write_tar(os.path.join(chart, "charts", "gadgets-v2.0.0.tgz"), {"gadgets/Chart.yaml": b"name: gadgets\n"})
        paths, bad = packed_subcharts(root)
        if len(paths) != 2 or bad:
            failed.append("a tree with each packed subchart (it must pass)")
    return failed


def selftest() -> int:
    docs = load_velero()
    crds = committed_crds()
    routes = rendered_routes()
    if judge(docs, crds) or judge_routes(docs, routes):
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
    with open(FIXTURE, "rb") as f:
        fixture_bytes = f.read()
    fixture = crds_of(load_docs(fixture_bytes))
    with open(ROUTE_FIXTURE) as f:
        route_fixture = load_docs(f)
    moved = copy.deepcopy(route_fixture)
    for route in moved:
        route["metadata"]["namespace"] = "gibson-gateway"

    def with_exclusion(pattern):
        changed = copy.deepcopy(docs)
        find(changed, "Schedule", SCHEDULE)["spec"]["template"]["excludedNamespaceScopedResources"].append(pattern)
        return changed

    widgets = both("fixture.zeroroot.test", "widgets", NOT_EXCLUDED)
    cases = [
        ("a new namespaced CRD that the role cannot read and the Schedule does not exclude",
         set(judge(docs, crds | fixture)), widgets),
        # A bare name selects one kind in Velero, and the core kind first. It
        # must not hide a kind of a CRD with the same name in another group.
        ("a Schedule that excludes the bare name `widgets`",
         set(judge(with_exclusion("widgets"), crds | fixture)), widgets),
        ("a Schedule that excludes `widgets.another.group`",
         set(judge(with_exclusion("widgets.another.group"), crds | fixture)), widgets),
        ("a Schedule that excludes `widgets.fixture.zeroroot.test` (it must pass)",
         set(judge(with_exclusion("widgets.fixture.zeroroot.test"), crds | fixture)), set()),
        ("a Schedule that excludes `*.fixture.zeroroot.test` (it must pass)",
         set(judge(with_exclusion("*.fixture.zeroroot.test"), crds | fixture)), set()),
        ("a route in a backed-up namespace that sets a request header",
         {b.split(" to a ")[0] for b in judge_routes(docs, routes + route_fixture)},
         {"HTTPRoute tenant-acme/provider sets the request header Authorization",
          "GRPCRoute gibson/provider sets the request header x-api-key"}),
        ("the same routes in the gateway namespace (they must pass)",
         set(judge_routes(docs, routes + moved)), set()),
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
    failed += selftest_packed(fixture_bytes, fixture)
    for kind in ("ClusterRole", "Schedule"):
        if not judge([d for d in docs if d.get("kind") != kind], crds):
            failed.append(f"a render with no {kind}")
    if not judge(docs, {c for c in crds if c[0] != "Namespaced"}):
        failed.append("a tree with no namespaced CRD")
    if failed:
        print("GUARD BROKEN: these broken inputs did not give the expected finding: " + "; ".join(failed),
              file=sys.stderr)
        return 2
    print(f"✅ self-test: {len(cases) + 7} inputs each give the expected result, and the committed render passes")
    return 0


def main(argv) -> int:
    if argv[1:] == ["--selftest"]:
        return selftest()
    packed, absent = packed_subcharts(ROOT)
    if absent:
        print("❌ a packed subchart is missing, so the check would measure fewer kinds:")
        for a in absent:
            print("   ", a)
        return 1
    crds = committed_crds() | packed_crds(packed)
    velero = load_velero()
    routes = judge_routes(velero, rendered_routes())
    if routes:
        print("❌ a Backup would hold a literal request header value (charts#825):")
        for r in routes:
            print("   ", r)
        return 1
    bad = judge(velero, crds)
    if bad:
        print("❌ a Backup cannot list a served kind (charts#660, charts#684, charts#818):")
        for b in bad:
            print("   ", b)
        print("    Decide for each kind (helm/gibson-velero/templates/rbac.yaml states the rule): grant "
              "get, list and watch in the ClusterRole, or exclude it in templates/schedule.yaml when it "
              "can hold secret material.")
        return 1
    count = sum(1 for scope, _, _ in crds if scope == "Namespaced")
    print(f"✅ each of {count} namespaced kinds from the shipped CRDs ({len(packed)} packed subcharts read) is readable "
          f"by ClusterRole {ROLE} or excluded by Schedule {SCHEDULE}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
