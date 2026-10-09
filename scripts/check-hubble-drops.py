#!/usr/bin/env python3
"""check-hubble-drops.py: no flow of the release is dropped by a network policy (ADR-0165 rule 4, D76, charts#489).

`make secure-pod` proves the network policy on the render. This check proves
it on a running cluster: it reads the Hubble flows of each Cilium agent after
the exit test assertions, and it fails on each flow with the verdict DROPPED
and a policy drop reason. The log names the source pod, the destination and
the port, so the missing label or policy is clear.

The exit tests turn Hubble on in the Cilium of their kind or k3d cluster
(.github/workflows/published-install.yml). The check reads each agent with
`hubble observe` inside the agent container, so it needs no Hubble relay.

  check-hubble-drops.py --namespace gibson [--since 2026-10-07T00:00:00Z]
        exit 1 when a flow to or from the namespace is dropped by policy
  check-hubble-drops.py --namespace gibson --since T --expect-drop <pod prefix>
        the live fixture: exit 0 only when a policy drop from a pod whose name
        starts with the prefix is in the flows, and print it
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
import json
import subprocess
import sys

# Hubble names a drop that a policy makes with one of these reasons.
POLICY_REASONS = ("POLICY_DENIED", "POLICY_DENY", "AUTH_REQUIRED")


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


def policy_drops(lines: list[str], namespace: str) -> list[dict]:
    """The flows that a policy dropped, to or from the namespace."""
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
        ip = flow.get("IP") or {}
        out.append({
            "source": endpoint(src, ip.get("source", "")),
            "source_pod": src.get("pod_name", ""),
            "destination": endpoint(dst, ip.get("destination", "")),
            "port": port_of(flow),
            "direction": flow.get("traffic_direction", "?"),
            "reason": reason,
            "time": flow.get("time", ""),
        })
    return out


def lines_of(files: list[str]) -> list[str]:
    return [line for f in files for line in open(f)]


def describe(d: dict) -> str:
    return (f"{d['source']} -> {d['destination']} port {d['port']} "
            f"({d['direction']}, {d['reason']}, {d['time']})")


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
    print("  ✓ selftest: a policy drop fails and names its source, destination and port; a forwarded flow, "
          "another drop reason and other namespaces pass; a caller fixture needs its forwarded flow to its port")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--namespace", default="gibson")
    ap.add_argument("--since", default="")
    ap.add_argument("--flows", nargs="*")
    ap.add_argument("--expect-drop", default="")
    ap.add_argument("--expect-forward", default="")
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
    else:
        lines = live_flows(a.namespace, a.since)
    drops = policy_drops(lines, a.namespace)
    if a.expect_drop:
        mine = [d for d in drops if d["source_pod"].startswith(a.expect_drop)]
        if not mine:
            print(f"::error::no policy drop from a pod named {a.expect_drop}*: the policy did not refuse the fixture, "
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
