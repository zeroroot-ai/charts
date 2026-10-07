#!/usr/bin/env python3
"""check-secret-reads-granted.py: every Secret a container reads with kubectl, its ServiceAccount may read.

Secret RBAC in this chart is scoped by name, so a Secret read can lose its
grant when a rule narrows. A container that runs `kubectl get secret <name>`
under a ServiceAccount that may not get that Secret does not fail at render
time. It fails on a cluster, often in an init container, and the pod sits in
Init:CrashLoopBackOff while everything that waits for it stalls. This guard
moves that failure into CI.

For every container and init container in every rendered pod spec, it finds
each `kubectl ... get secret[s] [<name>]` in the script. It resolves the name
and the namespace statically, from the script and the container's literal env:

  - a literal:                      kubectl get secret gibson-redis-stack
  - a literal assignment:           name="gibson-redis-stack"
  - a function argument:            has_key() { local name="$2"; ... }; has_key "$NS" "x"
  - a list and a field of it:       for e in $ENTRIES; do S=${e%%:*}
  - `read` into fields:             IFS=":" read -r A B S <<< "$e"

A name it cannot resolve fails the guard: a Secret read whose name no one can
read from the chart is a read no one can review. Then it asks the RBAC
evaluator of check-owner-credential-readers.py whether the pod's
ServiceAccount can do that verb (get for a name, list for none) on that
Secret in that namespace.

  check-secret-reads-granted.py             exit 1 on an ungranted or unresolved read
  check-secret-reads-granted.py --selftest  prove an ungranted read fails
"""
import importlib.util
import os
import re
import shlex
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "owner_readers", os.path.join(ROOT, "scripts", "check-owner-credential-readers.py"))
ocr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ocr)

VAR = re.compile(r'^\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))$')
ASSIGN = re.compile(r'''(?:^|[\s;(])(?:local\s+|export\s+|readonly\s+)?([A-Za-z_]\w*)=("(?:[^"\\]|\\.)*"|'[^']*'|[^\s;|&()]*)''')


def script_of(c: dict) -> str:
    text = "\n".join((c.get("command") or []) + (c.get("args") or []))
    return re.sub(r"\\\n\s*", " ", text)


def unquote(tok: str) -> str:
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in "\"'":
        return tok[1:-1]
    return tok


class Resolver:
    """Static resolution of shell variables to the literal values they can take."""

    def __init__(self, script: str, env: dict[str, str]):
        self.script = script
        self.env = env
        self.rules: dict[str, list[tuple]] = {}
        self._parse()

    def _add(self, var, rule):
        self.rules.setdefault(var, []).append(rule)

    def _parse(self):
        for m in re.finditer(r'(?m)^\s*([A-Za-z_]\w*)=\((.*?)\)\s*$', self.script, re.S):
            self._add(m.group(1), ("array", m.group(2)))
        func = None
        depth = 0
        for line in self.script.splitlines():
            s = line.strip()
            m = re.match(r'^([A-Za-z_][\w-]*)\s*\(\)\s*\{', s)
            if m:
                func, depth = m.group(1), 0
            if func:
                depth += s.count("{") - s.count("}")
            for var, val in ASSIGN.findall(line):
                raw = unquote(val) if val[:1] in "\"'" else val
                if raw == "":
                    continue
                p = re.fullmatch(r'\$\{?(\d)\}?', raw)
                if p and func:
                    self._add(var, ("arg", func, int(p.group(1))))
                    continue
                f = re.fullmatch(r'\$\{([A-Za-z_]\w*)%%(.)\*\}', raw)
                if f:
                    self._add(var, ("field", f.group(1), f.group(2), 0))
                    continue
                self._add(var, ("value", raw))
            m = re.search(r'\bfor\s+([A-Za-z_]\w*)\s+in\s+(.+?);?\s*(?:do)?\s*$', s)
            if m:
                self._add(m.group(1), ("words", m.group(2).rstrip("; ").removesuffix(" do").strip()))
            m = re.search(r'IFS=["\']?(.)["\']?\s+read\s+(?:-r\s+)?([\w\s]+?)\s*<<<\s*(\S+)', s)
            if m:
                sep, names, src = m.group(1), m.group(2).split(), unquote(m.group(3))
                v = VAR.match(src)
                if v:
                    for i, n in enumerate(names):
                        self._add(n, ("field", v.group(1) or v.group(2), sep, i))
            if func and depth <= 0 and s.endswith("}"):
                func = None

    def calls(self, func):
        for line in self.script.splitlines():
            s = line.strip()
            if s.startswith(func + " ") or s == func:
                try:
                    yield shlex.split(s, posix=True)[1:]
                except ValueError:
                    continue

    def values(self, token: str, seen=frozenset()) -> set[str] | None:
        """Every literal token can take, or None when it cannot be resolved."""
        tok = unquote(token)
        if "$" not in tok:
            return {tok}
        v = VAR.match(tok)
        if not v:
            return None
        name = v.group(1) or v.group(2)
        if name in seen:
            return None
        seen = seen | {name}
        out = set()
        if name in self.env:
            out.add(self.env[name])
        for rule in self.rules.get(name, []):
            got = self._rule(rule, seen)
            if got is None:
                return None
            out |= got
        return out or None

    def _rule(self, rule, seen):
        kind = rule[0]
        if kind == "value":
            return self.values(rule[1], seen)
        if kind == "arg":
            out = set()
            for args in self.calls(rule[1]):
                if len(args) < rule[2]:
                    return None
                got = self.values(args[rule[2] - 1], seen)
                if got is None:
                    return None
                out |= got
            return out
        if kind == "array":
            return set(shlex.split(rule[1], comments=True))
        if kind == "words":
            out = set()
            for w in shlex.split(rule[1]):
                a = re.fullmatch(r'\$\{([A-Za-z_]\w*)\[@\]\}', w)
                if a:
                    # "${ARR[@]}": one word per array item, never split.
                    got = self.values("$" + a.group(1), seen)
                    if got is None:
                        return None
                    out |= got
                elif w.startswith("$"):
                    got = self.values(w, seen)
                    if got is None:
                        return None
                    for g in got:
                        out |= set(g.split())
                else:
                    out.add(w)
            return out
        if kind == "field":
            src = self.values("$" + rule[1], seen)
            if src is None:
                return None
            out = set()
            for s in src:
                parts = s.split(rule[2])
                if rule[3] >= len(parts):
                    return None
                out.add(parts[rule[3]])
            return out
        return None


