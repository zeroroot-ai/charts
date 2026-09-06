#!/usr/bin/env bash
# render-diff.sh — the migration's verification harness.
#
# The chart guard suite was deleted (deploy#1830) because too much of it was
# blind or false. This replaces the part that actually mattered: proving a
# change removes exactly what it claims and nothing else.
#
# It renders every profile from a BASELINE ref and from the working tree, then
# reports the resource-level delta. A change is correct when the delta is the
# set the commit message claims.
#
# Usage:
#   scripts/render-diff.sh [BASELINE_REF]      # default: origin/main
#
# Env:
#   PROFILES   space-separated values files, relative to helm/gibson
#              default: the four that ship
set -euo pipefail

BASE_REF="${1:-origin/main}"
PROFILES="${PROFILES:-values-vanilla.yaml values-eks.yaml values-gke.yaml values-aks.yaml values-guest.yaml}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

BASE="$WORK/base"
git -C "$ROOT" worktree add --detach "$BASE" "$BASE_REF" >/dev/null 2>&1
cleanup() { git -C "$ROOT" worktree remove "$BASE" --force >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

# CRITICAL: the umbrella vendors the FIRST-PARTY sub-charts as packaged
# tarballs, and the .charts.stamp prerequisites track dependency SOURCES, not
# those charts' templates. Edit a gibson-workloads template and `make
# chart-deps` reports "current" while helm renders the stale tarball — the
# comparison then silently proves nothing. Force a repack on both sides.
repack() {
  rm -f "$1"/helm/gibson/charts/gibson-{workloads,operators,crds,common}-*.tgz \
        "$1"/helm/*/.charts.stamp 2>/dev/null || true
  make -C "$1" chart-deps >/dev/null 2>&1
}
echo "▶ vendoring baseline ($BASE_REF)"
repack "$BASE" || { echo "baseline chart-deps FAILED"; exit 1; }
echo "▶ vendoring working tree"
repack "$ROOT" || { echo "working-tree chart-deps FAILED"; exit 1; }

render() { # $1=root $2=profile $3=out
  helm template gibson "$1/helm/gibson" -f "$1/helm/gibson/$2" --namespace gibson >"$3" 2>"$3.err"
}

# kinds+names, one per line, so the diff is resource-level not text-level
index() { awk '/^kind:/{k=$2} /^  name:/{if(k){print k"/"$2; k=""}}' "$1" | sort -u; }

rc=0
for p in $PROFILES; do
  ok_b=1; ok_n=1
  render "$BASE" "$p" "$WORK/b.yaml" || ok_b=0
  render "$ROOT" "$p" "$WORK/n.yaml" || ok_n=0
  if [ "$ok_b" = 0 ] || [ "$ok_n" = 0 ]; then
    printf '  %-26s base=%s new=%s  RENDER FAILED\n' "$p" "$ok_b" "$ok_n"
    [ "$ok_n" = 0 ] && tail -3 "$WORK/n.yaml.err" | sed 's/^/      /'
    rc=1; continue
  fi
  index "$WORK/b.yaml" >"$WORK/b.idx"; index "$WORK/n.yaml" >"$WORK/n.idx"
  removed="$(comm -23 "$WORK/b.idx" "$WORK/n.idx" || true)"
  added="$(comm -13 "$WORK/b.idx" "$WORK/n.idx" || true)"
  nb=$(grep -c '^kind:' "$WORK/b.yaml" || true); nn=$(grep -c '^kind:' "$WORK/n.yaml" || true)
  printf '  %-26s %4s -> %-4s  -%s +%s\n' "$p" "$nb" "$nn" \
    "$(printf '%s' "$removed" | grep -c . || true)" "$(printf '%s' "$added" | grep -c . || true)"
  [ -n "$removed" ] && printf '%s\n' "$removed" | sed 's/^/      - /'
  [ -n "$added" ]   && printf '%s\n' "$added"   | sed 's/^/      + /'
done
exit $rc
