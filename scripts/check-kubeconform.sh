#!/usr/bin/env bash
# check-kubeconform.sh — the rendered umbrella validates against the
# Kubernetes and CRD schemas.
#
# Rebuilds kubeconform-umbrella, the last of the fifteen chart guards lost in
# the 2026-09-04 split (charts#17). `helm template` proves a template renders
# YAML; it proves nothing about whether the API server will accept it. A
# field at the wrong depth, a string where an integer belongs, an unknown key
# under strict validation: each reaches a cluster as a rejected apply, or as
# a silently ignored setting. kubeconform validates every rendered document:
# core kinds against the pinned Kubernetes version, custom resources against
# schemas converted from the CRDs the two CRD charts install (gibson-crds,
# gibson-operator-crds), so a Tenant, a Certificate, a CNPG Cluster and an
# ExternalSecret are judged by the schema the cluster will enforce.
#
#   check-kubeconform.sh             exit 1 on a schema violation, 0 when valid
#   check-kubeconform.sh --selftest  prove a Deployment with replicas: "two" is refused, then run
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
K8S_VERSION="${K8S_VERSION:-1.31.0}"   # kind runs 1.31 (helm/kind-config.yaml); the oldest control plane the chart installs on
command -v kubeconform >/dev/null || { echo "SETUP FAILURE: kubeconform is required (ci.yml installs the pinned release; locally: https://github.com/yannh/kubeconform)" >&2; exit 2; }
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
mkdir -p "$WORK/schemas"

# CRDs → JSON schemas kubeconform can load, laid out as
# <group>/<kind lowercase>_<version>.json, the shape its -schema-location
# template expects.
crd_schemas() {
  { helm template gibson-crds "$ROOT/helm/gibson-crds" --namespace gibson --include-crds
    helm template gibson-operator-crds "$ROOT/helm/gibson-operator-crds" --namespace gibson --include-crds
  } > "$WORK/crds.yaml"
  python3 - "$WORK/schemas" "$WORK/crds.yaml" <<'PY'
import json, os, sys, yaml
out, src = sys.argv[1], sys.argv[2]
n = 0
for d in yaml.safe_load_all(open(src)):
    if not d or d.get("kind") != "CustomResourceDefinition":
        continue
    group = d["spec"]["group"]; kind = d["spec"]["names"]["kind"].lower()
    for v in d["spec"].get("versions") or []:
        schema = ((v.get("schema") or {}).get("openAPIV3Schema"))
        if not schema:
            continue
        os.makedirs(os.path.join(out, group), exist_ok=True)
        with open(os.path.join(out, group, f"{kind}_{v['name']}.json"), "w") as f:
            json.dump(schema, f)
        n += 1
print(f"crd schemas: {n}", file=sys.stderr)
PY
}

validate() { # <manifest file>
  kubeconform -strict -summary -kubernetes-version "$K8S_VERSION" \
    -schema-location default \
    -schema-location "$WORK/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json" \
    -ignore-missing-schemas \
    "$1"
}

crd_schemas

if [ "${1:-}" = "--selftest" ]; then
  # THE FIXTURE THIS EXISTS FOR: a core kind with a field of the wrong type.
  printf 'apiVersion: apps/v1\nkind: Deployment\nmetadata: {name: x}\nspec:\n  replicas: "two"\n  selector: {matchLabels: {a: b}}\n  template: {metadata: {labels: {a: b}}, spec: {containers: [{name: c, image: i}]}}\n' > "$WORK/bad.yaml"
  if validate "$WORK/bad.yaml" >/dev/null 2>&1; then echo "SELFTEST FAIL: replicas: \"two\" must be refused"; exit 1; fi
  # A custom resource with an unknown field under strict validation.
  printf 'apiVersion: gibson.zeroroot.ai/v1alpha1\nkind: Tenant\nmetadata: {name: t}\nspec: {displayName: T, owner: o@example.test, notAField: 1}\n' > "$WORK/badcr.yaml"
  if validate "$WORK/badcr.yaml" >/dev/null 2>&1; then echo "SELFTEST FAIL: an unknown Tenant field must be refused under the CRD schema"; exit 1; fi
  echo "OK: a mistyped core field and an unknown CRD field are refused"
fi

helm template gibson "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" --namespace gibson > "$WORK/umbrella.yaml"
helm template velero "$ROOT/helm/gibson-velero" -f "$ROOT/helm/testdata/render-inputs/gibson-velero.yaml" --namespace velero > "$WORK/velero.yaml"
validate "$WORK/umbrella.yaml"
validate "$WORK/velero.yaml"
echo "✓ kubeconform: the umbrella and velero renders validate against Kubernetes ${K8S_VERSION} and the installed CRD schemas"
