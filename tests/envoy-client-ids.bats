#!/usr/bin/env bats
# render-envoy-config refuses an empty client_id (ADR-0031).
#
# The init container of the Envoy Deployment writes the dashboard client_id
# and the native-login (CLI) client_id into the jwt_authn audiences. An empty
# value must stop the pod with a named FATAL, never become a placeholder
# audience. The test renders the chart, takes the script of that container,
# points its paths at fixture files, and runs it.
#
# FAILING FIXTURE: an empty or missing native-login client_id. Before
# ADR-0031 was built the script substituted `native-login-unconfigured` and
# exited 0.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  R="$(mktemp -d)"; export R
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" > "$R/render.yaml"
  python3 - "$R/render.yaml" > "$R/script.sh" <<'PY'
import sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-envoy"):
        for c in d["spec"]["template"]["spec"].get("initContainers", []):
            if c["name"] == "render-envoy-config":
                print(c["args"][0]); sys.exit(0)
sys.exit("no render-envoy-config init container in the Envoy Deployment")
PY
}

teardown_file() { rm -rf "$R"; }

setup() {
  W="$(mktemp -d)"; export W
  mkdir -p "$W/dashboard-oidc" "$W/native-login-oidc" "$W/envoy-template" "$W/envoy"
  printf 'audiences: [${DASHBOARD_CLIENT_ID}, ${NATIVE_LOGIN_CLIENT_ID}]\n' > "$W/envoy-template/envoy.yaml"
  sed -e "s|/etc/dashboard-oidc|$W/dashboard-oidc|g" -e "s|/etc/native-login-oidc|$W/native-login-oidc|g" \
      -e "s|/etc/envoy-template|$W/envoy-template|g" -e "s|/etc/envoy/|$W/envoy/|g" "$R/script.sh" > "$W/run.sh"
}

teardown() { rm -rf "$W"; }

@test "the rendered script carries no placeholder audience" {
  run grep -c "native-login-unconfigured" "$R/script.sh"
  [ "$output" = "0" ]
}

@test "both client_ids present: the config holds both audiences" {
  printf '111' > "$W/dashboard-oidc/client_id"
  printf '222' > "$W/native-login-oidc/client_id"
  run sh "$W/run.sh"
  [ "$status" -eq 0 ]
  grep -q 'audiences: \[111, 222\]' "$W/envoy/envoy.yaml"
}

@test "FAILING FIXTURE: an empty native-login client_id stops the pod" {
  printf '111' > "$W/dashboard-oidc/client_id"
  : > "$W/native-login-oidc/client_id"
  run sh "$W/run.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"FATAL: gibson-native-login-oidc"* ]]
  [ ! -s "$W/envoy/envoy.yaml" ]
}

@test "FAILING FIXTURE: a missing native-login Secret stops the pod" {
  printf '111' > "$W/dashboard-oidc/client_id"
  run sh "$W/run.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"FATAL: gibson-native-login-oidc"* ]]
}

@test "an empty dashboard client_id stops the pod" {
  : > "$W/dashboard-oidc/client_id"
  printf '222' > "$W/native-login-oidc/client_id"
  run sh "$W/run.sh"
  [ "$status" -eq 2 ]
  [[ "$output" == *"FATAL: gibson-dashboard-oidc"* ]]
}
