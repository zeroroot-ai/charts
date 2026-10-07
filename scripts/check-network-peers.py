#!/usr/bin/env python3
"""check-network-peers.py: each peer and each outside host of the release policy is narrow (D76).

The check reads each golden render and fails on:

  1. an ingress peer (fromEndpoints) that selects pods by pod labels in any
     namespace: it matches `io.kubernetes.pod.namespace` with `Exists`, or
     it sits in a clusterwide policy and names no namespace, and it names no
     namespace label (`io.cilium.k8s.namespace.labels.*`),
  2. an egress rule with toFQDNs or toCIDR that opens no fixed port, and a
     toFQDNs entry with no host,
  3. a host that reaches each bucket of S3: "*", a name under amazonaws.com
     whose first label has a "*", or the shared S3 hosts (first label "s3").

scripts/.network-peers-exemptions.txt names an ingress peer that another chart
must narrow, keyed by the policy name and a label of the peer, with its
reason. A stale entry fails.

  check-network-peers.py             exit 1 on a finding
  check-network-peers.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
KINDS = ("CiliumNetworkPolicy", "CiliumClusterwideNetworkPolicy")
NS_KEY = "io.kubernetes.pod.namespace"
NS_LABELS = "io.cilium.k8s.namespace.labels."
EXEMPT = os.path.join("scripts", ".network-peers-exemptions.txt")
S3_ANY = {"s3.amazonaws.com", "*.s3.amazonaws.com", "s3.*.amazonaws.com", "*.s3.*.amazonaws.com"}


def norm(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else key


def rules(doc: dict) -> list[dict]:
    return ([doc["spec"]] if doc.get("spec") else []) + list(doc.get("specs") or [])


def any_namespace(peer: dict, clusterwide: bool = False) -> bool:
    keys = [norm(k) for k in (peer.get("matchLabels") or {})]
    exprs = [norm(e.get("key", "")) for e in peer.get("matchExpressions") or []]
    exists = any(norm(e.get("key", "")) == NS_KEY and e.get("operator") == "Exists"
                 for e in peer.get("matchExpressions") or [])
    names_ns = NS_KEY in keys or any(k.startswith(NS_LABELS) for k in keys + exprs)
    if clusterwide:
        return not names_ns or (exists and NS_KEY not in keys and not any(k.startswith(NS_LABELS) for k in keys))
    return exists and NS_KEY not in keys and not any(k.startswith(NS_LABELS) for k in keys)


def any_bucket(host: str) -> bool:
    if host in S3_ANY or host == "*":
        return True
    if host.split(".")[-2:] != ["amazonaws", "com"]:
        return False
    first = host.split(".", 1)[0]
    return "*" in first or first == "s3"


def load_exempt(root: str) -> dict[tuple[str, str], str]:
    out = {}
    path = os.path.join(root, EXEMPT)
    if os.path.exists(path):
        for line in open(path):
            line = line.split("#")[0].strip()
            if line:
                policy, label, reason = (line.split(" ", 2) + ["", ""])[:3]
                out[(policy, label)] = reason
    return out


def judge(docs: list, exempt: dict | None = None, used: set | None = None) -> list[str]:
    exempt = exempt or {}
    bad = []
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") not in KINDS:
            continue
        name = d["metadata"]["name"]
        for r in rules(d):
            for i in r.get("ingress") or []:
                for peer in i.get("fromEndpoints") or []:
                    if any_namespace(peer, d.get("kind") == "CiliumClusterwideNetworkPolicy"):
                        hit = [k for k in exempt if k[0] == name and
                               k[1] in {f"{kk}={vv}" for kk, vv in (peer.get("matchLabels") or {}).items()}]
                        if hit:
                            if used is not None:
                                used.update(hit)
                            continue
                        bad.append(f"{name}: an ingress peer selects pod labels {peer.get('matchLabels')} in any namespace")
            for e in r.get("egress") or []:
                if not (e.get("toFQDNs") or e.get("toCIDR") or e.get("toCIDRSet")):
                    continue
                ports = [p for tp in e.get("toPorts") or [] for p in tp.get("ports") or []]
                if not ports or any(not p.get("port") or str(p.get("port")) == "0" or p.get("endPort") for p in ports):
                    bad.append(f"{name}: an egress rule to {e.get('toFQDNs') or e.get('toCIDR') or e.get('toCIDRSet')}"
                               " opens no fixed port")
                for f in e.get("toFQDNs") or []:
                    host = f.get("matchName") or f.get("matchPattern") or ""
                    if not host:
                        bad.append(f"{name}: a toFQDNs entry names no host: {f}")
                    elif any_bucket(host):
                        bad.append(f"{name}: {host} reaches each bucket of S3, not the buckets of the install")
    return bad


def audit(root: str) -> tuple[list[str], int]:
    bad, seen, used = [], 0, set()
    exempt = load_exempt(root)
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = [d for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)]
        if not any(d.get("kind") in KINDS for d in docs):
            continue
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(docs, exempt, used)]
    bad += [f"stale exemption, delete the line: {k[0]} {k[1]} ({exempt[k]})" for k in sorted(set(exempt) - used)]
    if not any(any(e.get("toFQDNs") for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)
                   and d.get("kind") in KINDS for r in rules(d) for e in r.get("egress") or [])
               for f in glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        bad.append("no golden render holds a toFQDNs rule: this check is blind")
    return bad, seen


def selftest() -> int:
    def cnp(ingress=None, egress=None):
        return {"kind": "CiliumNetworkPolicy", "metadata": {"name": "p"},
                "spec": {"endpointSelector": {}, "ingress": ingress or [], "egress": egress or []}}

    https = [{"ports": [{"port": "443", "protocol": "TCP"}]}]
    good = [
        cnp(ingress=[{"fromEndpoints": [{"matchLabels": {"k8s:io.kubernetes.pod.namespace": "cnpg-system",
                                                          "app.kubernetes.io/name": "cloudnative-pg"}}]}]),
        cnp(ingress=[{"fromEndpoints": [{"matchLabels": {"k8s:" + NS_LABELS + "team": "a", "app": "x"},
                                         "matchExpressions": [{"key": "k8s:" + NS_KEY, "operator": "Exists"}]}]}]),
        cnp(egress=[{"toFQDNs": [{"matchName": "bucket.s3.amazonaws.com"}], "toPorts": https}]),
    ]
    if judge(good):
        print(f"SELFTEST FAIL: narrow peers and hosts must pass, got {judge(good)}")
        return 1
    failing = (
        ("a pod label from any namespace",
         cnp(ingress=[{"fromEndpoints": [{"matchLabels": {"app.kubernetes.io/name": "cloudnative-pg"},
                                          "matchExpressions": [{"key": "k8s:" + NS_KEY, "operator": "Exists"}]}]}])),
        ("an FQDN rule with no port", cnp(egress=[{"toFQDNs": [{"matchName": "api.example.com"}]}])),
        ("an FQDN rule with a port range",
         cnp(egress=[{"toFQDNs": [{"matchName": "api.example.com"}],
                      "toPorts": [{"ports": [{"port": "1", "endPort": 65535, "protocol": "TCP"}]}]}])),
        ("a CIDR rule with no port", cnp(egress=[{"toCIDR": ["10.0.0.1/32"]}])),
        ("each bucket of S3", cnp(egress=[{"toFQDNs": [{"matchPattern": "*.s3.amazonaws.com"}], "toPorts": https}])),
        ("each bucket of a region", cnp(egress=[{"toFQDNs": [{"matchPattern": "*.s3.us-east-1.amazonaws.com"}],
                                                 "toPorts": https}])),
        ("the shared regional S3 host", cnp(egress=[{"toFQDNs": [{"matchName": "s3.us-east-1.amazonaws.com"}],
                                                     "toPorts": https}])),
        ("each host", cnp(egress=[{"toFQDNs": [{"matchPattern": "*"}], "toPorts": https}])),
        ("an FQDN entry with no host", cnp(egress=[{"toFQDNs": [{}], "toPorts": https}])),
        ("a clusterwide peer with no namespace",
         dict(cnp(ingress=[{"fromEndpoints": [{"matchLabels": {"app": "x"}}]}]),
              kind="CiliumClusterwideNetworkPolicy")),
    )
    wide = failing[0][1]
    if judge([wide], {("p", "app.kubernetes.io/name=cloudnative-pg"): "r"}):
        print("SELFTEST FAIL: an exempt peer must pass")
        return 1
    used: set = set()
    judge([good[0]], {("p", "app=gone"): "r"}, used)
    if used:
        print("SELFTEST FAIL: an exemption that matches no finding must stay unused, so audit() reports it stale")
        return 1
    for what, doc in failing:
        if len(judge([doc])) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge([doc])}")
            return 1
    print("  ✓ selftest: a pod label from any namespace, an FQDN or CIDR rule with no fixed port, and each bucket of S3 fail; a stale exemption is reported")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("a peer or an outside host of the network policy is too wide (D76):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ network-peers: each peer names its namespace and each outside host its port, in {seen} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
