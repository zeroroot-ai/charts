#!/usr/bin/env bash
# check-anonymous-pull.sh — every published chart pulls with NO credential.
#
# WHY THIS EXISTS
#
# A new package on ghcr is PRIVATE by default. `gibson-operator-crds` was
# published for the first time at 0.127.2 and stayed private while the other
# three charts were public. Two things broke and neither said so.
#
#   The staging Argo Application could not pull the chart and sat in
#   ComparisonError. Nothing on the running cluster degraded, because those
#   CRDs were installed before the split and carry a keep policy, so only a
#   cluster built from scratch would have noticed.
#
#   Every stranger install broke. The documented claim is that the only tool an
#   installer needs is helm. That claim was false for as long as the package
#   stayed private.
#
# The publish workflow could not see either, because its own pull carries the
# workflow's token. A pull with a credential cannot answer "can someone with no
# credential get this?"
#
# TWO WAYS THIS TEST LIES TO YOU, both met the first time it was run by hand:
#
#   A logged-in registry config. helm reads ~/.config/helm/registry/config.json
#   and uses whatever is in it. Pointing HELM_REGISTRY_CONFIG at a file that
#   does NOT EXIST does not help: helm falls back and the pull succeeds with
#   real credentials while looking anonymous. The file must exist and must
#   contain an empty auth set.
#
#   A warm cache. helm answers from its repository cache without touching the
#   registry, so a chart pulled earlier with a token pulls again with none. The
#   whole helm home has to be fresh, not just the registry config.
#
# So this builds a throwaway HELM_*_HOME and an empty-but-present registry
# config, and pulls there.
#
# IT CANNOT FIX WHAT IT FINDS. Package visibility is a UI setting and the REST
# API has no endpoint for it:
#
#   $ gh api -X PATCH /orgs/zeroroot-ai/packages/container/charts%2Fgibson -f visibility=public
#   {"message": "Not Found", "status": "404"}
#
# so the failure names the package and the URL an owner opens.
#
# Usage: check-anonymous-pull.sh <version> <chart>...
set -euo pipefail

VERSION="${1:?usage: check-anonymous-pull.sh <version> <chart>...}"; shift
[ $# -gt 0 ] || { echo "check-anonymous-pull: name at least one chart" >&2; exit 2; }
REGISTRY="${REGISTRY:-ghcr.io/zeroroot-ai/charts}"
ORG="${ORG:-zeroroot-ai}"

command -v helm >/dev/null || { echo "helm is required" >&2; exit 2; }

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT

# anon_home — a fresh helm home with an empty, PRESENT registry config.
anon_home() {
  local d="$1"
  mkdir -p "$d/cache" "$d/config" "$d/data" "$d/out"
  printf '{"auths":{}}' > "$d/config/registry.json"
}

# pull_anon <dir> <chart> — pull with no credential and no cache. Stdout and
# stderr land in <dir>/pull.log for the caller to read.
pull_anon() {
  local d="$1" chart="$2"
  env -u DOCKER_CONFIG \
      HELM_REGISTRY_CONFIG="$d/config/registry.json" \
      HELM_CACHE_HOME="$d/cache" \
      HELM_CONFIG_HOME="$d/config" \
      HELM_DATA_HOME="$d/data" \
    helm pull "oci://${REGISTRY}/${chart}" --version "$VERSION" \
      --destination "$d/out" >"$d/pull.log" 2>&1
}

# --- self-tests ------------------------------------------------------------
# A guard that cannot fail is worse than no guard, and this one runs where a
# false pass is invisible: a green publish that shipped a private chart.

anon_home "$WORK/probe"
# 1. The isolation is real: the config exists and grants nothing.
[ -s "$WORK/probe/config/registry.json" ] \
  || { echo "✗ self-test: the anonymous registry config must EXIST — helm falls back to the real one when it does not" >&2; exit 2; }
grep -q '"auths":{}' "$WORK/probe/config/registry.json" \
  || { echo "✗ self-test: the anonymous registry config must grant nothing" >&2; exit 2; }

# 2. The pull path can fail. A name that was never published must not pass.
if pull_anon "$WORK/probe" "definitely-not-a-published-chart"; then
  echo "✗ self-test: a chart that does not exist pulled successfully, so this check cannot fail and proves nothing" >&2
  exit 2
fi
echo "✅ self-test: the anonymous home grants nothing, and an unpublished name fails"

# --- the check -------------------------------------------------------------
fail=0
for chart in "$@"; do
  d="$WORK/$chart"; anon_home "$d"
  if pull_anon "$d" "$chart"; then
    printf '  ✓ %-24s pulls with no credential\n' "$chart"
  else
    fail=1
    printf '✗ %s cannot be pulled anonymously at %s\n' "$chart" "$VERSION" >&2
    sed 's/^/      /' "$d/pull.log" >&2
    printf '  A package is PRIVATE by default on its first publish. Open:\n' >&2
    printf '    https://github.com/orgs/%s/packages/container/charts%%2F%s/settings\n' "$ORG" "$chart" >&2
    printf '  and set visibility to Public, matching the other charts. There is no\n' >&2
    printf '  REST endpoint for this, so it is an owner action in the UI.\n' >&2
  fi
done

[ "$fail" = 0 ] || {
  echo "  Until this passes, 'the only thing an installer needs is helm' is false." >&2
  exit 1
}
printf '✅ check-anonymous-pull: all %d published chart(s) pull at %s with no credential\n' "$#" "$VERSION"
