#!/usr/bin/env python3
"""check-xtrace-secrets.py: a shell script under scripts/ that handles a
sensitive value turns xtrace off before it handles one, and never turns it on
again.

WHY. An xtrace (bash -x, set -x, or xtrace in an exported SHELLOPTS) prints
each expanded command to stderr. `PAT="$(kubectl get secret ... | base64 -d)"`
then prints the PAT, and every later command that expands it prints it again.
An operator who debugs a script with `bash -x` copies the value into a
terminal, its scrollback or a job log.

HOW. The scan finds the scripts by their content, not by a list. It reads
every file under scripts/ (any depth) with a sh or bash shebang, or a name
that ends in .sh. It joins each line that ends in `\\` with the next one and
drops comments. A script HANDLES A SENSITIVE VALUE when its text matches:
  (a) a sensitive source ANYWHERE: `get secret`, base64 decode, an OpenBao or
      Vault read, random bytes, a keyring member, stdin (`$(cat)`), or a
      credential field (client_secret, access_token, id_token, a jq read of
      .token, ...). Anywhere covers a helper body, read from a file, a
      heredoc, a here-string, mapfile, backticks and a split line; or
  (b) an assignment from a command substitution, or a `read`, into a
      variable whose name has a segment PAT, TOKEN, SECRET, PASSWORD, PASSWD,
      PW, PASS, SEED, KEY or CREDENTIAL(S). The name decides, with no
      exception: TOKEN_ID or KEY_NAME can hold a value too.
Such a script must:
  1. have a top-level `set +x` (or `set +o xtrace`) as its first top-level
     `set` command, before the first line that handles the value; and
  2. turn xtrace on nowhere: no `set` at any indent with a flag group that
     holds x (`set -x`, `set -euxo pipefail`) or `-o xtrace`.
A finding names the path, the line number and a fixed label or the variable
name. It never prints the text of a line, which can hold a literal value.
tests/xtrace-secrets.bats proves that `set +x` works under bash -x.

Usage:
  check-xtrace-secrets.py [--root <dir>]   # default: scripts/
  check-xtrace-secrets.py --selftest
"""
import argparse
import os
import re
import sys
import tempfile

# (label, pattern): each label is a fixed text that a finding may print.
SOURCES = [
    ("a Secret read", r"\bget\s+secrets?\b"),
    ("a base64 decode", r"base64\s+(-d|--decode)\b|@base64d"),
    ("an OpenBao or Vault read", r"\b(bao|vault)\s+(read|kv\s+get)\b"),
    ("random bytes", r"openssl\s+rand\b|/dev/u?random"),
    ("a keyring member", r"\bkeyring_get\b"),
    ("a credential field", r"client_secret|access_token|refresh_token|id_token|X-Vault-Token|\.auth\.client_token"),
    ("a jq read of .token", r"\bjq\b[^|;]*['\"\s]\.token\b"),
    ("stdin", r"\$\(\s*cat\s*[;)]"),
]
SOURCE_RES = [(label, re.compile(p)) for label, p in SOURCES]
NAME_SEGMENT = re.compile(r"^(PAT|TOKEN|SECRET|PASSWORD|PASSWD|PW|PASS|SEED|KEY|CREDENTIALS?)$", re.I)
ASSIGN = re.compile(r"^\s*(?:(?:local|export|readonly|declare(?:\s+-\w+)*)\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
READ = re.compile(r"(?:^|[;&|]\s*|\bIFS=\S*\s+)read\s+(?:-\w+\s+)*([A-Za-z_][A-Za-z0-9_ ]*)")
XOFF = re.compile(r"^set\s+(\+x|\+o\s+xtrace)\s*$")
TOPSET = re.compile(r"^set\s")
# A `set` command anywhere on a line: at the start (any indent), or after ; & | { ( or a
# keyword. Group 1 is its arguments, up to the end of the command.
ANYSET = re.compile(r"(?:^\s*|[;&|{(]\s*|\b(?:then|do|else)\s+)set\s+([^;&|)}]*)")


def flagged_name(name):
    return any(NAME_SEGMENT.match(s) for s in name.split("_") if s)


def is_shell(path):
    if path.endswith(".sh"):
        return True
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"bash" in first or re.search(rb"\bsh\b", first) is not None)


def strip_comment(line):
    """The line without a comment. A # starts a comment at the line start or
    after a blank, outside quotes."""
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
    """[(first physical line number, joined text without comments)]."""
    out, buf, start = [], "", None
    for n, raw in enumerate(text.split("\n"), 1):
        if start is None:
            start = n
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
            continue
        out.append((start, strip_comment(buf + raw)))
        buf, start = "", None
    if buf:
        out.append((start, strip_comment(buf)))
    return out


