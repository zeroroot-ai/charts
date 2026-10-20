#!/usr/bin/env python3
"""check-hubble-drops.py: no flow of the release is dropped by a network policy (ADR-0165 rule 4, D76, charts#489).

`make secure-pod` proves the network policy on the render. This check proves
it on a running cluster: it reads the Hubble flows of each Cilium agent after
the exit test assertions, and it fails on each flow with the verdict DROPPED
and a policy drop reason. The log names the source pod, the destination and
the port, so the missing label or policy is clear.

The exit tests turn Hubble on in the Cilium of their kind cluster
(.github/workflows/published-install.yml). The check reads each agent with
`hubble observe` inside the agent container, so it needs no Hubble relay.

  check-hubble-drops.py --namespace gibson [--since 2026-10-07T00:00:00Z]
        exit 1 when a flow to or from the namespace is dropped by policy
  check-hubble-drops.py --namespace gibson --since T --expect-drop <pod prefix>[:<port>] [--direction EGRESS]
        the live fixture: exit 0 only when a policy drop from a pod whose name
        starts with the prefix (to the port, and on the side of the
        direction, when given) is in the flows, and print it. EGRESS means
        the policy of the source refused the flow; INGRESS means the policy
        of the destination did
  check-hubble-drops.py --namespace gibson --since T --expect-forward <pod prefix>:<port>
        a caller fixture: exit 0 only when a forwarded flow from a pod whose
        name starts with the prefix to the port is in the flows, and no
        policy drop from that pod. It proves that the flow happened, so the
        drop check of the namespace is not blind to it (charts#519)
  check-hubble-drops.py --flows <file of jsonpb lines> [...]
        judge recorded flows in place of a live cluster
  check-hubble-drops.py --selftest
        prove that a policy drop fails, other drops and forwarded flows pass,
        and the fixture flow of a pod with no client label that dials
        Postgres is reported with its source, destination and port
"""
import argparse
import ipaddress
import json
import subprocess
import sys

# Hubble names a drop that a policy makes with one of these reasons.
POLICY_REASONS = ("POLICY_DENIED", "POLICY_DENY", "AUTH_REQUIRED")


class Peers:
    """What the check knows of the cluster to judge a late packet: the pod
    CIDRs of the nodes and the ports that a pod listens on. A pod CIDR list
    that is empty, or a read that failed, makes no packet a late packet: the
    check fails closed."""

    def __init__(self, cidrs=None, ports=None, live=False):
        self.cidrs = [ipaddress.ip_network(c, strict=False) for c in (cidrs or [])]
        self.ports = {k: set(v) for k, v in (ports or {}).items()}
        self.live = live
        self.notices = []

    def listens(self, namespace: str, pod: str, port: int) -> bool:
        key = f"{namespace}/{pod}"
        if key not in self.ports and self.live:
            r = subprocess.run(["kubectl", "-n", namespace, "get", "pod", pod, "-o", "json"],
                               capture_output=True, text=True)
            got = set()
            if r.returncode == 0:
                for c in (json.loads(r.stdout).get("spec") or {}).get("containers") or []:
                    got |= {int(p["containerPort"]) for p in c.get("ports") or [] if "containerPort" in p}
            self.ports[key] = got
        return port in self.ports.get(key, set())

    def in_pod_cidr(self, ip: str) -> bool:
        try:
            a = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(a in n for n in self.cidrs)


