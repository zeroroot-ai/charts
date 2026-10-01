#!/usr/bin/env python3
"""check-values-consumed.py — no values key that no template reads.

The consumer-side half of the values contract (ADR-0094, charts#292). Every
existing guard here proves that a template's `.Values` reference is satisfied by
a declared default. Nothing proved the reverse, so `gibson-workloads/values.yaml`
carried 331 lines of Bitnami PostgreSQL defaults for four subchart aliases that
no `Chart.yaml` declares, including `tenant-postgresql.enabled: true`. Helm
discards values addressed to an absent subchart in silence, so the flag read as
"the in-chart tenant Postgres is on" and did nothing.

How a key is resolved to a consumer:

  * Collect every value reference in scope as a dotted CHAIN: `.Values.a.b.c`,
    `index .Values "dashed" "key"`, and `$.Values.a.b`. A parenthesised chain
    like `((.Values.a).b).c` yields `a`, which is deliberate: a short chain
    consumes everything beneath it, so under-reading a chain can only make the
    gate more permissive, never wrong.
  * A declared key is consumed when any reference chain is a PREFIX of it, or
    equal to it. That is what makes `{{ toYaml .Values.gibson.resources }}`
    consume `gibson.resources.limits.cpu` without naming it.
  * Scope for chart C is C's own templates plus `gibson-common`, the library
    chart whose helpers run with C's context, so a helper reading `.Values.foo`
    consumes C's `foo`.
  * A key whose first segment names a declared dependency is re-rooted into
    that dependency when the chart is in this tree (`gibson-operators`,
    `gibson-workloads`, `gibson-common`, `setec`), and skipped when it is not,
    because an upstream chart consumes its own values and we cannot see them.

The gate is deliberately conservative: a key is reported only when no chain is a
prefix of it AND its leaf segment appears in no template anywhere. False
failures block merges, so recall is traded for precision. The broader sweep that
produced charts#292 is the audit; this is the gate.

Exemptions live in `.values-consumed-exemptions.txt`, keyed `<chart>:<key.path>`
with a reason, never by line number. A stale exemption fails the build, so the
list cannot rot into permanent cover (ADR-0094 rule 4).

  check-values-consumed.py             exit 1 on an unconsumed key
  check-values-consumed.py --selftest  prove the failure modes fail and the tree passes
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXEMPTIONS = os.path.join(ROOT, "scripts", ".values-consumed-exemptions.txt")

LIB_CHART = "gibson-common"
SEG = r"[A-Za-z_][A-Za-z0-9_-]*"
CHAIN = re.compile(r"\.Values((?:\.%s)+)" % SEG)
INDEXED = re.compile(r'index\s+\$?\.Values\s+((?:"[^"]+"\s*)+)')
# Free-form maps: an operator may put anything in them, so an unused default is
# not a defect. Keys under a dependency's own annotations behave the same way.
FREEFORM = re.compile(r"(^|\.)(annotations|labels|podAnnotations|podLabels|"
                      r"commonAnnotations|commonLabels|nodeSelector|extraEnv|"
                      r"extraArgs|extraVolumes|extraVolumeMounts|tolerations)(\.|$)")


def flatten(node, prefix: str = "") -> dict[str, object]:
    out: dict[str, object] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            out[p] = v
            out.update(flatten(v, p))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.update(flatten(v, f"{prefix}[{i}]"))
    return out


def charts() -> dict[str, dict]:
    found: dict[str, dict] = {}
    base = os.path.join(ROOT, "helm")
    for name in sorted(os.listdir(base)):
        d = os.path.join(base, name)
        meta_path = os.path.join(d, "Chart.yaml")
        if not os.path.isdir(d) or not os.path.exists(meta_path):
            continue
        meta = yaml.safe_load(open(meta_path, encoding="utf-8", errors="replace")) or {}
        deps = {}
        conds = set()
        for dep in meta.get("dependencies") or []:
            key = dep.get("alias") or dep.get("name")
            if key:
                deps[key] = dep.get("name")
            # A `condition:` is a real consumer that lives in Chart.yaml, not in
            # a template. Without this the gate reports `externalDns.enabled`
            # and the other three operator seams as unread, which would be a
            # false failure on a key Helm itself evaluates.
            if dep.get("condition"):
                for c in str(dep["condition"]).split(","):
                    c = c.strip()
                    if c:
                        conds.add(c)
        found[name] = {"dir": d, "deps": deps, "conds": conds}
    return found


def template_text(chart_dir: str) -> str:
    buf = []
    for dirpath, dirs, files in os.walk(chart_dir):
        rel = os.path.relpath(dirpath, chart_dir)
        parts = [] if rel == "." else rel.split(os.sep)
        # Prune the packaged subcharts and any dotted directory. The chart root
        # itself has relpath "." and must NOT be pruned, which is why `parts` is
        # empty rather than ["."] for it.
        if parts and (parts[0] == "charts" or parts[0].startswith(".") or "testdata" in parts):
            dirs[:] = []
            continue
        if "templates" not in parts and not any(f.endswith(".tpl") for f in files):
            continue
        for f in files:
            if f.endswith((".yaml", ".yml", ".tpl", ".txt")):
                buf.append(open(os.path.join(dirpath, f), encoding="utf-8", errors="replace").read())
    return "\n".join(buf)


def chains(text: str) -> set[str]:
    out = set()
    for m in CHAIN.finditer(text):
        out.add(m.group(1).lstrip("."))
    for m in INDEXED.finditer(text):
        parts = re.findall(r'"([^"]+)"', m.group(1))
        if parts:
            out.add(".".join(parts))
    return out


# The umbrella reads INTO a subchart's values, because Helm gives a parent the
# whole tree. Three idioms, all in helm/gibson/templates:
#
#   {{- $wl := index .Values "gibson-workloads" -}} ... {{ $wl.firstAdmin }}
#   {{ ((index .Values "gibson-workloads").gibson).appUrl }}
#   {{- with (index .Values "gibson-workloads") -}} ... {{ .foo }}
#
# Without these, a key only the umbrella reads looks unread, which is not a
# cosmetic miss: deleting `firstAdmin` and `bootstrap` on that false reading
# rendered the first-admin Job with `image: ":"`. The golden diff caught it.
# `with` rebinds the dot and cannot be tracked, so it marks the whole subchart
# consumed via the ALL sentinel.
ALL = ""
ALIAS = re.compile(r'\$(%s)\s*:=\s*(?:\()?\s*index\s+\$?\.Values\s+"([^"]+)"' % SEG)
DIRECT = re.compile(r'\(?\s*index\s+\$?\.Values\s+"([^"]+)"\s*\)?((?:\.%s)+)' % SEG)
WITH_SUB = re.compile(r'with\s+\(+\s*index\s+\$?\.Values\s+"([^"]+)"')
INDEX_PATH = re.compile(r'index\s+\$?\.Values\s+"([^"]+)"((?:\s+"[^"]+")+)')


def cross_chart_refs(text: str, known: set[str]) -> dict[str, set[str]]:
    """chart -> chains that some OTHER chart's template reads out of its values."""
    out: dict[str, set[str]] = {}
    for m in ALIAS.finditer(text):
        var, chart = m.group(1), m.group(2)
        if chart not in known:
            continue
        for dm in re.finditer(r"\$%s((?:\.%s)+)" % (re.escape(var), SEG), text):
            out.setdefault(chart, set()).add(dm.group(1).lstrip("."))
    for m in DIRECT.finditer(text):
        if m.group(1) in known:
            out.setdefault(m.group(1), set()).add(m.group(2).lstrip("."))
    for m in INDEX_PATH.finditer(text):
        if m.group(1) in known:
            parts = re.findall(r'"([^"]+)"', m.group(2))
            if parts:
                out.setdefault(m.group(1), set()).add(".".join(parts))
    for m in WITH_SUB.finditer(text):
        if m.group(1) in known:
            out.setdefault(m.group(1), set()).add(ALL)
    return out


