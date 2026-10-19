#!/usr/bin/env python3
"""check-xtrace-secrets.py: a shell script under scripts/ that reads a secret
into a variable turns xtrace off before it reads one.

WHY. An xtrace (bash -x, set -x, or xtrace in an exported SHELLOPTS) prints
each expanded command to stderr. `PAT="$(kubectl get secret ... | base64 -d)"`
then prints the PAT, and every later command that expands it prints it again.
An operator who debugs a script with `bash -x` copies the secret into a
terminal, its scrollback or a job log.

HOW. The scan finds the scripts by their content, not by a list:
  - every file under scripts/ (any depth) with a sh or bash shebang, or a
    name that ends in .sh;
  - a SECRET READ is a line that assigns a variable from a command
    substitution, or reads one with `read`, where
      (a) the source is a secret: `get secret`, base64 decode, an OpenBao or
          Vault read, random bytes, a keyring member, or a credential field
          (client_secret, access_token, ...), or stdin (`$(cat)`); or
      (b) the variable has a secret name: a segment PAT, TOKEN, SECRET,
          PASSWORD, PASSWD, PW, SEED, KEY or CREDENTIAL(S), and it does not
          end in a segment that names a thing (FILE, PATH, NAME, ID, ...)
          and has no segment PUBLIC.
A script with a secret read must have a top-level `set +x` (or
`set +o xtrace`) that is its first top-level `set` command and comes before
its first secret read. tests/xtrace-secrets.bats proves the line works under
bash -x.

Usage:
  check-xtrace-secrets.py [--root <dir>]   # default: scripts/
  check-xtrace-secrets.py --selftest
"""
import argparse
import os
import re
import sys
import tempfile

SOURCE = re.compile(
    r"get\s+secrets?\b|base64\s+(-d|--decode)\b|@base64d|\b(bao|vault)\s+(read|kv\s+get)\b"
    r"|openssl\s+rand\b|/dev/urandom|\bkeyring_get\b|client_secret|access_token|refresh_token"
    r"|X-Vault-Token|\.auth\.client_token"
    # stdin: a value piped in, the way a caller hands a script a secret.
    r"|\$\(\s*cat\s*[;)]")
NAME_SECRET = re.compile(r"^(PAT|TOKEN|SECRET|PASSWORD|PASSWD|PW|PASS|SEED|KEY|CREDENTIALS?)$", re.I)
NAME_THING = re.compile(r"^(FILE|PATH|NAME|NAMES|ID|IDS|DIR|URL|REF|KIND|PREFIX|LEN|COUNT|PROP|"
                        r"PROPERTY|FIELD|ENV|VAR|SHA|FP|FINGERPRINT|SECRET_NAME|NS|MOUNT)$", re.I)
# NAME=$( ... or NAME="$( ... , optionally after local/export/readonly/declare.
ASSIGN = re.compile(r"^\s*(?:(?:local|export|readonly|declare(?:\s+-\w+)*)\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
READ = re.compile(r"(?:^|[;&|]\s*|\bIFS=\S*\s+)read\s+(?:-\w+\s+)*([A-Za-z_][A-Za-z0-9_ ]*)")
XOFF = re.compile(r"^set\s+(\+x|\+o\s+xtrace)\s*(#.*)?$")
TOPSET = re.compile(r"^set\s")


def secret_name(name):
    segs = [s for s in name.split("_") if s]
    if not segs or NAME_THING.match(segs[-1]):
        return False
    # A public key is not a secret.
    if any(s.upper() == "PUBLIC" for s in segs):
        return False
    return any(NAME_SECRET.match(s) for s in segs)


def is_shell(path):
    if path.endswith(".sh"):
        return True
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"bash" in first or re.search(rb"\bsh\b", first) is not None)


def secret_reads(lines):
    """[(line number, text)] of each secret read."""
    out = []
    for i, line in enumerate(lines, 1):
        code = line.split(" #", 1)[0]
        if code.lstrip().startswith("#"):
            continue
        m = ASSIGN.match(code)
        if m and "$(" in m.group(2):
            if SOURCE.search(m.group(2)) or secret_name(m.group(1)):
                out.append((i, line.rstrip()))
                continue
        r = READ.search(code)
        if r and any(secret_name(v) for v in r.group(1).split()):
            out.append((i, line.rstrip()))
    return out


