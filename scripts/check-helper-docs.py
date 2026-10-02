#!/usr/bin/env python3
"""check-helper-docs.py — a helper's documentation sits beside its define.

THE DEFECT

`helm/gibson-workloads/templates/_helpers.tpl` and
`helm/gibson-operators/templates/_helpers.tpl` each carried a run of comment
blocks with no `define` after them — `Jaeger host`, `Prometheus host`, `Loki
host`, `Vault host`, `LLM secrets name`, and twenty more. They read as a table
of contents for helpers a reader then cannot find, because the defines they
describe moved to `gibson-common` and the headers stayed behind. Nothing
connects a header to the define it describes, so nothing noticed.

Measured twice: `gibson.loki.host` was deleted in charts#302 and
`gibson.grafana.host` in charts#301, and both headers outlived their helper.
Measured a third time inside `gibson-common` itself, where the doc for
`gibson.netpolEgressDNS` sat above the doc for `gibson.netpolEgressAPIServer`,
which sat above the define for `gibson.netpolEgressAPIServer` — the first doc
documented a helper defined fifteen lines further down.

`check-orphan-templates.py` cannot see any of this. It answers "is every define
invoked" and "does every invoked define render something". A comment is neither.

THE RULE, in two clauses

  A. A comment block whose subject is a helper — the first `gibson.<name>`
     token in the block — must be immediately followed by `define
     "gibson.<name>"`. A doc for a helper defined somewhere else is a copy that
     drifts, and a doc for a helper defined nowhere is a header with no body.

  B. A comment block must be followed by a `define`, or by one comment block
     that is itself followed by a `define`. That one allowed block is the
     section banner, or a file header above the first helper's doc. A longer
     run is a table of contents.

Neither clause counts lines or pins a position. A block is judged by what
follows it and by the name it names.

WHAT IS OUT OF SCOPE

A comment inside a define body explains that body and is never judged: the
parser tracks nesting, so only depth-0 blocks are headers.

  check-helper-docs.py             exit 1 on a stranded or misplaced doc
  check-helper-docs.py --selftest  prove each clause fails on a fixture
"""
import os
import pathlib
import re
import sys

ROOT = pathlib.Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMMENT = re.compile(r'\{\{-?\s*/\*.*?\*/\s*-?\}\}', re.S)
ACTION = re.compile(r'\{\{-?(.*?)-?\}\}', re.S)
OPENER = re.compile(r'^\s*(if|range|with|define|block)\b')
CLOSER = re.compile(r'^\s*end\s*$')
DEFINE = re.compile(r'^\s*define\s+"([^"]+)"')
# A block's subject is a helper only when a prose line BEGINS with its name,
# which is this repository's convention: "gibson.spread — emit pod ...". A
# helper merely mentioned mid-sentence, or in a bulleted list inside a file
# header, is a cross-reference and not the block's subject.
SUBJECT = re.compile(r'^\s*(gibson\.[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*)\b', re.M)


def parse(src: str) -> list[dict]:
    """Top-level comment and define items, in file order.

    A comment is masked out before actions are scanned, so `define` written
    inside prose is prose. Nesting is counted over the define's own line too:
    a one-line define closes on it, and counting from the next line would run
    its extent to somebody else's `end`.
    """
    line_of = []
    n = 0
    for ch in src:
        line_of.append(n)
        if ch == "\n":
            n += 1
    line_of.append(n)

    spans = [(m.start(), m.end()) for m in COMMENT.finditer(src)]
    masked = list(src)
    for a, b in spans:
        for i in range(a, b):
            if masked[i] != "\n":
                masked[i] = " "
    masked = "".join(masked)

    events = [("comment", line_of[a], line_of[b - 1], src[a:b]) for a, b in spans]
    for m in ACTION.finditer(masked):
        if m.group(1).strip():
            events.append(("action", line_of[m.start()], line_of[m.end() - 1], m.group(1)))
    events.sort(key=lambda e: (e[1], e[2]))

    out, depth, pending = [], 0, None
    for kind, start, end, text in events:
        if kind == "comment":
            if depth == 0:
                out.append({"kind": "comment", "line": start, "text": text})
            continue
        d = DEFINE.match(text)
        if d and depth == 0:
            pending = {"kind": "define", "name": d.group(1), "line": start}
            depth = 1
            continue
        if OPENER.match(text):
            depth += 1
        elif CLOSER.match(text):
            depth -= 1
            if depth == 0 and pending:
                out.append(pending)
                pending = None
    if pending:
        out.append(pending)
    out.sort(key=lambda it: it["line"])
    return out