def consumed(key: str, refs: set[str]) -> bool:
    """A reference chain that is a prefix of key (or equal) consumes it."""
    if ALL in refs:
        return True
    for r in refs:
        if not r:
            continue
        if key == r or key.startswith(r + ".") or key.startswith(r + "["):
            return True
    return False


def load_exemptions() -> dict[str, str]:
    out: dict[str, str] = {}
    if not os.path.exists(EXEMPTIONS):
        return out
    for line in open(EXEMPTIONS, encoding="utf-8"):
        line = line.split("#")[0].strip()
        if not line:
            continue
        spec, _, reason = line.partition(" ")
        out[spec.strip()] = reason.strip()
    return out


def scan(root: str) -> tuple[list[str], list[str]]:
    """(unconsumed, stale_exemptions)"""
    global ROOT, EXEMPTIONS
    prev_root, prev_ex = ROOT, EXEMPTIONS
    ROOT = root
    EXEMPTIONS = os.path.join(root, "scripts", ".values-consumed-exemptions.txt")
    try:
        cs = charts()
        lib = template_text(cs[LIB_CHART]["dir"]) if LIB_CHART in cs else ""
        lib_refs = chains(lib)
        text_by_chart = {n: template_text(c["dir"]) for n, c in cs.items()}
        refs_by_chart = {
            n: chains(t) | lib_refs | cs[n]["conds"] for n, t in text_by_chart.items()
        }
        # Fold in what OTHER charts read out of each chart's values.
        for owner, t in text_by_chart.items():
            for chart, ch in cross_chart_refs(t, set(cs)).items():
                if chart != owner:
                    refs_by_chart.setdefault(chart, set()).update(ch)
        all_text = "\n".join(text_by_chart.values())
        all_leaves = set(re.findall(SEG, all_text))

        exempt = load_exemptions()
        seen_exempt: set[str] = set()
        bad: list[str] = []
        # Tops already reported whole, so their leaves are not reported again.
        dead_tops: set[tuple[str, str]] = set()

        # Assertion 2: a TOP-LEVEL key must be addressed to something. It is
        # either a declared dependency alias, an in-tree chart, or referenced by
        # a template in scope. Nothing else can ever read it, because Helm hands
        # a subchart only the block named after it and discards a block named
        # after a subchart that is not there — in silence. That silence is why
        # `tenant-postgresql.enabled: true` read as a live toggle for four
        # aliases no Chart.yaml declares. This assertion is structural and does
        # not depend on the leaf heuristic below.
        for name, c in cs.items():
            vf = os.path.join(c["dir"], "values.yaml")
            if not os.path.exists(vf):
                continue
            vals = yaml.safe_load(open(vf, encoding="utf-8", errors="replace")) or {}
            if not isinstance(vals, dict):
                continue
            refs = refs_by_chart.get(name, set())
            tops = {r.split(".")[0] for r in refs}
            for top in sorted(vals):
                if top in c["deps"] or top in cs or top == "global":
                    continue
                if top in tops:
                    continue
                if FREEFORM.search(top):
                    continue
                spec = f"{name}:{top}"
                if spec in exempt:
                    seen_exempt.add(spec)
                    continue
                bad.append(
                    f"{spec} (whole top-level block: names no dependency in "
                    f"{name}/Chart.yaml and no template reads it)"
                )
                dead_tops.add((name, top))

        for name, c in cs.items():
            vf = os.path.join(c["dir"], "values.yaml")
            if not os.path.exists(vf):
                continue
            vals = yaml.safe_load(open(vf, encoding="utf-8", errors="replace")) or {}
            for key, v in sorted(flatten(vals).items()):
                if isinstance(v, (dict, list)):
                    continue
                if FREEFORM.search(key):
                    continue
                top = key.split(".")[0].split("[")[0]
                if (name, top) in dead_tops:
                    continue
                target, lookup_key = name, key
                if top in c["deps"]:
                    if top in cs:                      # a first-party subchart in this tree
                        target = top
                        lookup_key = key[len(top) + 1 :]
                        if not lookup_key:
                            continue
                    else:                              # upstream chart: it reads its own values
                        continue
                leaf = key.split(".")[-1].split("[")[0]
                if not re.fullmatch(SEG, leaf):
                    continue
                if consumed(lookup_key, refs_by_chart.get(target, set())):
                    continue
                if leaf not in all_leaves:             # belt: nothing names it at all
                    pass
                else:
                    # the leaf is mentioned somewhere; too close to call for a gate
                    continue
                spec = f"{name}:{key}"
                if spec in exempt:
                    seen_exempt.add(spec)
                    continue
                bad.append(f"{spec} = {v!r}")

        stale = [f"{s}  ({exempt[s] or 'no reason given'})" for s in exempt if s not in seen_exempt]
        return bad, sorted(stale)
    finally:
        ROOT, EXEMPTIONS = prev_root, prev_ex


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        def chart(name, meta, values, tmpl):
            t = os.path.join(d, "helm", name, "templates")
            os.makedirs(t, exist_ok=True)
            open(os.path.join(d, "helm", name, "Chart.yaml"), "w").write(yaml.safe_dump(meta))
            open(os.path.join(d, "helm", name, "values.yaml"), "w").write(yaml.safe_dump(values))
            open(os.path.join(t, "a.yaml"), "w").write(tmpl)

        chart("gibson-common", {"name": "gibson-common"}, {},
              '{{- define "c.h" -}}{{ .Values.viaLibrary }}{{- end -}}')
        chart(
            "x",
            {
                "name": "x",
                "dependencies": [
                    {"name": "upstream"},
                    {"name": "gibson-common"},
                    {"name": "seam", "condition": "gatedSeam.enabled"},
                ],
            },
            {
                "read": "r",                                  # read directly
                "deep": {"nested": {"leaf": "v"}},            # consumed by a parent chain
                "viaLibrary": "l",                            # read by the library chart
                "dashed-key": {"inner": "d"},                 # read via index
                "dead": "x",                                  # top-level, nothing reads it
                "live": {"used": "u", "deadChild": "unreadLeafXyz"},  # parent read, child not
                "upstream": {"whatever": 1},                   # dependency passthrough
                "annotations": {"unused.example.com/x": "1"},  # free-form
                "gatedSeam": {"enabled": True},               # read by a Chart.yaml condition
            },
            'a: {{ .Values.read }}\n'
            'b: {{ toYaml .Values.deep }}\n'
            'c: {{ include "c.h" . }}\n'
            'd: {{ index .Values "dashed-key" "inner" }}\n'
            'e: {{ .Values.live.used }}\n',
        )
        bad, stale = scan(d)
        want = [
            "x:dead (whole top-level block: names no dependency in x/Chart.yaml "
            "and no template reads it)",
            "x:live.deadChild = 'unreadLeafXyz'",
        ]
        if sorted(bad) != sorted(want):
            print(f"SELFTEST FAIL:\n  want {sorted(want)}\n  got  {sorted(bad)}")
            return 1
        if stale:
            print(f"SELFTEST FAIL: no exemptions declared, got stale {stale}")
            return 1

        os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
        open(os.path.join(d, "scripts", ".values-consumed-exemptions.txt"), "w").write(
            "x:dead set by an operator at install time\n"
            "x:live.deadChild set by an operator at install time\n"
            "x:gone.away this target no longer exists\n"
        )
        bad, stale = scan(d)
        if bad:
            print(f"SELFTEST FAIL: the exemption should silence x:dead, got {bad}")
            return 1
        if len(stale) != 1 or not stale[0].startswith("x:gone.away"):
            print(f"SELFTEST FAIL: want x:gone.away reported stale, got {stale}")
            return 1

    bad, stale = scan(ROOT)
    if bad:
        print("SELFTEST FAIL: the tree has an unconsumed values key:\n  " + "\n  ".join(bad))
        return 1
    if stale:
        print("SELFTEST FAIL: the tree has a stale exemption:\n  " + "\n  ".join(stale))
        return 1
    print(
        "OK: an unread top-level block and an unread child both fail, a parent chain and a "
        "library read and an index read and "
        "a dependency passthrough and a free-form map and a Chart.yaml condition do not, "
        "an exemption silences one, "
        "a stale exemption fails, the tree is clean"
    )
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, stale = scan(ROOT)
    rc = 0
    if bad:
        print(
            "❌ values keys no template reads (ADR-0094: a declared key with no consumer is\n"
            "   a contract nothing honours — delete it, read it, or exempt it with a reason\n"
            "   in scripts/.values-consumed-exemptions.txt):\n  " + "\n  ".join(bad)
        )
        rc = 1
    if stale:
        print(
            "❌ exemptions whose target no longer exists (delete the line so the list cannot\n"
            "   rot into permanent cover):\n  " + "\n  ".join(stale)
        )
        rc = 1
    if rc == 0:
        print("✓ values-consumed: every declared values key has a consumer")
    return rc


if __name__ == "__main__":
    sys.exit(main())
