#!/usr/bin/env bats
# The push credential of the setec disk builder (D85, charts#574).
#
# registrySecret defaults to setec-disk-registry, so no install sets it.
# An ECR disk repository (an AWS env) renders, in the setec namespace, the
# key pair ExternalSecret, the ECRAuthorizationToken generator and the
# dockerconfigjson ExternalSecret setec-disk-registry, and the External
# Secrets controller reaches the ECR API of that region only. Any other
# registry (kind) renders a Fake generator with a config that holds no
# credential. Another registrySecret is a Secret of the operator, and the
# chart renders nothing.

ECR_REPO="123456789012.dkr.ecr.us-east-1.amazonaws.com/zeroroot-ai-staging-setec-disks"

render() { # <out> [helm args...]
  local out="$1"; shift
  helm template gibson "$ROOT/helm/gibson" --namespace gibson \
    -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
    "$@" > "$out" 2> "$out.err"
}

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
}

teardown_file() { rm -rf "$WORK"; }

# kinds <render>: one line per object of the disk registry chain, and the
# registry host and region it names.
chain() {
  python3 - "$1" <<'PY'
import json, sys, yaml
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d:
        continue
    k, n, ns = d.get("kind"), d["metadata"]["name"], d["metadata"].get("namespace", "")
    if k == "ECRAuthorizationToken":
        print(f"generator {ns}/{n} region={d['spec']['region']}")
    elif k == "Fake" and n == "setec-disk-registry":
        print(f"fake {ns}/{n} config={d['spec']['data']['config']}")
    elif k == "ExternalSecret" and n in ("setec-disk-registry", "setec-disk-push-key"):
        t = d["spec"]["target"]
        tpl = (t.get("template") or {})
        print(f"externalsecret {ns}/{n} refresh={d['spec']['refreshInterval']} type={tpl.get('type', '-')}")
        for v in (tpl.get("data") or {}).values():
            if v == "{{ .config }}":
                print("dockerconfig from-generator")
            else:
                print("dockerconfig " + ",".join(json.loads(v.replace("{{ .username }}", "u").replace("{{ .password }}", "p"))["auths"]))
    elif k == "Deployment" and n == "setec":
        for c in d["spec"]["template"]["spec"]["containers"]:
            for a in c.get("args") or []:
                if a.startswith("--disk-registry-secret"):
                    print("operator " + a)
    elif k == "CiliumNetworkPolicy" and n.endswith("egress-fqdn-external-secrets"):
        for r in d["spec"]["egress"]:
            for f in r.get("toFQDNs") or []:
                print("egress " + json.dumps(f, sort_keys=True))
PY
}

# has <text> <needle>: 0 when the text holds the needle, else 1 with a reason.
has() { [[ "$1" == *"$2"* ]] || { echo "missing: $2"; return 1; }; }
lacks() { [[ "$1" != *"$2"* ]] || { echo "must not hold: $2"; return 1; }; }

# assert_ecr_chain <render>: the ECR chain is whole. Each check returns 1
# with its reason, so a broken render fails here (FAILING FIXTURE below).
assert_ecr_chain() {
  local out; out="$(chain "$1")" || { echo "the render does not parse"; return 1; }
  has "$out" "generator setec-system/setec-disk-registry region=us-east-1" || return 1
  has "$out" "externalsecret setec-system/setec-disk-push-key refresh=60s type=-" || return 1
  has "$out" "externalsecret setec-system/setec-disk-registry refresh=6h type=kubernetes.io/dockerconfigjson" || return 1
  has "$out" "dockerconfig 123456789012.dkr.ecr.us-east-1.amazonaws.com" || return 1
  has "$out" "operator --disk-registry-secret=setec-disk-registry" || return 1
  has "$out" 'egress {"matchName": "api.ecr.us-east-1.amazonaws.com"}' || return 1
  lacks "$out" "fake " || return 1
}

# assert_kind_chain <render>: the config with no credential is whole, with
# no ECR part.
assert_kind_chain() {
  local out; out="$(chain "$1")" || { echo "the render does not parse"; return 1; }
  has "$out" 'fake setec-system/setec-disk-registry config={"auths":{}}' || return 1
  has "$out" "externalsecret setec-system/setec-disk-registry refresh=6h type=kubernetes.io/dockerconfigjson" || return 1
  has "$out" "dockerconfig from-generator" || return 1
  has "$out" "operator --disk-registry-secret=setec-disk-registry" || return 1
  lacks "$out" "generator " || return 1
  lacks "$out" "setec-disk-push-key" || return 1
  lacks "$out" "egress " || return 1
}

# drop <in> <out> <kind> <name>: the render with one object removed, the
# broken chain of a failing fixture.
drop() {
  python3 - "$@" <<'PY'
import sys, yaml
src, dst, kind, name = sys.argv[1:5]
docs = [d for d in yaml.safe_load_all(open(src)) if d]
keep = [d for d in docs if not (d.get("kind") == kind and d["metadata"]["name"] == name)]
assert len(keep) == len(docs) - 1, f"no {kind}/{name} in the render"
yaml.safe_dump_all(keep, open(dst, "w"))
PY
}

@test "an ECR disk repository renders the push credential chain and its egress, with no value set" {
  render "$WORK/ecr.yaml" --set-string "gibson-workloads.setec.launcher.diskRepo=$ECR_REPO"
  [ -s "$WORK/ecr.yaml" ] || { cat "$WORK/ecr.yaml.err"; false; }
  run assert_ecr_chain "$WORK/ecr.yaml"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: an ECR render with no ECRAuthorizationToken fails the chain check" {
  render "$WORK/ecr-b.yaml" --set-string "gibson-workloads.setec.launcher.diskRepo=$ECR_REPO"
  drop "$WORK/ecr-b.yaml" "$WORK/ecr-broken.yaml" ECRAuthorizationToken setec-disk-registry
  run assert_ecr_chain "$WORK/ecr-broken.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing: generator setec-system/setec-disk-registry"* ]] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: an ECR render with no key pair ExternalSecret fails the chain check" {
  render "$WORK/ecr-c.yaml" --set-string "gibson-workloads.setec.launcher.diskRepo=$ECR_REPO"
  drop "$WORK/ecr-c.yaml" "$WORK/ecr-broken2.yaml" ExternalSecret setec-disk-push-key
  run assert_ecr_chain "$WORK/ecr-broken2.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing: externalsecret setec-system/setec-disk-push-key"* ]] || { echo "$output"; false; }
}

@test "the baseline (kind) renders a registry config with no credential, and no ECR part" {
  render "$WORK/kind.yaml"
  [ -s "$WORK/kind.yaml" ] || { cat "$WORK/kind.yaml.err"; false; }
  run assert_kind_chain "$WORK/kind.yaml"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a kind render with no Fake generator fails the chain check" {
  render "$WORK/kind-b.yaml"
  drop "$WORK/kind-b.yaml" "$WORK/kind-broken.yaml" Fake setec-disk-registry
  run assert_kind_chain "$WORK/kind-broken.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *'missing: fake setec-system/setec-disk-registry'* ]] || { echo "$output"; false; }
}

@test "a registrySecret of the operator renders nothing of the chain" {
  render "$WORK/own.yaml" --set-string "gibson-workloads.setec.launcher.diskBuilder.registrySecret=my-registry"
  run chain "$WORK/own.yaml"
  [ "$status" -eq 0 ]
  [[ "$output" == "operator --disk-registry-secret=my-registry" ]] || { echo "$output"; false; }
}
