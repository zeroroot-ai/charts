#!/usr/bin/env bats
# The rotation of the OpenBao service tokens (ADR-0171, row
# openbao-service-tokens).
#
# The test renders the chart, takes the real token functions out of the
# rendered openbao-auto-init sidecar, and runs them under sh against stubs of
# curl (OpenBao), kube_curl (the Kubernetes API), kv_get and the mint. The
# rotation logic under test is the rendered text.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/tokens.sh" <<'PY'
import re, sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "vault_ensure_token()" not in a:
                    continue
                out = []
                for v in ("TOKEN_LIFETIME_S", "TOKEN_REVOKE_GRACE_S", "TOKEN_ROTATION_KEY",
                          "REVOKE_ACCESSOR_ANN", "REVOKE_AFTER_ANN"):
                    m = re.search(r"^\s*%s=.*$" % v, a, re.M)
                    out.append(m.group(0).strip())
                out.append(a[a.index("token_rotation_requested_at() {"):a.index("vault_ensure_tokens() {")])
                open(sys.argv[2], "w").write("\n".join(out) + "\n")
                sys.exit(0)
sys.exit("no token functions in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

# run_ensure <seconds since the token was created> <requested_at> <pending accessor>
# Env: HELD (default held), MINTFAIL=1 makes the mint fail, READFAIL=1 makes the
# Secret read fail.
# Prints one line per effect: "mint", "renew", "annotate <accessor> <after-now>", or "none".
run_ensure() {
  HELD="${HELD-held}" MINTFAIL="${MINTFAIL:-}" READFAIL="${READFAIL:-}" STATUS403="${STATUS403:-}" sh -c '
    set -u
    AGE="$1"; REQ="$2"; PENDING="$3"
    TMPD="$(mktemp -d)"; NAMESPACE=gibson; KUBE_API=https://k; BAO_ADDR_LOCAL=http://b
    PLATFORM_TOKEN_SECRET=gibson-platform-operator-vault
    SEEDER_ROLE=openbao-seeder; OWN_SA=gibson-openbao
    EFFECTS="$TMPD/effects"; : > "$EFFECTS"
    . "$WORK/tokens.sh"
    NOW=$(date +%s)
    kube_read_token() { echo tok-old; }
    curl() {
      out=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; *) last="$1"; shift ;; esac; done
      case "$last" in
        */lookup-self)
          printf "{\"data\":{\"ttl\":3000,\"policies\":[\"platform-operator\"],\"creation_time\":%s,\"accessor\":\"acc-old\"}}" $((NOW - AGE)) > "$out"
          printf 200 ;;
        */renew-self) echo renew >> "$EFFECTS"; printf 200 ;;
        *) printf 500 ;;
      esac
    }
    kube_curl() {
      case "$*" in
        *PATCH*) for a in "$@"; do case "$a" in {*) printf "%s\n" "$a" > "$TMPD/patch.json" ;; esac; done
                 echo "annotate $(jq -r ".metadata.annotations[\"gibson.zeroroot.ai/revoke-accessor\"]" "$TMPD/patch.json") $(( $(jq -r ".metadata.annotations[\"gibson.zeroroot.ai/revoke-after\"] | tonumber" "$TMPD/patch.json") - NOW ))" >> "$EFFECTS"
                 printf 200 ;;
        *) if [ -n "$READFAIL" ]; then return 7; fi
           if [ -n "${STATUS403:-}" ]; then echo "{\"kind\":\"Status\",\"metadata\":{},\"code\":403}"; return 0; fi
           if [ -n "$PENDING" ]; then printf "{\"kind\":\"Secret\",\"metadata\":{\"annotations\":{\"gibson.zeroroot.ai/revoke-accessor\":\"%s\"}}}" "$PENDING"; else echo "{\"kind\":\"Secret\",\"metadata\":{}}"; fi ;;
      esac
    }
    kv_get() { printf "{\"requested_at\":\"%s\"}" "$REQ"; }
    vault_mint_token() { if [ -n "$MINTFAIL" ]; then echo mint-failed >> "$EFFECTS"; return 1; fi; echo mint >> "$EFFECTS"; }
    seed_one() { :; }
    bao_login_seeder() { echo seeder; }
    vault_revoke_self() { :; }
    vault_ensure_token gibson-platform-operator-vault platform-operator "$HELD" >/dev/null 2>&1
    [ -s "$EFFECTS" ] && cat "$EFFECTS" || echo none
  ' _ "$1" "$2" "$3"
}

@test "a young token with no request is kept" {
  run run_ensure 3600 0 ""
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

@test "a token older than its lifetime gets a successor, and the old one is set for revoke after the grace" {
  run run_ensure 90000 0 ""
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "mint" ]
  # The template reads the clock after the harness does, so the grace is 900
  # seconds or a few more, never less.
  [[ "${lines[1]}" =~ ^annotate\ acc-old\ (900|90[0-9])$ ]]
}

