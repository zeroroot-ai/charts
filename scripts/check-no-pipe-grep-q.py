#!/usr/bin/env python3
"""check-no-pipe-grep-q.py: no shell text that sets pipefail pipes into
grep -q.

WHY. grep -q exits at its first match. When the command that writes into
the pipe still writes, it gets SIGPIPE, and under pipefail the pipeline
returns 141. An `if A | grep -q x` then reads no match, so it fails OPEN:
published-install.yml read a "migration failed" log line as no failure.
A `cmd | grep -q x || die` fails on a good input, which is the
baseline-up-one-path flake of a loaded make check (charts#754). The fix
is a here-string (`grep -q x <<<"$out"`) or `[ -n "$(cmd | grep x)" ]`.

HOW. The scan finds the text by its content:
  * each shell script under scripts/ (a .sh name or a sh/bash shebang);
  * each `run:` step of .github/workflows/*.yml. A step whose shell is
    bash (the step, the job or the workflow default) runs with pipefail
    (GitHub runs `bash -eo pipefail`);
  * each command, args and ConfigMap data string of the golden renders
    (helm/testdata/golden), which holds every shell text of the chart.
A text sets pipefail when its code (comments removed) holds the word
pipefail. A script under scripts/ that a pipefail script sources (a
`source` or `.` line that names it by its file name) runs with pipefail
too, whatever its own text says: scripts/lib/cluster-probe.sh runs inside
verify-selfhosted-seams.sh. In such a text, a line that pipes into grep,
egrep or fgrep with -q, --quiet or --silent is a finding.
The Makefile and the bats tests are out of scope: make runs each recipe
line with sh -c and no pipefail, and bats sets no pipefail. A finding names the file, the line or the object,
and a fixed label.

Usage:
  check-no-pipe-grep-q.py             # scan the repository
  check-no-pipe-grep-q.py --selftest  # every failing fixture fails
"""
import argparse
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPE_GREP_Q = re.compile(r"\|\s*[ef]?grep\b[^|;&]*?\s(-[a-zA-Z]*q[a-zA-Z]*|--quiet|--silent)\b")
LABEL = "pipes into grep -q under pipefail (use grep -q ... <<<\"$x\" or [ -n \"$(... | grep ...)\" ])"


def strip_comment(line):
    q = None
    for i, ch in enumerate(line):
        if q:
            if ch == q:
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
    return line


def logical_lines(text):
    out, buf, start = [], "", None
    for n, raw in enumerate(text.split("\n"), 1):
        if start is None:
            start = n
        if raw.rstrip().endswith("\\"):
            buf += strip_comment(raw.rstrip()[:-1]) + " "
            continue
        out.append((start, strip_comment(buf + raw)))
        buf, start = "", None
    if buf:
        out.append((start, buf))
    return out


def judge(text, pipefail=False):
    """[(line number, label)] of one shell text."""
    lines = logical_lines(text)
    if not pipefail and not any(re.search(r"\bpipefail\b", c) for _, c in lines):
        return []
    return [(n, LABEL) for n, c in lines if PIPE_GREP_Q.search(c)]


def is_shell(path):
    if path.endswith(".sh"):
        return True
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"bash" in first or re.search(rb"\bsh\b", first) is not None)


SOURCE_LINE = re.compile(r"^\s*(?:source|\.)\s+.*?([A-Za-z0-9_.-]+\.sh)\b")


def sets_pipefail(text):
    return any(re.search(r"\bpipefail\b", c) for _, c in logical_lines(text))


def sourced_by_pipefail(texts):
    """The file names that a pipefail script sources, followed through
    sourced files in turn."""
    names, changed = set(), True
    while changed:
        changed = False
        for p, text in texts.items():
            if not (sets_pipefail(text) or os.path.basename(p) in names):
                continue
            for _, c in logical_lines(text):
                m = SOURCE_LINE.match(c)
                if m and m.group(1) not in names:
                    names.add(m.group(1))
                    changed = True
    return names


