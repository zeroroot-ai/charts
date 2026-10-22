#!/usr/bin/env python3
"""check-no-secret-argv.py: no template, chart file or script puts a secret
value on the argument list of a process.

WHY. The argv of a process is not a secret store. /proc/<pid>/cmdline shows it
to each process of the pod and to root on the node, `ps` prints it, and the
API server records the argv of a `kubectl exec` in its audit log. A token on
the argv of curl inside a pod, or a password on the argv of kubectl on an
operator host, is a token that a reader of any of these holds.
A value goes on stdin, in a file of a mode-0700 directory, or in the
environment (a shell assignment prefix or an env var from a secretKeyRef).
curl reads a header with `-H @file` and a body with `--data-binary @file` or
`@-`. jq reads a value with --rawfile, from stdin, or as `env.NAME`.

HOW. The scan finds the text by its content, not by a list:
  * shell text: every file under helm/*/templates and helm/*/files, each
    values file helm/*/values*.yaml (a subchart renders some container lists
    of it verbatim), and every shell script under scripts/ (a .sh name or a
    sh/bash shebang). Each line
    that ends in a backslash is joined with the next one, and comments are
    removed. A line is split into words the way a shell splits it, with
    quotes and $(...) kept in one word.
  * rendered pods: every container of every golden render under
    helm/testdata/golden. Kubernetes expands $(NAME) in command and args, so
    a $(NAME) of an env var from a secretKeyRef puts the value on the argv.
A word names a sensitive value when it expands a variable whose name has a
segment TOKEN, TOK, SECRET, PASSWORD, PASSWD, PASS, PW, PAT, SEED, JWT,
CREDENTIAL(S), MASTERKEY, ROOT, MINTER, VALUE, BEARER or AUTH (split on _,
any case). A finding is one of:
  1. a curl header (-H, --header) that expands a variable, does not read a
     file (@), and is a credential header (X-Vault-Token, Authorization,
     Cookie, X-Api-Key, PRIVATE-TOKEN) or expands a sensitive name;
  2. a curl body or form (-d, --data*, --json, -F, --form) that expands a
     sensitive name and does not read a file;
  3. a curl --user or -u USER:$SECRET;
  4. a jq --arg or --argjson whose name or value names a sensitive value;
  5. a kubectl --from-literal with a command substitution on a `create
     secret` line, or that names a sensitive value;
  6. the env program (env NAME=$X cmd) with a sensitive name or value;
  7. a password option: --password, --token, --secret, --client-secret,
     --api-key, `pass:` (openssl), redis-cli -a, cypher-shell -p;
  8. a URL with a password in it (scheme://user:$X@host), or a conninfo
     password=$X, as a word of a command;
  9. a bao or vault command with a NAME=$X word that names a sensitive value,
     or a `login` that expands a variable;
 10. a kubectl patch whose -p or --patch expands a sensitive name;
 11. a rendered container whose command or args hold $(NAME) of an env var
     from a secretKeyRef.
A finding names the path, the line number (or the object and container) and
a fixed label. It never prints the text of a line, which can hold a value.

Usage:
  check-no-secret-argv.py            # scan the repository
  check-no-secret-argv.py --selftest # every failing fixture fails
"""
import argparse
import os
import re
import sys
import tempfile

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SENSITIVE = {
    "TOKEN", "TOK", "SECRET", "SECRETS", "PASSWORD", "PASSWD", "PASS", "PW", "PAT", "SEED", "JWT",
    "CREDENTIAL", "CREDENTIALS", "MASTERKEY", "ROOT", "MINTER", "VALUE", "VAL", "BEARER", "AUTH",
    "PRIV", "PRIVATE",
}
CRED_HEADER = re.compile(r"^\s*(X-Vault-Token|Authorization|Cookie|X-Api-Key|PRIVATE-TOKEN)\s*:", re.I)
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
DATA_OPTS = {"-d", "--data", "--data-binary", "--data-raw", "--data-ascii", "--data-urlencode", "--json",
             "-F", "--form", "--form-string"}
PW_OPTS = {"--password", "--token", "--secret", "--client-secret", "--api-key"}
URL_PW = re.compile(r"://[^/@\s:]*:[^@/\s]*\$[^@\s]*@")
CONNINFO_PW = re.compile(r"\bpassword=\$")
ASSIGN_WORD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


