#!/usr/bin/env python3
"""check-owner-credential-readers.py: only bootstrap reads the Zitadel owner credentials.

The Zitadel owner credentials can do anything to the identity provider:

  iam-admin-pat              PAT of the first-instance machine user (IAM_OWNER)
  iam-admin                  machine key of the same user (IAM_OWNER)
  gibson-zitadel-system-key  System API key pair (SYSTEM_OWNER)

A workload reads one of them in two ways. The kubelet gives it the Secret
(a secret volume, a projected secret source, env valueFrom.secretKeyRef or
envFrom.secretRef), or its ServiceAccount has RBAC get, list or watch on the
Secret, by name or through a rule with no resourceNames. This guard renders
the chart, finds every reader of each kind, and compares the set with
helm/gibson/owner-credential-readers.yaml. That file names each allowed
reader and says why. A new reader fails the build: a workload that mounts an
owner credential, or a ServiceAccount that can read one. A listed reader that
no render produces fails too, so the list cannot go stale.

The RBAC half answers the question `kubectl auth can-i` answers, for every
ServiceAccount the render binds: can it get secret/<name>, list secrets or
watch secrets in the release namespace? The evaluator follows RoleBindings
and ClusterRoleBindings to Roles and ClusterRoles, expands aggregated
ClusterRoles, and treats "*" in apiGroups, resources and verbs the way the
API server does. A binding to a group that holds every ServiceAccount
(system:serviceaccounts, system:serviceaccounts:<ns>, system:authenticated)
fails outright.

  check-owner-credential-readers.py             exit 1 on an unlisted or stale reader
  check-owner-credential-readers.py --selftest  prove each kind of new reader fails
  check-owner-credential-readers.py --print     print the readers the render produces
  check-owner-credential-readers.py --live      ask the current kube context with
                                                `kubectl auth can-i`, and read the
                                                live pods (k3d, kind, staging)

TAKEOVER. A ServiceAccount that cannot read an owner credential can still
become a workload that does. The guard also finds every ServiceAccount that
can do one of these to a reader, and fails unless the `takeovers` section of
the allowlist names it with its reason:

  exec         create or get pods/exec or pods/attach, patch or update
               pods/ephemeralcontainers, on a reader's pod, or get
               nodes/proxy (the kubelet runs exec on any pod it hosts)
  patch        patch or update a reader's Deployment, StatefulSet, DaemonSet,
               ReplicaSet, CronJob or Pod (the pod template names the image,
               the command and the ServiceAccount)
  create-pod   create a Pod, or an object that makes Pods, in the namespace
               that holds the owner credentials (the new Pod can mount any
               Secret there and run as any ServiceAccount there)
  token        create serviceaccounts/token for a reader's ServiceAccount
  impersonate  impersonate a reader's ServiceAccount, or its user name
  bind         create a RoleBinding in that namespace to a role that reads an
               owner credential or holds one of the powers above (bind or
               escalate on roles)

A reader is a workload the first half finds: it mounts an owner credential, or
its ServiceAccount can read one. An entry names the targets it may reach, or
`all` for an upstream controller whose rule cannot name them. A grant that
reaches one more target fails. An entry with `guarded-by` holds only while
that ValidatingAdmissionPolicy denies the request, so the render must carry
the policy and a Deny binding for it.
"""
import concurrent.futures
import json
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "helm", "gibson", "owner-credential-readers.yaml")
NS = "gibson"
READ_VERBS = ("get", "list", "watch")
ALL_SA_GROUPS = ("system:serviceaccounts", "system:authenticated")

# The umbrella profiles the golden snapshots cover, plus the Velero release,
# which baseline-up.sh installs on its own into namespace velero.
UMBRELLA_PROFILES = [
    ["values-baseline.yaml"],
    ["values-baseline.yaml", "values-eks.yaml"],
    ["values-baseline.yaml", "values-guest.yaml"],
]


def helm(args: list[str]) -> list[dict]:
    out = subprocess.run(["helm", "template", *args], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if isinstance(d, dict)]


def renders() -> list[list[dict]]:
    out = []
    for prof in UMBRELLA_PROFILES:
        args = ["gibson", "helm/gibson", "--namespace", NS,
                "-f", "helm/testdata/render-inputs/gibson.yaml"]
        for f in prof:
            args += ["-f", f"helm/gibson/{f}"]
        out.append(helm(args))
    out.append(helm(["velero", "helm/gibson-velero", "--namespace", "velero",
                     "-f", "helm/testdata/render-inputs/gibson-velero.yaml"]))
    return out


# ---------------------------------------------------------------- pod specs

