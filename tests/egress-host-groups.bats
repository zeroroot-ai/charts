#!/usr/bin/env bats
# The egress host groups that the values of an install name
# (global.networkPolicy.egressHostGroups, D76).
#
# Each test renders the umbrella with one group and reads the policy of that
# group, or expects the render to fail.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
}

setup() {
  W="$(mktemp -d)"; export W
}

teardown() { rm -rf "$W"; }

render() { # $1 = values file
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    -f "$1" 2>&1
}

@test "a group renders one policy with each host on its port" {
  cat > "$W/v.yaml" <<'V'
global:
  networkPolicy:
    egressHostGroups:
      payments:
        - https://api.payments.example
        - app.example.com:8443
        - http://203.0.113.7
V
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    -f "$W/v.yaml" > "$W/out.yaml"
  run python3 - "$W/out.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
pol = [d for d in docs if d["kind"] == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-egress-fqdn-payments"]
assert len(pol) == 1, "no policy for the group"
spec = pol[0]["spec"]
assert spec["endpointSelector"]["matchLabels"] == {"gibson.zeroroot.ai/egress-fqdn": "payments"}, spec["endpointSelector"]
got = []
for r in spec["egress"]:
    port = r["toPorts"][0]["ports"][0]["port"]
    if "toFQDNs" in r:
        got.append((r["toFQDNs"][0]["matchName"], port))
    else:
        got.append((r["toCIDR"][0], port))
want = [("api.payments.example", "443"), ("app.example.com", "8443"), ("203.0.113.7/32", "80")]
assert got == want, got
PY
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "a group with the name of a chart group fails the render" {
  printf 'global:\n  networkPolicy:\n    egressHostGroups:\n      zitadel:\n        - https://api.payments.example\n' > "$W/v.yaml"
  run render "$W/v.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *'the chart owns the egress host group "zitadel"'* ]]
}

@test "a host in the cluster fails the render" {
  printf 'global:\n  networkPolicy:\n    egressHostGroups:\n      payments:\n        - http://gibson-gibson-workloads.gibson.svc:50051\n' > "$W/v.yaml"
  run render "$W/v.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *'is not a host outside the cluster'* ]]
}

@test "a wildcard host fails the render" {
  printf 'global:\n  networkPolicy:\n    egressHostGroups:\n      payments:\n        - "*.payments.example"\n' > "$W/v.yaml"
  run render "$W/v.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *'has a wildcard'* ]]
}

@test "a group name that is not a label value fails the render" {
  printf 'global:\n  networkPolicy:\n    egressHostGroups:\n      Payments_1:\n        - https://api.payments.example\n' > "$W/v.yaml"
  run render "$W/v.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *'a group name is a label value'* ]]
}