@test "a rotation request newer than the token gets a successor" {
  run run_ensure 600 "$(date +%s)" ""
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "mint" ]
}

@test "a token with a revoke still pending is not rotated again" {
  run run_ensure 90000 "$(date +%s)" "acc-earlier"
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

# run_revoke <seconds until revoke-after> <HTTP code of revoke-accessor> [answer body]
run_revoke() {
  sh -c '
    set -u
    LEFT="$1"; CODE="$2"; BODY="${3:-{\}}"
    TMPD="$(mktemp -d)"; NAMESPACE=gibson; KUBE_API=https://k; BAO_ADDR_LOCAL=http://b
    . "$WORK/tokens.sh"
    NOW=$(date +%s)
    curl() {
      out=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2 ;; -d) echo "revoke $2" >> "$TMPD/effects"; shift 2 ;; *) shift ;; esac; done
      printf "%s" "$BODY" > "$out"; printf "%s" "$CODE"
    }
    kube_curl() {
      case "$*" in
        *PATCH*) case "$*" in *\":null*) echo cleared ;; *) echo rearmed ;; esac >> "$TMPD/effects"; printf 200 ;;
        *) case "$LEFT" in -*|[0-9]*) after=$((NOW + LEFT)) ;; *) after="$LEFT" ;; esac
           printf "{\"kind\":\"Secret\",\"metadata\":{\"annotations\":{\"gibson.zeroroot.ai/revoke-accessor\":\"acc-old\",\"gibson.zeroroot.ai/revoke-after\":\"%s\"}}}" "$after" ;;
      esac
    }
    token_revoke_pending gibson-platform-operator-vault seeder 2>/dev/null; rc=$?
    [ -f "$TMPD/effects" ] && cat "$TMPD/effects" || echo none
    echo "rc=$rc"
  ' _ "$1" "$2" "${3:-}"
}

@test "a pending revoke waits for its grace" {
  run run_revoke 600 204
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "none" ]
  [ "${lines[1]}" = "rc=0" ]
}

@test "a pending revoke after its grace revokes the old accessor and clears the record" {
  run run_revoke -5 204
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = 'revoke {"accessor":"acc-old"}' ]
  [ "${lines[1]}" = "cleared" ]
  [ "${lines[2]}" = "rc=0" ]
}

@test "FAILING FIXTURE: a refused revoke keeps the record and fails the pass" {
  run run_revoke -5 403
  [ "$status" -eq 0 ]
  [ "${lines[1]}" = "rc=1" ]
  [[ "$output" != *cleared* ]]
}

@test "a rotation whose mint fails renews the old token" {
  MINTFAIL=1 run run_ensure 90000 0 ""
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "mint-failed" ]
  [ "${lines[1]}" = "renew" ]
}

@test "with no held seeder token nothing rotates" {
  HELD="" run run_ensure 90000 "$(date +%s)" ""
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

@test "a rotation request in the future is ignored" {
  run run_ensure 600 "$(( $(date +%s) + 86400 ))" ""
  [ "$status" -eq 0 ]
  [ "$output" = "none" ]
}

@test "FAILING FIXTURE: a failed read of the Secret does not count as no pending revoke" {
  READFAIL=1 run run_ensure 90000 0 ""
  [ "$status" -eq 0 ]
  [[ "$output" != *mint* ]]
}

@test "an accessor whose token is gone counts as revoked" {
  run run_revoke -5 400 '{"errors":["token not found"]}'
  [ "$status" -eq 0 ]
  [ "${lines[1]}" = "cleared" ]
  [ "${lines[2]}" = "rc=0" ]
}

@test "FAILING FIXTURE: another 400 keeps the record" {
  run run_revoke -5 400 '{"errors":["missing accessor"]}'
  [ "$status" -eq 0 ]
  [[ "$output" != *cleared* ]]
  [[ "$output" == *"rc=1"* ]]
}

@test "FAILING FIXTURE: a Kubernetes error answer does not count as no pending revoke" {
  STATUS403=1 run run_ensure 90000 0 ""
  [ "$status" -eq 0 ]
  [[ "$output" != *mint* ]]
}

@test "an unknown accessor answer (200 with a warning) counts as revoked" {
  run run_revoke -5 200 '{"warnings":["No token found with this accessor"]}'
  [ "$status" -eq 0 ]
  [ "${lines[1]}" = "cleared" ]
  [ "${lines[2]}" = "rc=0" ]
}

@test "a revoke-after that is not a number waits one more grace, and revokes nothing now" {
  run run_revoke soon 204
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "rearmed" ]
  [ "${lines[1]}" = "rc=0" ]
  [[ "$output" != *revoke\ * ]]
}
