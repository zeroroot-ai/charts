#!/usr/bin/env bats
# The init container wait-for-system-key renders on each Zitadel workload
# (charts#576).
#
# Zitadel reads SystemAPIUsers from disk once, at boot. The init container
# holds the pod until both System API public keys are on disk. It sat at
# zitadel.initContainers, a key that the Zitadel chart 9.34.1 does not read,
# so no render held it. The chart reads zitadel.zitadel.initContainers for the
# Deployment and the init and setup Jobs.

setup_file() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  export ROOT
  WORK_DIR="$(mktemp -d)"
  export WORK_DIR
  render() { # <out> [helm args...]
    local out="$1"; shift
    helm template gibson "$ROOT/helm/gibson" --namespace gibson \
      -f "$ROOT/helm/gibson/values-baseline.yaml" -f "$ROOT/helm/testdata/render-inputs/gibson.yaml" \
      "$@" > "$out" 2>/dev/null
  }
  render "$WORK_DIR/render.yaml"
  # The failing fixture: the same render with the list the chart reads empty.
  render "$WORK_DIR/no-init.yaml" --set "zitadel.zitadel.initContainers=null"
}

teardown_file() { rm -rf "$WORK_DIR"; }

check_wait() { # <render file>
  python3 - "$1" <<'PY'
import sys, yaml
need = {"gibson-zitadel", "gibson-zitadel-init", "gibson-zitadel-setup"}
seen = set()
for d in yaml.safe_load_all(open(sys.argv[1])):
    if not d or d.get("kind") not in ("Deployment", "Job") or d["metadata"]["name"] not in need:
        continue
    ps = d["spec"]["template"]["spec"]
    vols = {v["name"]: v for v in ps.get("volumes") or []}
    for c in ps.get("initContainers") or []:
        if c["name"] != "wait-for-system-key":
            continue
        mounts = {m["name"]: m["mountPath"] for m in c.get("volumeMounts") or []}
        assert mounts.get("zitadel-system-key") == "/etc/zitadel-system-keys", f"{d['metadata']['name']}: no key mount"
        assert vols.get("zitadel-system-key", {}).get("secret", {}).get("secretName") == "gibson-zitadel-system-key", \
            f"{d['metadata']['name']}: the volume is not the Secret gibson-zitadel-system-key"
        seen.add(d["metadata"]["name"])
missing = sorted(need - seen)
if missing:
    print(f"no wait-for-system-key on {missing}")
    sys.exit(1)
PY
}

@test "each Zitadel workload waits for the System API keys" {
  run check_wait "$WORK_DIR/render.yaml"
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "FAILING FIXTURE: a render with no zitadel.zitadel.initContainers fails the check" {
  run check_wait "$WORK_DIR/no-init.yaml"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no wait-for-system-key on"* ]]
}
