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
pipefail. In such a text, a line that pipes into grep with -q, --quiet or
--silent is a finding. A finding names the file, the line or the object,
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
PIPE_GREP_Q = re.compile(r"\|\s*grep\b[^|;&]*?\s(-[a-zA-Z]*q[a-zA-Z]*|--quiet|--silent)\b")
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


def scan_scripts(root):
    out, n = [], 0
    for d, dirs, files in os.walk(os.path.join(root, "scripts")):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            p = os.path.join(d, name)
            if not is_shell(p):
                continue
            n += 1
            with open(p, encoding="utf-8", errors="replace") as f:
                out += [f"{os.path.relpath(p, root)}:{ln}: {lab}" for ln, lab in judge(f.read())]
    return out, n


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
    if bad:
        print(f"check-no-pipe-grep-q selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-no-pipe-grep-q selftest: {len(CASES) + 1} cases passed")
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