def secret_reads(script: str):
    """(verb, name token or None, namespace token or None) per kubectl secret read."""
    for m in re.finditer(r'\bkubectl\b([^|;&)<>\n]*)', script):
        try:
            toks = shlex.split(m.group(1), posix=True)
        except ValueError:
            toks = m.group(1).split()
        # keep quoting so variables stay tokens
        raw = re.findall(r'"[^"]*"|\'[^\']*\'|\S+', m.group(1))
        if "get" not in toks:
            continue
        ns, rest, i = None, [], 0
        while i < len(raw):
            t = raw[i]
            if t in ("-n", "--namespace") and i + 1 < len(raw):
                ns, i = raw[i + 1], i + 2
                continue
            if t.startswith("--namespace=") or t.startswith("-n="):
                ns = t.split("=", 1)[1]
            elif not t.startswith("-"):
                rest.append(t)
            elif t in ("-o", "--output", "-l", "--selector", "--field-selector") and i + 1 < len(raw):
                i += 1
            i += 1
        if len(rest) < 2 or rest[0] != "get":
            continue
        res = unquote(rest[1])
        kind, _, slash_name = res.partition("/")
        if kind not in ("secret", "secrets"):
            continue
        if slash_name:
            yield "get", slash_name, ns
        elif len(rest) > 2:
            yield "get", rest[2], ns
        else:
            yield "list", None, ns


def can(docs, sa: str, verb: str, ns: str, name: str | None, default_ns: str) -> bool:
    roles = ocr.role_rules(docs, default_ns)
    sa_ns, sa_name = sa.split("/", 1)
    for d in docs:
        kind = d.get("kind")
        if kind not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        bns = (d["metadata"].get("namespace") or default_ns) if kind == "RoleBinding" else None
        if bns is not None and bns != ns:
            continue
        if not any(s.get("kind") == "ServiceAccount" and s.get("name") == sa_name
                   and (s.get("namespace") or bns or default_ns) == sa_ns for s in d.get("subjects") or []):
            continue
        ref = d.get("roleRef") or {}
        rules = roles.get((ref.get("kind"), bns if ref.get("kind") == "Role" else "", ref.get("name")))
        if rules is None and ref.get("kind") == "ClusterRole":
            rules = ocr.BUILTIN_ROLES.get(ref.get("name"), [])
        if any(ocr.rule_allows(r, verb, name) for r in rules or []):
            return True
    return False


