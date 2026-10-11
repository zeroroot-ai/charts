#!/usr/bin/env bats
# The daemon config names the platform OpenBao at security.secret_source.address
# (gibson#1078, gibson#1079).
#
# From gibson 0.159 the start gate of the daemon asks the health of that
# address and the daemon is not Ready until it answers. A config with no
# address leaves the daemon never Ready on every install. The test reads the
# ConfigMap gibson-gibson-workloads-config of each profile of the goldens and
# requires the address of the in-chart OpenBao.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK="$(mktemp -d)"
  export WORK
}

teardown_file() { rm -rf "$WORK"; }

# check_address <render>: 0 when the daemon config holds the OpenBao address.
check_address() {
  python3 - "$1" <<'PY'
import sys, yaml
found = False
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") != "ConfigMap" or d["metadata"]["name"] != "gibson-gibson-workloads-config":
        continue
    found = True
    cfg = yaml.safe_load(d["data"]["gibson.yaml"])
    addr = (((cfg.get("security") or {}).get("secret_source") or {}).get("address") or "")
    if addr != "https://gibson-openbao.gibson.svc:8200":
        print(f"security.secret_source.address is {addr!r}, want the in-chart OpenBao")
        sys.exit(1)
if not found:
    print("no ConfigMap gibson-gibson-workloads-config in the render")
    sys.exit(1)
PY
}

@test "each profile of the goldens gives the daemon the OpenBao address" {
  for f in "$ROOT"/helm/testdata/golden/values-*.yaml; do
    run check_address "$f"
    [ "$status" -eq 0 ] || { echo "$(basename "$f"): $output"; false; }
  done
}

@test "FAILING FIXTURE: a daemon config with no secret_source fails the check" {
  python3 - "$ROOT/helm/testdata/golden/values-baseline.withcaps.yaml" "$WORK/broken.yaml" <<'PY'
import sys, yaml
docs = [d for d in yaml.safe_load_all(open(sys.argv[1])) if d]
for d in docs:
    if d.get("kind") == "ConfigMap" and d["metadata"]["name"] == "gibson-gibson-workloads-config":
        cfg = yaml.safe_load(d["data"]["gibson.yaml"])
        del cfg["security"]["secret_source"]
        d["data"]["gibson.yaml"] = yaml.safe_dump(cfg)
yaml.safe_dump_all(docs, open(sys.argv[2], "w"))
PY
  run check_address "$WORK/broken.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"security.secret_source.address is ''"* ]] || { echo "$output"; false; }
}
