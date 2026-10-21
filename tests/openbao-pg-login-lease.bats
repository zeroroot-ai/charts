#!/usr/bin/env bats
# A rotation or an expiry of the token that issues the Postgres login roles
# never drops a login role that a Secret holds (charts#602, ADR-0171 row
# postgres-role-passwords).
#
# OpenBao revokes the leases of a token when the token ends, and each lease
# of the database engine drops its login role. The test renders the chart,
# takes the real token functions out of the rendered openbao-auto-init
# sidecar, and runs them on a simulated clock against a fake of OpenBao and
# of the Kubernetes API (tests/fixtures/pg-login-lease/fake.py). The sidecar
# runs its pass every 30 minutes. External Secrets reads a new login role
# every 24 hours with the token of the Secret gibson-openbao-pg-logins. After
# each step, the login role of the newest read must be live.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml" 2>/dev/null
  python3 - "$WORK/render.yaml" "$WORK/tokens.sh" <<'PY'
import re, sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "vault_ensure_token()" not in a:
                    continue
                out = []
                for v in ("TOKEN_LIFETIME_S", "TOKEN_REVOKE_GRACE_S", "PG_LOGINS_TOKEN_SECRET", "PG_LEASE_MAX_S",
                          "PG_LOGINS_PERIOD_S", "PG_LOGINS_MAX_TTL_S", "TOKEN_ROTATION_KEY", "REVOKE_ACCESSOR_ANN", "REVOKE_AFTER_ANN"):
                    out.append(re.search(r"^\s*%s=.*$" % v, a, re.M).group(0).strip())
                out.append(a[a.index("hcurl() {"):a.index("\n}\n", a.index("hcurl() {")) + 3])
                out.append(a[a.index("vault_mint_token() {"):a.index("vault_ensure_tokens() {")])
                open(sys.argv[2], "w").write("\n".join(out) + "\n")
                sys.exit(0)
