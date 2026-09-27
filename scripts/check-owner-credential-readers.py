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
"""
import concurrent.futures
import json
import os
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


# ------------------------------------------------------------------- judge

def load_allowlist(path: str = ALLOWLIST) -> dict:
    data = yaml.safe_load(open(path)) or {}
    for section in ("mounts", "readers"):
        for k, v in (data.get(section) or {}).items():
            if not isinstance(v, dict) or v.get("class") not in CLASSES:
                raise SystemExit(f"{path}: {section} {k!r}: class must be one of {sorted(CLASSES)}")
            if v["class"] == "pending" and not v.get("removed-by"):
                raise SystemExit(f"{path}: {section} {k!r}: a pending reader must say what removes it (removed-by)")
    return data


CLASSES = {"bootstrap", "identity-provider", "infrastructure", "pending"}


def judge(mounts: set[str], reads: dict[str, set[str]], errors: list[str], allow: dict,
          stale: bool = True) -> list[str]:
    out = list(errors)
    allowed_m = allow.get("mounts") or {}
    allowed_r = allow.get("readers") or {}
    for m in sorted(mounts):
        if m not in allowed_m:
            out.append(f"unlisted mount of an owner credential: {m!r}")
    for sa in sorted(reads):
        if sa not in allowed_r:
            out.append(f"unlisted reader of an owner credential: {sa!r} can {sorted(reads[sa])}")
    if stale:
        for m in sorted(allowed_m):
            if m not in mounts:
                out.append(f"stale mounts entry (no render produces it): {m!r}")
        for sa in sorted(allowed_r):
            if sa not in reads and not allowed_r[sa].get("live-only"):
                out.append(f"stale readers entry (no render grants it): {sa!r}")
    return out


def rendered_findings(docsets: list[list[dict]], owner: set[str]):
    mounts, reads, errors = set(), {}, []
    for docs in docsets:
        mounts |= mount_readers(docs, owner)
        r, e = can_read(docs, owner)
        for k, v in r.items():
            reads.setdefault(k, set()).update(v)
        errors += [x for x in e if x not in errors]
    return mounts, reads, errors


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


def live_mounts(owner: set[str]) -> set[str]:
    """Pods in the release namespace, named by their controller the way the render names them."""
    rs_owner = {}
    for rs in kubectl_json(["get", "replicasets", "-n", NS])["items"]:
        for o in rs["metadata"].get("ownerReferences") or []:
            rs_owner[rs["metadata"]["name"]] = f"{o['kind']}/{o['name']}"
    job_owner = {}
    for j in kubectl_json(["get", "jobs", "-n", NS])["items"]:
        refs = j["metadata"].get("ownerReferences") or []
        job_owner[j["metadata"]["name"]] = f"{refs[0]['kind']}/{refs[0]['name']}" if refs else f"Job/{j['metadata']['name']}"
    out = set()
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
        for secret, keys in secret_uses(p["spec"], owner):
            out.add(f"{who} {secret}[{keys}]")
    return out


def live(allow: dict) -> int:
    owner = set(allow["owner_secrets"])
    reads = live_reads(owner)
    for pattern, why in (allow.get("live_namespaces") or {}).items():
        for sa in [s for s in reads if s.split("/", 1)[0] == pattern]:
            print(f"  allowed by namespace {pattern} ({why.strip().splitlines()[0]}): {sa} can {sorted(reads.pop(sa))}")
    mounts = live_mounts(owner)
    got = judge(mounts, reads, [], allow, stale=False)
    for sa in sorted(reads):
        print(f"  reader: {sa} can {sorted(reads[sa])}")
    for m in sorted(mounts):
        print(f"  mount:  {m}")
    if got:
        print("FAIL: the live cluster has owner-credential readers the allowlist does not name:\n  " + "\n  ".join(got))
        return 1
    print(f"OK: {len(reads)} ServiceAccounts can read an owner credential, {len(mounts)} pods mount one; every one is listed")
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

SELFTEST_ALLOW = {
    "owner_secrets": ["iam-admin-pat", "iam-admin", "gibson-zitadel-system-key"],
    "mounts": {},
    "readers": {"gibson/boot": {"class": "bootstrap", "why": "fixture"}},
}


def selftest() -> int:
    owner = set(SELFTEST_ALLOW["owner_secrets"])
    base = [d for d in yaml.safe_load_all(FIXTURE_BASE) if d]
    m, r, e = rendered_findings([base], owner)
    if judge(m, r, e, SELFTEST_ALLOW):
        print(f"SELFTEST FAIL: the base fixture must pass, got {judge(m, r, e, SELFTEST_ALLOW)}")
        return 1
    for what, text in FIXTURES_THAT_MUST_FAIL.items():
        docs = base + [d for d in yaml.safe_load_all(text) if d]
        m, r, e = rendered_findings([docs], owner)
        if not judge(m, r, e, SELFTEST_ALLOW):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    for what, text in FIXTURES_THAT_MUST_PASS.items():
        docs = base + [d for d in yaml.safe_load_all(text) if d]
        m, r, e = rendered_findings([docs], owner)
        got = judge(m, r, e, SELFTEST_ALLOW)
        if got:
            print(f"SELFTEST FAIL: {what}: the guard failed it: {got}")
            return 1
    stale = dict(SELFTEST_ALLOW, readers={**SELFTEST_ALLOW["readers"],
                                          "gibson/gone": {"class": "bootstrap", "why": "fixture"}})
    m, r, e = rendered_findings([base], owner)
    if not any(x.startswith("stale readers entry") for x in judge(m, r, e, stale)):
        print("SELFTEST FAIL: a stale readers entry must fail")
        return 1
    allow = load_allowlist()
    m, r, e = rendered_findings(renders(), set(allow["owner_secrets"]))
    got = judge(m, r, e, allow)
    if got:
        print("SELFTEST FAIL: the render and the allowlist disagree:\n  " + "\n  ".join(got))
        return 1
    print(f"OK: {len(FIXTURES_THAT_MUST_FAIL)} new-reader fixtures fail, {len(FIXTURES_THAT_MUST_PASS)} "
          "near misses pass, a stale entry fails; the render matches the allowlist")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    allow = load_allowlist()
    if "--live" in sys.argv:
        return live(allow)
    owner = set(allow["owner_secrets"])
    mounts, reads, errors = rendered_findings(renders(), owner)
    if "--print" in sys.argv:
        for m in sorted(mounts):
            print(f"mount   {m}")
        for sa in sorted(reads):
            print(f"reader  {sa}: {sorted(reads[sa])}")
        for e in errors:
            print(f"error   {e}")
        return 0
    got = judge(mounts, reads, errors, allow)
    if got:
        print("FAIL: a workload that is not on helm/gibson/owner-credential-readers.yaml can read a "
              "Zitadel owner credential. Only bootstrap may. Give the reader its own narrow credential, "
              "or scope the RBAC rule with resourceNames:\n  " + "\n  ".join(got))
        return 1
    pending = [k for s in ("mounts", "readers") for k, v in (allow.get(s) or {}).items() if v["class"] == "pending"]
    print(f"OK: {len(mounts)} mounts and {len(reads)} ServiceAccounts read an owner credential; every one is listed"
          + (f" ({len(pending)} pending removal)" if pending else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