def check(docs: list[dict], default_ns: str = ocr.NS) -> list[str]:
    out = []
    for d in docs:
        ps = ocr.pod_spec(d)
        if ps is None:
            continue
        pod_ns = d["metadata"].get("namespace") or default_ns
        sa = f"{pod_ns}/{ps.get('serviceAccountName') or ps.get('serviceAccount') or 'default'}"
        who = f"{d['kind']}/{d['metadata']['name']}"
        for c in (ps.get("initContainers") or []) + (ps.get("containers") or []):
            script = script_of(c)
            env = {e["name"]: e["value"] for e in c.get("env") or [] if isinstance(e.get("value"), str)}
            r = Resolver(script, env)
            for verb, name_tok, ns_tok in secret_reads(script):
                namespaces = r.values(ns_tok) if ns_tok else {pod_ns}
                names = r.values(name_tok) if name_tok else {None}
                if namespaces is None or names is None:
                    out.append(f"{who} container {c['name']}: cannot resolve the Secret read "
                               f"`kubectl get secret {name_tok or ''} -n {ns_tok or pod_ns}` to literal names")
                    continue
                for ns in sorted(namespaces):
                    for name in sorted(names, key=str):
                        if not can(docs, sa, verb, ns, name, default_ns):
                            target = f"secret/{name}" if name else "secrets"
                            out.append(f"{who} container {c['name']} runs `kubectl {verb} {target} -n {ns}`, "
                                       f"and ServiceAccount {sa} may not")
    return out


FIXTURE = """
apiVersion: v1
kind: ServiceAccount
metadata: {name: op, namespace: gibson}
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: op, namespace: gibson}
rules: [{apiGroups: [''], resources: [secrets], resourceNames: [redis-pw, a, b, c], verbs: [get]}]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: op, namespace: gibson}
roleRef: {kind: Role, name: op}
subjects: [{kind: ServiceAccount, name: op, namespace: gibson}]
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: op, namespace: gibson}
spec:
  template:
    spec:
      serviceAccountName: op
      initContainers:
      - name: wait
        image: x
        command: [/bin/bash, -c]
        args:
        - |
          name="redis-pw"
          ns="gibson"
          until kubectl get secret -n "${ns}" "${name}" \\
              -o jsonpath='{.data.p}' | grep -q .; do sleep 2; done
      containers:
      - name: c
        image: x
        env: [{name: ONE, value: a}]
        args:
        - |
          has() { local ns="$1" name="$2"; kubectl -n "$ns" get secret "$name" >/dev/null; }
          has gibson "$ONE"
          ENTRIES="b:x c:y"
          for e in $ENTRIES; do
            S=${e%%:*}
            kubectl -n gibson get secret "$S" -o json
          done
"""


def renders() -> list[list[dict]]:
    """The owner-credential guard's renders. The dashboard reads no Stripe
    Secret any more (charts#375), so no extra billing render is needed."""
    return ocr.renders()


def selftest() -> int:
    base = [d for d in yaml.safe_load_all(FIXTURE) if d]
    if got := check(base):
        print(f"SELFTEST FAIL: the fixture's reads are all granted, got {got}")
        return 1
    cases = {
        "the init container's Secret loses its grant (the kind break)":
            lambda t: t.replace("resourceNames: [redis-pw, a, b, c]", "resourceNames: [a, b, c]"),
        "a function argument names an ungranted Secret":
            lambda t: t.replace('has gibson "$ONE"', 'has gibson "other"'),
        "a list entry names an ungranted Secret":
            lambda t: t.replace('ENTRIES="b:x c:y"', 'ENTRIES="b:x d:y"'),
        "a read in another namespace":
            lambda t: t.replace('ns="gibson"', 'ns="tenant-a"'),
        "a list of every Secret":
            lambda t: t.replace('kubectl -n gibson get secret "$S" -o json', 'kubectl -n gibson get secrets -o json'),
        "a name that cannot be resolved":
            lambda t: t.replace('S=${e%%:*}', 'S=$(echo "$e" | cut -d: -f1)'),
    }
    for what, fn in cases.items():
        docs = [d for d in yaml.safe_load_all(fn(FIXTURE)) if d]
        if not check(docs):
            print(f"SELFTEST FAIL: {what}: the guard passed it")
            return 1
    for docs in renders():
        if got := check(docs):
            print("SELFTEST FAIL: the render has ungranted Secret reads:\n  " + "\n  ".join(got))
            return 1
    print(f"OK: {len(cases)} ungranted or unresolved reads fail; every rendered kubectl Secret read is granted")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    out = []
    for docs in renders():
        out += [x for x in check(docs) if x not in out]
    if out:
        print("FAIL: a container reads a Secret its ServiceAccount may not read. Grant `get` on "
              "that name to the ServiceAccount, or stop the read:\n  " + "\n  ".join(out))
        return 1
    print("OK: every kubectl Secret read in every rendered pod is granted to its ServiceAccount")
    return 0


if __name__ == "__main__":
    sys.exit(main())