def live_pod_cidrs() -> list:
    """The pod CIDRs of the nodes. Empty when the read fails."""
    r = subprocess.run(["kubectl", "get", "nodes", "-o", "jsonpath={.items[*].spec.podCIDRs}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"::error::cannot read the pod CIDRs of the nodes: {r.stderr.strip()[:200]}. "
              "No drop counts as a late packet to a gone peer.", file=sys.stderr)
        return []
    out = []
    for chunk in r.stdout.split():
        try:
            out += json.loads(chunk)
        except json.JSONDecodeError:
            out += [x for x in chunk.strip("[]").replace('"', "").split(",") if x]
    if not out:
        print("::error::the nodes name no pod CIDR. No drop counts as a late packet to a gone peer.",
              file=sys.stderr)
    return out


def gone_peer(flow: dict, peers: "Peers") -> bool:
    """True for a late reply of a server to a peer pod that is gone.

    When a pod ends, Cilium removes its endpoint and the connection tracking
    entries of its address. A late ACK, FIN or RST of a server to that
    address then has no entry and no identity, so the policy of the server
    sees new egress to the world entity and drops it. Main run 39447716168
    showed it: the OpenFGA server sent two packets to 10.244.0.187 port 60814
    (world), the ephemeral port of a client that had just ended.

    All of these must hold, or the drop stays a finding:
      - TCP with flags and no SYN: the packet starts no connection;
      - the destination is the world entity with no pod, at an address
        inside a pod CIDR of the cluster: a pod address that has no pod;
      - the source port is a port the source pod listens on, and the
        destination port is ephemeral (32768 or more): a server reply.
    A packet to an address outside the pod CIDRs, such as an outside host,
    is never passed. Each passed drop is printed as a notice.
    """
    tcp = (flow.get("l4") or {}).get("TCP")
    if not isinstance(tcp, dict):
        return False
    flags = tcp.get("flags") or {}
    if not flags or flags.get("SYN"):
        return False
    src, dst = flow.get("source") or {}, flow.get("destination") or {}
    if dst.get("pod_name") or "reserved:world" not in (dst.get("labels") or []):
        return False
    ip = (flow.get("IP") or {}).get("destination", "")
    if not peers.in_pod_cidr(ip):
        return False
    if int(tcp.get("destination_port") or 0) < 32768:
        return False
    if not src.get("pod_name") or not peers.listens(src.get("namespace", ""), src["pod_name"],
                                                    int(tcp.get("source_port") or 0)):
        return False
    return True


def endpoint(e: dict, ip: str) -> str:
    e = e or {}
    if e.get("pod_name"):
        return f"{e.get('namespace', '?')}/{e['pod_name']}"
    names = e.get("labels") or []
    reserved = [n.split(":", 1)[1] for n in names if n.startswith("reserved:")]
    return f"{ip or '?'}" + (f" ({','.join(reserved)})" if reserved else "")


def port_of(flow: dict) -> str:
    l4 = flow.get("l4") or {}
    for proto in ("TCP", "UDP", "SCTP"):
        if proto in l4:
            return f"{(l4[proto] or {}).get('destination_port', '?')}/{proto}"
    if "ICMPv4" in l4 or "ICMPv6" in l4:
        return "icmp"
    return "?"


def evidence_line(obj: dict) -> str:
    """The flow as one JSON line for the evidence artifact, with no l7 key.
    The artifact of a public repository is public: an HTTP rule of Cilium
    would put request headers (Authorization, Cookie) into l7."""
    obj = json.loads(json.dumps(obj))
    flow = obj.get("flow", obj)
    flow.pop("l7", None)
    return json.dumps(obj)


def policy_drops(lines: list[str], namespace: str, peers: "Peers" = None, evidence: list = None) -> list[dict]:
    """The flows that a policy dropped, to or from the namespace. A late
    reply to a gone peer (gone_peer) goes to peers.notices instead. Each
    policy drop of the namespace, passed or not, is appended to evidence as
    its raw flow."""
    peers = peers or Peers()
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        flow = obj.get("flow", obj)
        if flow.get("verdict") != "DROPPED":
            continue
        reason = str(flow.get("drop_reason_desc") or "")
        if reason not in POLICY_REASONS:
            continue
        src, dst = flow.get("source") or {}, flow.get("destination") or {}
        if namespace and namespace not in (src.get("namespace"), dst.get("namespace")):
            continue
        if evidence is not None:
            evidence.append(evidence_line(obj))
        ip = flow.get("IP") or {}
        item = {
            "source": endpoint(src, ip.get("source", "")),
            "source_pod": src.get("pod_name", ""),
            "destination": endpoint(dst, ip.get("destination", "")),
            "port": port_of(flow),
            "direction": flow.get("traffic_direction", "?"),
            "reason": reason,
            "time": flow.get("time", ""),
            "flags": ",".join(sorted(k for k, v in (((flow.get("l4") or {}).get("TCP") or {}).get("flags") or {}).items() if v)),
        }
        if gone_peer(flow, peers):
            peers.notices.append(item)
            continue
        out.append(item)
    return out


def lines_of(files: list[str]) -> list[str]:
    return [line for f in files for line in open(f)]


def describe(d: dict) -> str:
    flags = f", TCP {d['flags']}" if d.get("flags") else ""
    return (f"{d['source']} -> {d['destination']} port {d['port']} "
            f"({d['direction']}, {d['reason']}{flags}, {d['time']})")


def forwarded(lines: list[str], prefix: str, port: str) -> list[dict]:
    """The forwarded flows from a pod whose name starts with prefix, to the port."""
    out = []
    for line in lines:
        try:
            flow = json.loads(line).get("flow") or {}
        except (json.JSONDecodeError, AttributeError):
            continue
        src, dst = flow.get("source") or {}, flow.get("destination") or {}
        if flow.get("verdict") != "FORWARDED" or not str(src.get("pod_name", "")).startswith(prefix):
            continue
        if port_of(flow) != f"{port}/TCP":
            continue
        ip = flow.get("IP") or {}
        out.append({"source": endpoint(src, ip.get("source", "")), "source_pod": src.get("pod_name", ""),
                    "destination": endpoint(dst, ip.get("destination", "")), "port": port_of(flow),
                    "direction": flow.get("traffic_direction", "?"), "reason": "FORWARDED",
                    "time": flow.get("time", "")})
    return out


def expected_drops(drops: list[dict], spec: str, direction: str = "") -> list[dict]:
    """The policy drops from a pod whose name starts with the prefix of spec,
    to the port of spec when spec is <prefix>:<port>, on the side of
    direction when one is given."""
    prefix, _, port = spec.rpartition(":")
    if not prefix or not port.isdigit():
        prefix, port = spec, ""
    return [d for d in drops if d["source_pod"].startswith(prefix)
            and (not port or d["port"] == f"{port}/TCP")
            and (not direction or d["direction"] == direction)]


def live_flows(namespace: str, since: str, verdict: str = "DROPPED") -> list[str]:
    pods = subprocess.run(
        ["kubectl", "-n", "kube-system", "get", "pods", "-l", "k8s-app=cilium", "-o",
         "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"],
        capture_output=True, text=True, check=True).stdout.split()
    if not pods:
        raise SystemExit("no Cilium agent pod in kube-system: this check is blind")
    lines = []
    for pod in pods:
        args = ["kubectl", "-n", "kube-system", "exec", pod, "-c", "cilium-agent", "--",
                "hubble", "observe", "--namespace", namespace, "--verdict", verdict,
                "--output", "jsonpb", "--last", "1000000"]
        if since:
            args += ["--since", since]
        r = subprocess.run(args, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"hubble observe failed on {pod}: {r.stderr.strip()[:400]}\n"
                             "Is Hubble on in this Cilium (hubble.enabled=true)?")
        lines += r.stdout.splitlines()
    return lines


FIXTURE_UNLABELED_TO_POSTGRES = {
    "flow": {
        "time": "2026-10-07T00:00:00Z", "verdict": "DROPPED",
        "drop_reason_desc": "POLICY_DENIED", "traffic_direction": "EGRESS",
        "IP": {"source": "10.244.0.50", "destination": "10.244.0.20"},
        "l4": {"TCP": {"source_port": 40000, "destination_port": 5432}},
        "source": {"namespace": "gibson", "pod_name": "hubble-proof-unlabeled",
                   "labels": ["k8s:run=hubble-proof-unlabeled"]},
        "destination": {"namespace": "gibson", "pod_name": "platform-postgres-1",
                        "labels": ["k8s:gibson.zeroroot.ai/datastore=postgres"]},
    }
}
FIXTURE_FORWARDED = {"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"], "verdict": "FORWARDED",
                              "drop_reason_desc": ""}}
