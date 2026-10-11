#!/usr/bin/env bash
# check-admission-policies-compile.sh — the API server accepts every rendered
# ValidatingAdmissionPolicy.
#
# The API server type-checks the CEL of an admission policy when the policy is
# created. A policy that does not compile is refused, and Helm then refuses the
# whole release. `helm template` and kubeconform check no CEL, so this class of
# defect showed up only in the kind install of the exit test (charts#588:
# gibson-tenant-operator-clusterspiffeid-scope compared a map literal that
# mixed a string and a dyn value, "expected type 'string' but found 'dyn'").
#
# This guard starts a throwaway etcd and kube-apiserver (the envtest binaries,
# the Kubernetes version that kind runs), installs the CRDs of gibson-crds and
# gibson-operator-crds so that the type checker sees the same schemas as a
# cluster, and creates each policy of the goldens with a server-side dry run.
# The process is local to the guard. It is not a cluster and nothing reaches one.
#
#   check-admission-policies-compile.sh             exit 1 when a policy does not compile
#   check-admission-policies-compile.sh --selftest  prove the failing fixture is refused, then run
#
# ENVTEST_BIN names the directory that holds etcd, kube-apiserver and kubectl
# (ci.yml unpacks the pinned envtest release there).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIX="$ROOT/tests/fixtures/admission-policies-compile"
BIN="${ENVTEST_BIN:-}"
for b in etcd kube-apiserver kubectl; do
  [[ -n "$BIN" && -x "$BIN/$b" ]] || {
    echo "SETUP FAILURE: ENVTEST_BIN must name a directory with etcd, kube-apiserver and kubectl (ci.yml unpacks envtest-v1.31.0)" >&2
    exit 2
  }
done

WORK="$(mktemp -d)"
cleanup() {
  for p in api etcd; do
    [[ -f "$WORK/$p.pid" ]] && kill "$(cat "$WORK/$p.pid")" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  rm -rf "$WORK"
}
trap cleanup EXIT

free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

start_apiserver() {
  local etcd_port peer_port api_port
  etcd_port="$(free_port)"; peer_port="$(free_port)"; api_port="$(free_port)"
  openssl req -x509 -newkey rsa:2048 -nodes -keyout "$WORK/sa.key" -out "$WORK/sa.crt" \
    -subj /CN=sa -days 1 2>/dev/null
  echo "guard-token,guard,guard,system:masters" > "$WORK/tokens.csv"
  "$BIN/etcd" --data-dir "$WORK/etcd" \
    --listen-client-urls "http://127.0.0.1:$etcd_port" --advertise-client-urls "http://127.0.0.1:$etcd_port" \
    --listen-peer-urls "http://127.0.0.1:$peer_port" >"$WORK/etcd.log" 2>&1 &
  echo $! > "$WORK/etcd.pid"
  "$BIN/kube-apiserver" --etcd-servers "http://127.0.0.1:$etcd_port" \
    --bind-address 127.0.0.1 --secure-port "$api_port" --cert-dir "$WORK/certs" \
    --service-account-issuer https://guard.invalid --service-account-key-file "$WORK/sa.crt" \
    --service-account-signing-key-file "$WORK/sa.key" --token-auth-file "$WORK/tokens.csv" \
    --authorization-mode RBAC --service-cluster-ip-range 10.0.0.0/24 >"$WORK/api.log" 2>&1 &
  echo $! > "$WORK/api.pid"
  cat > "$WORK/kubeconfig" <<EOF
apiVersion: v1
kind: Config
clusters: [{name: guard, cluster: {server: "https://127.0.0.1:$api_port", insecure-skip-tls-verify: true}}]
users: [{name: guard, user: {token: guard-token}}]
contexts: [{name: guard, context: {cluster: guard, user: guard}}]
current-context: guard
EOF
  export KUBECONFIG="$WORK/kubeconfig"
  for _ in $(seq 1 90); do
    "$BIN/kubectl" get --raw /readyz >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "SETUP FAILURE: the guard kube-apiserver did not become ready" >&2
  tail -20 "$WORK/api.log" >&2
  exit 2
}

