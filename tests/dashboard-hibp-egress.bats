#!/usr/bin/env bats
# The dashboard reaches the HIBP Pwned Passwords range API and nothing else
# outside the cluster (hosted#724).
#
# The namespace default deny closes egress. A pod opens it only through an
# egress host group (gibson.zeroroot.ai/egress-fqdn: <group>). The dashboard
# carries the group `dashboard`, whose one host is api.pwnedpasswords.com on
# 443, when gibson-workloads.dashboard.auth.hibp.enabled is true. With the
# check off, the group has no host and renders no policy.
#
# check_render <render.yaml> <expect-policy: yes|no>: exit 0 when the render
# holds the policy as described (or none, when told so), else 1 with the
# reason. The FAILING FIXTURE tests feed it a render with the policy removed,
# with a second host, and with the pod label removed.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
}

setup() {
  W="$(mktemp -d)"; export W
}

teardown() { rm -rf "$W"; }

render() { # $@ = extra helm args
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" "$@"
}

check_render() { # $1 = render file, $2 = yes|no (a policy is expected)
  python3 - "$1" "$2" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
expect = sys.argv[2] == "yes"
HOST, GROUP = "api.pwnedpasswords.com", "dashboard"
dash = [d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-dashboard")]
if len(dash) != 1:
    sys.exit(f"{len(dash)} dashboard Deployments in the render, want one")
labels = dash[0]["spec"]["template"]["metadata"]["labels"]
if labels.get("gibson.zeroroot.ai/egress-fqdn") != GROUP:
    sys.exit(f"the dashboard pod does not carry gibson.zeroroot.ai/egress-fqdn: {GROUP}: {labels}")
pol = [d for d in docs if d["kind"] == "CiliumNetworkPolicy" and d["metadata"]["name"] == f"gibson-egress-fqdn-{GROUP}"]
if not expect:
    if pol:
        sys.exit("the dashboard egress policy renders with the HIBP check off")
    print("ok: no dashboard egress policy with the HIBP check off")
    sys.exit(0)
if len(pol) != 1:
    sys.exit(f"{len(pol)} dashboard egress policies, want one")
spec = pol[0]["spec"]
if spec["endpointSelector"]["matchLabels"] != {"gibson.zeroroot.ai/egress-fqdn": GROUP}:
    sys.exit(f"the policy selects {spec['endpointSelector']}, want the {GROUP} group label")
hosts, dns = [], []
for r in spec["egress"]:
    if "toEndpoints" in r:
        dns += [n for tp in r["toPorts"] for n in tp["rules"]["dns"]]
        continue
    if "toCIDR" in r:
        sys.exit(f"the policy opens a CIDR, want host names only: {r['toCIDR']}")
    ports = [(p["port"], p["protocol"]) for tp in r["toPorts"] for p in tp["ports"]]
    for f in r["toFQDNs"]:
        hosts.append((f.get("matchName") or f.get("matchPattern"), ports))
if hosts != [(HOST, [("443", "TCP")])]:
    sys.exit(f"the policy opens {hosts}, want exactly {HOST} on 443/TCP")
if dns != [{"matchName": HOST}]:
    sys.exit(f"the DNS rule resolves {dns}, want exactly {HOST}")
print(f"ok: the dashboard pod reaches {HOST}:443 and no other outside host")
PY
}

@test "the dashboard pod carries the group, and the policy opens api.pwnedpasswords.com on 443 only" {
  render > "$W/out.yaml"
  run check_render "$W/out.yaml" yes
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "with the HIBP check off the group renders no policy" {
  render --set gibson-workloads.dashboard.auth.hibp.enabled=false > "$W/out.yaml"
  run check_render "$W/out.yaml" no
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a render without the dashboard egress policy fails" {
  render > "$W/out.yaml"
  python3 - "$W/out.yaml" "$W/planted.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
docs = [d for d in docs if not (d["kind"] == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-egress-fqdn-dashboard")]
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run check_render "$W/planted.yaml" yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"0 dashboard egress policies"* ]]
}

@test "FAILING FIXTURE: a policy with a second host fails" {
  render > "$W/out.yaml"
  python3 - "$W/out.yaml" "$W/planted.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for d in docs:
    if d["kind"] == "CiliumNetworkPolicy" and d["metadata"]["name"] == "gibson-egress-fqdn-dashboard":
        d["spec"]["egress"].append({"toFQDNs": [{"matchName": "haveibeenpwned.com"}],
                                    "toPorts": [{"ports": [{"port": "443", "protocol": "TCP"}]}]})
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run check_render "$W/planted.yaml" yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"want exactly api.pwnedpasswords.com"* ]]
}

@test "FAILING FIXTURE: a dashboard pod without the group label fails" {
  render > "$W/out.yaml"
  python3 - "$W/out.yaml" "$W/planted.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for d in docs:
    if d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-dashboard"):
        del d["spec"]["template"]["metadata"]["labels"]["gibson.zeroroot.ai/egress-fqdn"]
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run check_render "$W/planted.yaml" yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"does not carry gibson.zeroroot.ai/egress-fqdn"* ]]
}
