#!/usr/bin/env bash
# check-rungs.sh — every rung of the profile ladder renders, and shrinks.
#
# ADR-0090 names five rungs: baseline < developer < CI < staging < production.
# Two of them live in this chart as overlays on the baseline:
#
#   helm install gibson . -f values-baseline.yaml -f values-developer.yaml
#
# A rung overlay is the easiest thing in this repository to break silently. It
# names value paths it does not own, so when a path moves the overlay still
# renders, still passes YAML parsing, and simply stops lowering anything. The
# symptom appears much later, on the node, as a Pending pod with "Insufficient
# cpu". Measured on run 32773702081: the whole platform failed to schedule and
# the post-install hook timed out at 30 minutes.
#
# So this asserts the thing that matters: a rung must actually SHRINK the
# baseline. Rendering is not enough.
#
# WHAT IT DOES NOT DO. It does not require developer and CI to be equal. They
# ship identical content today, and the reason they are two files is that
# either may change later without a rename (ADR-0090). A guard forcing them
# equal would defeat the naming. It REPORTS whether they agree, so a divergence
# is visible on the run that introduces it rather than discovered months later.
#
# The node budget itself is not checked here. That needs the Velero release,
# the metrics-server add-on and a local cluster's own control plane, none of
# which live in this repo; scripts/check-2core-overlay.py in zeroroot-ai/hosted
# counts the whole node.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
CHART="$ROOT/helm/gibson"
BASE="$CHART/values-baseline.yaml"
RUNGS="developer ci"

command -v helm >/dev/null || { echo "helm is required" >&2; exit 2; }
[ -r "$BASE" ] || { echo "check-rungs: no $BASE" >&2; exit 2; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# total_cpu_m <rendered file> — the sum of every container CPU request, in
# millicores. Pod-creating kinds only: a CR the operator expands later is not
# a pod in this render.
total_cpu_m() {
  python3 - "$1" <<'PY'
import sys, yaml
total = 0
for doc in yaml.safe_load_all(open(sys.argv[1], encoding="utf-8")):
    if not doc or doc.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet"):
        continue
    spec = (doc.get("spec") or {}).get("template", {}).get("spec", {})
    replicas = (doc.get("spec") or {}).get("replicas", 1) or 1
    for c in spec.get("containers", []) or []:
        cpu = ((c.get("resources") or {}).get("requests") or {}).get("cpu")
        if cpu is None:
            continue
        cpu = str(cpu)
        total += (int(cpu[:-1]) if cpu.endswith("m") else int(float(cpu) * 1000)) * replicas
print(total)
PY
}

render() {  # render <out> [extra values file]
  local out="$1"; shift
  # The installer inputs sit between the baseline and the rung, so a rung
  # that carries its own (kind's bucket) still wins.
  local args=(-f "$BASE" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml")
  [ $# -gt 0 ] && args+=(-f "$1")
  helm template gibson "$CHART" "${args[@]}" --namespace gibson > "$out" 2>"$out.err" \
    || { echo "✗ check-rungs: the render failed: $(tail -n2 "$out.err")" >&2; return 1; }
}

render "$WORK/baseline.yaml"
base_cpu="$(total_cpu_m "$WORK/baseline.yaml")"
[ "$base_cpu" -gt 0 ] || { echo "✗ check-rungs: the baseline renders 0m of CPU requests, so the measurement itself is broken" >&2; exit 2; }

# --- failing fixture -------------------------------------------------------
# A rung whose value paths no longer match is the failure this guard exists
# for, and it looks exactly like a rung that works: valid YAML, a clean
# render, and nothing lowered. Prove the measurement sees it, on every run.
cat > "$WORK/fixture-stale-paths.yaml" <<'FIXTURE'
# Every path here is wrong, the way a real overlay goes wrong when a value
# moves. It parses, it renders, and it changes nothing.
gibsonWorkloads:
  daemonThatMovedAway:
    resources:
      requests:
        cpu: 1m
FIXTURE
render "$WORK/fixture.yaml" "$WORK/fixture-stale-paths.yaml" || {
  echo "✗ check-rungs self-test: the fixture must RENDER, or it is not testing what a stale overlay does" >&2; exit 2; }
if [ "$(total_cpu_m "$WORK/fixture.yaml")" -lt "$base_cpu" ]; then
  echo "✗ check-rungs self-test: a rung of stale paths lowered the total, so the measurement is not measuring the rung" >&2
  exit 2
fi
echo "✅ self-test: a rung whose paths no longer match renders cleanly and shrinks nothing, and is seen"

fail=0
for rung in $RUNGS; do
  f="$CHART/values-${rung}.yaml"
  if [ ! -r "$f" ]; then
    echo "✗ check-rungs: the ladder names a '${rung}' rung and helm/gibson/values-${rung}.yaml is missing" >&2
    fail=1; continue
  fi
  render "$WORK/${rung}.yaml" "$f" || { fail=1; continue; }
  cpu="$(total_cpu_m "$WORK/${rung}.yaml")"
  if [ "$cpu" -ge "$base_cpu" ]; then
    echo "✗ check-rungs: the ${rung} rung renders ${cpu}m against the baseline's ${base_cpu}m. A rung that does not shrink the baseline is not doing its job; the usual cause is a value path that moved, which no longer matches and silently does nothing." >&2
    fail=1; continue
  fi
  printf '  %-10s %sm of the baseline %sm\n' "$rung" "$cpu" "$base_cpu"
done

[ "$fail" = 0 ] || exit 1

# Report, never enforce.
if diff -q "$WORK/developer.yaml" "$WORK/ci.yaml" >/dev/null 2>&1; then
  echo "  developer and ci render identically, as they are meant to today"
else
  echo "  NOTE: developer and ci no longer render identically. That is allowed"
  echo "        (ADR-0090 keeps them separate so either may change). Say why in"
  echo "        the file that changed, because nothing else records it."
fi

echo "✅ check-rungs: every rung of the ladder renders and shrinks the baseline"
