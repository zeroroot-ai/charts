#!/usr/bin/env python3
"""check-hook-jobs-sh.py — a hook Job that runs under sh is POSIX sh.

Rebuilds the shell half of lint-hook-jobs, lost in the 2026-09-04 split
(charts#17, origin deploy#608); the NetworkPolicy half is
check-netpol-coverage.py. A hook Job whose command is `sh -c` (or `sh -ec`,
`/bin/sh`) runs under busybox or dash, where a bashism is a syntax error
at the worst moment: a pre-install hook that dies on `[[` or `${x//}`
wedges the install. A parse alone does not catch a bashism (`[[` is a
word to a parser), so every such script goes through shellcheck in sh
mode, which reports each non-POSIX construct as an SC3xxx finding. A Job
that names bash runs under bash and is not judged here.

  check-hook-jobs-sh.py             exit 1 on a script POSIX sh rejects, 0 when clean
  check-hook-jobs-sh.py --selftest  prove a bashism under sh fails and passes under bash
"""
import os
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def shellcheck() -> str:
    path = shutil.which("shellcheck")
    if not path:
        print("SETUP FAILURE: shellcheck is required (sh mode finds the bashisms a parse cannot)", file=sys.stderr)
        sys.exit(2)
    return path


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def scripts_under_sh(docs: list[dict]) -> list[tuple[str, str]]:
    found = []
    for d in docs:
        if d.get("kind") != "Job" or "helm.sh/hook" not in ((d.get("metadata") or {}).get("annotations") or {}):
            continue
        spec = d["spec"]["template"]["spec"]
        for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
            cmd = c.get("command") or []
            args = c.get("args") or []
            shell = os.path.basename(cmd[0]) if cmd else ""
            if shell != "sh":
                continue
            # `sh -c SCRIPT` or `sh -ec SCRIPT`: the script is the argument after the flags
            tokens = cmd[1:] + args
            script = next((t for t in tokens if not t.startswith("-")), None)
            if script:
                found.append((f"Job/{d['metadata']['name']}/{c['name']}", script))
    return found


def judge(scripts: list[tuple[str, str]]) -> list[str]:
    sc = shellcheck()
    out = []
    for name, script in scripts:
        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
            f.write("#!/bin/sh\n" + script)
            path = f.name
        try:
            r = subprocess.run([sc, "-s", "sh", "-f", "gcc", path], capture_output=True, text=True)
            posix = [l for l in (r.stdout or "").splitlines() if "SC3" in l]
            if posix:
                out.append(f"{name}: not POSIX sh: " + "; ".join(l.split(":", 3)[-1].strip() for l in posix[:3]))
        finally:
            os.unlink(path)
    return out


def selftest() -> int:
    bashism = 'if [[ "$x" == a* ]]; then echo "${x//a/b}"; fi\n'
    got = judge([("Job/fixture/c", bashism)])
    if len(got) != 1:
        print(f"SELFTEST FAIL: a bashism under sh must be rejected, got {got}")
        return 1
    if judge([("Job/fixture/c", 'if [ "$x" = a ]; then echo "$x"; fi\n')]):
        print("SELFTEST FAIL: POSIX sh must pass")
        return 1
    docs = [d for d in yaml.safe_load_all("""
apiVersion: batch/v1
kind: Job
metadata: {name: bashjob, annotations: {helm.sh/hook: post-install}}
spec:
  template:
    spec:
      containers:
        - {name: c, command: [bash, -ec], args: ['[[ 1 == 1 ]]']}
---
apiVersion: batch/v1
kind: Job
metadata: {name: shjob, annotations: {helm.sh/hook: post-install}}
spec:
  template:
    spec:
      containers:
        - {name: c, command: [sh, -ec], args: ['[[ 1 == 1 ]]']}
""") if d]
    picked = scripts_under_sh(docs)
    if [n for n, _ in picked] != ["Job/shjob/c"]:
        print(f"SELFTEST FAIL: only the sh Job is judged, got {picked}")
        return 1
    live = judge(scripts_under_sh(render()))
    if live:
        print("SELFTEST FAIL: a hook Job script is not POSIX sh:\n  " + "\n  ".join(live))
        return 1
    print("OK: a bashism under sh fails, POSIX sh passes, a bash Job is not judged, every rendered sh hook parses")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    scripts = scripts_under_sh(render())
    got = judge(scripts)
    if got:
        print("❌ hook Jobs under sh with non-POSIX scripts:\n  " + "\n  ".join(got))
        return 1
    print(f"✓ hook-jobs-sh: {len(scripts)} hook Job script(s) under sh parse as POSIX sh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
