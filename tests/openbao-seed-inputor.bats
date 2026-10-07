#!/usr/bin/env bats
# The inputor seed kind of the openbao-auto-init sidecar (charts#486).
#
# The test renders the chart, takes the real seed_input and seed_one
# functions out of the rendered sidecar script, and runs them against a stub
# key-value store. kv_get and kv_put are the only stubs: the seed logic under
# test is the rendered text.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    > "$WORK/render.yaml"
  python3 - "$WORK/render.yaml" "$WORK/seed.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "StatefulSet" and d["metadata"]["name"].endswith("-openbao"):
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in (c.get("command") or []) + (c.get("args") or []):
                if "seed_one()" in a:
                    a_ = a[a.index("seed_input() {"):a.index("vault_seed_platform_secrets() {")]
                    # seed_generate is between them; keep it, the literal branch is used.
                    open(sys.argv[2], "w").write(a_)
                    sys.exit(0)
sys.exit("no seed functions in the rendered openbao sidecar")
PY
}

teardown_file() { rm -rf "$WORK"; }

run_seed() {
  # $1 stored JSON of the key, $2 the stripe-secret-key input ("" = empty file, "-" = no file)
  bash -c '
    set -u
    INPUTS_DIR="$WORK/inputs"; rm -rf "$INPUTS_DIR"; mkdir -p "$INPUTS_DIR"
    if [ "$2" != "-" ]; then printf "%s" "$2" > "$INPUTS_DIR/stripe-secret-key"; fi
    STORE="$1"
    seed_checked=0; seed_written=0
    kv_get() { printf "%s" "$STORE"; }
    kv_put() { printf "%s" "$3" > "$WORK/put.json"; }
    rm -f "$WORK/put.json"
    . "$WORK/seed.sh"
    seed_one tok gibson-stripe-credentials "secret_key:inputor:stripe-secret-key:sk_test_placeholder" >/dev/null
    if [ -f "$WORK/put.json" ]; then jq -r .secret_key "$WORK/put.json"; else echo "<unchanged>"; fi
  ' _ "$1" "$2"
}

@test "an empty input on a fresh key writes the placeholder" {
  run run_seed '{}' ''
  [ "$status" -eq 0 ]
  [ "$output" = "sk_test_placeholder" ]
}

@test "a missing input file on a fresh key writes the placeholder" {
  run run_seed '{}' '-'
  [ "$status" -eq 0 ]
  [ "$output" = "sk_test_placeholder" ]
}

@test "a non-empty input replaces the placeholder on the next pass" {
  run run_seed '{"secret_key":"sk_test_placeholder"}' 'sk_live_real'
  [ "$status" -eq 0 ]
  [ "$output" = "sk_live_real" ]
}

@test "an empty input keeps the stored value" {
  run run_seed '{"secret_key":"sk_live_real"}' ''
  [ "$status" -eq 0 ]
  [ "$output" = "<unchanged>" ]
}

@test "the same input writes nothing" {
  run run_seed '{"secret_key":"sk_live_real"}' 'sk_live_real'
  [ "$status" -eq 0 ]
  [ "$output" = "<unchanged>" ]
}

@test "the seed table seeds the Stripe key from the keyring with placeholders" {
  grep -q '^gibson-stripe-credentials publishable_key:inputor:stripe-publishable-key:pk_test_placeholder secret_key:inputor:stripe-secret-key:sk_test_placeholder webhook_secret:inputor:stripe-webhook-secret:whsec_placeholder$' \
    "$ROOT/helm/gibson-workloads/files/openbao-seed-keys.txt"
}
