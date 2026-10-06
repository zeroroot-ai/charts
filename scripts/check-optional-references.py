#!/usr/bin/env python3
"""check-optional-references.py: each optional reference and empty default has a reason.

ADR-0003: the render fails when a required value is empty. Two template
patterns let a render or a pod start pass when an input is absent:

  1. `optional: true` on a secretKeyRef, configMapKeyRef, secretRef,
     configMapRef, or on a Secret or ConfigMap volume or projection;
  2. a `| default ""` fallback.

Each one is either correct (the input is truly optional, and the code that
reads it handles the absence) or a defect that hides a missing input. This
guard lists each case in the first-party templates, comments stripped, and
permits it only when scripts/.optional-references.yaml names it with a reason.

The key of a case is its content, never a line number:

  optional  "<file> optional <ref kind> <name>[/<key>]"
  default   "<file> default <the template expression, spaces folded>"

An entry that matches no case fails, so the list cannot go stale.

  check-optional-references.py             exit 1 on a case with no entry, 0 when clean
  check-optional-references.py --selftest  prove each kind of finding fails
  check-optional-references.py --print     print each case with its key
"""
import os
import re
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "scripts", ".optional-references.yaml")
TEMPLATE_COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)
YAML_COMMENT = re.compile(r"^[ \t]*#.*$", re.M)
OPTIONAL = re.compile(r"^([ \t]*)optional:[ \t]*true\b", re.M)
EMPTY_DEFAULT = re.compile(r'\|\s*default\s+""')
ACTION = re.compile(r"\{\{.*?\}\}", re.S)
REF_KINDS = ("secretKeyRef", "configMapKeyRef", "secretRef", "configMapRef", "secret", "configMap")


def template_files(root: str) -> list[str]:
    out = []
    helm = os.path.join(root, "helm")
    for chart in sorted(os.listdir(helm)):
        base = os.path.join(helm, chart, "templates")
        for dirpath, _, names in os.walk(base):
            out += [os.path.join(dirpath, n) for n in sorted(names) if n.endswith((".yaml", ".yml", ".tpl"))]
    return out


def strip(text: str) -> str:
    # Keep line structure, so that the indentation walk below still works.
    text = TEMPLATE_COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    return YAML_COMMENT.sub("", text)


def ref_of(lines: list[str], i: int, indent: int) -> str:
    """'<ref kind> <name>[/<key>]' of the reference block that holds lines[i]."""
    name = key = kind = ""
    j = i - 1
    while j >= 0:
        line = lines[j]
        if not line.strip():
            j -= 1
            continue
        ind = len(line) - len(line.lstrip())
        text = line.strip().lstrip("- ")
        if ind == indent and not name and text.startswith(("name:", "secretName:")):
            name = text.split(":", 1)[1].strip().strip("\"'")
        if ind == indent and not key and text.startswith("key:"):
            key = text.split(":", 1)[1].strip().strip("\"'")
        if ind < indent:
            kind = text.split(":", 1)[0]
            break
        j -= 1
    # A name or key that comes after `optional:` in the same block.
    k = i + 1
    while k < len(lines) and lines[k].strip() and len(lines[k]) - len(lines[k].lstrip()) == indent:
        text = lines[k].strip()
        if not name and text.startswith(("name:", "secretName:")):
            name = text.split(":", 1)[1].strip().strip("\"'")
        if not key and text.startswith("key:"):
            key = text.split(":", 1)[1].strip().strip("\"'")
        k += 1
    return f"{kind} {name}" + (f"/{key}" if key else "")


def cases(root: str) -> dict[str, str]:
    """key -> 'file:line' of the first occurrence."""
    out = {}
    for f in template_files(root):
        rel = os.path.relpath(f, root).replace(os.sep, "/")
        text = strip(open(f, encoding="utf-8").read())
        lines = text.split("\n")
        for m in OPTIONAL.finditer(text):
            i = text.count("\n", 0, m.start())
            key = f"{rel} optional {ref_of(lines, i, len(m.group(1)))}"
            out.setdefault(key, f"{rel}:{i + 1}")
        for a in ACTION.finditer(text):
            if EMPTY_DEFAULT.search(a.group(0)):
                expr = " ".join(a.group(0).split())
                key = f"{rel} default {expr}"
                out.setdefault(key, f"{rel}:{text.count(chr(10), 0, a.start()) + 1}")
    return out


def load(path: str = ALLOWLIST) -> dict[str, str]:
    data = (yaml.safe_load(open(path)) if os.path.exists(path) else None) or {}
    entries = data.get("cases") or {}
    for k, v in entries.items():
        if not str(v or "").strip():
            raise SystemExit(f"{path}: case {k!r} must state its reason")
    return entries


def audit(found: dict[str, str], allowed: dict[str, str]) -> list[str]:
    bad = [f"{where}: no reason recorded for: {key!r}" for key, where in sorted(found.items()) if key not in allowed]
    bad += [f"stale entry in scripts/.optional-references.yaml (no template has it): {key!r}"
            for key in sorted(allowed) if key not in found]
    if not found:
        bad.append("found no case at all: this guard is blind")
    return bad


FIXTURE = '''{{- /* optional: true in a comment is not a case */ -}}
env:
  - name: A
    valueFrom:
      secretKeyRef:
        name: some-secret
        key: token
        optional: true
  - name: B
    value: {{ .Values.b | default "" | quote }}
  # optional: true in a YAML comment is not a case
volumes:
  - name: v
    secret:
      secretName: other-secret
      optional: true
'''


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "c", "templates")
        os.makedirs(t)
        open(os.path.join(t, "a.yaml"), "w").write(FIXTURE)
        found = cases(d)
        # The fixture path is joined, so the path guard does not read it as a
        # path of this repository.
        fx = "/".join(("helm", "c", "templates", "a.yaml"))
        gone = "/".join(("helm", "c", "templates", "gone.yaml"))
        want = {f"{fx} optional secretKeyRef some-secret/token",
                f"{fx} optional secret other-secret",
                fx + ' default {{ .Values.b | default "" | quote }}'}
        if set(found) != want:
            print(f"SELFTEST FAIL: want {sorted(want)}, got {sorted(found)}")
            return 1
        full = {k: "fixture" for k in want}
        if audit(found, full):
            print("SELFTEST FAIL: each case with a reason must pass")
            return 1
        less = dict(full)
        less.pop(f"{fx} optional secret other-secret")
        if len(audit(found, less)) != 1:
            print("SELFTEST FAIL: a case with no reason must fail")
            return 1
        if not any("stale" in x for x in audit(found, dict(full, **{f"{gone} default x": "r"}))):
            print("SELFTEST FAIL: a stale entry must fail")
            return 1
        if not audit({}, {}):
            print("SELFTEST FAIL: a tree with no case must fail as blind")
            return 1
    print("  ✓ selftest: an optional reference and an empty default with no reason fail, a stale entry fails, "
          "comments are not cases, a blind tree fails")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    found = cases(ROOT)
    if "--print" in sys.argv:
        for k, where in sorted(found.items(), key=lambda x: x[1]):
            print(f"{where}\t{k}")
        return 0
    bad = audit(found, load())
    if bad:
        print("an optional reference or an empty default has no recorded reason (ADR-0003):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ optional-references: {len(found)} optional references and empty defaults, each with a reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