def scan_texts(texts, rel):
    """Findings of a set of scripts {path: text}."""
    sourced = sourced_by_pipefail(texts)
    out = []
    for p in sorted(texts):
        pf = os.path.basename(p) in sourced
        out += [f"{rel(p)}:{ln}: {lab}" for ln, lab in judge(texts[p], pf)]
    return out


def scan_scripts(root):
    texts = {}
    for d, dirs, files in os.walk(os.path.join(root, "scripts")):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            p = os.path.join(d, name)
            if not is_shell(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                texts[p] = f.read()
    return scan_texts(texts, lambda p: os.path.relpath(p, root)), len(texts)


def bash_shell(*shells):
    """True when the first set shell of step, job, workflow is bash."""
    for s in shells:
        if s:
            return s.strip().split()[0] == "bash"
    return False


def scan_workflow(path, rel):
    with open(path, encoding="utf-8") as f:
        wf = yaml.safe_load(f) or {}
    out, n = [], 0
    wdef = ((wf.get("defaults") or {}).get("run") or {}).get("shell")
    for jname, job in (wf.get("jobs") or {}).items():
        jdef = (((job or {}).get("defaults") or {}).get("run") or {}).get("shell")
        for i, step in enumerate((job or {}).get("steps") or []):
            run = step.get("run")
            if not isinstance(run, str):
                continue
            n += 1
            pf = bash_shell(step.get("shell"), jdef, wdef)
            for ln, lab in judge(run, pf):
                out.append(f"{rel}: job {jname} step {step.get('name') or i} line {ln}: {lab}")
    return out, n


def scan_workflows(root):
    out, n = [], 0
    d = os.path.join(root, ".github", "workflows")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if name.endswith((".yml", ".yaml")):
            o, k = scan_workflow(os.path.join(d, name), os.path.join(".github/workflows", name))
            out += o
            n += k
    return out, n


def strings_of(doc):
    """(where, text) of each command, args and ConfigMap data string."""
    if doc.get("kind") == "ConfigMap":
        for k, v in (doc.get("data") or {}).items():
            if isinstance(v, str):
                yield f"data {k}", v

    def walk(node):
        if isinstance(node, dict):
            for key in ("command", "args"):
                for a in node.get(key) or [] if isinstance(node.get(key), list) else []:
                    if isinstance(a, str):
                        yield f"container {node.get('name')}", a
            for v in node.values():
                yield from walk(v)
        elif isinstance(node, list):
            for v in node:
                yield from walk(v)
    yield from walk(doc.get("spec") or {})


def scan_golden(root):
    out, n = [], 0
    d = os.path.join(root, "helm", "testdata", "golden")
    for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not name.endswith(".yaml"):
            continue
        with open(os.path.join(d, name), encoding="utf-8") as f:
            for doc in yaml.safe_load_all(f):
                if not isinstance(doc, dict):
                    continue
                for where, text in strings_of(doc):
                    n += 1
                    for ln, lab in judge(text):
                        what = f"{doc.get('kind')}/{(doc.get('metadata') or {}).get('name')} {where}"
                        out.append(f"helm/testdata/golden/{name}: {what} line {ln}: {lab}")
    return out, n


CASES = {
    # name: (text, pipefail from outside, a finding is wanted)
    "printf-grep-q": ('set -euo pipefail\nif printf "%s" "$log" | grep -q "migration failed"; then exit 1; fi\n', False, True),
    "kubectl-grep-q": ('set -o pipefail\nkubectl get nodes -o json | grep -Eq k3s && echo k3s\n', False, True),
    "grep-quiet": ('set -eo pipefail\ncurl -s x | grep --quiet ok || exit 1\n', False, True),
    "split-line": ('set -euo pipefail\nif printf "%s" "$x" \\\n  | grep -q y; then :; fi\n', False, True),
    "workflow-bash-shell": ('if printf "%s" "$log" | grep -q bad; then exit 1; fi\n', True, True),
    "ok-here-string": ('set -euo pipefail\nif grep -q "migration failed" <<<"$log"; then exit 1; fi\n', False, False),
    "ok-command-substitution": ('set -euo pipefail\n[ -n "$(kubectl get x | grep y)" ]\n', False, False),
    "ok-no-pipefail": ('set -eu\nif printf "%s" "$x" | grep -q y; then :; fi\n', False, False),
    "ok-comment": ('set -euo pipefail\n# printf ... | grep -q is what this replaced\n', False, False),
    "egrep-q": ('set -euo pipefail\nif printf "%s" "$x" | egrep -q "a|b"; then :; fi\n', False, True),
    "ok-grep-c": ('set -euo pipefail\nn="$(printf "%s" "$x" | grep -c y || true)"\n', False, False),
}


def selftest():
    bad = 0
    for name, (text, pf, want) in CASES.items():
        got = judge(text, pf)
        ok = bool(got) == want
        print(f"  {'ok  ' if ok else 'FAIL'} {'FAILING FIXTURE: ' if want else ''}{name}")
        bad += 0 if ok else 1
    wf = ("jobs:\n  j:\n    runs-on: x\n    steps:\n      - name: a\n        shell: bash\n"
          "        run: |\n          if printf x | grep -q y; then :; fi\n"
          "      - name: b\n        run: |\n          if printf x | grep -q y; then :; fi\n")
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "w.yml")
        with open(p, "w") as f:
            f.write(wf)
        got, n = scan_workflow(p, "w.yml")
    ok = len(got) == 1 and n == 2 and "step a" in got[0]
    print(f"  {'ok  ' if ok else 'FAIL'} FAILING FIXTURE: a workflow step with shell bash, and none without")
    bad += 0 if ok else 1
    # A lib with no pipefail word, sourced by a pipefail script, and a lib
    # that only a script without pipefail sources.
    texts = {
        "/s/main.sh": '#!/usr/bin/env bash\nset -euo pipefail\nsource "${DIR}/lib/probe.sh"\n',
        "/s/lib/probe.sh": '# shellcheck shell=bash\nis_ip() { if printf "%s" "$1" | grep -qE "^[0-9.]+$"; then return 0; fi; }\n',
        "/s/other.sh": '#!/usr/bin/env bash\nset -eu\n. "$(dirname "$0")/lib/plain.sh"\n',
        "/s/up.sh": '#!/usr/bin/env bash\nset -euo pipefail\n. "$(dirname "$0")/lib/hosts.sh"\n',
        "/s/lib/hosts.sh": 'h() { kubectl get cm | grep -q x; }\n',
        "/s/lib/plain.sh": 'f() { printf x | grep -q x; }\n',
    }
    got = scan_texts(texts, lambda p: p)
    ok = len(got) == 2 and {g.split(":")[0] for g in got} == {"/s/lib/probe.sh", "/s/lib/hosts.sh"}
    print(f"  {'ok  ' if ok else 'FAIL'} FAILING FIXTURE: a sourced lib with no pipefail word, and none in a lib of a script without it")
    bad += 0 if ok else 1
    if bad:
        print(f"check-no-pipe-grep-q selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-no-pipe-grep-q selftest: {len(CASES) + 2} cases passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    s, ns = scan_scripts(a.root)
    w, nw = scan_workflows(a.root)
    g, ng = scan_golden(a.root)
    if min(ns, nw, ng) == 0:
        print(f"FAIL: the scan read {ns} scripts, {nw} workflow steps and {ng} rendered strings: it looked at nothing",
              file=sys.stderr)
        return 1
    findings = s + w + g
    for f in findings:
        print(f"FAIL: {f}", file=sys.stderr)
    if findings:
        return 1
    print(f"no-pipe-grep-q: {ns} scripts, {nw} workflow steps and {ng} rendered strings pipe into no grep -q under pipefail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