def prose(text: str) -> list[str]:
    t = re.sub(r'^\s*\{\{-?\s*/\*', '', text)
    t = re.sub(r'\*/\s*-?\}\}\s*$', '', t)
    return [l for l in (x.rstrip() for x in t.split("\n")) if l.strip()]


def subject(text: str) -> str | None:
    """The helper a block is about: the first one a prose line begins with."""
    for line in prose(text):
        m = SUBJECT.match(line)
        if m:
            return m.group(1)
    return None


def violations(items: list[dict], defines: set[str], label: str) -> list[str]:
    bad = []
    for n, it in enumerate(items):
        if it["kind"] != "comment":
            continue
        after = items[n + 1:]
        nxt = after[0] if after else None
        second = after[1] if len(after) > 1 else None
        at = f"{label}:{it['line'] + 1}"
        first = prose(it["text"])[0].strip()[:56] if prose(it["text"]) else ""

        s = subject(it["text"])
        if s is not None:
            if not (nxt and nxt["kind"] == "define" and nxt["name"] == s):
                if s not in defines:
                    bad.append(f"{at}: documents {s}, which no define provides — {first!r}")
                else:
                    bad.append(f"{at}: documents {s}, whose define is elsewhere — {first!r}")
                continue
        if nxt is None:
            bad.append(f"{at}: last item in the file, and no define follows it — {first!r}")
        elif nxt["kind"] != "define" and not (second and second["kind"] == "define"):
            bad.append(f"{at}: neither it nor the one block after it is followed by a define — {first!r}")
    return bad


def tpls() -> list[pathlib.Path]:
    return sorted(ROOT.glob("helm/*/templates/**/*.tpl"))


def check() -> list[str]:
    parsed = {p: parse(p.read_text()) for p in tpls()}
    defines = {it["name"] for items in parsed.values() for it in items if it["kind"] == "define"}
    bad = []
    for p, items in parsed.items():
        bad += violations(items, defines, str(p.relative_to(ROOT)))
    return bad


CLEAN = """{{/*
gibson.one — a helper whose doc sits where it belongs.
*/}}
{{- define "gibson.one" -}}one{{- end -}}

{{/* ===== Section ===== */}}

{{/*
gibson.two — a banner above a doc above its define is the allowed shape.
*/}}
{{- define "gibson.two" -}}
{{- if .x }}
{{/* a comment inside a body explains the body and is not a header */}}
{{- end }}
two
{{- end -}}
"""

CLAUSE_A = """{{/*
gibson.one — this doc names a helper the next define is not.
*/}}
{{- define "gibson.other" -}}x{{- end -}}
"""

CLAUSE_A_DEAD = """{{/*
gibson.gone — this doc names a helper nothing defines.
*/}}
{{- define "gibson.one" -}}x{{- end -}}
"""

# Three headers above one define. The first two are reported: the third is in
# the one allowed slot, the banner slot, and clause B allows it on purpose.
CLAUSE_B = """{{/* Jaeger host */}}

{{/* Prometheus host */}}

{{/* Loki host */}}

{{/* Vault host */}}

{{- define "gibson.one" -}}x{{- end -}}
"""


def selftest() -> int:
    cases = [
        ("a clean file", CLEAN, 0),
        ("a doc whose define is elsewhere", CLAUSE_A, 1),
        ("a doc for a helper nothing defines", CLAUSE_A_DEAD, 1),
        ("a run of four headers above one define", CLAUSE_B, 2),
    ]
    for name, src, want in cases:
        items = parse(src)
        defines = {it["name"] for it in items if it["kind"] == "define"}
        got = violations(items, defines, "fixture")
        if len(got) != want:
            print(f"SELFTEST FAIL: {name} must yield {want} violation(s), got {len(got)}: {got}")
            return 1
    # the parser must agree with the files it judges: every define closed.
    for p in tpls():
        items = parse(p.read_text())
        raw = len(re.findall(r'^\{\{-?\s*define\s+"', p.read_text(), re.M))
        got = sum(1 for it in items if it["kind"] == "define")
        if got != raw:
            print(f"SELFTEST FAIL: {p} has {raw} top-level defines, the parser found {got}")
            return 1
    live = check()
    if live:
        print("SELFTEST FAIL: the tree is not clean:\n  " + "\n  ".join(live))
        return 1
    print(f"OK: a misplaced doc, a doc for a missing helper and a run of headers all fail; "
          f"a banner above a doc and a comment inside a body pass; {len(tpls())} .tpl file(s) clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = check()
    if bad:
        print(f"❌ {len(bad)} helper doc(s) are not beside their define:\n  " + "\n  ".join(bad))
        return 1
    print(f"✓ helper-docs: every doc comment in {len(tpls())} .tpl file(s) sits beside the define it describes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
