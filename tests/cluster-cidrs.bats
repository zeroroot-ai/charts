#!/usr/bin/env bats
# The pod and the Service ranges of the cluster
# (global.networkPolicy.clusterCIDRs, CONNECTOR_CLUSTER_CIDRS).
#
# The connector operator stops at start on a value that net.ParseCIDR
# refuses. Sprig has no CIDR parser, so the helper
# gibson-operators.validCIDR checks each part. Each bad value below must fail
# the render, and the good values must render into the env var.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
}

setup() {
  W="$(mktemp -d)"; export W
}

teardown() { rm -rf "$W"; }

render() { # $@ = the CIDRs of the list
  {
    echo "global:"
    echo "  networkPolicy:"
    echo "    clusterCIDRs:"
    for c in "$@"; do echo "      - \"$c\""; done
  } > "$W/v.yaml"
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    -f "$W/v.yaml" 2>&1
}

refused() { # $1 = one bad CIDR
  run render "$1"
  [ "$status" -ne 0 ] || { echo "the render accepted $1"; false; }
  [[ "$output" == *"clusterCIDRs: $1 is not a CIDR"* ]] || { echo "$output" | tail -n 3; false; }
}

@test "good IPv4 and IPv6 ranges render into CONNECTOR_CLUSTER_CIDRS" {
  run render 10.244.0.0/16 0.0.0.0/0 255.255.255.255/32 fd00:10:96::/112 ::/0 \
    2001:db8:0:0:0:0:0:1/128 ::1/128 fe80::/10
  [ "$status" -eq 0 ] || { echo "$output" | tail -n 3; false; }
  want='value: "10.244.0.0/16,0.0.0.0/0,255.255.255.255/32,fd00:10:96::/112,::/0,2001:db8:0:0:0:0:0:1/128,::1/128,fe80::/10"'
  [[ "$output" == *"$want"* ]] || { echo "no env value: $want"; false; }
}

@test "FAILING FIXTURE: an IPv4 prefix over 32 fails" { refused 10.0.0.0/33; }
@test "FAILING FIXTURE: a prefix of three digits on IPv4 fails" { refused 10.0.0.0/999; }
@test "FAILING FIXTURE: an octet over 255 fails" { refused 999.1.1.1/8; }
@test "FAILING FIXTURE: a name that is not an address fails" { refused abc/12; }
@test "FAILING FIXTURE: an octet with a leading zero fails" { refused 10.0.0.010/8; }
@test "FAILING FIXTURE: an address with no prefix fails" { refused 10.0.0.0; }
@test "FAILING FIXTURE: an IPv6 prefix over 128 fails" { refused fd00::/129; }
@test "FAILING FIXTURE: two :: in one IPv6 address fail" { refused fd00::1::/64; }
@test "FAILING FIXTURE: nine IPv6 groups fail" { refused 1:2:3:4:5:6:7:8:9/64; }
@test "FAILING FIXTURE: seven IPv6 groups with no :: fail" { refused 1:2:3:4:5:6:7/64; }
@test "FAILING FIXTURE: an IPv6 group of five digits fails" { refused fd000::/64; }
@test "FAILING FIXTURE: a trailing single colon fails" { refused fd00::1:/64; }

@test "FAILING FIXTURE: an empty list fails" {
  printf 'global:\n  networkPolicy:\n    clusterCIDRs: []\n' > "$W/v.yaml"
  run helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" -f "$W/v.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"global.networkPolicy.clusterCIDRs is required"* ]] || { echo "$output" | tail -n 3; false; }
}
