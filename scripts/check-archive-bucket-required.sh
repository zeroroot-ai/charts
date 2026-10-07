#!/usr/bin/env bash
# check-archive-bucket-required.sh — no profile ships a default archive
# bucket, and the render refuses to go without one.
#
# The umbrella used to default platformPostgres.backup.destinationPath to
# kind's stage-0 bucket on the kind MinIO address, and gibson-velero
# defaulted bucket.name the same way. The supported self-hosted profile
# therefore archived a customer's WAL to the vendor's dev bucket address,
# the documented restore path recovered nothing, and the `required` guard
# in the Cluster template could never fire because a default always
# satisfied it. A guard that cannot fail is worse than no guard. This one
# proves both guards fire, and that the render fixture satisfies them.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
fail=0

must_fail() { # <label> <expected message fragment> <helm args...>
  local label="$1" want="$2"; shift 2
  if helm template gibson "$@" --namespace gibson > /dev/null 2> "$WORK/err"; then
    echo "❌ $label rendered with no bucket; the required guard did not fire"; fail=1
  elif ! grep -qE -- "$want" "$WORK/err"; then
    echo "❌ $label failed, but not on the bucket: $(tail -n1 "$WORK/err")"; fail=1
  fi
}
must_pass() { # <label> <helm args...>
  local label="$1"; shift
  helm template gibson "$@" --namespace gibson > /dev/null 2> "$WORK/err" \
    || { echo "❌ $label must render: $(tail -n1 "$WORK/err")"; fail=1; }
}

# The other install inputs with no default (ADR-0166): the registry and the
# public key of the sandbox disks. Given here so the one missing input is the
# bucket, and the render refuses on that one.
SETEC_INPUTS=(
  --set-string "gibson-workloads.setec.launcher.diskRepo=registry.example.com/setec-disks"
  --set-string "gibson-workloads.setec.launcher.diskBuilder.publicKeys[0]=ExAmPlEpUbLiCkEy0000000000000000000000000000="
)
# THE FIXTURE THIS EXISTS FOR: the baseline alone has no bucket and must refuse.
# Two values name the durable bucket: the WAL archive and the audit export
# (charts#446). Either required guard may fire first.
must_fail "helm/gibson baseline alone" "destinationPath is REQUIRED|auditExport.bucket is REQUIRED" \
  "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" "${SETEC_INPUTS[@]}"
must_fail "helm/gibson baseline with an archive bucket only" "auditExport.bucket is REQUIRED" \
  "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" "${SETEC_INPUTS[@]}" \
  --set "platformPostgres.backup.destinationPath=s3://example-durable-bucket/backups/postgres/"
must_fail "helm/gibson with two different buckets" "differ: both name the one durable bucket" \
  "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
  --set "gibson-workloads.gibson.auditExport.bucket=another-bucket"
must_fail "helm/gibson-velero alone" "bucket.name is required" \
  "$ROOT/helm/gibson-velero"
# The installer's inputs satisfy both.
must_pass "helm/gibson baseline + render inputs" \
  "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml"
must_pass "helm/gibson-velero + render inputs" \
  "$ROOT/helm/gibson-velero" -f "$ROOT/helm/testdata/render-inputs/gibson-velero.yaml"
# kind's rungs carry kind's bucket, so baseline + rung renders on its own.
for rung in developer ci; do
  must_pass "helm/gibson baseline + $rung rung" \
    "$ROOT/helm/gibson" -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/gibson/values-$rung.yaml" "${SETEC_INPUTS[@]}"
  must_pass "helm/gibson-velero $rung rung" \
    "$ROOT/helm/gibson-velero" -f "$ROOT/helm/gibson-velero/values-$rung.yaml"
done
# And the defaults name nobody's bucket.
if grep -nE '^\s*(destinationPath|endpointURL|name|endpoint|bucket): *"(s3://gibson-kind|gibson-kind|http://172\.18\.255\.250).*"' \
     "$ROOT/helm/gibson/values.yaml" "$ROOT/helm/gibson-velero/values.yaml" "$ROOT/helm/gibson-workloads/values.yaml"; then
  echo "❌ a chart default names kind's stage-0 bucket or MinIO address"; fail=1
fi

[ "$fail" -eq 0 ] && echo "✓ archive-bucket-required: no default bucket, both guards fire, the rungs and the render inputs satisfy them"
exit "$fail"