# A KEY segment names a key value when another segment says which kind
# (SEAL_KEY, API_KEY, PRIVATE_KEY, MASTER_KEY). A bare KEY is often the name
# of a key (a KV path, a data key), so it alone is not sensitive: a variable
# that holds key bytes is caught by TAINTED instead.
KEY_KIND = {"SEAL", "UNSEAL", "API", "PRIVATE", "PRIV", "MASTER", "SIGNING", "ENCRYPTION", "ACCESS",
            "HMAC", "AES", "KEK", "DEK", "SSH", "TLS", "CLIENT", "SESSION", "WRAP", "UNWRAP"}
# A variable assigned from a command that reads or makes a secret holds one,
# whatever its name (kh="$(printf '%s' "$2" | hex_of)" after a read of a
# masterkey). scan_text fills TAINTED for each file before it judges a line.
TAINT_SOURCES = re.compile(
    r"\bget\s+secrets?\b|base64\s+(-d|--decode)\b|@base64d|\b(bao|vault)\s+(read|kv\s+get)\b"
    r"|openssl\s+rand\b|/dev/u?random|\bkeyring_get\b|client_secret|access_token|refresh_token|id_token"
    r"|client_token|X-Vault-Token|\bjq\b[^|;]*['\"\s]\.token\b|\bgen_(rand|pw)\b"
    r"|\$\{?[A-Z_]*(MASTERKEY|PASSWORD|TOKEN|SECRET|SEAL_KEY)[A-Z_]*")
TAINTED = set()
# A read of a field that holds no secret (an id, a user name, the metadata of
# an object) taints nothing, even from a Secret.
NOT_SECRET_FIELD = re.compile(r"\.metadata\.|\.data\.\[?\"?(\w*_id|username|user|host|port|url)\b")


def sensitive_name(name, tainted=True):
    """True when the name says it holds a secret, or (tainted) when a secret
    reached the variable of that name."""
    segs = [x.upper() for x in name.split("_") if x]
    if tainted and name in TAINTED:
        return True
    if any(x in SENSITIVE for x in segs):
        return True
    return "KEY" in segs and any(x in KEY_KIND for x in segs)


def sensitive_word(word):
    """True when the word expands a variable with a sensitive name, or one
    assigned from a secret."""
    return any(sensitive_name(v) for v in VAR.findall(word))


