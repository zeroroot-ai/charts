#!/usr/bin/env bash
# check-alert-rules-test.sh: the alert rules fire as their tests say (charts#446).
#
# For each tests/alerts/<name>.test.yaml it renders the umbrella with the
# monitoring CRDs, takes the PrometheusRule <release>-<name>-alerts, writes its
# groups to <name>.rules.yaml next to a copy of the test, and runs
# `promtool test rules`. A test whose rule object has another name states it
# in a line `# rule: <object name>`. It fails when promtool is absent, when a
# test names a rule object that the render does not hold, and when a test
# fails.
#
# A test can name a metric that the render builds from an Envoy stat_prefix
# with the placeholder @ratelimit:<mode>:<n>@ (charts#736). The harness reads
# every ratelimit filter of the rendered Envoy config: <mode> is fail_open
# (failure_mode_deny false) or fail_closed (true), and <n> counts the filters
# of that mode in the order of their sorted stat_prefix values. A placeholder
# with no filter fails, and a rendered ratelimit filter that no test names
# fails, so a renamed or a new filter cannot leave an alert untested.
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

# placeholders <render file> <test file|--coverage test files...>
# Prints the test with each @ratelimit:<mode>:<n>@ replaced by the stat_prefix
# of that rendered ratelimit filter. With --coverage it prints nothing and
# fails when a rendered ratelimit filter is named by no test.
placeholders() {
  python3 - "$@" <<'PY'
import re, sys, yaml
render, args = sys.argv[1], sys.argv[2:]
RL = "type.googleapis.com/envoy.extensions.filters.http.ratelimit.v3.RateLimit"
found = {"fail_open": set(), "fail_closed": set()}

def walk(node):
    if isinstance(node, dict):
        tc = node.get("typed_config")
        if isinstance(tc, dict) and tc.get("@type") == RL and "stat_prefix" in tc:
            mode = "fail_closed" if tc.get("failure_mode_deny") is True else "fail_open"
            found[mode].add(tc["stat_prefix"])
        for v in node.values():
            walk(v)
    elif isinstance(node, list):
        for v in node:
            walk(v)

for d in yaml.safe_load_all(open(render)):
    if not d or d.get("kind") != "ConfigMap":
        continue
    for v in (d.get("data") or {}).values():
        if RL not in v:
            continue
        for doc in yaml.safe_load_all(v):
            walk(doc)
prefixes = {m: sorted(v) for m, v in found.items()}
if not any(prefixes.values()):
    print("FAIL: the render holds no Envoy ratelimit filter: the placeholders name nothing", file=sys.stderr)
    sys.exit(1)
PH = re.compile(r"@ratelimit:(fail_open|fail_closed):(\d+)@")
if args[0] == "--coverage":
    named = set()
    for t in args[1:]:
        named |= {(m, int(n)) for m, n in PH.findall(open(t).read())}
    missing = [(m, i, p) for m, ps in prefixes.items() for i, p in enumerate(ps) if (m, i) not in named]
    for m, i, p in missing:
        print(f"FAIL: the rendered ratelimit filter with stat_prefix {p} ({m}) is named by no "
              f"@ratelimit:{m}:{i}@ in tests/alerts", file=sys.stderr)
    sys.exit(1 if missing else 0)
text = open(args[0]).read()

def sub(m):
    mode, n = m.group(1), int(m.group(2))
    if n >= len(prefixes[mode]):
        print(f"FAIL: {args[0]} names @ratelimit:{mode}:{n}@, and the render holds "
              f"{len(prefixes[mode])} {mode} ratelimit filter(s)", file=sys.stderr)
        sys.exit(1)
    return prefixes[mode][n]

sys.stdout.write(PH.sub(sub, text))
PY
}

run_one() { # <render file> <test file> <workdir> [sed expression applied to the rules]
  local name object; name="$(basename "$2" .test.yaml)"
  object="$(sed -n 's/^# rule: *\([^ ]*\) *$/\1/p' "$2" | head -1)"
  # The caller runs this in an if, where set -e is off: each step returns.
  rules "$1" "${object:-gibson-${name}-alerts}" "$3/${name}.rules.yaml" || return 1
  if [ -n "${4:-}" ]; then sed -i "$4" "$3/${name}.rules.yaml" || return 1; fi
  placeholders "$1" "$2" > "$3/.test.tmp" || return 1
  mv "$3/.test.tmp" "$3/$(basename "$2")"
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
  # A test that names a rule object the render does not hold must fail.
  mkdir "$work/named"
  sed 's/^# rule: .*/# rule: gibson-no-such-rules/' tests/alerts/tenant-operator.test.yaml > "$work/named/tenant-operator.test.yaml"
  if run_one "$work/render.yaml" "$work/named/tenant-operator.test.yaml" "$work/named" 2>/dev/null; then
    echo "SELFTEST FAIL: a test that names an absent rule object passed" >&2; exit 1
  fi
  # charts#736: each edge rate-limit alert fails its test when its expression
  # no longer selects the metric of one filter, or when a stat_prefix moves.
  for broken in 's/ratelimit_public_per_client_failure/ratelimit_public_per_clientx_failure/' \
                's/ratelimit_api_per_client_failure/ratelimit_api_per_clientx_failure/' \
                's/public_signup_error/public_signup_errors/' \
                's/public_signup_error\[5m\])) > 0/public_signup_error[5m])) > 5/'; do
    d="$(mktemp -d -p "$work")"
    if run_one "$work/render.yaml" tests/alerts/ratelimit.test.yaml "$d" "$broken"; then
      echo "SELFTEST FAIL: a broken rate-limit alert expression passed its test ($broken)" >&2; exit 1
    fi
  done
  for prefix in public_per_client api_per_client public_signup; do
    d="$(mktemp -d -p "$work")"
    sed "s/stat_prefix: ${prefix}\$/stat_prefix: ${prefix}_renamed/" "$work/render.yaml" > "$d/render.yaml"
    cmp -s "$d/render.yaml" "$work/render.yaml" && { echo "SELFTEST FAIL: the render holds no stat_prefix ${prefix}" >&2; exit 1; }
    if run_one "$d/render.yaml" tests/alerts/ratelimit.test.yaml "$d" 2>/dev/null; then
      echo "SELFTEST FAIL: the rate-limit test passed with the stat_prefix ${prefix} renamed" >&2; exit 1
    fi
  done
  # A ratelimit filter that no test names fails the coverage step.
  printf '%s\n' '# names no ratelimit filter' > "$work/none.test.yaml"
  if placeholders "$work/render.yaml" --coverage "$work/none.test.yaml" 2>/dev/null; then
    echo "SELFTEST FAIL: a ratelimit filter that no test names passed the coverage step" >&2; exit 1
  fi
  echo "  ✓ selftest: a rule with a moved threshold fails its promtool test, and a test that names an absent rule object fails"
  echo "  ✓ selftest: a broken rate-limit alert expression, a renamed stat_prefix and an unnamed ratelimit filter each fail"
  exit 0
fi

placeholders "$work/render.yaml" --coverage "${tests[@]}"
for t in "${tests[@]}"; do
  d="$work/$(basename "$t" .test.yaml)"; mkdir "$d"
  if ! run_one "$work/render.yaml" "$t" "$d"; then
    echo "FAIL: $t" >&2
    (cd "$d" && promtool test rules "$(basename "$t")") >&2 || true
    exit 1
  fi
done
echo "  ✓ alert-rules-test: ${#tests[@]} promtool rule test(s) pass on the rendered rules"
