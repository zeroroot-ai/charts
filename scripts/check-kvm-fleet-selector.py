#!/usr/bin/env python3
"""check-kvm-fleet-selector.py: the KVM preflight checks the nodes of the device plugin (charts#508).

scripts/preflight-kvm.sh probes each node that FLEET_NODE_SELECTOR names. Its
default must be the nodeSelector of the device plugin DaemonSet of setec, the
Pod that offers /dev/kvm to the launcher Pods. setec owns that value. When the
setec chart changes it, the preflight checks another set of nodes and nothing
else says so.

The check renders the umbrella, reads the nodeSelector of the DaemonSet that
runs the container "device-plugin", and fails when it differs from the default
selector of the preflight, or when the render holds no such DaemonSet.

  check-kvm-fleet-selector.py             exit 1 on a finding
  check-kvm-fleet-selector.py --selftest  prove each finding fails
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "preflight-kvm.sh")
DEFAULT = re.compile(r'^SELECTOR="\$\{FLEET_NODE_SELECTOR:-([^}]*)\}"$', re.M)


def preflight_default(text):
    m = DEFAULT.search(text)
    if not m:
        return None
    return dict(kv.split("=", 1) for kv in m.group(1).split(",") if kv)


def plugin_selectors(docs):
    out = []
    for d in docs:
        if not (isinstance(d, dict) and d.get("kind") == "DaemonSet"):
            continue
        spec = d["spec"]["template"]["spec"]
        if any(c.get("name") == "device-plugin" for c in spec.get("containers") or []):
            out.append((d["metadata"]["name"], spec.get("nodeSelector") or {}))
    return out


def findings(default, selectors):
    if default is None:
        return ["scripts/preflight-kvm.sh has no line SELECTOR=\"${FLEET_NODE_SELECTOR:-...}\"; "
                "the check read nothing."]
    if not selectors:
        return ["the render holds no DaemonSet with a container \"device-plugin\"; "
                "the check read nothing."]
    out = []
    for name, sel in selectors:
        if sel != default:
            want = ",".join(f"{k}={v}" for k, v in sorted(sel.items()))
            out.append(f"the default FLEET_NODE_SELECTOR of scripts/preflight-kvm.sh is "
                       f"{default}, and the device plugin DaemonSet {name} selects {sel}. "
                       f"Set the default to {want}.")
    return out


def render():
    cmd = ["helm", "template", "gibson", os.path.join(ROOT, "helm", "gibson"),
           "-f", os.path.join(ROOT, "helm", "gibson", "values-baseline.yaml"),
           "-f", os.path.join(ROOT, "helm", "testdata", "render-inputs", "gibson.yaml"),
           "--namespace", "gibson"]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return list(yaml.safe_load_all(p.stdout))


def selftest():
    plugin = {"kind": "DaemonSet", "metadata": {"name": "setec-device-plugin"},
              "spec": {"template": {"spec": {
                  "nodeSelector": {"kubernetes.io/arch": "amd64", "kubernetes.io/os": "linux"},
                  "containers": [{"name": "device-plugin"}]}}}}
    same = preflight_default('SELECTOR="${FLEET_NODE_SELECTOR:-kubernetes.io/os=linux,kubernetes.io/arch=amd64}"')
    other = preflight_default('SELECTOR="${FLEET_NODE_SELECTOR:-kubernetes.io/arch=amd64}"')
    cases = [
        ("the same selector in another order passes", findings(same, plugin_selectors([plugin])), False),
        ("a default that misses a label fails", findings(other, plugin_selectors([plugin])), True),
        ("no device plugin DaemonSet fails", findings(same, plugin_selectors([])), True),
        ("no default line fails", findings(preflight_default("SELECTOR=x"), plugin_selectors([plugin])), True),
    ]
    bad = [name for name, got, want in cases if bool(got) != want]
    for name in bad:
        print(f"  ✗ selftest: {name}")
    if bad:
        return 1
    print("  ✓ selftest: a default that differs from the device plugin, no device plugin "
          "and no default line each fail")
    return 0


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    if sys.argv[1:]:
        print(__doc__)
        return 2
    with open(SCRIPT) as f:
        default = preflight_default(f.read())
    out = findings(default, plugin_selectors(render()))
    for f in out:
        print(f"FAIL: {f}")
    if out:
        return 1
    print(f"  ✓ kvm-fleet-selector: the preflight default {default} is the nodeSelector "
          "of the device plugin DaemonSet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