ASSIGN_LINE = re.compile(r"^\s*(?:(?:local|export|readonly)\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def taint(lines):
    """The variables of a file that a secret reaches, by assignment."""
    out = set()
    for _ in range(3):
        for _, code in lines:
            m = ASSIGN_LINE.match(code)
            if not m:
                continue
            name, rhs = m.group(1), m.group(2)
            if NOT_SECRET_FIELD.search(rhs):
                continue
            if TAINT_SOURCES.search(rhs) or any(v in out for v in VAR.findall(rhs)):
                out.add(name)
    return out


def strip_comment(line):
    q = None
    depth = 0
    for i, ch in enumerate(line):
        if q:
            if ch == q and (q == "'" or line[i - 1] != "\\"):
                q = None
        elif ch in "'\"":
            q = ch
        elif ch == "(" and i and line[i - 1] == "$":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif ch == "#" and depth == 0 and (i == 0 or line[i - 1] in " \t") and not line[i:].startswith("#{"):
            return line[:i]
    return line


def logical_lines(text):
    out, buf, start = [], "", None
    for n, raw in enumerate(text.split("\n"), 1):
        if start is None:
            start = n
        s = raw.rstrip()
        if s.endswith("\\") and not s.endswith("\\\\"):
            buf += strip_comment(s[:-1]) + " "
            continue
        out.append((start, strip_comment(buf + raw)))
        buf, start = "", None
    if buf:
        out.append((start, buf))
    return out


def words(line):
    """Split a line into shell words. Quotes are removed, $(...) and ${...}
    stay in one word, and ; | & ( ) end a command (as a word of their own)."""
    out, cur, q, depth, i = [], "", None, 0, 0
    has = False
    while i < len(line):
        ch = line[i]
        if q:
            if ch == q:
                q = None
            else:
                cur += ch
        elif depth:
            cur += ch
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
        elif ch in "'\"":
            q = ch
            has = True
        elif ch == "$" and i + 1 < len(line) and line[i + 1] == "(":
            cur += "$("
            depth = 1
            i += 1
            has = True
        elif ch in " \t":
            if cur or has:
                out.append(cur)
            cur, has = "", False
        elif ch in ";|&()":
            if cur or has:
                out.append(cur)
            cur, has = "", False
            out.append(ch)
        else:
            cur += ch
            has = True
        i += 1
    if cur or has:
        out.append(cur)
    return out


def substitutions(code):
    """The text inside each outermost $(...) of a line."""
    out, i = [], 0
    while True:
        i = code.find("$(", i)
        if i < 0:
            return out
        depth, j = 1, i + 2
        while j < len(code) and depth:
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
            j += 1
        out.append(code[i + 2:j - 1])
        i = j


def line_findings(code):
    """[label] for one logical line of shell text, and for each command
    substitution in it."""
    out = _line_findings(code)
    inner = substitutions(code)
    # The script of `sh -c '<script>'` (or bash -c) is shell text too.
    w = words(code)
    for i, word in enumerate(w[:-1]):
        if word in ("-c", "-ec", "-euc") and i and re.search(r"(^|/)(ba)?sh$", w[i - 1]):
            inner += [ln for _, ln in logical_lines(w[i + 1])]
    for text in inner:
        out += [x for x in line_findings(text) if x not in out]
    return out


def _line_findings(code):
    w = words(code)
    if not w:
        return []
    out = []
    low = code
    # curl, or a wrapper of it (kube_curl, bao_curl).
    is_curl = re.search(r"curl\b", low) is not None
    for i, word in enumerate(w):
        nxt = w[i + 1] if i + 1 < len(w) else ""
        # 1. curl header
        hdr = None
        if word in ("-H", "--header"):
            hdr = nxt
        elif word.startswith("--header="):
            hdr = word[len("--header="):]
        elif word.startswith("-H") and len(word) > 2 and is_curl:
            hdr = word[2:]
        if hdr is not None and "$" in hdr and not hdr.startswith("@"):
            if CRED_HEADER.match(hdr) or sensitive_word(hdr):
                out.append("a credential header on the argv of curl (use -H @file)")
        # 2. curl body
        # Any body that expands a variable: the name of a variable does not
        # tell what a built body holds ($body held the login JWT).
        if word in DATA_OPTS and is_curl and "$" in nxt and not nxt.startswith("@") \
                and not re.match(r"^[A-Za-z0-9_]*@", nxt):
            out.append("a secret in a curl body on the argv (use --data-binary @file or @-)")
        for opt in DATA_OPTS:
            if word.startswith(opt + "=") and is_curl and "$" in word:
                out.append("a secret in a curl body on the argv (use --data-binary @file or @-)")
        # 3. curl user
        if is_curl and word in ("-u", "--user") and ":" in nxt and "$" in nxt.split(":", 1)[1]:
            out.append("a password in curl --user on the argv")
        # 4. jq --arg
        if word in ("--arg", "--argjson") and i + 2 < len(w) and re.search(r"\bjq\b", code):
            name, val = w[i + 1], w[i + 2]
            # A positional parameter ($1 .. $9) of a helper function can be
            # anything its caller passed, a token or a KV object included.
            if "$" in val and (sensitive_name(name, False) or sensitive_word(val) or re.search(r"\$\{?[1-9]", val)
                                    or "$(" in val):
                out.append("a secret in a jq --arg on the argv (use --rawfile, stdin or env)")
        # 5. kubectl --from-literal
        if word.startswith("--from-literal="):
            lit = word[len("--from-literal="):]
            # A command substitution in a Secret is a value made here (a
            # random key, a line of a keyring file).
            if "$" in lit and ((re.search(r"create\s+secret", code) and "$(" in lit) or sensitive_word(lit)
                               or sensitive_name(lit.split("=", 1)[0], False)):
                out.append("a secret in kubectl --from-literal on the argv (use --from-file=NAME=/dev/stdin)")
        # 6. env program
        if word == "env" and (i == 0 or w[i - 1] in (";", "|", "&", "(", "--", "exec", "then", "do", "!")):
            for a in w[i + 1:]:
                if not ASSIGN_WORD.match(a):
                    if a.startswith("-"):
                        continue
                    break
                name, val = a.split("=", 1)
                if "$" in val and (sensitive_name(name) or sensitive_word(val)):
                    out.append("a secret in an env NAME=VALUE word on the argv (use a shell assignment prefix or stdin)")
                    break
        # 7. password options
        if word in PW_OPTS and "$" in nxt:
            out.append("a secret after a password option on the argv")
        for opt in PW_OPTS:
            if word.startswith(opt + "=") and "$" in word:
                out.append("a secret after a password option on the argv")
        if re.search(r"(^|[^A-Za-z])pass:\$", word):
            out.append("a secret in an openssl pass: argument on the argv")
        # openssl takes a raw key (-K) or a password (-k, -pass, -passin,
        # -passout) on its argv. Only env:, file: and fd: sources stay off it.
        if word in ("-K", "-k", "-pass", "-passin", "-passout", "-kfile") and "$" in nxt \
                and re.search(r"\bopenssl\b", code) and not re.match(r"^(env|file|fd):", nxt):
            out.append("a key or password in an openssl option on the argv (use -pass env: or file:)")
        if re.search(r"\b(mysql|mariadb|mysqldump)\b", code) and (re.match(r"^-p.*\$", word)
                                                                 or re.match(r"^--password=.*\$", word)):
            out.append("a password in mysql -p on the argv (use MYSQL_PWD or an option file)")
        if re.search(r"\bredis-cli\b", code) and re.match(r"^>\$", word):
            out.append("a password in a redis-cli ACL argument on the argv (use redis-cli -x and stdin)")
        if word == "-a" and "$" in nxt and re.search(r"\bredis-cli\b", code):
            out.append("a password in redis-cli -a on the argv (use REDISCLI_AUTH)")
        if word == "-p" and "$" in nxt and re.search(r"\bcypher-shell\b", code):
            out.append("a password in cypher-shell -p on the argv (use NEO4J_PASSWORD)")
        # 8. URL password, conninfo password
        # A YAML key (value:) or a template action is not a command.
        if (URL_PW.search(word) or CONNINFO_PW.search(word)) and not (w[0].endswith(":") or w[0].startswith("{{")):
            if not (ASSIGN_WORD.match(word) and (i == 0 or w[i - 1] in (";", "|", "&", "(", "export", "local", "readonly"))):
                out.append("a password in a URL or conninfo on the argv")
        # 10. kubectl patch
        if word in ("-p", "--patch") and re.search(r"\bkubectl\b.*\bpatch\b", code) and sensitive_word(nxt):
            out.append("a secret in a kubectl patch on the argv")
    # 9. bao / vault CLI
    m = re.search(r"(?:^|[\s;|&(])(bao|vault)\s+(login|write|kv\s+put|kv\s+patch)\b(.*)", code)
    if m:
        rest = words(m.group(3))
        if m.group(2) == "login" and any("$" in r for r in rest if not r.startswith("-")):
            out.append("a token on the argv of a bao or vault login")
        for r in rest:
            if ASSIGN_WORD.match(r):
                name, val = r.split("=", 1)
                if "$" in val and not val.startswith("@") and (sensitive_name(name) or sensitive_word(val)):
                    out.append("a secret in a bao or vault NAME=VALUE word on the argv (use NAME=@file or -)")
                    break
    seen, res = set(), []
    for label in out:
        if label not in seen:
            seen.add(label)
            res.append(label)
    return res


def scan_text(path, text):
    out = []
    lines = logical_lines(text)
    TAINTED.clear()
    TAINTED.update(taint(lines))
    for n, code in lines:
        for label in line_findings(code):
            out.append(f"{path}:{n}: {label}")
    return out


def is_shell(path):
    if path.endswith(".sh"):
        return True
    try:
        with open(path, "rb") as f:
            first = f.readline(200)
    except OSError:
        return False
    return first.startswith(b"#!") and (b"bash" in first or re.search(rb"\bsh\b", first) is not None)


def text_files(root):
    """The shell text of the repository: chart templates and files, and shell scripts."""
    out = []
    helm = os.path.join(root, "helm")
    for chart in sorted(os.listdir(helm)) if os.path.isdir(helm) else []:
        # The values files of a chart. A subchart takes some lists verbatim
        # (zitadel.zitadel.initContainers, zitadel.setupJob.extraContainers),
        # so the shell of those containers is in a values file and in no
        # template.
        base = os.path.join(helm, chart)
        if os.path.isdir(base):
            for name in sorted(os.listdir(base)):
                if name.startswith("values") and name.endswith((".yaml", ".yml")):
                    out.append(os.path.join(base, name))
        for sub in ("templates", "files"):
            base = os.path.join(helm, chart, sub)
            for d, dirs, files in os.walk(base):
                dirs[:] = sorted(x for x in dirs if not x.startswith("."))
                for name in sorted(files):
                    if name.endswith((".yaml", ".yml", ".tpl", ".sh", ".txt", ".conf")) or is_shell(os.path.join(d, name)):
                        out.append(os.path.join(d, name))
    scripts = os.path.join(root, "scripts")
    for d, dirs, files in os.walk(scripts):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for name in sorted(files):
            p = os.path.join(d, name)
            if is_shell(p):
                out.append(p)
    return out


POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet"}


def pod_specs(doc):
    kind = doc.get("kind")
    spec = doc.get("spec") or {}
    if kind == "Pod":
        return [spec]
    if kind == "CronJob":
        return [((spec.get("jobTemplate") or {}).get("spec") or {}).get("template", {}).get("spec") or {}]
    if kind in POD_KINDS:
        return [(spec.get("template") or {}).get("spec") or {}]
    return []


def scan_render(path):
    out, pods = [], 0
    with open(path, encoding="utf-8") as f:
        docs = list(yaml.safe_load_all(f))
    for doc in docs:
        if not isinstance(doc, dict) or doc.get("kind") not in POD_KINDS:
            continue
        for spec in pod_specs(doc):
            pods += 1
            for c in (spec.get("initContainers") or []) + (spec.get("containers") or []):
                secret_env = {e["name"] for e in c.get("env") or []
                              if isinstance(e, dict) and (e.get("valueFrom") or {}).get("secretKeyRef")}
                argv = [str(a) for a in (c.get("command") or []) + (c.get("args") or [])]
                for a in argv:
                    for name in re.findall(r"(?<!\$)\$\(([A-Za-z_][A-Za-z0-9_]*)\)", a):
                        if name in secret_env:
                            out.append(f"{path}: {doc['kind']}/{doc['metadata']['name']} container {c.get('name')}: "
                                       f"$({name}) of a secretKeyRef env var in command or args")
    return out, pods


def scan(root):
    findings, files = [], 0
    for p in text_files(root):
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        files += 1
        findings += scan_text(os.path.relpath(p, root), text)
    golden = os.path.join(root, "helm", "testdata", "golden")
    pods = 0
    if os.path.isdir(golden):
        for name in sorted(os.listdir(golden)):
            if name.endswith(".yaml"):
                f, n = scan_render(os.path.join(golden, name))
                findings += [x.replace(root + os.sep, "") for x in f]
                pods += n
    return findings, files, pods


# name: (shell text, a finding is wanted)
CASES = {
    "vault-token-header": ('curl -sS -H "X-Vault-Token: $VAULT_TOKEN" "$BAO/v1/x"', True),
    "positional-token-header": ('c=$(curl -sS -X POST -H "X-Vault-Token: $1" \\\n  "$BAO/v1/x")', True),
    "bearer-header": ('curl -H "Authorization: Bearer ${PAT}" https://x', True),
    "header-variable": ('TOKEN_HEADER="X-Vault-Token: ${T}"\ncurl -H "$TOKEN_HEADER" x', True),
    "header-equals": ('curl --header="Authorization: Bearer $tok" x', True),
    "body-jq-arg": ('curl -d "$(jq -nc --arg v "$VALUE" \'{v:$v}\')" x', True),
    "body-urlencode": ('curl --data-urlencode "client_secret=${client_secret}" x', True),
    "curl-user": ('curl -u "admin:$ADMIN_PASSWORD" x', True),
    "jq-arg": ('jq -n --arg p "$PASSWORD" \'{p:$p}\'', True),
    "jq-argjson-positional": ('c=$(jq -nc --argjson d "$3" \'{data: $d}\' | hcurl "$H" --data-binary @- x)', True),
    "jq-arg-name": ('jq -n --arg token "$x" \'{t:$token}\'', True),
    "from-literal-create-secret": ('kubectl create secret generic s --from-literal="k=$(head -c 32 /dev/urandom)"', True),
    "from-literal-name": ('kubectl create configmap c --from-literal=lastKnownPassword="$CURRENT_PASSWORD"', True),
    "env-program": ('kubectl exec -i p -- env "BAO_TOKEN=$BAO_ADMIN_TOKEN" sh -s', True),
    "password-option": ('mc admin user add x --password "$SECRET_KEY"', True),
    "redis-cli-acl": ("sh -c 'IFS= read -r n; REDISCLI_AUTH=\"$p\" exec redis-cli ACL SETUSER default \">$n\"' x", True),
    "ok-redis-cli-x": ("sh -c 'IFS= read -r n; printf \">%s\" \"$n\" | REDISCLI_AUTH=\"$p\" redis-cli -x ACL SETUSER default' x", False),
    "redis-cli-a": ('redis-cli -a "$REDIS_PASSWORD" ping', True),
    "cypher-shell-p": ('cypher-shell -u neo4j -p "${A#*/}" "RETURN 1"', True),
    "ok-env-value-url": ('              value: {{ printf "postgres://$(U):$(DB_PASSWORD)@%s" .h }}', False),
    "url-password": ('psql "postgres://app:${DB_PASSWORD}@db:5432/app" -c "select 1"', True),
    "conninfo-password": ('psql "host=db user=app password=$PGPASS" -c x', True),
    "openssl-K": ('bin_of "$c" | openssl enc -d -aes-256-cfb -K "$kh" -iv "$iv" -nopad', True),
    "openssl-k": ('openssl enc -aes-256-cbc -k "$x" -in f', True),
    "openssl-passin": ('openssl rsa -in k.pem -passin "$P"', True),
    "ok-openssl-pass-env": ('openssl rsa -in k.pem -passin env:KEY_PASS', False),
    "mysql-p": ('mysql -u root -p"$DB_ROOT" -e "select 1"', True),
    "seal-key-name": ('curl -d "x" -H "X-Seal: $UNSEAL_KEY" https://x', True),
    "api-key-name": ('jq -n --arg k "$API_KEY" \'{k:$k}\'', True),
    "tainted-opaque-name": ('kh="$(kubectl get secret s -o jsonpath={.data.k} | base64 -d)"\njq -n --arg k "$kh" \'{k:$k}\'', True),
    "tainted-propagates": ('mk="$ZITADEL_MASTERKEY"\nkh="$(printf %s "$mk" | od -An -tx1)"\nfoo --opt "$kh" | jq -n --arg h "$kh" \'.\'', True),
    "ok-secret-id-field": ('ORG="$(kubectl get secret s -o jsonpath={.data.org_id} | base64 -d)"\ncurl -H "x-org: ${ORG}" x', False),
    "ok-arg-name-tainted": ('t="$(cat /run/token)"\njq -n --arg t "$PERIOD" \'{t:$t}\'', False),
    "ok-key-name": ('jq -r --arg k "$KEYRING_KEY" \'.data[$k]\'', False),
    "openssl-pass": ('openssl enc -aes-256-cbc -pass "pass:$SEED" -in x', True),
    "bao-write": ('bao write auth/x/role/r secret_id="$SECRET_ID"', True),
    "bao-login": ('bao login "$ROOT_TOKEN"', True),
    "kubectl-patch": ('kubectl patch secret s -p "{\\"data\\":{\\"v\\":\\"$VALUE\\"}}"', True),
    "yaml-list-item": ('                - curl -sS -H "X-Vault-Token: ${root}" "$A"', True),
    # Clean shapes.
    "ok-header-file": ('curl -sS -H "@$d/hdr" "$BAO/v1/x"', False),
    "body-plain-name": ('code=$(curl -sS -X POST -d "$body" "$BAO/v1/auth/jwt/login")', True),
    "kube-curl-body": ('kube_curl -X POST --data-binary "$op_secret_body" "$KUBE_API/x"', True),
    "ok-content-type": ('curl -H "$CT" -H "Content-Type: application/json" x', False),
    "ok-body-file": ('curl --data-binary "@$d/body" x', False),
    "ok-body-stdin": ('printf \'%s\' "$body" | curl --data-binary @- x', False),
    "ok-jq-rawfile": ('jq -c --rawfile v "$d/value" \'{v:$v}\' cur.json', False),
    "ok-jq-env": ('V="$VALUE" jq -n \'{v: env.V}\'', False),
    "ok-jq-arg-name": ('jq -r --arg k "$KEY" \'.data[$k]\'', False),
    "ok-assign-prefix": ('REDISCLI_AUTH="$p" exec redis-cli --no-auth-warning PING', False),
    "ok-env-name": ('kubectl exec -i p -- env "SET_KEY=$KEY" sh -c "$S"', False),
    "ok-url-assign": ('export DSN="postgres://app:${DB_PASSWORD}@db/app"', False),
    "ok-from-file": ('kubectl create secret generic s --from-file=k=/dev/stdin', False),
    "ok-secret-version-literal": ('kubectl create secret generic s --from-literal=lastProcessedVersion="$CURRENT_VERSION"', False),
    "ok-configmap-literal": ('kubectl create configmap c --from-literal=Corefile="$rewritten"', False),
    "ok-comment": ('# curl -H "X-Vault-Token: $VAULT_TOKEN" is what this replaced', False),
    "ok-mkdir-p": ('mkdir -p "$ROOT/x"', False),
}


def selftest():
    bad = 0
    for name, (body, want) in CASES.items():
        got = scan_text(name, body)
        leaked = [g for g in got if any(len(l.strip()) > 12 and l.strip() in g for l in body.split("\n"))]
        ok = bool(got) == want and not leaked
        print(f"  {'ok  ' if ok else 'FAIL'} {'FAILING FIXTURE: ' if want else ''}{name}")
        if not ok:
            bad += 1
            for g in got:
                print(f"         {g}")
    # A rendered container with $(SECRET_ENV) in its args.
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "render.yaml")
        with open(p, "w") as f:
            f.write(
                "apiVersion: batch/v1\nkind: Job\nmetadata: {name: j}\nspec:\n  template:\n    spec:\n"
                "      containers:\n        - name: c\n          image: x\n"
                "          args: [\"--token=$(API_TOKEN)\", \"$(PLAIN)\", \"$$(API_TOKEN)\"]\n"
                "          env:\n            - name: API_TOKEN\n              valueFrom: {secretKeyRef: {name: s, key: k}}\n"
                "            - name: PLAIN\n              value: x\n")
        got, pods = scan_render(p)
        ok = len(got) == 1 and pods == 1
        print(f"  {'ok  ' if ok else 'FAIL'} FAILING FIXTURE: render-args-secret-env")
        bad += 0 if ok else 1
    # A container of a values file that a subchart renders verbatim: the
    # store container of the Zitadel setup Job (charts#758) passed the
    # OpenBao token to curl as a header on its argv.
    values = (
        "zitadel:\n  setupJob:\n    extraContainers:\n      - name: login-client-pat-store\n"
        "        command: [bash, -ec]\n        args:\n          - |\n"
        "            status=$(curl -sS -o /tmp/resp.json -w '%{http_code}' -X POST \\\n"
        "              HEADER -H \"Content-Type: application/json\" \\\n"
        "              --data-binary @/tmp/body.json \"$BAO_ADDR/v1/secret/data/$KEY\")\n")
    for name, header, want in (
            ("values-subchart-container-token-header", '-H "X-Vault-Token: $VAULT_TOKEN"', True),
            ("ok-values-subchart-container-header-file", "-H @/tmp/auth.hdr", False)):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "helm", "gibson"))
            p = os.path.join(d, "helm", "gibson", "values.yaml")
            with open(p, "w") as f:
                f.write(values.replace("HEADER", header))
            files = text_files(d)
            got = [g for x in files for g in scan_text(os.path.relpath(x, d), open(x).read())]
            ok = p in files and bool(got) == want
            print(f"  {'ok  ' if ok else 'FAIL'} {'FAILING FIXTURE: ' if want else ''}{name}")
            bad += 0 if ok else 1
    if bad:
        print(f"check-no-secret-argv selftest: {bad} case(s) failed", file=sys.stderr)
        return 1
    print(f"check-no-secret-argv selftest: {len(CASES) + 3} cases passed")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args()
    if yaml is None:
        print("FAIL: PyYAML is missing; the rendered pods cannot be read", file=sys.stderr)
        return 1
    if a.selftest:
        return selftest()
    findings, files, pods = scan(a.root)
    if files == 0 or pods == 0:
        print(f"FAIL: the scan read {files} files and {pods} rendered pods, so it looked at nothing", file=sys.stderr)
        return 1
    if findings:
        for f in findings:
            print(f"FAIL: {f}", file=sys.stderr)
        print("Pass the value on stdin, in a file of a 0700 directory, or in the environment. "
              "curl reads -H @file and --data-binary @file, jq reads --rawfile or env.", file=sys.stderr)
        return 1
    print(f"no-secret-argv: {files} files and {pods} rendered pods put no secret on an argv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
