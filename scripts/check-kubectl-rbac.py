#!/usr/bin/env python3
"""check-kubectl-rbac.py: each kubectl call of a chart script is allowed by the
RBAC that the render grants to the ServiceAccount of that script.

WHY. In charts 0.150.0 the redis rotation CronJob ran `kubectl exec` with a Role
that granted `pods/exec create` and no `pods get`. kubectl reads the pod before
it opens the exec stream, so every Job failed before it added the new password.
The rotation cascade of drill run 39125128505 came from that one missing verb.
No check compared the script with its own Role.

HOW. A test runs the rendered script against a stub kubectl that records each
argv as one JSON array per line (tests/redis-rotation.bats). This script reads
that log and the render. It maps each call to the API requests kubectl makes
(verb, API group, resource, subresource, namespace, name), and checks each
request against the rules of every Role and ClusterRole bound to the
ServiceAccount in the render. A request that no rule allows fails the check,
with the call and the missing grant named.

The names come from the recorded argv, so a name that the script builds at run
time is checked as it was used.

Usage:
  check-kubectl-rbac.py --render <render.yaml> --sa <namespace>/<name> --calls <calls.jsonl>
  check-kubectl-rbac.py --selftest
"""
import argparse
import json
import sys

import yaml

# kubectl resource names and short names -> (API group, plural resource).
KINDS = {
    "pod": ("", "pods"), "pods": ("", "pods"), "po": ("", "pods"),
    "secret": ("", "secrets"), "secrets": ("", "secrets"),
    "configmap": ("", "configmaps"), "configmaps": ("", "configmaps"), "cm": ("", "configmaps"),
    "serviceaccount": ("", "serviceaccounts"), "serviceaccounts": ("", "serviceaccounts"), "sa": ("", "serviceaccounts"),
    "service": ("", "services"), "services": ("", "services"), "svc": ("", "services"),
    "statefulset": ("apps", "statefulsets"), "statefulsets": ("apps", "statefulsets"), "sts": ("apps", "statefulsets"),
    "deployment": ("apps", "deployments"), "deployments": ("apps", "deployments"), "deploy": ("apps", "deployments"),
    "daemonset": ("apps", "daemonsets"), "daemonsets": ("apps", "daemonsets"), "ds": ("apps", "daemonsets"),
    "job": ("batch", "jobs"), "jobs": ("batch", "jobs"),
    "externalsecret": ("external-secrets.io", "externalsecrets"),
    "externalsecrets": ("external-secrets.io", "externalsecrets"),
    "es": ("external-secrets.io", "externalsecrets"),
}
# Flags that take the next argv as their value.
VALUE_FLAGS = {"-n", "--namespace", "-c", "--container", "-l", "--selector", "-o", "--output",
               "-f", "--filename", "--field-selector", "--from-literal", "--type", "--timeout",
               "--context", "--kubeconfig", "-p", "--patch"}

# The documented create shortcuts that make one named object.
CREATE_SHORTCUTS = {"secret", "configmap", "serviceaccount"}


class CallError(Exception):
    pass