FIXTURE_OTHER_DROP = {"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"], "drop_reason_desc": "CT_MAP_INSERTION_FAILED"}}
FIXTURE_OTHER_NAMESPACE = {"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"],
                                    "source": {"namespace": "tenant-a", "pod_name": "x"},
                                    "destination": {"namespace": "tenant-b", "pod_name": "y"}}}


def selftest() -> int:
    lines = [json.dumps(f) for f in (FIXTURE_FORWARDED, FIXTURE_OTHER_DROP, FIXTURE_OTHER_NAMESPACE)]
    if policy_drops(lines, "gibson"):
        print(f"SELFTEST FAIL: a forwarded flow, a drop for another reason and a drop in other namespaces must pass: "
              f"{policy_drops(lines, 'gibson')}")
        return 1
    got = policy_drops(lines + [json.dumps(FIXTURE_UNLABELED_TO_POSTGRES), "not json"], "gibson")
    if len(got) != 1:
        print(f"SELFTEST FAIL: the policy drop of a pod with no client label that dials Postgres must fail, got {got}")
        return 1
    text = describe(got[0])
    for want in ("gibson/hubble-proof-unlabeled", "gibson/platform-postgres-1", "5432/TCP", "POLICY_DENIED"):
        if want not in text:
            print(f"SELFTEST FAIL: the report must name {want}: {text}")
            return 1
    ingress = {"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"], "traffic_direction": "INGRESS",
                        "source": {"labels": ["reserved:world"]}}}
    got = policy_drops([json.dumps(ingress)], "gibson")
    if len(got) != 1 or "world" not in got[0]["source"]:
        print(f"SELFTEST FAIL: a drop from outside the cluster must fail and name the world entity: {got}")
        return 1
    # A late reply of a server to a pod address with no pod passes, as a
    # notice. Each other shape fails.
    peers = Peers(cidrs=["10.244.0.0/24"], ports={"gibson/gibson-openfga-x": [8080]})
    late = {"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"],
                     "IP": {"source": "10.244.0.30", "destination": "10.244.0.187"},
                     "l4": {"TCP": {"source_port": 8080, "destination_port": 60814,
                                    "flags": {"ACK": True, "FIN": True}}},
                     "source": {"namespace": "gibson", "pod_name": "gibson-openfga-x"},
                     "destination": {"labels": ["reserved:world"]}}}
    if policy_drops([json.dumps(late)], "gibson", peers) or len(peers.notices) != 1:
        print("SELFTEST FAIL: a late FIN of a server to a pod address with no pod must pass as a notice")
        return 1
    tcp = late["flow"]["l4"]["TCP"]
    outside = {"flow": {**late["flow"], "IP": {"source": "10.244.0.30", "destination": "203.0.113.5"},
                        "l4": {"TCP": {**tcp, "destination_port": 443, "flags": {"ACK": True, "PSH": True}}}}}
    cases = (
        ("a no-SYN packet to 203.0.113.5:443 (ACK,PSH)", outside, peers),
        ("a SYN to a pod address with no pod", {"flow": {**late["flow"], "l4": {"TCP": {**tcp, "flags": {"SYN": True}}}}}, peers),
        ("a late packet to a live pod", {"flow": {**late["flow"], "destination": {"namespace": "gibson", "pod_name": "gibson-x", "labels": ["k8s:app=x"]}}}, peers),
        ("a packet with no TCP flags", {"flow": {**late["flow"], "l4": {"TCP": {**tcp, "flags": {}}}}}, peers),
        ("a reply from a port the pod does not listen on", {"flow": {**late["flow"], "l4": {"TCP": {**tcp, "source_port": 9999}}}}, peers),
        ("a packet to a port that is not ephemeral", {"flow": {**late["flow"], "l4": {"TCP": {**tcp, "destination_port": 5432}}}}, peers),
        ("a late reply with no pod CIDR read (fails closed)", late, Peers(cidrs=[], ports={"gibson/gibson-openfga-x": [8080]})),
    )
    for what, f, pr in cases:
        if len(policy_drops([json.dumps(f)], "gibson", pr)) != 1:
            print(f"SELFTEST FAIL: {what} must fail")
            return 1
    ev = []
    policy_drops([json.dumps(late), json.dumps(outside)], "gibson", Peers(cidrs=["10.244.0.0/24"], ports={"gibson/gibson-openfga-x": [8080]}), ev)
    if len(ev) != 2:
        print("SELFTEST FAIL: the evidence must hold the raw flow of each policy drop, the passed one too")
        return 1
    with_l7 = {"flow": {**outside["flow"], "l7": {"http": {"headers": [{"key": "Authorization", "value": "Bearer sekret-l7"}]}}}}
    ev = []
    policy_drops([json.dumps(with_l7)], "gibson", Peers(cidrs=["10.244.0.0/24"]), ev)
    if len(ev) != 1 or "l7" in json.loads(ev[0])["flow"] or "sekret-l7" in ev[0]:
        print("SELFTEST FAIL: the evidence must hold no l7 key, so no request header reaches the artifact")
        return 1
    trainer = {"flow": {**FIXTURE_FORWARDED["flow"], "l4": {"TCP": {"destination_port": 50051}},
                        "source": {"namespace": "tenant-primary", "pod_name": "belief-trainer-proof-x1"},
                        "destination": {"namespace": "gibson", "pod_name": "gibson-gibson-workloads-0"}}}
    if len(forwarded([json.dumps(trainer)], "belief-trainer-proof", "50051")) != 1:
        print("SELFTEST FAIL: a forwarded flow of the caller to its port must be found")
        return 1
    for what, f in (("another port", {**trainer["flow"], "l4": {"TCP": {"destination_port": 50001}}}),
                    ("a dropped flow", {**trainer["flow"], "verdict": "DROPPED"}),
                    ("another pod", {**trainer["flow"], "source": {"namespace": "x", "pod_name": "other"}})):
        if forwarded([json.dumps({"flow": f})], "belief-trainer-proof", "50051"):
            print(f"SELFTEST FAIL: {what} must not count as the forwarded flow of the caller")
            return 1
    runner = policy_drops([json.dumps({"flow": {**FIXTURE_UNLABELED_TO_POSTGRES["flow"],
                                                 "l4": {"TCP": {"destination_port": 50001}},
                                                 "source": {"namespace": "tenant-primary",
                                                            "pod_name": "hubble-proof-connector-runner"},
                                                 "destination": {"namespace": "gibson",
                                                                 "pod_name": "gibson-gibson-workloads-0"}}})],
                          "tenant-primary")
    if len(expected_drops(runner, "hubble-proof-connector-runner:50001")) != 1:
        print("SELFTEST FAIL: the drop of the runner fixture on its port must be found")
        return 1
    for what, spec in (("another port", "hubble-proof-connector-runner:50051"), ("another pod", "other:50001")):
        if expected_drops(runner, spec):
            print(f"SELFTEST FAIL: {what} must not count as the expected drop")
            return 1
    # The fixture drop is on the EGRESS side. A drop at the daemon (INGRESS)
    # means a policy of the source let the flow out: the connector rule that
    # gibson#1065 deletes did that for the daemon ports 8080 and 50100.
    if len(expected_drops(runner, "hubble-proof-connector-runner:50001", "EGRESS")) != 1:
        print("SELFTEST FAIL: the EGRESS drop of the runner fixture must be found")
        return 1
    ingress = [{**runner[0], "direction": "INGRESS"}]
    if expected_drops(ingress, "hubble-proof-connector-runner:50001", "EGRESS"):
        print("SELFTEST FAIL: a drop at the destination must not count as an EGRESS drop")
        return 1
    print("  ✓ selftest: a policy drop fails and names its source, destination and port; a forwarded flow, "
          "another drop reason, other namespaces and a late server reply to a pod address with no pod pass; "
          "a no-SYN packet to an outside host, a SYN, a live pod, no flags, a port the pod does not listen on, "
          "a port that is not ephemeral and no pod CIDR read fail; a caller fixture needs its forwarded flow to "
          "its port; "
          "an expected drop matches its pod and port only")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--namespace", default="gibson")
    ap.add_argument("--since", default="")
    ap.add_argument("--flows", nargs="*")
    ap.add_argument("--expect-drop", default="")
    ap.add_argument("--expect-forward", default="")
    ap.add_argument("--direction", default="", choices=["", "EGRESS", "INGRESS"])
    ap.add_argument("--pod-cidr", action="append", default=[],
                    help="with --flows: a pod CIDR of the cluster (live, the nodes are read)")
    ap.add_argument("--listen-port", action="append", default=[],
                    help="with --flows: <namespace>/<pod>=<port>, a port the pod listens on")
    ap.add_argument("--evidence", default="", help="write the raw flow of each policy drop to this file")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.expect_forward:
        prefix, _, port = a.expect_forward.rpartition(":")
        if not prefix or not port.isdigit():
            print("::error::--expect-forward takes <pod prefix>:<port>", file=sys.stderr)
            return 2
        flows = (lines_of(a.flows) if a.flows else live_flows(a.namespace, a.since, "FORWARDED"))
        drops = [d for d in policy_drops(lines_of(a.flows) if a.flows else live_flows(a.namespace, a.since), a.namespace)
                 if d["source_pod"].startswith(prefix)]
        for d in drops:
            print(f"::error::the caller was dropped: {describe(d)}", file=sys.stderr)
        mine = forwarded(flows, prefix, port)
        if not mine:
            print(f"::error::no forwarded flow from a pod named {prefix}* to port {port}: the caller did not "
                  "reach it, so the drop check cannot speak for this flow", file=sys.stderr)
            return 1
        if drops:
            return 1
        print(f"  ✓ the caller reaches its port: {describe(mine[0])}")
        return 0
    if a.flows:
        lines = lines_of(a.flows)
        ports = {}
        for lp in a.listen_port:
            k, _, v = lp.partition("=")
            ports.setdefault(k, []).append(int(v))
        peers = Peers(a.pod_cidr, ports)
    else:
        lines = live_flows(a.namespace, a.since)
        peers = Peers(live_pod_cidrs(), live=True)
    evidence = []
    drops = policy_drops(lines, a.namespace, peers, evidence)
    if a.evidence:
        with open(a.evidence, "a") as f:
            f.writelines(x.rstrip("\n") + "\n" for x in evidence)
    for n in peers.notices:
        print(f"::notice::a late server reply to a pod address with no pod, not a policy finding: {describe(n)}")
    if a.expect_drop:
        mine = expected_drops(drops, a.expect_drop, a.direction)
        if not mine:
            print(f"::error::no policy drop that matches {a.expect_drop} (<pod prefix>[:<port>]) "
                  f"{('on the ' + a.direction + ' side') if a.direction else ''}: the policy did not "
                  "refuse the fixture there, "
                  f"or Hubble did not record it. {len(drops)} other policy drop(s).", file=sys.stderr)
            return 1
        for d in mine:
            print(f"  ✓ the fixture is refused: {describe(d)}")
        return 0
    if drops:
        print(f"::error::{len(drops)} flow(s) of namespace {a.namespace} were dropped by a network policy "
              "(ADR-0165 rule 4). Give the pod its network labels (gibson.netLabels):", file=sys.stderr)
        for d in drops:
            print(f"  {describe(d)}", file=sys.stderr)
        return 1
    print(f"  ✓ hubble-drops: {len(lines)} dropped flow(s) read, none of namespace {a.namespace} dropped by a policy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
