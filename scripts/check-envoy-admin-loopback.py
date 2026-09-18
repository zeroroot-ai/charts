#!/usr/bin/env python3
"""check-envoy-admin-loopback.py — Envoy's admin interface never leaves the pod.

The admin interface serves /config_dump, /runtime_modify and /quitquitquit
with no authentication. It binds loopback. The kubelet probes and the
metrics scraper reach a separate listener on the pod IP that forwards
exactly two paths, /ready and /stats/prometheus, to the loopback admin and
nothing else. This guard reads files/envoy/envoy.yaml as text (it is a Helm
tpl input, not plain YAML) and fails when the admin bind leaves loopback,
when the probe listener routes any other path, or when a probe or scrape in
the Envoy templates points at a port the probe listener does not serve.

  check-envoy-admin-loopback.py             exit 1 on a violation, 0 when clean
  check-envoy-admin-loopback.py --selftest  prove a 0.0.0.0 admin bind fails
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENVOY = os.path.join(ROOT, "helm", "gibson-workloads", "files", "envoy", "envoy.yaml")
PROBE_PORT = 9901
ALLOWED_PATHS = {"/ready", "/stats/prometheus"}


def judge(text: str) -> list[str]:
    out = []
    m = re.search(r"^admin:\n(?:.*\n)*?\s*socket_address:\s*\{\s*address:\s*([^,\s]+),\s*port_value:\s*(\d+)\s*\}", text, re.M)
    if not m:
        return ["no admin socket_address found"]
    admin_addr, admin_port = m.group(1), int(m.group(2))
    if admin_addr != "127.0.0.1":
        out.append(f"admin binds {admin_addr}, not 127.0.0.1")
    if admin_port == PROBE_PORT:
        out.append(f"admin binds the probe port {PROBE_PORT}; the probe listener must front it")
    lm = re.search(r"- name: admin_probe\n(?:.*\n)*?\s*socket_address:\s*\{\s*address:\s*0\.0\.0\.0,\s*port_value:\s*(\d+)\s*\}", text)
    if not lm:
        return out + ["no admin_probe listener on 0.0.0.0"]
    if int(lm.group(1)) != PROBE_PORT:
        out.append(f"admin_probe listens on {lm.group(1)}, not {PROBE_PORT}")
    block = text[lm.start():]
    block = block[: block.find("\n  clusters:")]
    paths = set(re.findall(r"match:\s*\{\s*path:\s*\"([^\"]+)\"\s*\}", block))
    prefixes = re.findall(r"match:\s*\{\s*prefix:", block)
    if prefixes:
        out.append("admin_probe routes by prefix; only exact paths are allowed")
    if paths != ALLOWED_PATHS:
        out.append(f"admin_probe routes {sorted(paths)}, want exactly {sorted(ALLOWED_PATHS)}")
    cm = re.search(r"- name: envoy_admin_loopback\n(?:.*\n)*?\s*socket_address:\s*\{\s*address:\s*([^,\s]+),\s*port_value:\s*(\d+)\s*\}", text)
    if not cm:
        out.append("no envoy_admin_loopback cluster")
    elif cm.group(1) != "127.0.0.1" or int(cm.group(2)) != admin_port:
        out.append(f"envoy_admin_loopback dials {cm.group(1)}:{cm.group(2)}, want 127.0.0.1:{admin_port}")
    return out


def templates_use_probe_port() -> list[str]:
    out = []
    tdir = os.path.join(ROOT, "helm", "gibson-workloads", "templates", "envoy")
    for f in ("deployment.yaml", "networkpolicy.yaml"):
        text = open(os.path.join(tdir, f)).read()
        for port in set(re.findall(r"(?:containerPort|port):\s*(99\d\d)\b", text)):
            if int(port) != PROBE_PORT:
                out.append(f"templates/envoy/{f} names port {port}; only the probe port {PROBE_PORT} is reachable from the pod IP")
    return out


def selftest() -> int:
    text = open(ENVOY).read()
    if judge(text):
        print("SELFTEST FAIL: the shipped config must pass:\n  " + "\n  ".join(judge(text)))
        return 1
    # THE FIXTURE THIS EXISTS FOR: the admin bind goes back to 0.0.0.0.
    bad = text.replace("socket_address: { address: 127.0.0.1, port_value: 9902 }", "socket_address: { address: 0.0.0.0, port_value: 9902 }", 1)
    got = judge(bad)
    if not got or "admin binds 0.0.0.0" not in got[0]:
        print(f"SELFTEST FAIL: a 0.0.0.0 admin bind must fail, got {got}")
        return 1
    # A third route on the probe listener fails.
    wider = text.replace('- match: { path: "/ready" }', '- match: { path: "/ready" }\n                        - match: { path: "/config_dump" }\n                          route: { cluster: envoy_admin_loopback }', 1)
    got = judge(wider)
    if len(got) != 1 or "routes" not in got[0]:
        print(f"SELFTEST FAIL: an extra probe route must fail, got {got}")
        return 1
    # A prefix route fails.
    pfx = text.replace('- match: { path: "/stats/prometheus" }', '- match: { prefix: "/stats" }', 1)
    got = judge(pfx)
    if not got or not any("prefix" in g for g in got):
        print(f"SELFTEST FAIL: a prefix route must fail, got {got}")
        return 1
    print("OK: a 0.0.0.0 admin bind, a third route and a prefix route fail; the shipped config passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(open(ENVOY).read()) + templates_use_probe_port()
    if got:
        print("❌ Envoy's admin interface is reachable beyond /ready and /stats/prometheus:\n  " + "\n  ".join(got))
        return 1
    print("✓ envoy-admin-loopback: admin on 127.0.0.1, the pod-IP listener serves /ready and /stats/prometheus only")
    return 0


if __name__ == "__main__":
    sys.exit(main())
