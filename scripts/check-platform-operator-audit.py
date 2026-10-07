#!/usr/bin/env python3
"""check-platform-operator-audit.py: the platform-operator can send its audit records to the daemon (gibson#583).

The platform-operator does not start without GIBSON_DAEMON_GRPC_ADDRESS and
GIBSON_DAEMON_SPIFFE_ID, and it dials the daemon with its own SVID from the
SPIRE agent socket. The check reads each golden render and fails when:

  - the platform-operator container lacks either env, or the daemon ID is not
    a spiffe:// ID of platform/daemon,
  - the container has no SPIFFE_ENDPOINT_SOCKET or no mount of the SPIRE
    agent socket at /run/spire/sockets,
  - no ClusterSPIFFEID gives the platform-operator pods
    spiffe://<td>/platform/platform-operator,
  - the daemon allow-list (GIBSON_SPIFFE_ALLOWED_PEER_IDS) lacks that ID,
  - the render has the D76 label policies and the platform-operator pod has
    another net-role label than the daemon, so no shared policy lets it reach
    the daemon.

  check-platform-operator-audit.py             exit 1 on a finding
  check-platform-operator-audit.py --selftest  prove each finding fails
"""
import copy
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
ROLE = "gibson.zeroroot.ai/net-role"
SOCKET = "/run/spire/sockets"


def find(docs, kind, pred):
    return next((d for d in docs if d.get("kind") == kind and pred(d)), None)


def judge(docs: list) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    out = []
    po = find(docs, "Deployment", lambda d: d["metadata"]["name"].endswith("platform-operator"))
    daemon = find(docs, "StatefulSet", lambda d: (d["spec"]["template"]["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "daemon")
    if po is None or daemon is None:
        return ["the render has no platform-operator Deployment or no daemon StatefulSet: this check is blind"]
    c = po["spec"]["template"]["spec"]["containers"][0]
    env = {e.get("name"): str(e.get("value") or "") for e in c.get("env") or []}
    if not env.get("GIBSON_DAEMON_GRPC_ADDRESS"):
        out.append("the platform-operator has no GIBSON_DAEMON_GRPC_ADDRESS")
    sid = env.get("GIBSON_DAEMON_SPIFFE_ID", "")
    if not (sid.startswith("spiffe://") and sid.endswith("/platform/daemon")):
        out.append(f"the platform-operator GIBSON_DAEMON_SPIFFE_ID is {sid!r}, want spiffe://<td>/platform/daemon")
    if not env.get("SPIFFE_ENDPOINT_SOCKET"):
        out.append("the platform-operator has no SPIFFE_ENDPOINT_SOCKET")
    if not any(m.get("mountPath") == SOCKET for m in c.get("volumeMounts") or []):
        out.append(f"the platform-operator mounts nothing at {SOCKET}")
    ids = [d for d in docs if d.get("kind") == "ClusterSPIFFEID"
           and ((d["spec"].get("podSelector") or {}).get("matchLabels") or {}).get("app.kubernetes.io/component") == "platform-operator"
           and str(d["spec"].get("spiffeIDTemplate", "")).endswith("/platform/platform-operator")]
    if not ids:
        out.append("no ClusterSPIFFEID gives the platform-operator pods spiffe://<td>/platform/platform-operator")
    denv = {e.get("name"): str(e.get("value") or "") for x in daemon["spec"]["template"]["spec"]["containers"] for e in x.get("env") or []}
    if not any(p.endswith("/platform/platform-operator") for p in denv.get("GIBSON_SPIFFE_ALLOWED_PEER_IDS", "").split(",")):
        out.append("the daemon GIBSON_SPIFFE_ALLOWED_PEER_IDS lacks platform/platform-operator")
    if any(d.get("kind") == "CiliumNetworkPolicy"
           and ROLE in (((d.get("spec") or {}).get("endpointSelector") or {}).get("matchLabels") or {}) for d in docs):
        pr = (po["spec"]["template"]["metadata"].get("labels") or {}).get(ROLE)
        dr = (daemon["spec"]["template"]["metadata"].get("labels") or {}).get(ROLE)
        if not pr or pr != dr:
            out.append(f"the platform-operator {ROLE} is {pr!r} and the daemon's is {dr!r}: no shared label policy lets it reach the daemon")
    return out


def selftest() -> int:
    good = [
        {"kind": "Deployment", "metadata": {"name": "gibson-platform-operator"}, "spec": {"template": {
            "metadata": {"labels": {ROLE: "platform"}}, "spec": {"containers": [{"env": [
                {"name": "GIBSON_DAEMON_GRPC_ADDRESS", "value": "d:50051"},
                {"name": "GIBSON_DAEMON_SPIFFE_ID", "value": "spiffe://td/platform/daemon"},
                {"name": "SPIFFE_ENDPOINT_SOCKET", "value": "unix:///run/spire/sockets/api.sock"}],
                "volumeMounts": [{"mountPath": SOCKET}]}]}}}},
        {"kind": "StatefulSet", "metadata": {"name": "d"}, "spec": {"template": {
            "metadata": {"labels": {"app.kubernetes.io/component": "daemon", ROLE: "platform"}},
            "spec": {"containers": [{"env": [{"name": "GIBSON_SPIFFE_ALLOWED_PEER_IDS",
                                              "value": "spiffe://td/platform/tenant-operator,spiffe://td/platform/platform-operator"}]}]}}}},
        {"kind": "ClusterSPIFFEID", "spec": {"podSelector": {"matchLabels": {"app.kubernetes.io/component": "platform-operator"}},
                                             "spiffeIDTemplate": "spiffe://td/platform/platform-operator"}},
        {"kind": "CiliumNetworkPolicy", "spec": {"endpointSelector": {"matchLabels": {ROLE: "platform"}}}},
    ]
    if judge(good):
        print(f"SELFTEST FAIL: a complete render must pass, got {judge(good)}")
        return 1
    def drop_env(name):
        d = copy.deepcopy(good)
        c = d[0]["spec"]["template"]["spec"]["containers"][0]
        c["env"] = [e for e in c["env"] if e["name"] != name]
        return d
    no_mount = copy.deepcopy(good); no_mount[0]["spec"]["template"]["spec"]["containers"][0]["volumeMounts"] = []
    no_id = [d for d in copy.deepcopy(good) if d["kind"] != "ClusterSPIFFEID"]
    no_peer = copy.deepcopy(good); no_peer[1]["spec"]["template"]["spec"]["containers"][0]["env"][0]["value"] = "spiffe://td/platform/tenant-operator"
    other_role = copy.deepcopy(good); other_role[0]["spec"]["template"]["metadata"]["labels"][ROLE] = "system"
    for what, docs in (("no daemon address", drop_env("GIBSON_DAEMON_GRPC_ADDRESS")),
                       ("no daemon ID", drop_env("GIBSON_DAEMON_SPIFFE_ID")),
                       ("no socket env", drop_env("SPIFFE_ENDPOINT_SOCKET")),
                       ("no socket mount", no_mount), ("no ClusterSPIFFEID", no_id),
                       ("no daemon allow-list entry", no_peer), ("another net-role", other_role)):
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: each missing env, the socket mount, the identity, the allow-list entry and a different net-role fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(ROOT, GOLDEN, "values-*.yaml"))):
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(list(yaml.safe_load_all(open(f))))]
    if bad:
        print("the platform-operator cannot send its audit records to the daemon (gibson#583):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ platform-operator-audit: {seen} renders give the platform-operator the daemon env, the SPIRE socket, its identity and a daemon allow-list entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