def pod_spec(d: dict) -> dict | None:
    kind = d.get("kind")
    spec = d.get("spec") or {}
    if kind == "Pod":
        return spec
    if kind == "CronJob":
        return (((spec.get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec")
    if kind in ("Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"):
        return (spec.get("template") or {}).get("spec")
    return None


def secret_uses(ps: dict, owner: set[str]) -> set[tuple[str, str]]:
    """(secret, keys) for every owner Secret the pod spec hands to a container."""
    uses = set()

    def keys_of(items):
        return ",".join(sorted(i.get("key", "") for i in items or [])) or "*"

    for v in ps.get("volumes") or []:
        s = v.get("secret")
        if s and s.get("secretName") in owner:
            uses.add((s["secretName"], keys_of(s.get("items"))))
        for src in (v.get("projected") or {}).get("sources") or []:
            s = src.get("secret")
            if s and s.get("name") in owner:
                uses.add((s["name"], keys_of(s.get("items"))))
    containers = (ps.get("initContainers") or []) + (ps.get("containers") or []) \
        + (ps.get("ephemeralContainers") or [])
    for c in containers:
        for e in c.get("env") or []:
            ref = (e.get("valueFrom") or {}).get("secretKeyRef") or {}
            if ref.get("name") in owner:
                uses.add((ref["name"], ref.get("key") or "*"))
        for e in c.get("envFrom") or []:
            ref = e.get("secretRef") or {}
            if ref.get("name") in owner:
                uses.add((ref["name"], "*"))
    return uses


def mount_readers(docs: list[dict], owner: set[str], ns: str = NS) -> set[str]:
    out = set()
    for d in docs:
        ps = pod_spec(d)
        if ps is None or (d["metadata"].get("namespace") or ns) != ns:
            continue
        for secret, keys in secret_uses(ps, owner):
            out.add(f"{d['kind']}/{d['metadata']['name']} {secret}[{keys}]")
    return out


# --------------------------------------------------------------------- RBAC

def rule_allows(rule: dict, verb: str, name: str | None) -> bool:
    groups = rule.get("apiGroups") or []
    if "" not in groups and "*" not in groups:
        return False
    res = rule.get("resources") or []
    if "secrets" not in res and "*" not in res:
        return False
    verbs = rule.get("verbs") or []
    if verb not in verbs and "*" not in verbs:
        return False
    names = rule.get("resourceNames") or []
    if not names:
        return True
    return name is not None and name in names


def matches_selector(labels: dict, sel: dict) -> bool:
    for k, v in (sel.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for expr in sel.get("matchExpressions") or []:
        val, op, vals = labels.get(expr["key"]), expr["operator"], expr.get("values") or []
        if op == "In" and val not in vals:
            return False
        if op == "NotIn" and val in vals:
            return False
        if op == "Exists" and expr["key"] not in labels:
            return False
        if op == "DoesNotExist" and expr["key"] in labels:
            return False
    return True


# Built-in ClusterRoles a chart may bind. Their secret reach is what the API
# server ships; anything else external is a finding, because the guard cannot
# see its rules.
BUILTIN_ROLES = {
    "cluster-admin": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}],
    "admin": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list", "watch"]}],
    "edit": [{"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "list", "watch"]}],
    "view": [],
    "system:auth-delegator": [],
}


def role_rules(docs: list[dict], default_ns: str) -> dict:
    roles = {}
    for d in docs:
        if d.get("kind") == "Role":
            roles[("Role", d["metadata"].get("namespace") or default_ns, d["metadata"]["name"])] = d.get("rules") or []
    crs = [d for d in docs if d.get("kind") == "ClusterRole"]
    for d in crs:
        rules = list(d.get("rules") or [])
        for sel in (d.get("aggregationRule") or {}).get("clusterRoleSelectors") or []:
            for other in crs:
                if other is not d and matches_selector(other["metadata"].get("labels") or {}, sel):
                    rules += other.get("rules") or []
        roles[("ClusterRole", "", d["metadata"]["name"])] = rules
    return roles


def can_read(docs: list[dict], owner: set[str], default_ns: str = NS) -> tuple[dict[str, set[str]], list[str]]:
    """ServiceAccount -> the owner-Secret reads it holds, and hard errors.

    A read is "get <secret>", "list <secret>", "watch <secret>" (a rule that
    names it), or "list secrets" / "watch secrets" (a rule with no names,
    which returns every Secret in the namespace)."""
    roles = role_rules(docs, default_ns)
    reads: dict[str, set[str]] = {}
    errors = []
    for d in docs:
        kind = d.get("kind")
        if kind not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        bns = (d["metadata"].get("namespace") or default_ns) if kind == "RoleBinding" else None
        if bns is not None and bns != NS:
            continue  # a RoleBinding elsewhere cannot reach namespace gibson
        ref = d.get("roleRef") or {}
        key = (ref.get("kind"), bns if ref.get("kind") == "Role" else "", ref.get("name"))
        rules = roles.get(key)
        if rules is None and ref.get("kind") == "ClusterRole":
            rules = BUILTIN_ROLES.get(ref.get("name"))
        if rules is None:
            errors.append(f"{kind} {d['metadata']['name']} binds {ref.get('kind')}/{ref.get('name')}, "
                          "which the render does not contain, so its Secret reach is unknown")
            continue
        held = set()
        for verb in READ_VERBS:
            if any(rule_allows(r, verb, None) for r in rules):
                held.add(f"{verb} secrets")
                continue
            for name in owner:
                if any(rule_allows(r, verb, name) for r in rules):
                    held.add(f"{verb} {name}")
        if not held:
            continue
        for s in d.get("subjects") or []:
            if s.get("kind") == "Group":
                g = s.get("name", "")
                if g in ALL_SA_GROUPS or g.startswith("system:serviceaccounts:"):
                    errors.append(f"{kind} {d['metadata']['name']} grants {sorted(held)} to group {g}, "
                                  "which holds every ServiceAccount")
                continue
            if s.get("kind") != "ServiceAccount":
                continue
            subj = f"{s.get('namespace') or bns or default_ns}/{s['name']}"
            reads.setdefault(subj, set()).update(held)
    return reads, errors


# ---------------------------------------------------------------- takeover

POWERS = ("exec", "patch", "create-pod", "token", "impersonate", "bind")
RBAC_GROUP = "rbac.authorization.k8s.io"
# The object that owns a pod template, and the resource that patches it. A
# Job is absent: its pod template is immutable once created.
KIND_RESOURCE = {
    "Deployment": ("apps", "deployments"),
    "StatefulSet": ("apps", "statefulsets"),
    "DaemonSet": ("apps", "daemonsets"),
    "ReplicaSet": ("apps", "replicasets"),
    "CronJob": ("batch", "cronjobs"),
    "Pod": ("", "pods"),
}
# Every resource whose create makes a Pod run.
POD_MAKERS = [("", "pods"), ("", "replicationcontrollers"), ("apps", "deployments"),
              ("apps", "statefulsets"), ("apps", "daemonsets"), ("apps", "replicasets"),
              ("batch", "jobs"), ("batch", "cronjobs")]
# The subresources that run a command in, or attach to, a running container.
EXEC_SUBRESOURCES = [("pods/exec", ("create", "get")), ("pods/attach", ("create", "get")),
                     ("pods/ephemeralcontainers", ("patch", "update"))]


def resource_matches(listed: list[str], resource: str) -> bool:
    for r in listed:
        if r in ("*", resource):
            return True
        if "/" in resource:
            base, sub = resource.split("/", 1)
            if r in (f"{base}/*", f"*/{sub}"):
                return True
    return False


def grant(rules: list[dict], group: str, resource: str, verbs) -> tuple[bool, bool, set[str]]:
    """(granted, for every name, the names) for any of verbs on group/resource."""
    hit, every, names = False, False, set()
    for r in rules:
        groups = r.get("apiGroups") or []
        if group not in groups and "*" not in groups:
            continue
        if not resource_matches(r.get("resources") or [], resource):
            continue
        rv = r.get("verbs") or []
        if not any(v in rv or "*" in rv for v in verbs):
            continue
        hit = True
        rn = r.get("resourceNames") or []
        if rn:
            names |= set(rn)
        else:
            every = True
    return hit, every, names


def pod_named(kind: str, name: str, names: set[str]) -> bool:
    """Can a name-scoped pod rule reach this workload's pods? Only a bare Pod
    and a StatefulSet (<name>-<ordinal>) have pod names known in advance."""
    if kind == "Pod":
        return name in names
    if kind == "StatefulSet":
        return any(re.fullmatch(re.escape(name) + r"-\d+", n) for n in names)
    return False


def workloads(docs: list[dict], default_ns: str) -> list[dict]:
    out = []
    for d in docs:
        ps = pod_spec(d)
        if ps is None:
            continue
        out.append({"kind": d["kind"], "name": d["metadata"]["name"],
                    "ns": d["metadata"].get("namespace") or default_ns,
                    "sa": ps.get("serviceAccountName") or ps.get("serviceAccount") or "default",
                    "spec": ps})
    return out


def reader_targets(wls: list[dict], owner: set[str], reader_sas: set[str]) -> tuple[list[dict], set[str]]:
    """The reader workloads and the reader ServiceAccounts. A workload that
    mounts an owner credential makes its ServiceAccount a reader too, because
    a token for that ServiceAccount runs a pod that mounts the same Secret."""
    sas = set(reader_sas)
    for w in wls:
        if w["ns"] == NS and secret_uses(w["spec"], owner):
            sas.add(f"{w['ns']}/{w['sa']}")
    targets = [w for w in wls if f"{w['ns']}/{w['sa']}" in sas
               or (w["ns"] == NS and secret_uses(w["spec"], owner))]
    return targets, sas


def role_is_reader(rules: list[dict], owner: set[str]) -> bool:
    for verb in READ_VERBS:
        if any(rule_allows(r, verb, None) for r in rules):
            return True
        if any(rule_allows(r, verb, n) for r in rules for n in owner):
            return True
    return False


def powers(rules: list[dict], bns: str | None, holder: str, targets: list[dict], sas: set[str],
           roles_by_ref, owner: set[str], depth: int = 0) -> dict[str, set[str]]:
    """power -> the targets that `rules`, effective in namespace bns (None =
    every namespace), let `holder` take over. A holder never counts against
    itself: a workload that can restart its own pods gains nothing."""
    got: dict[str, set[str]] = {}

    def add(p, t):
        got.setdefault(p, set()).add(t)

    def in_scope(ns):
        return bns is None or bns == ns

    # nodes/proxy reaches the kubelet API of every node, and the kubelet
    # runs /exec and /attach on any pod it hosts. Cluster-scoped, so only a
    # ClusterRoleBinding grants it.
    node_exec = bns is None and grant(rules, "", "nodes/proxy", ("get", "create"))[0]
    for w in targets:
        if not in_scope(w["ns"]) or f"{w['ns']}/{w['sa']}" == holder:
            continue
        wid = f"{w['kind']}/{w['name']}"
        if node_exec:
            add("exec", wid)
        for sub, verbs in EXEC_SUBRESOURCES:
            hit, every, names = grant(rules, "", sub, verbs)
            if hit and (every or pod_named(w["kind"], w["name"], names)):
                add("exec", wid)
        hit, every, names = grant(rules, "", "pods", ("patch", "update"))
        if hit and (every or pod_named(w["kind"], w["name"], names)):
            add("patch", wid)
        if w["kind"] in KIND_RESOURCE and w["kind"] != "Pod":
            g, r = KIND_RESOURCE[w["kind"]]
            hit, every, names = grant(rules, g, r, ("patch", "update"))
            if hit and (every or w["name"] in names):
                add("patch", wid)
    if in_scope(NS) and any(grant(rules, g, r, ("create",))[0] for g, r in POD_MAKERS):
        add("create-pod", f"Namespace/{NS}")
    for sa in sorted(sas):
        if sa == holder:
            continue
        ns, name = sa.split("/", 1)
        if in_scope(ns):
            hit, every, names = grant(rules, "", "serviceaccounts/token", ("create",))
            if hit and (every or name in names):
                add("token", f"ServiceAccount/{sa}")
            hit, every, names = grant(rules, "", "serviceaccounts", ("impersonate",))
            if hit and (every or name in names):
                add("impersonate", f"ServiceAccount/{sa}")
        if bns is None:
            hit, every, names = grant(rules, "", "users", ("impersonate",))
            if hit and (every or f"system:serviceaccount:{ns}:{name}" in names):
                add("impersonate", f"ServiceAccount/{sa}")
    if depth == 0 and in_scope(NS):
        if grant(rules, RBAC_GROUP, "rolebindings", ("create", "update", "patch"))[0] \
                or (bns is None and grant(rules, RBAC_GROUP, "clusterrolebindings", ("create", "update", "patch"))[0]):
            for kind, res in (("ClusterRole", "clusterroles"), ("Role", "roles")):
                hit, every, names = grant(rules, RBAC_GROUP, res, ("bind",))
                if hit and every:
                    add("bind", f"{kind}/*")
                for n in sorted(names):
                    bound = roles_by_ref(kind, n)
                    if bound is None:
                        add("bind", f"{kind}/{n}")
                    elif role_is_reader(bound, owner) or powers(bound, NS, holder, targets, sas,
                                                                roles_by_ref, owner, depth + 1):
                        add("bind", f"{kind}/{n}")
        if grant(rules, RBAC_GROUP, "roles", ("escalate",))[0] \
                and grant(rules, RBAC_GROUP, "roles", ("create", "update", "patch"))[0]:
            add("bind", "Role/*")
        if bns is None and grant(rules, RBAC_GROUP, "clusterroles", ("escalate",))[0] \
                and grant(rules, RBAC_GROUP, "clusterroles", ("create", "update", "patch"))[0]:
            add("bind", "ClusterRole/*")
    return got


def rendered_takeovers(docsets: list[list[dict]], owner: set[str], reader_sas: set[str]):
    """(holder, power) -> targets, over every render, and hard errors."""
    wls = []
    for docs in docsets:
        default_ns = next((d["metadata"].get("namespace") for d in docs
                           if (d.get("metadata") or {}).get("namespace")), NS)
        wls += workloads(docs, default_ns)
    targets, sas = reader_targets(wls, owner, reader_sas)
    found: dict[tuple[str, str], set[str]] = {}
    errors = []
    for docs in docsets:
        roles = role_rules(docs, NS)

        def by_ref(kind, name):
            rules = roles.get((kind, NS if kind == "Role" else "", name))
            if rules is None and kind == "ClusterRole":
                rules = BUILTIN_ROLES.get(name)
            return rules

        for d in docs:
            kind = d.get("kind")
            if kind not in ("RoleBinding", "ClusterRoleBinding"):
                continue
            bns = (d["metadata"].get("namespace") or NS) if kind == "RoleBinding" else None
            ref = d.get("roleRef") or {}
            rules = roles.get((ref.get("kind"), bns if ref.get("kind") == "Role" else "", ref.get("name")))
            if rules is None and ref.get("kind") == "ClusterRole":
                rules = BUILTIN_ROLES.get(ref.get("name"))
            if rules is None:
                continue  # can_read already reports an unknown role
            for s in d.get("subjects") or []:
                if s.get("kind") == "Group":
                    g = s.get("name", "")
                    if (g in ALL_SA_GROUPS or g.startswith("system:serviceaccounts:")) \
                            and powers(rules, bns, "", targets, sas, by_ref, owner):
                        errors.append(f"{kind} {d['metadata']['name']} lets group {g}, which holds every "
                                      "ServiceAccount, take over an owner-credential reader")
                    continue
                if s.get("kind") != "ServiceAccount":
                    continue
                holder = f"{s.get('namespace') or bns or NS}/{s['name']}"
                for p, t in powers(rules, bns, holder, targets, sas, by_ref, owner).items():
                    found.setdefault((holder, p), set()).update(t)
    return found, errors


def admission_guards(docsets: list[list[dict]]) -> set[str]:
    """ValidatingAdmissionPolicies the render carries with a Deny binding."""
    out = set()
    for docs in docsets:
        vaps = {d["metadata"]["name"] for d in docs if d.get("kind") == "ValidatingAdmissionPolicy"}
        for d in docs:
            if d.get("kind") != "ValidatingAdmissionPolicyBinding":
                continue
            spec = d.get("spec") or {}
            if spec.get("policyName") in vaps and "Deny" in (spec.get("validationActions") or []):
                out.add(f"ValidatingAdmissionPolicy/{spec['policyName']}")
    return out


# ------------------------------------------------------------------- judge

def load_allowlist(path: str = ALLOWLIST) -> dict:
    data = yaml.safe_load(open(path)) or {}
    for section in ("mounts", "readers", "takeovers"):
        for k, v in (data.get(section) or {}).items():
            if not isinstance(v, dict) or v.get("class") not in CLASSES:
                raise SystemExit(f"{path}: {section} {k!r}: class must be one of {sorted(CLASSES)}")
            if v["class"] == "pending" and not v.get("removed-by"):
                raise SystemExit(f"{path}: {section} {k!r}: a pending reader must say what removes it (removed-by)")
            if section == "takeovers":
                if len(k.split(" ")) != 2 or k.split(" ")[1] not in POWERS:
                    raise SystemExit(f"{path}: takeovers {k!r}: the key is '<namespace>/<serviceaccount> <power>', "
                                     f"power one of {list(POWERS)}")
                if v["class"] == "admission-fenced" and not v.get("guarded-by"):
                    raise SystemExit(f"{path}: takeovers {k!r}: an admission-fenced grant must name its policy (guarded-by)")
                t = v.get("targets")
                if t != "all" and not (isinstance(t, list) and t):
                    raise SystemExit(f"{path}: takeovers {k!r}: targets must be `all` or a list of targets")
    return data


CLASSES = {"bootstrap", "identity-provider", "infrastructure", "secret-store", "admission-fenced", "pending"}


def judge(mounts: set[str], reads: dict[str, set[str]], errors: list[str], allow: dict,
          stale: bool = True, takeovers: dict | None = None, guards: set[str] | None = None) -> list[str]:
    out = list(errors)
    allowed_m = allow.get("mounts") or {}
    allowed_r = allow.get("readers") or {}
    allowed_t = allow.get("takeovers") or {}
    takeovers = takeovers or {}
    for m in sorted(mounts):
        if m not in allowed_m:
            out.append(f"unlisted mount of an owner credential: {m!r}")
    for sa in sorted(reads):
        if sa not in allowed_r:
            out.append(f"unlisted reader of an owner credential: {sa!r} can {sorted(reads[sa])}")
    for (holder, power), targets in sorted(takeovers.items()):
        key = f"{holder} {power}"
        entry = allowed_t.get(key)
        if entry is None:
            out.append(f"unlisted takeover of an owner-credential reader: {key!r} reaches {sorted(targets)}")
            continue
        if entry["targets"] != "all":
            extra = sorted(targets - set(entry["targets"]))
            if extra:
                out.append(f"takeover {key!r} reaches targets its entry does not name: {extra}")
        g = entry.get("guarded-by")
        if g and guards is not None and g not in guards:
            out.append(f"takeover {key!r} is allowed only while {g} denies it, and there is no such "
                       "policy with a Deny binding")
    if stale:
        for m in sorted(allowed_m):
            if m not in mounts:
                out.append(f"stale mounts entry (no render produces it): {m!r}")
        for sa in sorted(allowed_r):
            if sa not in reads and not allowed_r[sa].get("live-only"):
                out.append(f"stale readers entry (no render grants it): {sa!r}")
        for key, entry in sorted(allowed_t.items()):
            holder, power = key.split(" ")
            got = takeovers.get((holder, power))
            if got is None:
                if not entry.get("live-only"):
                    out.append(f"stale takeovers entry (no render grants it): {key!r}")
            elif entry["targets"] != "all":
                gone = sorted(set(entry["targets"]) - got)
                if gone:
                    out.append(f"stale targets in takeovers entry {key!r} (no render reaches them): {gone}")
    return out


def rendered_findings(docsets: list[list[dict]], owner: set[str]):
    mounts, reads, errors = set(), {}, []
    for docs in docsets:
        mounts |= mount_readers(docs, owner)
        r, e = can_read(docs, owner)
        for k, v in r.items():
            reads.setdefault(k, set()).update(v)
        errors += [x for x in e if x not in errors]
    takeovers, terr = rendered_takeovers(docsets, owner, set(reads))
    errors += [x for x in terr if x not in errors]
    return mounts, reads, errors, takeovers


# -------------------------------------------------------------------- live

def kubectl_json(args: list[str]) -> dict:
    out = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def can_i(sa: str, verb: str, target: str) -> bool:
    ns, name = sa.split("/", 1)
    r = subprocess.run(["kubectl", "auth", "can-i", verb, target, "-n", NS,
                        f"--as=system:serviceaccount:{ns}:{name}"],
                       capture_output=True, text=True)
    ans = r.stdout.strip()
    if ans not in ("yes", "no"):
        raise SystemExit(f"kubectl auth can-i {verb} {target} --as {sa}: {r.stderr.strip() or ans}")
    return ans == "yes"


def live_reads(owner: set[str]) -> dict[str, set[str]]:
    sas = [f"{i['metadata']['namespace']}/{i['metadata']['name']}"
           for i in kubectl_json(["get", "serviceaccounts", "-A"])["items"]]
    checks = [(verb, "secrets") for verb in ("list", "watch")] + \
             [(verb, f"secret/{n}") for verb in READ_VERBS for n in sorted(owner)]
    jobs = [(sa, v, t) for sa in sas for v, t in checks]
    reads: dict[str, set[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for (sa, v, t), yes in zip(jobs, pool.map(lambda j: can_i(*j), jobs)):
            if yes:
                reads.setdefault(sa, set()).add(f"{v} {t.removeprefix('secret/')}")
    return reads


def live_pods() -> list[dict]:
    """Pods in the release namespace, named by their controller the way the render names them."""
    rs_owner = {}
    for rs in kubectl_json(["get", "replicasets", "-n", NS])["items"]:
        for o in rs["metadata"].get("ownerReferences") or []:
            rs_owner[rs["metadata"]["name"]] = f"{o['kind']}/{o['name']}"
    job_owner = {}
    for j in kubectl_json(["get", "jobs", "-n", NS])["items"]:
        refs = j["metadata"].get("ownerReferences") or []
        job_owner[j["metadata"]["name"]] = f"{refs[0]['kind']}/{refs[0]['name']}" if refs else f"Job/{j['metadata']['name']}"
    out = []
    for p in kubectl_json(["get", "pods", "-n", NS])["items"]:
        refs = p["metadata"].get("ownerReferences") or []
        who = f"Pod/{p['metadata']['name']}"
        if refs:
            o = refs[0]
            if o["kind"] == "ReplicaSet":
                who = rs_owner.get(o["name"], f"ReplicaSet/{o['name']}")
            elif o["kind"] == "Job":
                who = job_owner.get(o["name"], f"Job/{o['name']}")
            else:
                who = f"{o['kind']}/{o['name']}"
        kind, name = who.split("/", 1)
        out.append({"kind": kind, "name": name, "ns": NS, "spec": p["spec"],
                    "sa": p["spec"].get("serviceAccountName") or "default"})
    return out


def live_mounts(owner: set[str], pods: list[dict]) -> set[str]:
    return {f"{w['kind']}/{w['name']} {secret}[{keys}]"
            for w in pods for secret, keys in secret_uses(w["spec"], owner)}


def rules_review(sa: str, ns: str) -> list[dict]:
    """What `sa` may do in namespace ns: a SelfSubjectRulesReview made as it."""
    sns, name = sa.split("/", 1)
    body = json.dumps({"apiVersion": "authorization.k8s.io/v1", "kind": "SelfSubjectRulesReview",
                       "spec": {"namespace": ns}})
    r = subprocess.run(["kubectl", "create", "-f", "-", "-o", "json",
                        f"--as=system:serviceaccount:{sns}:{name}"],
                       input=body, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"SelfSubjectRulesReview as {sa} in {ns}: {r.stderr.strip()}")
    status = json.loads(r.stdout).get("status") or {}
    if status.get("incomplete"):
        raise SystemExit(f"SelfSubjectRulesReview as {sa} in {ns} is incomplete ({status.get('evaluationError')}); "
                         "the cluster has an authorizer RBAC does not describe, so this check cannot answer")
    return status.get("resourceRules") or []


def live_takeovers(owner: set[str], holders: list[str], targets: list[dict], sas: set[str]) -> dict:
    """(holder, power) -> targets. Each holder's rules in every namespace a
    target lives in, evaluated with the same code the render uses."""
    namespaces = sorted({NS} | {s.split("/", 1)[0] for s in sas})
    cache: dict = {}

    def by_ref(kind, name):
        key = (kind, name)
        if key not in cache:
            args = ["get", "clusterrole" if kind == "ClusterRole" else "role", name]
            if kind == "Role":
                args += ["-n", NS]
            r = subprocess.run(["kubectl", *args, "-o", "json"], capture_output=True, text=True)
            cache[key] = (json.loads(r.stdout).get("rules") or []) if r.returncode == 0 else None
        return cache[key]

    jobs = [(h, ns) for h in holders for ns in namespaces]
    found: dict[tuple[str, str], set[str]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        for (h, ns), rules in zip(jobs, pool.map(lambda j: rules_review(*j), jobs)):
            scoped_targets = [w for w in targets if w["ns"] == ns]
            scoped_sas = {s for s in sas if s.split("/", 1)[0] == ns}
            # The review merges cluster-wide rules into the namespace's. The
            # two cluster-scoped paths, user impersonation and nodes/proxy,
            # are judged once, from the release namespace's review, as a
            # ClusterRoleBinding would be (bns=None).
            for p, t in powers(rules, ns, h, scoped_targets, scoped_sas, by_ref, owner).items():
                found.setdefault((h, p), set()).update(t)
            if ns == NS:
                cluster = [r for r in rules if {"users", "nodes/proxy", "*"} & set(r.get("resources") or [])]
                wide = powers(cluster, None, h, targets, sas, by_ref, owner)
                for p in ("impersonate", "exec"):
                    if p in wide:
                        found.setdefault((h, p), set()).update(wide[p])
    return found


def live_guards(allow: dict) -> set[str]:
    wanted = {v["guarded-by"] for v in (allow.get("takeovers") or {}).values() if v.get("guarded-by")}
    out = set()
    bindings = kubectl_json(["get", "validatingadmissionpolicybindings"])["items"]
    for g in wanted:
        name = g.split("/", 1)[1]
        r = subprocess.run(["kubectl", "get", "validatingadmissionpolicy", name], capture_output=True, text=True)
        if r.returncode == 0 and any((b.get("spec") or {}).get("policyName") == name
                                     and "Deny" in ((b.get("spec") or {}).get("validationActions") or [])
                                     for b in bindings):
            out.add(g)
    return out


def live(allow: dict) -> int:
    owner = set(allow["owner_secrets"])
    reads = live_reads(owner)
    allowed_ns = allow.get("live_namespaces") or {}
    all_readers = set(reads)
    for pattern, why in allowed_ns.items():
        for sa in [s for s in reads if s.split("/", 1)[0] == pattern]:
            print(f"  allowed by namespace {pattern} ({why.strip().splitlines()[0]}): {sa} can {sorted(reads.pop(sa))}")
    pods = live_pods()
    mounts = live_mounts(owner, pods)
    targets, sas = reader_targets(pods, owner, all_readers)
    holders = [f"{i['metadata']['namespace']}/{i['metadata']['name']}"
               for i in kubectl_json(["get", "serviceaccounts", "-A"])["items"]
               if i["metadata"]["namespace"] not in allowed_ns]
    takeovers = live_takeovers(owner, holders, targets, sas)
    got = judge(mounts, reads, [], allow, stale=False, takeovers=takeovers, guards=live_guards(allow))
    for sa in sorted(reads):
        print(f"  reader: {sa} can {sorted(reads[sa])}")
    for m in sorted(mounts):
        print(f"  mount:  {m}")
    for (h, p), t in sorted(takeovers.items()):
        print(f"  takeover: {h} {p} -> {sorted(t)}")
    if got:
        print("FAIL: the live cluster has owner-credential readers, or ways to take one over, "
              "that the allowlist does not name:\n  " + "\n  ".join(got))
        return 1
    print(f"OK: {len(reads)} ServiceAccounts can read an owner credential, {len(mounts)} pods mount one, "
          f"{len(takeovers)} grants can take a reader over; every one is listed")
    return 0


# --------------------------------------------------------------- self-test

FIXTURE_BASE = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: boot, namespace: gibson}
rules:
- {apiGroups: [''], resources: [secrets], resourceNames: [iam-admin-pat], verbs: [get]}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: boot, namespace: gibson}
roleRef: {kind: Role, name: boot}
subjects: [{kind: ServiceAccount, name: boot, namespace: gibson}]
---
apiVersion: batch/v1
kind: Job
metadata: {name: boot, namespace: gibson}
spec:
  template:
    spec:
      serviceAccountName: boot
      containers: [{name: c, image: x}]
"""

# Each fixture is one new reader. Every one of them must fail the judge.
FIXTURES_THAT_MUST_FAIL = {
    "a Deployment mounts the owner PAT as a volume": """
apiVersion: apps/v1
kind: Deployment
metadata: {name: app, namespace: gibson}
spec:
  template:
    spec:
      containers: [{name: c, image: x}]
      volumes: [{name: v, secret: {secretName: iam-admin-pat}}]
""",
    "a CronJob reads the machine key through env": """
apiVersion: batch/v1
kind: CronJob
metadata: {name: app, namespace: gibson}
spec:
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: c
            image: x
            env: [{name: K, valueFrom: {secretKeyRef: {name: iam-admin, key: iam-admin.json}}}]
""",
    "an init container reads the System API key through envFrom": """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: app, namespace: gibson}
spec:
  template:
    spec:
      initContainers: [{name: i, image: x, envFrom: [{secretRef: {name: gibson-zitadel-system-key}}]}]
      containers: [{name: c, image: x}]
""",
    "a Pod mounts the owner PAT through a projected volume": """
apiVersion: v1
kind: Pod
metadata: {name: app, namespace: gibson}
spec:
  containers: [{name: c, image: x}]
  volumes: [{name: v, projected: {sources: [{secret: {name: iam-admin-pat}}]}}]
""",
    "a Role reads every Secret in the namespace": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: wide, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], verbs: [get, list]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: wide, namespace: gibson}
roleRef: {kind: Role, name: wide}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "a ClusterRole with resources '*' is bound cluster-wide": """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: star}
rules: [{apiGroups: ['*'], resources: ['*'], verbs: [watch]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: star}
roleRef: {kind: ClusterRole, name: star}
subjects: [{kind: ServiceAccount, name: app, namespace: other}]
""",
    "a Role names the machine key": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: named, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], resourceNames: [iam-admin], verbs: [get]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: named, namespace: gibson}
roleRef: {kind: Role, name: named}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "a RoleBinding in gibson grants the built-in edit ClusterRole": """
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: editor, namespace: gibson}
roleRef: {kind: ClusterRole, name: edit}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "an aggregated ClusterRole picks up a Secret read": """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: agg}
aggregationRule: {clusterRoleSelectors: [{matchLabels: {agg: 'true'}}]}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: part, labels: {agg: 'true'}}
rules: [{apiGroups: [''], resources: [secrets], verbs: [list]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {name: agg}
roleRef: {kind: ClusterRole, name: agg}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "a Secret read is granted to every ServiceAccount through a group": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: grp, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], verbs: [get]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: grp, namespace: gibson}
roleRef: {kind: Role, name: grp}
subjects: [{kind: Group, name: 'system:serviceaccounts:gibson'}]
""",
    "a binding names a ClusterRole the render does not contain": """
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: ext, namespace: gibson}
roleRef: {kind: ClusterRole, name: somebody-elses-role}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
}

# Grants that look close but cannot read an owner Secret. Each must pass.
FIXTURES_THAT_MUST_PASS = {
    "a Role that names another Secret": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: other, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], resourceNames: [gibson-redis-stack], verbs: [get]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: other, namespace: gibson}
roleRef: {kind: Role, name: other}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "a wide Secret read bound in another namespace": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: wide, namespace: tenant-a}
rules: [{apiGroups: [''], resources: [secrets], verbs: [get, list, watch]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: wide, namespace: tenant-a}
roleRef: {kind: Role, name: wide}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "create on Secrets, which reads nothing": """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: create, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], verbs: [create]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: create, namespace: gibson}
roleRef: {kind: Role, name: create}
subjects: [{kind: ServiceAccount, name: app, namespace: gibson}]
""",
    "a pod that mounts only another Secret": """
apiVersion: v1
kind: Pod
metadata: {name: app, namespace: gibson}
spec:
  containers: [{name: c, image: x, env: [{name: P, valueFrom: {secretKeyRef: {name: gibson-redis-stack, key: p}}}]}]
""",
}

# The base adds a reader Deployment and a StatefulSet that reads nothing, so
# each takeover fixture has a target and each near miss has a decoy.
TAKEOVER_BASE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: reader, namespace: gibson}
spec:
  template:
    spec:
      serviceAccountName: boot
      containers: [{name: c, image: x}]
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: cache, namespace: gibson}
spec:
  template:
    spec:
      serviceAccountName: cache
      containers: [{name: c, image: x}]
"""


def grant_fixture(rules: str, cluster: bool = False, ns: str = "gibson", extra: str = "") -> str:
    """One Role (or ClusterRole) with `rules`, bound to ServiceAccount gibson/app."""
    if cluster:
        return f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {{name: t}}
rules: {rules}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: {{name: t}}
roleRef: {{kind: ClusterRole, name: t}}
subjects: [{{kind: ServiceAccount, name: app, namespace: gibson}}]
{extra}"""
    return f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {{name: t, namespace: {ns}}}
rules: {rules}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {{name: t, namespace: {ns}}}
roleRef: {{kind: Role, name: t}}
subjects: [{{kind: ServiceAccount, name: app, namespace: gibson}}]
{extra}"""


READS_SECRETS_ROLE = """---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: secret-reader}
rules: [{apiGroups: [''], resources: [secrets], verbs: [list]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: {name: config-reader}
rules: [{apiGroups: [''], resources: [configmaps], verbs: [list]}]
"""

# One fixture per takeover path. Every one must fail the judge.
TAKEOVERS_THAT_MUST_FAIL = {
    "exec into any pod in the namespace (the postgres-exec shape)":
        grant_fixture("[{apiGroups: [''], resources: [pods/exec], verbs: [create]}]"),
    "exec by the websocket verb get": grant_fixture("[{apiGroups: [''], resources: [pods/exec], verbs: [get]}]"),
    "attach to any pod": grant_fixture("[{apiGroups: [''], resources: [pods/attach], verbs: [create]}]"),
    "add an ephemeral container to any pod":
        grant_fixture("[{apiGroups: [''], resources: [pods/ephemeralcontainers], verbs: [patch]}]"),
    "patch every Deployment (the Reloader shape)":
        grant_fixture("[{apiGroups: [apps], resources: [deployments, statefulsets], verbs: [patch]}]"),
    "patch the reader Deployment by name (the openbao auto-init shape)":
        grant_fixture("[{apiGroups: [apps], resources: [deployments], resourceNames: [reader], verbs: [patch]}]"),
    "update every pod": grant_fixture("[{apiGroups: [''], resources: [pods], verbs: [update]}]"),
    "create a pod in the namespace": grant_fixture("[{apiGroups: [''], resources: [pods], verbs: [create]}]"),
    "create a Job in the namespace": grant_fixture("[{apiGroups: [batch], resources: [jobs], verbs: [create]}]"),
    "create a token for any ServiceAccount, cluster-wide (the External Secrets shape)":
        grant_fixture("[{apiGroups: [''], resources: [serviceaccounts/token], verbs: [create]}]", cluster=True),
    "create a token for the reader's ServiceAccount by name":
        grant_fixture("[{apiGroups: [''], resources: [serviceaccounts/token], resourceNames: [boot], verbs: [create]}]"),
    "impersonate the reader's ServiceAccount":
        grant_fixture("[{apiGroups: [''], resources: [serviceaccounts], verbs: [impersonate]}]"),
    "impersonate the reader's user name":
        grant_fixture("[{apiGroups: [''], resources: [users], resourceNames: ['system:serviceaccount:gibson:boot'], "
                      "verbs: [impersonate]}]", cluster=True),
    "bind a Secret-reading ClusterRole anywhere (the tenant-operator shape)":
        grant_fixture("[{apiGroups: [rbac.authorization.k8s.io], resources: [rolebindings], verbs: [create]}, "
                      "{apiGroups: [rbac.authorization.k8s.io], resources: [clusterroles], "
                      "resourceNames: [secret-reader], verbs: [bind]}]", cluster=True, extra=READS_SECRETS_ROLE),
    "escalate: write a Role with any rule":
        grant_fixture("[{apiGroups: [rbac.authorization.k8s.io], resources: [roles], verbs: [create, escalate]}]"),
    "a wildcard ClusterRole": grant_fixture("[{apiGroups: ['*'], resources: ['*'], verbs: ['*']}]", cluster=True),
    "reach the kubelet through nodes/proxy":
        grant_fixture("[{apiGroups: [''], resources: [nodes/proxy], verbs: [get]}]", cluster=True),
}

# Grants that look close but reach no reader. Each must pass.
TAKEOVERS_THAT_MUST_PASS = {
    "exec into one named StatefulSet pod that reads nothing (the redis-rotation shape)":
        grant_fixture("[{apiGroups: [''], resources: [pods/exec], resourceNames: [cache-0], verbs: [create]}]"),
    "patch a Deployment that reads nothing, by name":
        grant_fixture("[{apiGroups: [apps], resources: [deployments], resourceNames: [other], verbs: [patch]}]"),
    "create a token for its own ServiceAccount only":
        grant_fixture("[{apiGroups: [''], resources: [serviceaccounts/token], resourceNames: [app], verbs: [create]}]"),
    "create pods in another namespace":
        grant_fixture("[{apiGroups: [''], resources: [pods], verbs: [create]}]", ns="tenant-a"),
    "bind a ClusterRole that reads no Secret":
        grant_fixture("[{apiGroups: [rbac.authorization.k8s.io], resources: [rolebindings], verbs: [create]}, "
                      "{apiGroups: [rbac.authorization.k8s.io], resources: [clusterroles], "
                      "resourceNames: [config-reader], verbs: [bind]}]", cluster=True, extra=READS_SECRETS_ROLE),
    "read pods and Deployments": grant_fixture("[{apiGroups: ['', apps], resources: [pods, deployments], "
                                               "verbs: [get, list, watch]}]"),
}

VAP_FIXTURE = """
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicy
metadata: {name: fence}
spec: {}
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingAdmissionPolicyBinding
metadata: {name: fence}
spec: {policyName: fence, validationActions: [Deny]}
"""

SELFTEST_ALLOW = {
    "owner_secrets": ["iam-admin-pat", "iam-admin", "gibson-zitadel-system-key"],
    "mounts": {},
    "readers": {"gibson/boot": {"class": "bootstrap", "why": "fixture"}},
    "takeovers": {},
}


def docs_of(*texts: str) -> list[dict]:
    return [d for t in texts for d in yaml.safe_load_all(t) if d]


def run(docs: list[dict], allow: dict, owner: set[str]) -> list[str]:
    m, r, e, t = rendered_findings([docs], owner)
    return judge(m, r, e, allow, takeovers=t, guards=admission_guards([docs]))


def selftest() -> int:
    owner = set(SELFTEST_ALLOW["owner_secrets"])
    base = docs_of(FIXTURE_BASE, TAKEOVER_BASE)
    got = run(base, SELFTEST_ALLOW, owner)
    if got:
        print(f"SELFTEST FAIL: the base fixture must pass, got {got}")
        return 1
    for what, text in {**FIXTURES_THAT_MUST_FAIL, **TAKEOVERS_THAT_MUST_FAIL}.items():
        if not run(base + docs_of(text), SELFTEST_ALLOW, owner):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    for what, text in {**FIXTURES_THAT_MUST_PASS, **TAKEOVERS_THAT_MUST_PASS}.items():
        got = run(base + docs_of(text), SELFTEST_ALLOW, owner)
        if got:
            print(f"SELFTEST FAIL: {what}: the guard failed it: {got}")
            return 1
    stale = dict(SELFTEST_ALLOW, readers={**SELFTEST_ALLOW["readers"],
                                          "gibson/gone": {"class": "bootstrap", "why": "fixture"}})
    if not any(x.startswith("stale readers entry") for x in run(base, stale, owner)):
        print("SELFTEST FAIL: a stale readers entry must fail")
        return 1
    # A listed takeover passes with exactly its targets, fails on one more,
    # fails as stale when a named target is no longer reached, and fails when
    # the admission policy it depends on is gone.
    named = grant_fixture("[{apiGroups: [apps], resources: [deployments], resourceNames: [reader], verbs: [patch]}]")
    listed = dict(SELFTEST_ALLOW, takeovers={"gibson/app patch": {
        "class": "infrastructure", "why": "fixture", "targets": ["Deployment/reader"]}})
    if run(base + docs_of(named), listed, owner):
        print(f"SELFTEST FAIL: a listed takeover must pass: {run(base + docs_of(named), listed, owner)}")
        return 1
    wide = grant_fixture("[{apiGroups: [apps], resources: [deployments, statefulsets], verbs: [patch]}]")
    extra_reader = """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: reader2, namespace: gibson}
spec: {template: {spec: {serviceAccountName: boot, containers: [{name: c, image: x}]}}}
"""
    if not any("does not name" in x for x in run(base + docs_of(wide, extra_reader), listed, owner)):
        print("SELFTEST FAIL: a listed takeover that reaches one more target must fail")
        return 1
    two = dict(listed, takeovers={"gibson/app patch": dict(listed["takeovers"]["gibson/app patch"],
                                                           targets=["Deployment/reader", "Deployment/gone"])})
    if not any(x.startswith("stale targets") for x in run(base + docs_of(named), two, owner)):
        print("SELFTEST FAIL: a takeovers entry that names a target no render reaches must fail")
        return 1
    fenced = dict(listed, takeovers={"gibson/app patch": dict(listed["takeovers"]["gibson/app patch"],
                                                              **{"guarded-by": "ValidatingAdmissionPolicy/fence"})})
    if run(base + docs_of(named, VAP_FIXTURE), fenced, owner):
        print("SELFTEST FAIL: a guarded takeover must pass while its policy is there")
        return 1
    if not any("guarded" in x or "denies it" in x for x in run(base + docs_of(named), fenced, owner)):
        print("SELFTEST FAIL: a guarded takeover must fail when its admission policy is gone")
        return 1
    allow = load_allowlist()
    docsets = renders()
    m, r, e, t = rendered_findings(docsets, set(allow["owner_secrets"]))
    got = judge(m, r, e, allow, takeovers=t, guards=admission_guards(docsets))
    if got:
        print("SELFTEST FAIL: the render and the allowlist disagree:\n  " + "\n  ".join(got))
        return 1
    print(f"OK: {len(FIXTURES_THAT_MUST_FAIL)} new-reader and {len(TAKEOVERS_THAT_MUST_FAIL)} takeover fixtures "
          f"fail, {len(FIXTURES_THAT_MUST_PASS) + len(TAKEOVERS_THAT_MUST_PASS)} near misses pass, stale entries "
          "and a missing admission policy fail; the render matches the allowlist")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    allow = load_allowlist()
    if "--live" in sys.argv:
        return live(allow)
    owner = set(allow["owner_secrets"])
    docsets = renders()
    mounts, reads, errors, takeovers = rendered_findings(docsets, owner)
    if "--print" in sys.argv:
        for m in sorted(mounts):
            print(f"mount     {m}")
        for sa in sorted(reads):
            print(f"reader    {sa}: {sorted(reads[sa])}")
        for (h, p), t in sorted(takeovers.items()):
            print(f"takeover  {h} {p}: {sorted(t)}")
        for e in errors:
            print(f"error     {e}")
        return 0
    got = judge(mounts, reads, errors, allow, takeovers=takeovers, guards=admission_guards(docsets))
    if got:
        print("FAIL: a workload that is not on helm/gibson/owner-credential-readers.yaml can read a "
              "Zitadel owner credential, or take over a workload that does. Only bootstrap may. Give the "
              "workload its own narrow credential, or scope the RBAC rule with resourceNames:\n  "
              + "\n  ".join(got))
        return 1
    pending = [k for s in ("mounts", "readers", "takeovers") for k, v in (allow.get(s) or {}).items()
               if v["class"] == "pending"]
    print(f"OK: {len(mounts)} mounts and {len(reads)} ServiceAccounts read an owner credential, "
          f"{len(takeovers)} grants can take a reader over; every one is listed"
          + (f" ({len(pending)} pending removal)" if pending else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
