#!/usr/bin/env bats
# No secret on the argv of curl or jq inside the OpenBao sidecar
# (GHSA-5c5h-rrh4-5xw3).
#
# The test renders the chart and takes the real functions out of the rendered
# openbao-auto-init sidecar: hcurl, kube_curl, vault_token_usable, kv_put and
# the seed write of vault_mint_token. It runs them under sh with a curl and a
# jq on PATH that record each argv, and with the real jq behind the record.
# A token or a secret value in any recorded argv fails the test. The record
# must hold the calls, and the header file of curl must hold the token, so
# the test cannot pass on an empty record.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/fns.sh" <<'PY'
import re, sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "kv_put()" not in a:
                    continue
                out = []
                for name in ("hcurl", "kube_curl", "vault_token_usable", "kv_put", "vault_mint_token",
                             "token_period_s", "token_max_ttl_s"):
                    m = re.search(r"^%s\(\) \{\n.*?^\}$" % name, a, re.M | re.S)
                    if not m:
                        sys.exit(f"no function {name} in the rendered openbao sidecar")
                    out.append(m.group(0))
                open(sys.argv[2], "w").write("\n".join(out) + "\n")
                sys.exit(0)
sys.exit("no openbao-auto-init sidecar in the render")
PY
}

teardown_file() { rm -rf "$WORK"; }

setup() {
  S="$(mktemp -d)"; export S
  mkdir -p "$S/bin"
  REAL_JQ="$(command -v jq)"; export REAL_JQ
  # curl: record the argv and each header file, answer 200.
  cat > "$S/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf 'curl' >> "$S/argv"; printf ' [%s]' "$@" >> "$S/argv"; printf '\n' >> "$S/argv"
out=""; method=GET; url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -H) case "$2" in @*) cat "${2#@}" >> "$S/headers" ;; esac; shift 2 ;;
    -o) out="$2"; shift 2 ;;
    -X) method="$2"; shift 2 ;;
    --data-binary) [ "$2" != @- ] || { cat >> "$S/bodies"; echo >> "$S/bodies"; }; shift 2 ;;
    -*) shift ;;
    *) url="$1"; shift ;;
  esac
done
if [ -n "$out" ]; then
  printf '{"auth":{"client_token":"minted-tok-5e8b"},"kind":"Secret"}' > "$out"
fi
# A create of a Kubernetes object answers 201, every other call 200.
case "$method $url" in "POST "*/api/v1/namespaces/*) printf 201 ;; *) printf 200 ;; esac
STUB
  # jq: record the argv, then run the real jq.
  cat > "$S/bin/jq" <<'STUB'
#!/usr/bin/env bash
printf 'jq' >> "$S/argv"; printf ' [%s]' "$@" >> "$S/argv"; printf '\n' >> "$S/argv"
exec "$REAL_JQ" "$@"
STUB
  chmod +x "$S/bin/curl" "$S/bin/jq"
  printf 'kube-sa-token-4c1e' > "$S/satoken"
}

teardown() { rm -rf "$S"; }

# run_fns <file of functions>: the token, the KV value and the minted token
# pass through the functions once each.
run_fns() {
  run env PATH="$S/bin:$PATH" S="$S" REAL_JQ="$REAL_JQ" sh -c '
    set -u
    TMPD="$(mktemp -d)"; NAMESPACE=gibson; KUBE_API=https://k; BAO_ADDR_LOCAL=https://b
    KUBE_TOKEN_FILE="$S/satoken"; KUBE_CACERT=/dev/null
    PLATFORM_TOKEN_SECRET=p; PG_LOGINS_TOKEN_SECRET=l; TOKEN_LIFETIME_S=86400; PG_LOGINS_PERIOD_S=3600; PG_LOGINS_MAX_TTL_S=0
    . "$1"
    vault_token_usable "bao-token-7f3a" || exit 11
    kv_put "bao-token-7f3a" some-key "{\"password\":\"kv-secret-2b9d\"}" || exit 12
    kube_curl -o /dev/null "$KUBE_API/api/v1/namespaces/gibson/secrets/x" >/dev/null || exit 13
    vault_mint_token "bao-token-7f3a" platform-operator p >/dev/null || exit 14
  ' _ "$1"
}

@test "no recorded argv of curl or jq holds a token or a secret value" {
  run_fns "$WORK/fns.sh"
  echo "$output"
  [ "$status" -eq 0 ]
  # A floor: the record holds the calls, and the header files and bodies
  # held the values, so the check below looked at them.
  [ "$(grep -c '^curl' "$S/argv")" -ge 4 ]
  grep -q 'X-Vault-Token: bao-token-7f3a' "$S/headers"
  grep -q 'Authorization: Bearer kube-sa-token-4c1e' "$S/headers"
  grep -q 'kv-secret-2b9d' "$S/bodies"
  grep -q "$(printf minted-tok-5e8b | base64)" "$S/bodies"
  [ "$(grep -c -e bao-token -e kv-secret -e kube-sa-token -e minted-tok -e "$(printf minted-tok-5e8b | base64)" "$S/argv")" -eq 0 ] \
    || { cat "$S/argv"; false; }
}

@test "FAILING FIXTURE: the old curl -H \"X-Vault-Token: \$1\" form puts the token on the argv" {
  sed 's|hcurl "X-Vault-Token: ${tok}" |curl -H "X-Vault-Token: ${tok}" |' "$WORK/fns.sh" > "$S/old.sh"
  # bats ignores a bare `!`, so the sed must be seen to change the text.
  run cmp -s "$S/old.sh" "$WORK/fns.sh"
  [ "$status" -ne 0 ]
  run_fns "$S/old.sh"
  [ "$status" -eq 0 ]
  [ "$(grep -c bao-token-7f3a "$S/argv")" -gt 0 ]
}

@test "FAILING FIXTURE: the old jq --argjson form puts the KV value on the argv" {
  sed 's|KV_DATA="$3" jq -nc '"'"'{data: (env.KV_DATA \| fromjson)}'"'"'|jq -nc --argjson d "$3" '"'"'{data: $d}'"'"'|' "$WORK/fns.sh" > "$S/old.sh"
  # bats ignores a bare `!`, so the sed must be seen to change the text.
  run cmp -s "$S/old.sh" "$WORK/fns.sh"
  [ "$status" -ne 0 ]
  run_fns "$S/old.sh"
  [ "$status" -eq 0 ]
  [ "$(grep -c kv-secret-2b9d "$S/argv")" -gt 0 ]
}