def parse(argv):
    """Split a kubectl argv into (subcommand words, positional args, flags dict)."""
    flags, pos = {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            pos.append(("--", argv[i + 1:]))
            break
        if a.startswith("-"):
            if "=" in a and a.startswith("--"):
                k, v = a.split("=", 1)
                flags.setdefault(k, []).append(v)
            elif a in VALUE_FLAGS and i + 1 < len(argv):
                flags.setdefault(a, []).append(argv[i + 1])
                i += 1
            else:
                flags.setdefault(a, []).append("")
        else:
            pos.append(a)
        i += 1
    return pos, flags


def flag(flags, *names):
    for n in names:
        if n in flags:
            return flags[n][-1]
    return None


def kind_of(word):
    k = KINDS.get(word.lower())
    if k is None:
        raise CallError(f"unknown resource {word!r}: add it to KINDS in scripts/check-kubectl-rbac.py")
    return k


def targets(args):
    """kubectl's resource arguments -> [(group, resource, name or None)]."""
    if not args:
        raise CallError("no resource named")
    if "/" in args[0]:
        out = []
        for a in args:
            k, _, n = a.partition("/")
            g, r = kind_of(k)
            out.append((g, r, n))
        return out
    g, r = kind_of(args[0])
    names = args[1:]
    return [(g, r, n) for n in names] if names else [(g, r, None)]


def _is_dry_create(argv):
    pos, flags = parse(argv)
    return bool(pos) and pos[0] == "create" and flag(flags, "--dry-run") == "client"


def _is_apply_stdin(argv):
    pos, flags = parse(argv)
    return bool(pos) and pos[0] == "apply" and flag(flags, "-f", "--filename") == "-"


def pipe_order(calls):
    """`create --dry-run=client -o yaml | apply -f -` starts both processes at
    once, so a log can hold the apply first. Put each such create before the
    apply right next to it."""
    calls = list(calls)
    for i in range(len(calls) - 1):
        if _is_apply_stdin(calls[i]) and _is_dry_create(calls[i + 1]):
            calls[i], calls[i + 1] = calls[i + 1], calls[i]
    return calls


def requests(calls):
    """Yield (call, [request]) for each recorded call. A request is a dict."""
    calls = pipe_order(calls)
    pending_apply = None  # the object of the last `create ... --dry-run=client -o yaml`
    for argv in calls:
        pos, flags = parse(argv)
        ns = flag(flags, "-n", "--namespace")
        sub = pos[0] if pos else ""
        rest = [p for p in pos[1:] if not isinstance(p, tuple)]
        reqs = []

        def req(verb, g, r, name=None, subres="", in_ns=None):
            reqs.append({"verb": verb, "group": g, "resource": r, "sub": subres,
                         "ns": in_ns if in_ns is not None else ns, "name": name})

        if sub == "get":
            selector = flag(flags, "-l", "--selector") is not None
            for g, r, n in targets(rest):
                if n is None or selector:
                    req("list", g, r)
                else:
                    req("get", g, r, n)
            if "--watch" in flags or "-w" in flags:
                for g, r, _ in targets(rest):
                    req("watch", g, r)
        elif sub == "delete":
            for g, r, n in targets(rest):
                if n is None:
                    req("deletecollection", g, r)
                else:
                    req("delete", g, r, n)
            # The default --wait=true waits through a watch of the object.
            if flag(flags, "--wait") not in ("false",):
                for g, r, _ in targets(rest):
                    req("watch", g, r)
        elif sub == "exec":
            if not rest:
                raise CallError("exec names no pod")
            pod = rest[0].split("/", 1)[-1]
            # kubectl reads the pod first (its containers), then opens the stream.
            req("get", "", "pods", pod)
            req("create", "", "pods", pod, "exec")
        elif sub == "create":
            dry = flag(flags, "--dry-run") in ("client",)
            if rest and rest[0] in CREATE_SHORTCUTS:
                kind = rest[0]
                name = rest[2] if kind == "secret" and len(rest) > 2 else (rest[1] if len(rest) > 1 else None)
                g, r = kind_of(kind)
                if dry:
                    pending_apply = (g, r, name, ns)
                else:
                    req("create", g, r)
            else:
                raise CallError("create of a manifest: the check cannot tell its object")
        elif sub == "apply":
            if flag(flags, "-f", "--filename") != "-" or pending_apply is None:
                raise CallError("apply of an object the check cannot tell (only `create ... --dry-run=client -o yaml | apply -f -`)")
            g, r, n, obj_ns = pending_apply
            pending_apply = None
            # The object carries the namespace of its create, unless apply names one.
            at = ns if ns is not None else obj_ns
            # Client-side apply reads the object, then creates or patches it.
            req("get", g, r, n, in_ns=at)
            req("create", g, r, in_ns=at)
            req("patch", g, r, n, in_ns=at)
        elif sub in ("annotate", "label", "patch"):
            for g, r, n in targets(rest):
                req("get", g, r, n)
                req("patch", g, r, n)
        elif sub == "rollout":
            action, args = (rest[0], rest[1:]) if rest else ("", [])
            for g, r, n in targets(args):
                req("get", g, r, n)
                if action == "restart":
                    req("patch", g, r, n)
                elif action == "status":
                    req("watch", g, r)
                else:
                    raise CallError(f"rollout {action!r} is not modelled")
        elif sub in ("version", "config", "auth"):
            pass
        else:
            raise CallError(f"kubectl {sub!r} is not modelled: add it to scripts/check-kubectl-rbac.py")
        yield argv, reqs


def bound_rules(docs, sa_ns, sa_name):
    """The rules of each Role and ClusterRole bound to the ServiceAccount."""
    roles = {}
    for d in docs:
        if d.get("kind") in ("Role", "ClusterRole"):
            m = d["metadata"]
            roles[(d["kind"], m.get("namespace") if d["kind"] == "Role" else None, m["name"])] = d.get("rules") or []
    out = []
    for d in docs:
        if d.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        subj = [s for s in d.get("subjects") or []
                if s.get("kind") == "ServiceAccount" and s.get("name") == sa_name
                and s.get("namespace", d["metadata"].get("namespace")) == sa_ns]
        if not subj:
            continue
        ref = d["roleRef"]
        bns = d["metadata"].get("namespace") if d["kind"] == "RoleBinding" else None
        key = (ref["kind"], bns if ref["kind"] == "Role" else None, ref["name"])
        for rule in roles.get(key, []):
            # A RoleBinding grants in its own namespace, a ClusterRoleBinding everywhere.
            out.append((bns, rule))
    return out


def allowed(rq, rules):
    res = rq["resource"] + ("/" + rq["sub"] if rq["sub"] else "")
    for ns, rule in rules:
        if ns is not None and rq["ns"] != ns:
            continue
        if not ({rq["group"], "*"} & set(rule.get("apiGroups") or [])):
            continue
        if not ({res, "*"} & set(rule.get("resources") or [])):
            continue
        if not ({rq["verb"], "*"} & set(rule.get("verbs") or [])):
            continue
        names = rule.get("resourceNames") or []
        if names:
            # Kubernetes checks resourceNames only for a request that names
            # an object: create of a new object, list, watch and
            # deletecollection never match. A create on a subresource
            # (pods/exec) names an existing object, so it can match.
            if rq["name"] is None or rq["verb"] in ("list", "watch", "deletecollection") \
                    or (rq["verb"] == "create" and not rq["sub"]):
                continue
            if rq["name"] not in names:
                continue
        return True
    return False


def check(docs, sa, calls):
    """Return a list of findings (empty when every call is allowed)."""
    sa_ns, _, sa_name = sa.partition("/")
    rules = bound_rules(docs, sa_ns, sa_name)
    findings = []
    if not rules:
        return [f"no Role or ClusterRole in the render is bound to ServiceAccount {sa}"]
    if not calls:
        return ["the call log is empty: the test recorded no kubectl call, so the check looked at nothing"]
    try:
        for argv, reqs in requests(calls):
            for rq in reqs:
                if not allowed(rq, rules):
                    res = rq["resource"] + ("/" + rq["sub"] if rq["sub"] else "")
                    grp = rq["group"] or "core"
                    name = f" {rq['name']}" if rq["name"] else ""
                    findings.append(f"`kubectl {' '.join(argv)}` needs {rq['verb']} {res}{name} "
                                    f"({grp}, namespace {rq['ns']}), and no rule bound to {sa} allows it")
    except CallError as e:
        findings.append(str(e))
    return sorted(set(findings))


# --- selftest -------------------------------------------------------------

SELFTEST_RENDER = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: gibson-redis-rotation, namespace: gibson}
rules:
  - {apiGroups: [""], resources: [secrets], resourceNames: [gibson-redis-stack], verbs: [get]}
  - {apiGroups: [""], resources: [secrets], verbs: [create]}
  - {apiGroups: [""], resources: [secrets], resourceNames: [gibson-redis-rotation-state], verbs: [get, update, patch, delete]}
  - {apiGroups: [apps], resources: [statefulsets], resourceNames: [gibson-gibson-workloads], verbs: [get]}
  - {apiGroups: [""], resources: [pods], verbs: [get, list, delete]}
  - {apiGroups: [""], resources: [pods/exec], resourceNames: [gibson-redis-stack-0], verbs: [create]}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: gibson-redis-rotation, namespace: gibson}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: gibson-redis-rotation}
