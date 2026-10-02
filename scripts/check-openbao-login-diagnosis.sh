#!/usr/bin/env bash
# A failed OpenBao kubernetes-auth login must say WHICH step failed (charts#330).
#
# OpenBao answers every login failure with the same bare 403 "permission
# denied". charts#80 lost two days to that body and gibson's exit-test-bank
# lost five (2026-09-28 .. 2026-10-02, 44 runs on main), because the sidecar
# printed the three words and nothing that distinguishes:
#
#   1. this pod presents no ServiceAccount token at all
#   2. it presents one the role does not bind
#   3. the apiserver refuses OpenBao's review (no system:auth-delegator)
#   4. the apiserver never answers (no egress to it)
#   5. the apiserver is content and OpenBao refused anyway
#
# bao_login_diagnose() separates those five. This EXERCISES it: the functions
# are lifted out of the rendered sidecar and run under a real shell against a
# stubbed apiserver, once per case. It is not an inventory of the source — a
# check that only greps for the lines would pass on a diagnosis that cannot
# run, which is the failure mode it exists to prevent.
#
# The boundary: kube_curl is stubbed. curl's own behaviour is not under test;
# the branch taken for each answer is.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Repack first, exactly as scripts/golden.sh does. `helm template` on the
# umbrella reads helm/gibson/charts/*.tgz, so without this it renders the
# subchart as it was when the tarball was built and an edit to the sidecar is
# invisible. Every mutation of the diagnosis passed this check before the
# repack went in, which is the whole reason it is here and not an optimisation
# someone may remove.
rm -f "$ROOT"/helm/gibson/charts/gibson-{workloads,operators,crds,common}-*.tgz \
      "$ROOT"/helm/*/.charts.stamp 2>/dev/null || true
make -C "$ROOT" chart-deps >/dev/null

RENDER="$WORK/render.yaml"
helm template gibson "$ROOT/helm/gibson" --namespace gibson \
  -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
  -f "$ROOT/helm/gibson/values-baseline.yaml" >"$RENDER"

# Lift the two functions out of the rendered sidecar script.
python3 - "$RENDER" "$WORK/lifted.sh" <<'PY'
import re, sys
src, out = sys.argv[1], sys.argv[2]
text = open(src).read()
want = ("b64url_decode", "bao_login_diagnose")
lifted = {}
for name in want:
    m = re.search(r'^([ \t]*)' + name + r'\(\) \{$', text, re.M)
    if not m:
        raise SystemExit(f"check-openbao-login-diagnosis: {name}() is not in the rendered "
                         f"sidecar. The diagnosis the exit-test-bank depends on is gone, or "
                         f"it was renamed without this check following it.")
    indent = m.group(1)
    lines = text[m.start():].split("\n")
    body = [lines[0]]
    for line in lines[1:]:
        body.append(line)
        if line == indent + "}":
            break
    else:
        raise SystemExit(f"check-openbao-login-diagnosis: {name}() has no closing brace at "
                         f"its own indent; the lift would run into the next function.")
    lifted[name] = "\n".join(l[len(indent):] if l.startswith(indent) else l for l in body)
open(out, "w").write("\n".join(lifted[n] for n in want) + "\n")

# A working diagnosis that nothing calls is the same silence with more code.
# Lifting and running the function proves it works; this proves it runs.
#
# The sidecar has several `if [ "$code" != "200" ]` branches, so the login one
# is identified by its own message, not by being first.
candidates = list(re.finditer(r'^([ \t]*)if \[ "\$code" != "200" \]; then$', text, re.M))
login_branch = None
for m in candidates:
    indent = m.group(1)
    lines = []
    for line in text[m.end():].split("\n"):
        if line == indent + "fi":
            break
        lines.append(line)
    blob = "\n".join(lines)
    if "WARN: login as" in blob:
        login_branch = blob
        break
if login_branch is None:
    raise SystemExit("check-openbao-login-diagnosis: no `if [ \"$code\" != \"200\" ]` branch in "
                     "the rendered sidecar carries the login failure message, so this check "
                     "cannot tell whether the diagnosis is reached. %d branch(es) were scanned."
                     % len(candidates))
if not re.search(r'^\s*bao_login_diagnose\s*$', login_branch, re.M):
    raise SystemExit("check-openbao-login-diagnosis: the login-failure branch does NOT call "
                     "bao_login_diagnose. A failed login would print the bare 403 and nothing "
                     "that says which step broke, which is the five-day red this check exists "
                     "to prevent (gibson exit-test-bank, 2026-09-28 .. 2026-10-02).")
PY

# One forged ServiceAccount token. base64url, unsigned: the diagnosis reads
# claims and never verifies, which is the point — it reports what the apiserver
# will be asked to judge.
mk_jwt() { # $1 sub
  local hdr pay
  hdr=$(printf '%s' '{"alg":"RS256"}' | base64 -w0 | tr '/+' '_-' | tr -d '=')
  pay=$(printf '{"sub":"%s","aud":["https://kubernetes.default.svc"]}' "$1" \
        | base64 -w0 | tr '/+' '_-' | tr -d '=')
  printf '%s.%s.c2ln' "$hdr" "$pay"
}

run_case() { # $1 label, $2 token-file content ("" = absent), $3 stub code, $4 stub body
  local label="$1" tokfile="$WORK/token" out="$WORK/out.$$"
  rm -f "$tokfile"
  [ -n "$2" ] && printf '%s' "$2" >"$tokfile"
  cat >"$WORK/case.sh" <<SH
set -u
TMPD="$WORK"
KUBE_TOKEN_FILE="$tokfile"
KUBE_API="https://kubernetes.default.svc"
NAMESPACE="gibson"
OWN_SA="gibson-openbao"
AUTH_MOUNT="kubernetes"
SEEDER_ROLE="openbao-seeder"
kube_curl() {
  # Mimics the real one's contract: body to the -o path, code on stdout.
  dest=""
  while [ \$# -gt 0 ]; do
    case "\$1" in -o) dest="\$2"; shift 2 ;; *) shift ;; esac
  done
  [ -n "\$dest" ] && printf '%s' '$4' >"\$dest"
  [ "$3" = "000" ] && return 7
  printf '%s' '$3'
}
. "$WORK/lifted.sh"
bao_login_diagnose
SH
  sh "$WORK/case.sh" >"$out" 2>&1 || {
    echo "FAIL [$label]: the diagnosis itself exited non-zero" >&2
    cat "$out" >&2; exit 1
  }
  printf '%s' "$label"
  cat "$out"
}

assert() { # $1 label, $2 haystack-file, $3 phrase that must appear
  grep -qF -- "$3" "$2" || {
    echo "FAIL [$1]: the diagnosis never said \"$3\". It printed:" >&2
    sed 's/^/    /' "$2" >&2
    exit 1
  }
}
refute() { # $1 label, $2 file, $3 phrase that must NOT appear
  grep -qF -- "$3" "$2" && {
    echo "FAIL [$1]: the diagnosis printed \"$3\", which must never leave the pod." >&2
    exit 1
  }
  return 0
}

GOOD="$(mk_jwt system:serviceaccount:gibson:gibson-openbao)"
WRONG="$(mk_jwt system:serviceaccount:gibson:somebody-else)"
CASES=0

check() { # $1 label, $2 token, $3 code, $4 body, then phrases
  local label="$1" tok="$2" code="$3" body="$4"; shift 4
  run_case "$label" "$tok" "$code" "$body" >/dev/null
  sh "$WORK/case.sh" >"$WORK/o" 2>&1
  local p
  for p in "$@"; do assert "$label" "$WORK/o" "$p"; done
  # No case may ever echo the credential back.
  [ -n "$tok" ] && refute "$label" "$WORK/o" "$tok"
  CASES=$((CASES + 1))
  echo "  ok  $label"
}

echo "check-openbao-login-diagnosis: five login failures, each named"

check "no token mounted" "" "201" '{}' \
  "MISSING OR EMPTY" "automountServiceAccountToken"

check "subject the role does not bind" "$WRONG" "201" \
  '{"status":{"authenticated":true,"user":{"username":"system:serviceaccount:gibson:somebody-else"}}}' \
  "SUBJECT MISMATCH" "system:serviceaccount:gibson:gibson-openbao"

check "apiserver refuses the review" "$GOOD" "403" \
  '{"message":"tokenreviews.authentication.k8s.io is forbidden"}' \
  "REFUSES this pod" "system:auth-delegator" "gibson-openbao-auth-delegator"

check "apiserver unreachable" "$GOOD" "000" '' \
  "did not complete" "global.networkPolicy.apiServerCIDRs"

check "apiserver content, OpenBao refused" "$GOOD" "201" \
  '{"status":{"authenticated":true,"user":{"username":"system:serviceaccount:gibson:gibson-openbao"}}}' \
  "authenticated=true" "the refusal is OpenBao's own" "subject matches"

[ "$CASES" = 5 ] || { echo "check-openbao-login-diagnosis: ran $CASES of 5 cases" >&2; exit 1; }
echo "check-openbao-login-diagnosis: $CASES/5 login failures each produce their own named cause"
