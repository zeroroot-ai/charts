#!/usr/bin/env bash
# golden.sh — the chart's snapshot test.
#
# Renders every shipped profile, twice: once as a bare cluster and once with
# the Prometheus Operator API present. That second render is not decoration —
# the ServiceMonitors, PrometheusRules and grafana_dashboard ConfigMaps only
# exist when the cluster supplies monitoring.coreos.com/v1, and that gap IS the
# guest-versus-greenfield difference. A snapshot that captured only one state
# would silently stop covering the other.
#
#   scripts/golden.sh update   regenerate helm/testdata/golden/
#   scripts/golden.sh check    fail when the render no longer matches
#
# The chart guard suite was deleted (#1830). This replaces the part that
# mattered: proving a change alters exactly what it claims.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/helm/testdata/golden"
# A profile is one or more values files, layered left to right. The guest
# entry is the brownfield shape: the same vanilla install with the four
# operator-substitution seams off, proving the chart is a guest on a cluster
# that already owns cert-manager, External Secrets, ExternalDNS and CNPG.
PROFILES="values-vanilla.yaml values-vanilla.yaml+values-eks.yaml values-vanilla.yaml+values-guest.yaml"
CAPS="monitoring.coreos.com/v1"

# gibson-crds and gibson-velero are SEPARATE releases, not umbrella
# dependencies, so rendering only helm/gibson never touched them. A YAML break
# inside a CRD description reached a live cluster with every snapshot green.
STANDALONE="gibson-crds gibson-velero"

# The umbrella vendors first-party sub-charts as packaged tarballs, and
# .charts.stamp tracks dependency SOURCES, not those charts' templates. Edit a
# gibson-workloads template and helm renders the STALE tarball while
# chart-deps reports "current". Force the repack or every snapshot is a lie.
repack() {
  rm -f "$ROOT"/helm/gibson/charts/gibson-{workloads,operators,crds,common}-*.tgz \
        "$ROOT"/helm/*/.charts.stamp 2>/dev/null || true
  make -C "$ROOT" chart-deps >/dev/null
}

render() { # $1=profile $2=variant $3=dest
  local args=(--namespace gibson) f
  for f in ${1//+/ }; do args+=(-f "$ROOT/helm/gibson/$f"); done
  [ "$2" = withcaps ] && args+=(--api-versions "$CAPS")
  helm template gibson "$ROOT/helm/gibson" "${args[@]}" >"$3"
}

render_standalone() { # $1=chart $2=dest
  helm template "$1" "$ROOT/helm/$1" --namespace gibson --include-crds >"$2"
}

name_of() { echo "$1" | tr '/+' '__' | sed 's/\.yaml//g'; }

cmd="${1:-check}"
repack
mkdir -p "$OUT"
rc=0
for p in $PROFILES; do
  for v in bare withcaps; do
    f="$OUT/$(name_of "$p").$v.yaml"
    tmp="$(mktemp)"
    if ! render "$p" "$v" "$tmp"; then
      echo "  RENDER FAILED  $p ($v)"; rm -f "$tmp"; rc=1; continue
    fi
    if [ "$cmd" = update ]; then
      mv "$tmp" "$f"
      printf '  updated  %-34s %5s resources\n' "$(basename "$f")" "$(grep -c '^kind:' "$f")"
    else
      if [ ! -f "$f" ]; then echo "  MISSING GOLDEN  $(basename "$f") — run: scripts/golden.sh update"; rc=1
      elif ! diff -q "$f" "$tmp" >/dev/null; then
        echo "  CHANGED  $(basename "$f")"
        diff "$f" "$tmp" | grep -E '^[<>] (kind|  name):' | sort | uniq -c | sed 's/^/      /' || true
        rc=1
      else
        printf '  ok       %-34s %5s resources\n' "$(basename "$f")" "$(grep -c '^kind:' "$f")"
      fi
      rm -f "$tmp"
    fi
  done
done
for c in $STANDALONE; do
  f="$OUT/$c.yaml"; tmp="$(mktemp)"
  if ! render_standalone "$c" "$tmp"; then
    echo "  RENDER FAILED  $c"; rm -f "$tmp"; rc=1; continue
  fi
  if [ "$cmd" = update ]; then
    mv "$tmp" "$f"; printf '  updated  %-34s %5s resources\n' "$c.yaml" "$(grep -c '^kind:' "$f")"
  elif [ ! -f "$f" ]; then echo "  MISSING GOLDEN  $c.yaml"; rc=1; rm -f "$tmp"
  elif ! diff -q "$f" "$tmp" >/dev/null; then echo "  CHANGED  $c.yaml"; rc=1; rm -f "$tmp"
  else printf '  ok       %-34s %5s resources\n' "$c.yaml" "$(grep -c '^kind:' "$f")"; rm -f "$tmp"; fi
done

[ "$cmd" = check ] && [ $rc -ne 0 ] && echo "
Golden snapshots differ. If the change is intended:  scripts/golden.sh update"
exit $rc