def check_file(path):
    """Return a finding, or None."""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().split("\n")
    reads = secret_reads(lines)
    if not reads:
        return None
    first_read = reads[0][0]
    sets = [(i, l.strip()) for i, l in enumerate(lines, 1) if TOPSET.match(l)]
    xoff = [i for i, l in sets if XOFF.match(l)]
    where = f"{path}:{first_read} reads a secret ({reads[0][1].strip()[:80]})"
    if not xoff:
        return f"{where}, and the script has no top-level `set +x`"
    if sets[0][0] != xoff[0]:
        return f"{where}, and its first top-level set command is `{sets[0][1]}`, not `set +x`"
    if xoff[0] > first_read:
        return f"{where} before its `set +x` at line {xoff[0]}"
    return None


def scan(root):
    findings, n = [], 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            p = os.path.join(d, name)
            if not is_shell(p):
                continue
            with open(p, encoding="utf-8", errors="replace") as f:
                if secret_reads(f.read().split("\n")):
                    n += 1
            f2 = check_file(p)
            if f2:
                findings.append(f2)
    return findings, n


def selftest():
    cases = {
        # name: (content, a finding is wanted)
        "leak-secret-read.sh": ('#!/usr/bin/env bash\nset -euo pipefail\n'
                                'PAT="$(kubectl get secret iam-admin-pat -o jsonpath={.data.pat} | base64 -d)"\n', True),
        "leak-name.sh": ('#!/usr/bin/env bash\nset -eu\nDAEMON_CLIENT_SECRET="$(cat /run/x)"\n', True),
        "leak-read.sh": ('#!/bin/sh\nset -e\nIFS= read -r password\n', True),
        "leak-late-off.sh": ('#!/usr/bin/env bash\nTOKEN="$(curl -s x | jq -r .access_token)"\nset +x\n', True),
        "leak-off-not-first.sh": ('#!/usr/bin/env bash\nset -euo pipefail\nset +x\nFGA_KEY="$(kubectl get secret k -o json)"\n', True),
        "leak-stdin.sh": ('#!/usr/bin/env bash\nset -eu\nVALUE="$(cat; printf x)"\n', True),
        "leak-noext": ('#!/usr/bin/env bash\nSEED="$(head -c 32 /dev/urandom | base64)"\n', True),
        "ok-off-first.sh": ('#!/usr/bin/env bash\n# reads a PAT\nset +x\nset -euo pipefail\n'
                            'PAT="$(kubectl get secret p -o jsonpath={.data.pat} | base64 -d)"\n', False),
        "ok-o-xtrace.sh": ('#!/usr/bin/env bash\nset +o xtrace\nOPENBAO_SEAL_KEY="$(keyring_get OPENBAO_SEAL_KEY)"\n', False),
        "ok-no-secret.sh": ('#!/usr/bin/env bash\nset -euo pipefail\nKEYRING_FILE="$(substrate_get KEYRING_FILE)"\n'
                            'DOMAIN="$(kubectl get configmap d -o jsonpath={.data.domain})"\n'
                            '# PAT="$(kubectl get secret p)" is only a comment\n', False),
        "ok-public-key.sh": ('#!/usr/bin/env bash\nset -eu\nSETEC_DISK_PUBLIC_KEY="$(./derive.sh k)"\n', False),
        "ok-python.py": ('#!/usr/bin/env python3\nPAT = "$(kubectl get secret x)"\n', False),
    }
    bad = 0
    with tempfile.TemporaryDirectory() as root:
        for name, (body, _) in cases.items():
            with open(os.path.join(root, name), "w") as f:
                f.write(body)
        for name, (_, want) in cases.items():
            got = check_file(os.path.join(root, name)) if is_shell(os.path.join(root, name)) else None
            ok = (got is not None) == want
            label = ("FAILING FIXTURE: " if want else "") + name
            print(f"  {'ok  ' if ok else 'FAIL'} {label}{'' if ok else ': ' + str(got)}")
            bad += 0 if ok else 1
        findings, n = scan(root)
        want_n = sum(1 for k, (_, w) in cases.items() if w) + 2  # two ok scripts read a secret
        if n != want_n or len(findings) != want_n - 2:
            print(f"  FAIL scan of the fixtures: {n} scripts with a secret read, {len(findings)} findings")
            bad += 1
        else:
            print(f"  ok   scan of the fixtures: {n} scripts with a secret read, {len(findings)} findings")
    if bad:
        print(f"check-xtrace-secrets selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-xtrace-secrets selftest: {len(cases) + 1} cases passed")
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
        print(f"FAIL: no script under {a.root} reads a secret, so the scan looked at nothing", file=sys.stderr)
        return 1
    if findings:
        for f in findings:
            print(f"FAIL: {f}", file=sys.stderr)
        print("Put `set +x` at the top of the script, before its first `set` command.", file=sys.stderr)
        return 1
    print(f"xtrace-secrets: {n} scripts under {a.root} read a secret, and each turns xtrace off first")
    return 0


if __name__ == "__main__":
    sys.exit(main())
