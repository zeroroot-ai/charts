#!/usr/bin/env python3
"""check-orphan-templates.py — no Helm named template that nothing invokes.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1456).
A `define` nothing `include`s or `template`s is dead code that still reads
as a contract: the next author trusts it, copies it, or extends it, and the
helper lock it claims never engages. Defines and calls are collected across
every chart under helm/ (gibson-common is a library the others include from),
with Go-template comment blocks stripped so a documented example is not a
define.

  check-orphan-templates.py             exit 1 on an orphan, 0 when clean
  check-orphan-templates.py --selftest  prove an uncalled define fails and the tree passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)
DEFINE = re.compile(r'\{\{-?\s*define\s+"([^"]+)"')
CALL = re.compile(r'\b(?:include|template)\s+"([^"]+)"')


def collect(root: str) -> tuple[dict[str, str], set[str]]:
    defs: dict[str, str] = {}
    uses: set[str] = set()
    for dirpath, dirs, files in os.walk(os.path.join(root, "helm")):
        rel = os.path.relpath(dirpath, root).replace(os.sep, "/")
        if "/charts" in rel or rel.startswith("helm/testdata"):
            dirs[:] = []
            continue
        if "/templates" not in rel:
            continue
        for f in files:
            if not f.endswith((".yaml", ".yml", ".tpl")):
                continue
            p = os.path.join(dirpath, f)
            text = COMMENT.sub("", open(p, encoding="utf-8", errors="replace").read())
            for m in DEFINE.finditer(text):
                defs.setdefault(m.group(1), os.path.relpath(p, root))
            for m in CALL.finditer(text):
                uses.add(m.group(1))
    return defs, uses


def orphans(root: str) -> list[str]:
    defs, uses = collect(root)
    return [f"{name} (defined in {path})" for name, path in sorted(defs.items()) if name not in uses]


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "x", "templates")
        os.makedirs(t)
        open(os.path.join(t, "_helpers.tpl"), "w").write(
            '{{/* an example: {{- define "x.documented" -}} */}}\n'
            '{{- define "x.used" -}}u{{- end -}}\n{{- define "x.orphan" -}}o{{- end -}}\n'
        )
        open(os.path.join(t, "a.yaml"), "w").write('name: {{ include "x.used" . }}\n')
        got = orphans(d)
        if len(got) != 1 or not got[0].startswith("x.orphan "):
            print(f"SELFTEST FAIL: want exactly x.orphan flagged (and the commented example ignored), got {got}")
            return 1
    live = orphans(ROOT)
    if live:
        print("SELFTEST FAIL: the tree has an orphan named template:\n  " + "\n  ".join(live))
        return 1
    print("OK: an uncalled define fails, a commented example does not, the tree is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = orphans(ROOT)
    if got:
        print("❌ named templates nothing invokes (delete them, or call them):\n  " + "\n  ".join(got))
        return 1
    print("✓ orphan-templates: every named template is invoked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
