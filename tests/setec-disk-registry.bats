#!/usr/bin/env bats
# The push credential of the setec disk builder (D85, charts#574).
#
# An AWS env keeps its sandbox disks in an ECR repository. The chart then
# renders, in the setec namespace, the key pair ExternalSecret, the
# ECRAuthorizationToken generator and the dockerconfigjson ExternalSecret
# that setec.launcher.diskBuilder.registrySecret names, and the External
# Secrets controller reaches the ECR API of that region only. kind and every
# other registry render none of it.

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
    elif k == "ExternalSecret" and n in ("setec-disk-registry", "setec-disk-push-credential"):
        t = d["spec"]["target"]
        tpl = (t.get("template") or {})
        print(f"externalsecret {ns}/{n} refresh={d['spec']['refreshInterval']} type={tpl.get('type', '-')}")
        for v in (tpl.get("data") or {}).values():
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

@test "an ECR disk repository renders the push credential chain and its egress" {
  render "$WORK/ecr.yaml" \
    --set-string "gibson-workloads.setec.launcher.diskRepo=$ECR_REPO" \
    --set-string "gibson-workloads.setec.launcher.diskBuilder.registrySecret=setec-disk-registry"
  [ -s "$WORK/ecr.yaml" ] || { cat "$WORK/ecr.yaml.err"; false; }
  run chain "$WORK/ecr.yaml"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  echo "$output"
  [[ "$output" == *"generator setec-system/setec-disk-registry region=us-east-1"* ]]
  [[ "$output" == *"externalsecret setec-system/setec-disk-push-credential refresh=60s type=-"* ]]
  [[ "$output" == *"externalsecret setec-system/setec-disk-registry refresh=6h type=kubernetes.io/dockerconfigjson"* ]]
  [[ "$output" == *"dockerconfig 123456789012.dkr.ecr.us-east-1.amazonaws.com"* ]]
  [[ "$output" == *"operator --disk-registry-secret=setec-disk-registry"* ]]
  [[ "$output" == *'egress {"matchName": "api.ecr.us-east-1.amazonaws.com"}'* ]]
}

@test "the baseline (kind) renders no part of the chain" {
  render "$WORK/kind.yaml"
  [ -s "$WORK/kind.yaml" ] || { cat "$WORK/kind.yaml.err"; false; }
  run chain "$WORK/kind.yaml"
  [ "$status" -eq 0 ]
  [ -z "$output" ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: an ECR disk repository with no registrySecret fails the render" {
  run render "$WORK/ecr-nosecret.yaml" \
    --set-string "gibson-workloads.setec.launcher.diskRepo=$ECR_REPO"
  [ "$status" -ne 0 ]
  grep -q "Set registrySecret to setec-disk-registry" "$WORK/ecr-nosecret.yaml.err"
}

@test "FAILING FIXTURE: a registrySecret on a registry that is not ECR fails the render" {
  run render "$WORK/kind-secret.yaml" \
    --set-string "gibson-workloads.setec.launcher.diskBuilder.registrySecret=setec-disk-registry"
  [ "$status" -ne 0 ]
  grep -q "is not on ECR" "$WORK/kind-secret.yaml.err"
}