sys.exit("no token functions in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

# simulate <hours> [sidecar stop: <from hour> <to hour>]
# Env: OLD_DESIGN=1 runs the login token like every other service token
# (period 1h, revoke after the grace): the failing fixture.
simulate() {
  run sh -c '
    set -u
    HOURS="$1"; STOP_FROM="${2:--1}"; STOP_TO="${3:--1}"
    TMPD="$(mktemp -d)"; NAMESPACE=gibson; KUBE_API=https://k; BAO_ADDR_LOCAL=http://b
    PLATFORM_TOKEN_SECRET=gibson-platform-operator-vault; SEEDER_ROLE=openbao-seeder; OWN_SA=gibson-openbao
    FAKE="$ROOT/tests/fixtures/pg-login-lease/fake.py"; STATE="$TMPD/state.json"; NOW=1000000
    . "$WORK/tokens.sh"
    if [ -n "${OLD_DESIGN:-}" ]; then PG_LOGINS_TOKEN_SECRET=no-such-secret; fi
    MAX_TTL="$PG_LOGINS_MAX_TTL_S"
    if [ -n "${NO_MAX:-}" ]; then token_max_ttl_s() { echo 0; }; fi
    date() { if [ "${1:-}" = "+%s" ]; then echo "$NOW"; else command date "$@"; fi; }
    req() { # <bao|kube> <args...>: run curl-like args against the fake
      kind="$1"; shift
      out=""; fmt=""; method=GET; data=""; url=""; vt=""
      while [ $# -gt 0 ]; do case "$1" in
        -o) out="$2"; shift 2 ;; -w) fmt="$2"; shift 2 ;; -X) method="$2"; shift 2 ;;
        # A body and a token header come on stdin and in a file (GHSA-5c5h-rrh4-5xw3).
        -d|--data-binary) data="$2"; [ "$data" != @- ] || data="$(cat)"; shift 2 ;;
        -H) case "$2" in X-Vault-Token:*) vt="${2#X-Vault-Token: }" ;;
              @*) h="$(sed -n "s/^X-Vault-Token: //p" "${2#@}")"; [ -z "$h" ] || vt="$h" ;; esac; shift 2 ;;
        -sS|-s) shift ;; *) url="$1"; shift ;; esac; done
      [ "$method" = GET ] && [ -n "$data" ] && method=POST
      body="$TMPD/body.$$"
      if [ "$kind" = bao ]; then
        code=$(python3 "$FAKE" "$STATE" "$NOW" bao "$method" "${url#http://b/v1/}" "$vt" "$data" "$body")
      else
        name=""; case "$url" in */secrets/*) name="${url##*/secrets/}" ;; esac
        code=$(python3 "$FAKE" "$STATE" "$NOW" kube "$method" "$name" "$data" "$body")
      fi
      if [ -n "$out" ]; then cp "$body" "$out"; else cat "$body"; fi
      [ -n "$fmt" ] && printf "%s" "$code"
      return 0
    }
    curl() { req bao "$@"; }
    kube_curl() { req kube "$@"; }
    kv_get() { echo "{}"; }
    seed_one() { :; }
    bao_login_seeder() { echo seeder; }
    vault_revoke_self() { :; }
    S=gibson-openbao-pg-logins
    pass() {
      vault_ensure_token "$S" postgres-logins held >>"$TMPD/log" 2>&1 || true
      token_revoke_pending "$S" held >/dev/null 2>&1 || true
    }
    pass
    lease=""; step=0; last=$((HOURS * 2))
    while [ "$step" -le "$last" ]; do
      h=$((step / 2))
      if [ $((step % 48)) -eq 2 ]; then
        lease=$(python3 "$FAKE" "$STATE" "$NOW" creds gibson-platform) || { echo "FAIL at hour $h: $lease"; tail -20 "$TMPD/log"; exit 1; }
      fi
      if [ "$h" -lt "$STOP_FROM" ] || [ "$h" -ge "$STOP_TO" ]; then pass; fi
      if [ -n "${HOLDER:-}" ]; then
        python3 "$FAKE" "$STATE" "$NOW" holder
        age=$(python3 "$FAKE" "$STATE" "$NOW" oldest)
        if [ "$age" -gt "$MAX_TTL" ]; then
          echo "FAIL at hour $h: a token renewed by a holder is live at age ${age}s, past ${MAX_TTL}s"; exit 1
        fi
      fi
      if [ -n "$lease" ]; then
        msg=$(python3 "$FAKE" "$STATE" "$NOW" alive "$lease") || { echo "FAIL at hour $h: the login role of the Secret dropped: $msg"; exit 1; }
      fi
      NOW=$((NOW + 1800)); step=$((step + 1))
    done
    rotations=$(jq "[.tokens[]] | length" "$STATE")
    echo "ok: $HOURS hours, $rotations token(s), the login role of the Secret stayed live"
  ' sh "$@"
}

@test "the login role of the Secret stays live across three token rotations" {
  simulate 96
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  # One token at the start and one for each 24-hour rotation.
  [[ "$output" == *"5 token(s)"* ]] || { echo "$output"; false; }
}

@test "the login role of the Secret stays live while the sidecar is stopped for 48 hours" {
  simulate 96 30 78
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "a holder that renews each old token cannot keep one past its explicit maximum" {
  HOLDER=1 simulate 168
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: with no explicit maximum, a holder keeps an old token alive" {
  HOLDER=1 NO_MAX=1 simulate 168
  [ "$status" -ne 0 ]
  [[ "$output" == *"renewed by a holder is live at age"* ]] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a token with a period of 1h that a rotation revokes drops the login role" {
  OLD_DESIGN=1 simulate 96
  [ "$status" -ne 0 ]
  [[ "$output" == *"the login role of the Secret dropped"* ]] || { echo "$output"; false; }
  [[ "$output" == *"ended by its token"* ]] || { echo "$output"; false; }
}
