# shellcheck shell=bash
# cluster-probe.sh — shared, FAIL-CLOSED preflight for live-cluster verification
# suites (deploy#1039, deploy#1041).
#
# Sourced, never executed:
#   source "$(dirname "$0")/lib/cluster-probe.sh"
#
# WHY THIS EXISTS
# ---------------
# The failure mode this library is built to prevent is a verification suite that
# reports success when it verified nothing. The anti-pattern is already in the
# tree — scripts/smoke-auth-chain.sh:95-99 exits 0 ("gracefully skipping") when
# the workload under test is absent, so a cluster missing ext-authz entirely
# produces the same green output as a healthy one.
#
# Every probe here does the inverse: an absent cluster, an absent namespace, an
# absent workload, or an indeterminate deployment profile is exit 2. There is no
# code path in this file that returns success without having established the
# thing it was asked to establish.
#
# Exit-code convention, matching scripts/smoke-signup.sh:
#   0 — all assertions passed
#   1 — an assertion failed (the suite ran and found a real defect)
#   2 — preflight failed (the suite could not run; NOT a pass)
#
# The distinction between 1 and 2 is load-bearing. A caller that cannot tell
# "the product is broken" from "I never reached the product" cannot be trusted
# as a gate.

# ---------------------------------------------------------------------------
# Output helpers — same verbs and colours as scripts/verify-profile.sh
# ---------------------------------------------------------------------------
GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YELLOW=$'\033[0;33m'; CYAN=$'\033[0;36m'; NC=$'\033[0m'

fails=0
pass() { printf '%s[PASS]%s %s\n' "$GREEN" "$NC" "$1"; }
fail() { printf '%s[FAIL]%s %s\n' "$RED" "$NC" "$1"; fails=$((fails + 1)); }
info() { printf '%s[info]%s %s\n' "$YELLOW" "$NC" "$1"; }
step() { printf '\n%s=== %s ===%s\n' "$CYAN" "$1" "$NC"; }

# preflight_die — the only way this library reports "could not run".
preflight_die() {
  printf '%s[PREFLIGHT]%s %s\n' "$RED" "$NC" "$1" >&2
  printf '%s[PREFLIGHT]%s exiting 2 — this is NOT a pass. The suite did not run.\n' "$RED" "$NC" >&2
  exit 2
}

# ---------------------------------------------------------------------------
# probe_cluster <namespace>
#
# Establishes that a cluster exists, is reachable, and carries the namespace.
# Also refuses the customer cluster outright, matching the guard in
# test/preflight/kind-preflight.sh:25-29 and test/smoke/cutover-smoke.sh:197-208.
# ---------------------------------------------------------------------------
probe_cluster() {
  local ns="$1"

  command -v kubectl >/dev/null 2>&1 \
    || preflight_die "kubectl not on PATH"

  local ctx
  ctx="$(kubectl config current-context 2>/dev/null || true)"
  [ -n "$ctx" ] \
    || preflight_die "no current kubectl context — there is no cluster to verify against"

  case "$ctx" in
    kind-gibson-customer|gibson-customer)
      preflight_die "refusing to run against the customer cluster (context: $ctx)"
      ;;
  esac

  kubectl cluster-info >/dev/null 2>&1 \
    || preflight_die "kubectl context '$ctx' is configured but unreachable — no live cluster"

  kubectl get ns "$ns" >/dev/null 2>&1 \
    || preflight_die "namespace '$ns' does not exist on context '$ctx'"

  pass "cluster reachable (context=$ctx, namespace=$ns)"
}

# ---------------------------------------------------------------------------
# require_workload <namespace> <resource> <name> <why>
#
# Absent workload is exit 2, never a skip. If the thing under test is not
# deployed, the suite has established nothing and must say so.
# ---------------------------------------------------------------------------
require_workload() {
  local ns="$1" res="$2" name="$3" why="$4"
  kubectl -n "$ns" get "$res" "$name" >/dev/null 2>&1 \
    || preflight_die "$res/$name absent in ns/$ns — cannot verify $why"
  pass "$res/$name present"
}

# ---------------------------------------------------------------------------
# detect_profile <namespace>
#
# Echoes "saas" or "self-hosted". Exits 2 when indeterminate.
#
# The discriminator is the presence of the SaaS-only entitlements-svc Service.
# Per ADR-0006 the SaaS overlay is a separate set of charts composed on top of
# the core umbrella, and entitlements-svc is one of its deployables — so its
# Service existing is the cleanest deployment-profile signal available
# in-cluster.
#
# It used to be www-svc. That stopped working when the marketing site became an
# off-cluster surface (ADR-0009): www-svc exists in NEITHER audience now, so
# every cluster would have reported "self-hosted" and every SaaS suite would
# have refused to run — silently, because require_profile skips rather than
# fails on a mismatch.
# ---------------------------------------------------------------------------
# NOTE ON SUBSHELLS: this function and resolve_edge below are called inside
# command substitution, where `exit` would only terminate the subshell and let
# the caller carry on with an empty value. They therefore RETURN non-zero and
# leave the dying to the caller. (This was a real bug: an unresolvable edge used
# to leave EDGE empty and run every probe against nothing, turning "could not
# run" into "ran and found 8 defects".)
detect_profile() {
  local ns="$1" svc

  svc="$(kubectl -n "$ns" get svc -o name 2>/dev/null | grep -c 'entitlements-svc')"
  if ! kubectl -n "$ns" get svc -o name >/dev/null 2>&1; then
    return 1
  fi

  if [ "${svc:-0}" -gt 0 ]; then
    echo "saas"
  else
    echo "self-hosted"
  fi
}