def handling_lines(lines):
    """[(line number, label)] of each line that handles a sensitive value.
    A label is a fixed text or a variable name, never the line text."""
    out = []
    for n, code in lines:
        hit = next((label for label, rx in SOURCE_RES if rx.search(code)), None)
        if hit:
            out.append((n, hit))
            continue
        m = ASSIGN.match(code)
        if m and ("$(" in m.group(2) or "`" in m.group(2)) and flagged_name(m.group(1)):
            out.append((n, f"the variable {m.group(1)}"))
            continue
        r = READ.search(code)
        if r:
            names = [v for v in r.group(1).split() if flagged_name(v)]
            if names:
                out.append((n, f"the variable {names[0]}"))
    return out


def xtrace_on(args):
    """True when the arguments of a `set` command turn xtrace on."""
    words = args.split()
    for i, w in enumerate(words):
        if re.fullmatch(r"-[A-Za-z]*x[A-Za-z]*", w):
            return True
        if w in ("-o",) and i + 1 < len(words) and words[i + 1] == "xtrace":
            return True
        if w == "-o" and i + 1 < len(words) and words[i + 1].startswith("xtrace"):
            return True
    return False


def check_file(path):
    """Return a list of findings (path, line number and a fixed label only)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = logical_lines(f.read())
    hits = handling_lines(lines)
    if not hits:
        return []
    first, label = hits[0]
    where = f"{path}:{first} handles a sensitive value ({label})"
    out = []
    sets = [(n, c.strip()) for n, c in lines if TOPSET.match(c)]
    xoff = [n for n, c in sets if XOFF.match(c)]
    if not xoff:
        out.append(f"{where}, and the script has no top-level `set +x`")
    elif sets[0][0] != xoff[0]:
        out.append(f"{where}, and its first top-level set command (line {sets[0][0]}) is not `set +x`")
    elif xoff[0] > first:
        out.append(f"{where} before its `set +x` at line {xoff[0]}")
    for n, c in lines:
        for m in ANYSET.finditer(c):
            if xtrace_on(m.group(1)):
                out.append(f"{path}:{n} turns xtrace on again, and the script handles a sensitive value (line {first})")
    return out


def scan(root):
    findings, n = [], 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            p = os.path.join(d, name)
            if not is_shell(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                if handling_lines(logical_lines(f.read())):
                    n += 1
            findings += check_file(p)
    return findings, n


OFF = "#!/usr/bin/env bash\nset +x\nset -euo pipefail\n"
CASES = {
    # name: (content, a finding is wanted)
    "leak-secret-read.sh": ('#!/usr/bin/env bash\nset -euo pipefail\n'
                            'PAT="$(kubectl get secret iam-admin-pat -o jsonpath={.data.pat} | base64 -d)"\n', True),
    "leak-name.sh": ('#!/usr/bin/env bash\nset -eu\nDAEMON_CLIENT_SECRET="$(cat /run/x)"\n', True),
    "leak-name-token-id.sh": ('#!/usr/bin/env bash\nset -eu\nTOKEN_ID="$(curl -s x)"\n', True),
    "leak-name-key-name.sh": ('#!/usr/bin/env bash\nset -eu\nKEY_NAME="$(curl -s x)"\n', True),
    "leak-read.sh": ('#!/bin/sh\nset -e\nIFS= read -r password\n', True),
    "leak-late-off.sh": ('#!/usr/bin/env bash\nTOKEN="$(curl -s x | jq -r .access_token)"\nset +x\n', True),
    "leak-off-not-first.sh": ('#!/usr/bin/env bash\nset -euo pipefail\nset +x\nFGA_KEY="$(kubectl get secret k -o json)"\n', True),
    "leak-stdin.sh": ('#!/usr/bin/env bash\nset -eu\nVALUE="$(cat; printf x)"\n', True),
    "leak-noext": ('#!/usr/bin/env bash\nSEED="$(head -c 32 /dev/urandom | base64)"\n', True),
    # The source rule: a source anywhere, in any form.
    "leak-helper-body.sh": ('#!/usr/bin/env bash\nset -eu\nget_it() {\n  kubectl get secret s -o json\n}\nV="$(get_it)"\n', True),
    "leak-read-file.sh": ('#!/usr/bin/env bash\nset -eu\nkubectl get secret s -o jsonpath={.data.v} > /tmp/v\nread -r v < /tmp/v\n', True),
    "leak-heredoc.sh": ('#!/usr/bin/env bash\nset -eu\nv=$(cat <<EOF\n$(vault read -field=v secret/x)\nEOF\n)\n', True),
    "leak-herestring.sh": ('#!/usr/bin/env bash\nset -eu\nread -r v <<< "$(bao kv get -field=v secret/x)"\n', True),
    "leak-mapfile.sh": ('#!/usr/bin/env bash\nset -eu\nmapfile -t vs < <(kubectl get secret s -o json | jq -r .data[])\n', True),
    "leak-backticks.sh": ('#!/usr/bin/env bash\nset -eu\nv=`kubectl get secret s -o json`\n', True),
    "leak-split-line.sh": ('#!/usr/bin/env bash\nset -eu\nv="$(kubectl get \\\n  secret s -o json)"\n', True),
    "leak-id-token.sh": ('#!/usr/bin/env bash\nset -eu\nt="$(curl -s x | jq -r .id_token)"\n', True),
    "leak-jq-token.sh": ('#!/usr/bin/env bash\nset -eu\nt="$(curl -s x | jq -r .token)"\n', True),
    # Xtrace turned on again after set +x.
    "leak-on-set-x.sh": (OFF + 'PAT="$(kubectl get secret p -o json)"\nset -x\n', True),
    "leak-on-euxo.sh": (OFF + 'PAT="$(kubectl get secret p -o json)"\nset -euxo pipefail\n', True),
    "leak-on-o-xtrace.sh": (OFF + 'PAT="$(kubectl get secret p -o json)"\nset -o xtrace\n', True),
    "leak-on-in-function.sh": (OFF + 'debug() {\n    set -x\n}\nPAT="$(kubectl get secret p -o json)"\n', True),
    "leak-on-after-semicolon.sh": (OFF + 'PAT="$(kubectl get secret p -o json)"; set -x\n', True),
    "ok-off-first.sh": ('#!/usr/bin/env bash\n# reads a PAT\nset +x\nset -euo pipefail\n'
                        'PAT="$(kubectl get secret p -o jsonpath={.data.pat} | base64 -d)"\n', False),
    "ok-o-xtrace.sh": ('#!/usr/bin/env bash\nset +o xtrace\nOPENBAO_SEAL_KEY="$(keyring_get OPENBAO_SEAL_KEY)"\n', False),
    "ok-off-and-set-e-later.sh": (OFF + 'PAT="$(kubectl get secret p -o json)"\nset +e\nset -e\n', False),
    "ok-no-value.sh": ('#!/usr/bin/env bash\nset -euo pipefail\nset -x\nDOMAIN="$(kubectl get configmap d -o jsonpath={.data.domain})"\n'
                       '# PAT="$(kubectl get secret p)" is only a comment\n', False),
    "ok-python.py": ('#!/usr/bin/env python3\nPAT = "$(kubectl get secret x)"\n', False),
}


def selftest():
    bad = 0
    with tempfile.TemporaryDirectory() as root:
        for name, (body, _) in CASES.items():
            with open(os.path.join(root, name), "w") as f:
                f.write(body)
        for name, (body, want) in CASES.items():
            p = os.path.join(root, name)
            got = check_file(p) if is_shell(p) else []
            ok = bool(got) == want
            # A finding never holds the text of a line.
            leaked = [g for g in got if any(len(l.strip()) > 12 and l.strip() in g for l in body.split("\n"))]
            if leaked:
                ok = False
            label = ("FAILING FIXTURE: " if want else "") + name
            print(f"  {'ok  ' if ok else 'FAIL'} {label}")
            if not ok:
                bad += 1
                for g in got:
                    print(f"         {g}")
        findings, n = scan(root)
        want_files = sum(1 for _, w in CASES.values() if w)
        files_with_findings = len({f.split(":", 1)[0] for f in findings})
        if files_with_findings != want_files:
            print(f"  FAIL scan of the fixtures: {files_with_findings} scripts with findings, want {want_files}")
            bad += 1
        else:
            print(f"  ok   scan of the fixtures: {n} scripts handle a value, {files_with_findings} with findings")
    if bad:
        print(f"check-xtrace-secrets selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-xtrace-secrets selftest: {len(CASES) + 1} cases passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=os.path.join(os.path.dirname(os.path.abspath(__file__))))
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    findings, n = scan(a.root)
    if n == 0:
        print(f"FAIL: no script under {a.root} handles a sensitive value, so the scan looked at nothing", file=sys.stderr)
        return 1
    if findings:
        for f in findings:
            print(f"FAIL: {f}", file=sys.stderr)
        print("Put `set +x` at the top of the script, before its first `set` command, and never turn xtrace on.",
              file=sys.stderr)
        return 1
    print(f"xtrace-secrets: {n} scripts under {a.root} handle a sensitive value, and each turns xtrace off first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
