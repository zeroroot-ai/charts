#!/usr/bin/env bash
# check-values-no-duplicate-keys.sh — no values file declares a key twice.
#
# YAML keeps the LAST of two identical keys in a mapping and discards the
# first, silently. Neither `helm template` nor `helm lint` nor a golden
# snapshot says a word: the render is valid, it is just not the render the
# file appears to describe.
#
# helm/gibson-crds/values.yaml carried `prometheus-operator-crds:` twice — a
# block of ten per-CRD toggles, and further down a bare `enabled: true`. The
# second won. Every toggle was thrown away on every render since the day they
# were written, so all eight CRDs the platform never instantiates shipped
# anyway: 3.8 MB of schema, and most of the reason `helm install gibson-crds`
# blew the 1 MiB release-record cap and failed on every cluster. The comment
# above the toggles described behaviour the file did not have. Fixing the one
# duplicate took the rendered manifest from 4573 KB to 256 KB and the release
# record from 894 KB to 136 KB. Measured 2026-09-14 (charts#82).
#
# That is the whole defect class: a values file that reads correctly and does
# something else. It is invisible by construction, so it needs a gate.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# scan <file> — print one line per duplicate key, empty when clean.
# PyYAML accepts duplicates by design, so the loader is taught to object.
scan() {
  python3 - "$1" <<'PY'
import sys, yaml

class DupDetector(yaml.SafeLoader):
    pass

def no_duplicates(loader, node, deep=False):
    seen, mapping = {}, {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            print("%s (line %d, first seen on line %d)"
                  % (key, key_node.start_mark.line + 1, seen[key] + 1))
        seen[key] = key_node.start_mark.line
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping

DupDetector.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, no_duplicates)

try:
    for _ in yaml.load_all(open(sys.argv[1]), Loader=DupDetector):
        pass
except yaml.YAMLError as exc:
    # A file that cannot parse is a different failure, and one the render
    # already catches loudly. Say so rather than reporting it as clean.
    sys.stderr.write("cannot parse: %s\n" % exc)
    sys.exit(2)
PY
}

# --- self-test: a planted duplicate must be found -------------------------
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
cat > "$WORK/dup.yaml" <<'YAML'
someChart:
  crds:
    thing:
      enabled: false
other: 1
someChart:
  enabled: true
YAML
if [ -z "$(scan "$WORK/dup.yaml")" ]; then
  echo "✗ self-test broken: a duplicate top-level key must be reported" >&2
  exit 1
fi
cat > "$WORK/clean.yaml" <<'YAML'
someChart:
  enabled: true
  crds:
    thing:
      enabled: false
YAML
if [ -n "$(scan "$WORK/clean.yaml")" ]; then
  echo "✗ self-test broken: a file with no duplicate must report nothing" >&2
  exit 1
fi

# --- every values file in the chart tree ----------------------------------
fail=0
scanned=0
while IFS= read -r f; do
  scanned=$((scanned + 1))
  out="$(scan "$f")" || { echo "✗ $f" >&2; fail=1; continue; }
  if [ -n "$out" ]; then
    fail=1
    printf '✗ %s declares a key twice; YAML keeps the LAST and silently drops the first:\n' "${f#$ROOT/}" >&2
    printf '%s\n' "$out" | sed 's/^/    /' >&2
  fi
# Relative to helm/, so the exclusion means "a vendored subchart" and not
# "every path in a repository that happens to be called charts".
done < <(cd "$ROOT/helm" && find . -name 'values*.yaml' -not -path './*/charts/*' -printf '%P\n' | sed "s|^|$ROOT/helm/|" | sort)

if [ "$fail" = 1 ]; then
  echo "  merge the blocks. Nothing else will tell you: the render stays valid." >&2
  exit 1
fi

printf '✅ self-tests: a planted duplicate is found and a clean file is not; %d values files declare no key twice\n' "$scanned"
