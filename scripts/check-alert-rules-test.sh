#!/usr/bin/env bash
# check-alert-rules-test.sh: the alert rules fire as their tests say (charts#446).
#
# For each tests/alerts/<name>.test.yaml it renders the umbrella with the
# monitoring CRDs, takes the PrometheusRule <release>-<name>-alerts, writes its
# groups to <name>.rules.yaml next to a copy of the test, and runs
# `promtool test rules`. It fails when promtool is absent, when a test names a
# rule object that the render does not hold, and when a test fails.
#
#   check-alert-rules-test.sh             run every test
#   check-alert-rules-test.sh --selftest  prove a broken rule fails its test
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v promtool >/dev/null || { echo "FAIL: promtool is not on PATH; the alert tests cannot run" >&2; exit 1; }

render() {
  helm template gibson helm/gibson --namespace gibson \
    -f helm/gibson/values-baseline.yaml -f helm/testdata/render-inputs/gibson.yaml \
    --api-versions monitoring.coreos.com/v1
}

# rules <render file> <rule object name> <out file>
rules() {
  python3 - "$1" "$2" "$3" <<'PY'
import sys, yaml
src, name, out = sys.argv[1:]
for d in yaml.safe_load_all(open(src)):
    if d and d.get("kind") == "PrometheusRule" and d["metadata"]["name"] == name:
        yaml.safe_dump({"groups": d["spec"]["groups"]}, open(out, "w"))
        sys.exit(0)
print(f"FAIL: the render holds no PrometheusRule {name}", file=sys.stderr)
sys.exit(1)
PY
}

run_one() { # <render file> <test file> <workdir> [sed expression applied to the rules]
  local name; name="$(basename "$2" .test.yaml)"
  rules "$1" "gibson-${name}-alerts" "$3/${name}.rules.yaml"
  [ -n "${4:-}" ] && sed -i "$4" "$3/${name}.rules.yaml"
  cp "$2" "$3/"
  (cd "$3" && promtool test rules "$(basename "$2")" >/dev/null 2>&1)
}

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
render > "$work/render.yaml"
tests=(tests/alerts/*.test.yaml)
[ -e "${tests[0]}" ] || { echo "FAIL: no test in tests/alerts: this check is blind" >&2; exit 1; }

if [ "${1:-}" = "--selftest" ]; then
  # A rule whose threshold moved must fail its test.
  mkdir "$work/self"
  if run_one "$work/render.yaml" tests/alerts/audit-export.test.yaml "$work/self" 's/> 3600/> 999999/'; then
    echo "SELFTEST FAIL: a lag rule with a moved threshold passed its test" >&2; exit 1
  fi
  echo "  ✓ selftest: a rule with a moved threshold fails its promtool test"
  exit 0
fi

for t in "${tests[@]}"; do
  d="$work/$(basename "$t" .test.yaml)"; mkdir "$d"
  if ! run_one "$work/render.yaml" "$t" "$d"; then
    echo "FAIL: $t" >&2
    (cd "$d" && promtool test rules "$(basename "$t")") >&2 || true
    exit 1
  fi
done
echo "  ✓ alert-rules-test: ${#tests[@]} promtool rule test(s) pass on the rendered rules"