install_crds() {
  { helm template gibson-crds "$ROOT/helm/gibson-crds" --namespace gibson --include-crds
    helm template gibson-operator-crds "$ROOT/helm/gibson-operator-crds" --namespace gibson --include-crds
  } | python3 -c '
import sys, yaml
docs = [d for d in yaml.safe_load_all(sys.stdin) if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition"]
yaml.safe_dump_all(docs, sys.stdout)
' > "$WORK/crds.yaml"
  "$BIN/kubectl" apply --server-side -f "$WORK/crds.yaml" >/dev/null
  "$BIN/kubectl" wait --for condition=established --timeout=60s crd --all >/dev/null
}

# Write each distinct admission policy of the given files to $WORK/policies/.
split_policies() { # <out dir> <yaml file>...
  python3 - "$@" <<'PY'
import os, sys, yaml
out, files = sys.argv[1], sys.argv[2:]
os.makedirs(out, exist_ok=True)
seen, n = set(), 0
for f in files:
    for d in yaml.safe_load_all(open(f)):
        if not isinstance(d, dict) or d.get("kind") not in ("ValidatingAdmissionPolicy", "MutatingAdmissionPolicy"):
            continue
        key = (d["kind"], d["metadata"]["name"], yaml.safe_dump(d.get("spec"), sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        with open(os.path.join(out, f"{n:03d}-{d['metadata']['name']}.yaml"), "w") as fh:
            yaml.safe_dump(d, fh)
        n += 1
print(n)
PY
}

# Print one line for each policy that the API server refuses. Exit 1 if any.
check_dir() { # <dir>
  local bad=0 f
  for f in "$1"/*.yaml; do
    if ! "$BIN/kubectl" create --dry-run=server -f "$f" >/dev/null 2>"$WORK/err"; then
      echo "FINDING: $(basename "$f" .yaml): $(tr '\n' ' ' < "$WORK/err" | grep -o 'compilation failed.*' | cut -c1-300 || head -c 300 "$WORK/err")"
      bad=1
    fi
  done
  return "$bad"
}

start_apiserver
install_crds

if [[ "${1:-}" == "--selftest" ]]; then
  split_policies "$WORK/fail" "$FIX/fail-dyn-map-literal.yaml" >/dev/null
  if check_dir "$WORK/fail" >"$WORK/selftest.out"; then
    echo "SELFTEST FAILURE: the dyn map literal fixture compiled; the guard cannot fail" >&2
    exit 1
  fi
  grep -q "expected type 'string' but found 'dyn'" "$WORK/selftest.out" || {
    echo "SELFTEST FAILURE: the fixture was refused for another reason:" >&2
    cat "$WORK/selftest.out" >&2
    exit 1
  }
  split_policies "$WORK/pass" "$FIX/pass-string-map-literal.yaml" >/dev/null
  check_dir "$WORK/pass" || { echo "SELFTEST FAILURE: the passing fixture was refused" >&2; exit 1; }
  echo "selftest: the failing fixture is refused, the passing fixture compiles"
fi

shopt -s nullglob
goldens=("$ROOT"/helm/testdata/golden/values-*.yaml)
n="$(split_policies "$WORK/golden" "${goldens[@]}")"
[[ "$n" -ge 1 ]] || { echo "FAILURE: the goldens hold no admission policy; the guard looked at nothing" >&2; exit 1; }
if check_dir "$WORK/golden"; then
  echo "admission-policies-compile: $n distinct policies compile on kube-apiserver $("$BIN/kube-apiserver" --version | awk '{print $2}')"
else
  echo "admission-policies-compile: a policy does not compile; the API server refuses it and Helm refuses the release" >&2
  exit 1
fi
