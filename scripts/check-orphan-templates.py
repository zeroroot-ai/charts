#!/usr/bin/env python3
"""check-orphan-templates.py — no Helm named template that nothing invokes,
and none that is invoked and does nothing.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origin deploy#1456).
A `define` nothing `include`s or `template`s is dead code that still reads
as a contract: the next author trusts it, copies it, or extends it, and the
helper lock it claims never engages. Defines and calls are collected across
every chart under helm/ (gibson-common is a library the others include from),
with Go-template comment blocks stripped so a documented example is not a
define.

The second check is the consumer-side half of the same defect (ADR-0094,
charts#293). An orphan define is invisible because nothing calls it. The
opposite failure is worse: a define whose body is entirely comment, which IS
still called, so the `include` line reads as coverage while the helper asserts
nothing. Two render validators shipped that way, `gibson.validateSpire` and
`gibson.validateEnvoyGateway`, each still invoked once. A guard that cannot
fail is worse than no guard.

There is no exemption list. A validator staged ahead of activation keeps its
body and gates on a values flag, so it can still fail; an empty body is never
the right way to stage one.

  check-orphan-templates.py             exit 1 on an orphan or an inert define
  check-orphan-templates.py --selftest  prove each failure mode fails and the tree passes
"""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMENT = re.compile(r"\{\{-?\s*/\*.*?\*/\s*-?\}\}", re.S)
DEFINE = re.compile(r'\{\{-?\s*define\s+"([^"]+)"')
CALL = re.compile(r'\b(?:include|template)\s+"([^"]+)"')

# Every Go-template action, in order, so a define body can be read with its
# nested blocks balanced. A naive `define .*? end` stops at the first inner
# `end`, which under-reads any helper containing an if/with/range.
ACTION = re.compile(r"\{\{-?(.*?)-?\}\}", re.S)
OPENS = ("if", "range", "with", "define", "block")


def _define_bodies(text: str) -> list[tuple[str, str]]:
    """(name, body) for each define, with nested if/with/range/block balanced."""
    out: list[tuple[str, str]] = []
    actions = list(ACTION.finditer(text))
    for i, m in enumerate(actions):
        d = DEFINE.match(m.group(0))
        if not d:
            continue
        depth = 1
        for j in range(i + 1, len(actions)):
            kw = actions[j].group(1).strip().split()
            head = kw[0] if kw else ""
            if head in OPENS:
                depth += 1
            elif head == "end":
                depth -= 1
                if depth == 0:
                    out.append((d.group(1), text[m.end() : actions[j].start()]))
                    break
    return out


def _is_inert(body: str) -> bool:
    """A body that renders nothing at all: only comments and whitespace."""
    return COMMENT.sub("", body).strip() == ""


def collect(root: str) -> tuple[dict[str, str], set[str], dict[str, str]]:
    defs: dict[str, str] = {}
    uses: set[str] = set()
    inert: dict[str, str] = {}
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
            raw = open(p, encoding="utf-8", errors="replace").read()
            for name, body in _define_bodies(raw):
                if _is_inert(body):
                    inert.setdefault(name, os.path.relpath(p, root))
            text = COMMENT.sub("", raw)
            for m in DEFINE.finditer(text):
                defs.setdefault(m.group(1), os.path.relpath(p, root))
            for m in CALL.finditer(text):
                uses.add(m.group(1))
    return defs, uses, inert


def orphans(root: str) -> list[str]:
    defs, uses, _ = collect(root)
    return [f"{name} (defined in {path})" for name, path in sorted(defs.items()) if name not in uses]


def inert_called(root: str) -> list[str]:
    """Defines that ARE invoked and whose body renders nothing."""
    defs, uses, inert = collect(root)
    return [
        f"{name} (defined in {path})"
        for name, path in sorted(inert.items())
        if name in uses and name in defs
    ]


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        t = os.path.join(d, "helm", "x", "templates")
        os.makedirs(t)
        open(os.path.join(t, "_helpers.tpl"), "w").write(
            '{{/* an example: {{- define "x.documented" -}} */}}\n'
            '{{- define "x.used" -}}u{{- end -}}\n{{- define "x.orphan" -}}o{{- end -}}\n'
            # invoked, body is comment-only: must be caught as inert
            '{{- define "x.inert" -}}\n{{- /* intentional no-op */ -}}\n{{- end }}\n'
            # invoked, body is comment-only but ALSO never called: an orphan, not inert
            '{{- define "x.inert.orphan" -}}\n{{- /* nothing */ -}}\n{{- end }}\n'
            # a real validator whose body nests two blocks: must NOT read as inert,
            # which a naive `define .*? end` match would get wrong
            '{{- define "x.nested" -}}\n'
            '{{- if .Values.a -}}{{- with .Values.b -}}{{ fail "no" }}{{- end -}}{{- end -}}\n'
            "{{- end }}\n"
        )
        open(os.path.join(t, "a.yaml"), "w").write(
            'name: {{ include "x.used" . }}\n'
            '{{- include "x.inert" . }}\n'
            '{{- include "x.nested" . }}\n'
        )

        got = orphans(d)
        want_orphans = ["x.inert.orphan ", "x.orphan "]
        if len(got) != 2 or not all(g.startswith(w) for g, w in zip(got, want_orphans)):
            print(f"SELFTEST FAIL: want x.inert.orphan and x.orphan flagged, got {got}")
            return 1

        got = inert_called(d)
        if len(got) != 1 or not got[0].startswith("x.inert "):
            print(
                "SELFTEST FAIL: want exactly x.inert flagged as invoked-but-inert "
                f"(x.nested nests two blocks and must not be), got {got}"
            )
            return 1

    live = orphans(ROOT)
    if live:
        print("SELFTEST FAIL: the tree has an orphan named template:\n  " + "\n  ".join(live))
        return 1
    live = inert_called(ROOT)
    if live:
        print("SELFTEST FAIL: the tree has an invoked define that renders nothing:\n  " + "\n  ".join(live))
        return 1
    print(
        "OK: an uncalled define fails, an invoked define with a comment-only body fails, "
        "a commented example and a nested-block validator do not, the tree is clean"
    )
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    rc = 0
    got = orphans(ROOT)
    if got:
        print("❌ named templates nothing invokes (delete them, or call them):\n  " + "\n  ".join(got))
        rc = 1
    got = inert_called(ROOT)
    if got:
        print(
            "❌ named templates that ARE invoked and render nothing (ADR-0094: a guard that\n"
            "   cannot fail is worse than no guard — delete the define and its include, or\n"
            "   give it a body that can fail):\n  " + "\n  ".join(got)
        )
        rc = 1
    if rc == 0:
        print("✓ orphan-templates: every named template is invoked, and every invoked template does something")
    return rc


if __name__ == "__main__":
    sys.exit(main())