# ---------------------------------------------------------------------------
# require_profile <namespace> <expected>
#
# Refuses to run a SaaS suite against a self-hosted cluster and vice versa.
#
# Without this, a SaaS suite's assertions would run green-ish against a
# self-hosted cluster where the SaaS surfaces are *correctly* absent, and a
# self-hosted suite's "surface is unrouted" checks would pass trivially
# anywhere the SaaS deployable happens to be down. Asserting the profile first
# is what makes each suite's results mean something.
# ---------------------------------------------------------------------------
require_profile() {
  local ns="$1" want="$2" got
  got="$(detect_profile "$ns")" \
    || preflight_die "could not enumerate Services in ns/$ns — deployment profile indeterminate"
  [ "$got" = "$want" ] \
    || preflight_die "cluster is running the '$got' profile, this suite verifies '$want' — refusing to report a result"
  pass "deployment profile is '$want'"
}

# ---------------------------------------------------------------------------
# resolve_edge <namespace>
#
# Echoes "<ip-or-host>:<port>" for the Envoy edge, so probes can use
# `curl --resolve` instead of depending on the operator's /etc/hosts. The
# existing scripts assume host-side DNS for app./api./www./docs. (see the
# /etc/hosts note at scripts/smoke-signup.sh:353); deriving the edge from the
# Service makes the same probe work on kind (NodePort) and EKS (LoadBalancer).
# ---------------------------------------------------------------------------
resolve_edge() {
  local ns="$1" svc type addr port

  if [ -n "${EDGE_ADDR:-}" ]; then
    echo "$EDGE_ADDR"; return 0
  fi

  svc="$(kubectl -n "$ns" get svc -l app.kubernetes.io/component=envoy -o name 2>/dev/null | head -1)"
  [ -n "$svc" ] || svc="$(kubectl -n "$ns" get svc -o name 2>/dev/null | grep -m1 'envoy' || true)"
  [ -n "$svc" ] || return 1

  type="$(kubectl -n "$ns" get "$svc" -o jsonpath='{.spec.type}' 2>/dev/null)"
  case "$type" in
    NodePort)
      port="$(kubectl -n "$ns" get "$svc" -o jsonpath='{.spec.ports[?(@.name=="https")].nodePort}' 2>/dev/null)"
      [ -n "$port" ] || port="$(kubectl -n "$ns" get "$svc" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null)"
      addr="$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null)"
      ;;
    LoadBalancer)
      addr="$(kubectl -n "$ns" get "$svc" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null)"
      [ -n "$addr" ] || addr="$(kubectl -n "$ns" get "$svc" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)"
      port="443"
      ;;
    *)
      return 2
      ;;
  esac

  { [ -n "$addr" ] && [ -n "$port" ]; } || return 3

  echo "${addr}:${port}"
}

# ---------------------------------------------------------------------------
# _edge_curl_args <edge> <host>
#
# Echoes the curl args that force <host> at <edge>.
#
# `curl --resolve` only accepts an IP address. A LoadBalancer Service on EKS
# often reports a hostname (`...elb.amazonaws.com`) rather than an IP, so in
# that case the override is dropped and normal DNS resolution is used — which is
# correct there, because a real EKS env has real DNS for app./www./docs. via
# external-dns. Silently emitting an invalid --resolve would make every probe
# return 000 and read as a product failure.
# ---------------------------------------------------------------------------
_edge_curl_args() {
  local edge="$1" host="$2" addr="${1%:*}" port="${1##*:}"
  if printf '%s' "$addr" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'; then
    printf -- '--resolve\n%s:%s:%s\n' "$host" "$port" "$addr"
  fi
}

# ---------------------------------------------------------------------------
# edge_status <edge> <host> <path>
#
# Echoes the HTTP status code for https://<host><path> forced at <edge>.
# Echoes 000 on transport failure — callers must treat 000 as a failure, never
# as "not applicable".
# ---------------------------------------------------------------------------
edge_status() {
  local edge="$1" host="$2" path="$3" port="${1##*:}"
  local -a extra=()
  local code
  mapfile -t extra < <(_edge_curl_args "$edge" "$host")
  # curl prints "000" AND exits non-zero on a transport failure, so a naive
  # `|| echo 000` concatenates to "000000". Capture, then normalise.
  code="$(curl -sk -o /dev/null -w '%{http_code}' --max-time 20 \
    "${extra[@]}" \
    "https://${host}:${port}${path}" 2>/dev/null)"
  case "$code" in
    ""|000) echo "000" ;;
    *)      echo "$code" ;;
  esac
}

# ---------------------------------------------------------------------------
# edge_body <edge> <host> <path>  — the response body, for content assertions.
# ---------------------------------------------------------------------------
edge_body() {
  local edge="$1" host="$2" path="$3" port="${1##*:}"
  local -a extra=()
  mapfile -t extra < <(_edge_curl_args "$edge" "$host")
  curl -sk --max-time 20 \
    "${extra[@]}" \
    "https://${host}:${port}${path}" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# summary <suite-name>  — the single exit point. Non-zero on any failure.
# ---------------------------------------------------------------------------
summary() {
  local name="$1"
  step "Summary"
  printf '  failed assertions: %d\n' "$fails"
  if [ "$fails" -gt 0 ]; then
    printf '%s%s: %d assertion(s) failed%s\n' "$RED" "$name" "$fails" "$NC" >&2
    exit 1
  fi
  pass "$name: all assertions passed"
  exit 0
}