subjects: [{kind: ServiceAccount, name: gibson-redis-rotation, namespace: gibson}]
"""

SELFTEST_CALLS = [
    ["get", "secret", "-n", "gibson", "gibson-redis-stack", "-o", "jsonpath={.metadata.resourceVersion}"],
    ["get", "secret", "-n", "gibson", "gibson-redis-rotation-state", "-o", "name"],
    ["exec", "-i", "-n", "gibson", "gibson-redis-stack-0", "-c", "redis-stack", "--", "sh", "-c", "x"],
    ["-n", "gibson", "get", "statefulset/gibson-gibson-workloads", "-o", "json"],
    ["-n", "gibson", "get", "pods", "-l", "app=x", "-o", "json"],
    ["-n", "gibson", "delete", "pod", "x-0", "--ignore-not-found", "--wait=false"],
    ["create", "secret", "generic", "gibson-redis-rotation-state", "-n", "gibson",
     "--from-literal=lastProcessedVersion=2", "--save-config", "--dry-run=client", "-o", "yaml"],
    ["apply", "-f", "-"],
]


def selftest():
    good = list(yaml.safe_load_all(SELFTEST_RENDER))
    sa = "gibson/gibson-redis-rotation"
    cases = []

    def expect(label, docs, calls, want_substr):
        got = check(docs, sa, calls)
        if want_substr is None:
            ok = got == []
        else:
            ok = any(want_substr in f for f in got)
        cases.append((label, ok, got))

    expect("the 0.151.1 Role allows every call of the rotation", good, SELFTEST_CALLS, None)

    # FAILING FIXTURE: the 0.150.0 Role. pods/exec create and no pods get.
    no_get = yaml.safe_load(SELFTEST_RENDER.split("---")[0])
    no_get["rules"] = [r for r in no_get["rules"] if r.get("resources") != ["pods"]]
    expect("FAILING FIXTURE: the 0.150.0 Role with no pods get fails on exec",
           [no_get, good[1]], SELFTEST_CALLS, "needs get pods gibson-redis-stack-0")

    # FAILING FIXTURE: an exec into a pod that the pods/exec rule does not name.
    expect("FAILING FIXTURE: an exec into another pod fails",
           good, [["exec", "-i", "-n", "gibson", "gibson-openbao-0", "--", "sh"]], "needs create pods/exec gibson-openbao-0")

    # FAILING FIXTURE: a delete that waits needs a watch on pods.
    expect("FAILING FIXTURE: a delete with the default wait needs watch pods",
           good, [["-n", "gibson", "delete", "pod", "x-0"]], "needs watch pods")

    # FAILING FIXTURE: apply needs patch on the state Secret; a rule that names
    # another Secret does not allow it.
    other = yaml.safe_load(SELFTEST_RENDER.split("---")[0])
    for r in other["rules"]:
        if r.get("resourceNames") == ["gibson-redis-rotation-state"]:
            r["resourceNames"] = ["some-other-secret"]
    expect("FAILING FIXTURE: apply of a Secret that no rule names fails",
           [other, good[1]], SELFTEST_CALLS, "needs patch secrets gibson-redis-rotation-state")

    # FAILING FIXTURE: create cannot be granted by a name.
    named_create = yaml.safe_load(SELFTEST_RENDER.split("---")[0])
    named_create["rules"] = [r for r in named_create["rules"] if r.get("verbs") != ["create"]]
    for r in named_create["rules"]:
        if r.get("resourceNames") == ["gibson-redis-rotation-state"]:
            r["verbs"] = ["get", "create", "patch"]
    expect("FAILING FIXTURE: a create allowed only by a resourceNames rule fails",
           [named_create, good[1]], SELFTEST_CALLS, "needs create secrets")

    # FAILING FIXTURE: a call in another namespace.
    expect("FAILING FIXTURE: a Role grants only in its own namespace",
           good, [["get", "secret", "-n", "kube-system", "gibson-redis-stack"]], "namespace kube-system")

    # The two processes of a pipe can log in either order.
    swapped = SELFTEST_CALLS[:-2] + [SELFTEST_CALLS[-1], SELFTEST_CALLS[-2]]
    expect("an apply logged before its dry-run create is paired with it", good, swapped, None)

    # FAILING FIXTURE: an apply with no dry-run create next to it.
    expect("FAILING FIXTURE: an apply of an unknown object fails",
           good, [["apply", "-f", "-"]], "apply of an object the check cannot tell")

    # FAILING FIXTURE: an empty call log measures nothing.
    expect("FAILING FIXTURE: an empty call log fails", good, [], "the call log is empty")

    # FAILING FIXTURE: an unbound ServiceAccount.
    expect("FAILING FIXTURE: no binding for the ServiceAccount fails", [good[0]], SELFTEST_CALLS, "no Role or ClusterRole")

    # FAILING FIXTURE: a kubectl subcommand the check does not model.
    expect("FAILING FIXTURE: an unmodelled subcommand fails", good, [["cp", "a", "b"]], "is not modelled")

    bad = 0
    for label, ok, got in cases:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            bad += 1
            for f in got:
                print(f"         {f}")
    if bad:
        print(f"check-kubectl-rbac selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-kubectl-rbac selftest: {len(cases)} cases passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--render")
    ap.add_argument("--sa", help="<namespace>/<name>")
    ap.add_argument("--calls", help="one JSON array of kubectl argv per line")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.render and a.sa and a.calls):
        ap.error("--render, --sa and --calls are required")
    docs = [d for d in yaml.safe_load_all(open(a.render)) if d]
    calls = [json.loads(line) for line in open(a.calls) if line.strip()]
    findings = check(docs, a.sa, calls)
    if findings:
        for f in findings:
            print(f"FAIL: {f}", file=sys.stderr)
        return 1
    print(f"kubectl-rbac: {len(calls)} kubectl calls of {a.sa}, each allowed by its Role")
    return 0


if __name__ == "__main__":
    sys.exit(main())
