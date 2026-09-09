#!/usr/bin/env bash
# check-seed-passwords.sh — every password the OpenBao seeder mints is safe
# to hand to a program as an argument: letters and digits only, never a
# leading '-'.
#
# WHY. The seeder's `rand` kind is URL-safe base64 (helm/gibson-workloads/
# templates/auth/openbao-deployment.yaml, gen_rand), so one draw in 64
# starts with '-'. The Neo4j image runs `neo4j-admin dbms
# set-initial-password <password>`; picocli reads a leading '-' as an
# option, prints its usage, exits 64, and gibson-0 crashloops until someone
# rotates the key. Blocker 3 loops #16 and #18 and hosted run 34345134204
# (2026-09-09) all died that way, on a fresh bringup, with no defect in any
# chart or script that a diff would show. Redis takes --requirepass the
# same way.
#
# WHAT. Two checks, and a self-test that plants the defect in each:
#   1. files/openbao-seed-keys.txt: no property named `password`, and not
#      grafana-admin-password, is a base64 draw (rand, rand8, randb64);
#      the ones the seeder generates are kind `pw`.
#   2. The rendered seeder's gen_pw, sampled 2000 times, emits exactly 43
#      characters, all [A-Za-z0-9].
#
# Usage: scripts/check-seed-passwords.sh
# Exit:  0 both hold · 1 a password is not argument-safe · 2 could not run
set -euo pipefail
CHART_DIR="${CHART_DIR:-helm/gibson}"
TABLE="helm/gibson-workloads/files/openbao-seed-keys.txt"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fail() { printf '\033[0;31m✗ check-seed-passwords: %s\033[0m\n' "$*" >&2; exit 1; }
die()  { printf '\033[0;31mcheck-seed-passwords: %s\033[0m\n' "$*" >&2; exit 2; }

# ---- 1. the table ---------------------------------------------------------
table_violations() {  # stdin: the table; stdout: one line per violation
  awk '
    /^[[:space:]]*(#|$)/ { next }
    {
      key = $1
      for (i = 2; i <= NF; i++) {
        split($i, kv, ":")
        prop = kv[1]; kind = kv[2]
        # A password we GENERATE must be pw. empty, input, inputjson and
        # literal are not ours to shape (the operator or the keyring
        # supplies them); rand, rand8 and randb64 are the URL-safe and
        # standard base64 draws that can start with "-" or "+".
        if ((prop == "password" || key == "grafana-admin-password") && kind ~ /^rand(8|b64)?$/)
          print key " " prop ":" kind
      }
    }'
}
[ -f "$TABLE" ] || die "no seed table at $TABLE"
bad="$(table_violations < "$TABLE" || true)"
[ -z "$bad" ] || fail "a seeded password is not kind pw (argument-safe): $bad"

# ---- 2. the generator, sampled --------------------------------------------
helm template gibson "$CHART_DIR" -f "$CHART_DIR/values-vanilla.yaml" --namespace gibson \
  > "$WORK/render.yaml" 2> "$WORK/render.err" || die "helm template failed: $(tail -n1 "$WORK/render.err")"
python3 - "$WORK/render.yaml" "$WORK/gen_pw.sh" <<'PY'
import re, sys, yaml
src = ""
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") not in ("StatefulSet", "Deployment") or "openbao" not in d["metadata"]["name"]:
        continue
    spec = d["spec"]["template"]["spec"]
    for c in (spec.get("containers") or []) + (spec.get("initContainers") or []):
        for a in (c.get("args") or []) + (c.get("command") or []):
            if "gen_pw()" in a:
                src = a
if not src:
    sys.exit("no container of the openbao StatefulSet defines gen_pw()")
m = re.search(r"gen_pw\(\) \{.*?\n\s*\}", src, re.S)
if not m:
    sys.exit("gen_pw() body not found")
open(sys.argv[2], "w").write(m.group(0) + "\n")
PY
sample() {  # sample <definition file> -> stdout: the first bad sample, or nothing
  bash -c '
    set -euo pipefail
    . "$1"
    for i in $(seq 1 2000); do
      v="$(gen_pw 43)"
      case "$v" in
        *[!A-Za-z0-9]*|"") printf "%s\n" "$v"; exit 0 ;;
      esac
      [ "${#v}" -eq 43 ] || { printf "%s\n" "$v"; exit 0; }
    done' _ "$1"
}
badpw="$(sample "$WORK/gen_pw.sh")"
[ -z "$badpw" ] || fail "gen_pw emitted a value that is not 43 alphanumerics: '${badpw}'"

# ---- self-test: the guard must fail on each planted defect ----------------
printf 'gibson-neo4j-password password:rand\n' | table_violations | grep -q . \
  || die "self-test: password:rand in the table was not detected"
printf 'gen_pw() {\n  head -c 32 /dev/urandom | base64 | tr -d "\\n=" | tr "+/" "-_"\n}\n' > "$WORK/old.sh"
old_bad="$(sample "$WORK/old.sh")"
[ -n "$old_bad" ] || die "self-test: the old URL-safe base64 generator passed 2000 samples; the sampler cannot fail"

echo "✅ check-seed-passwords: every seeded password is kind pw, gen_pw sampled 2000x is 43 alphanumerics; self-test rejected password:rand and the base64 generator"
