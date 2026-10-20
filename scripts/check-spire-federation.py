#!/usr/bin/env python3
"""check-spire-federation.py: an install federates with the trust domain of
an outside setec fleet, from values, and with nothing on the baseline
(setec#251, ADR-0164).

WHY. The daemon dials an outside fleet (gibson-workloads gibson.sandbox.setec) and
accepts only its SPIFFE ID. A fleet in another trust domain has an SVID that
the daemon can verify only with the bundle of that domain. SPIRE fetches the
bundle for a ClusterFederatedTrustDomain, and gives it to the daemon only
when the daemon registration lists the domain under federatesWith. The
fleet verifies the daemon with the bundle of this install, which the SPIRE
server serves on its federation endpoint. A part that is missing renders
fine and fails each sandboxed call in a TLS handshake.

HOW. It renders the umbrella:
  1. the baseline: no ClusterFederatedTrustDomain gibson-setec-fleet, no
     federatesWith on the daemon, no federation port on the SPIRE Service,
     and no SPIRE federation policy;
  2. the baseline plus tests/fixtures/spire-federation/values.yaml: the
     trust domain of the fleet ID in the daemon config, the trustDomain of
     the ClusterFederatedTrustDomain and the federatesWith of the daemon
     are one domain, and not the install domain; the Service has the
     federation port; the policy admits the peer namespace on that port
     and opens the fleet bundle endpoint, its namespace and its port;
  3. six values that must fail the render, each with its own reason: a
     fleet in another domain with no bundle source, a federation value
     with no fleet ID, an endpoint ID outside the fleet domain, an empty
     endpoint ID, the endpoint on with no peer, and a fleet ID in the
     install domain (read from the baseline render, not from a file).
--selftest proves that a render with a part missing or wrong fails.

Usage:
  check-spire-federation.py [--selftest]
"""
import argparse
import copy
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests/fixtures/spire-federation/values.yaml")
REFUSED = [
    # (label, extra helm args, a text the refusal must hold)
    ("a fleet in another domain with no bundle source",
     ["--set", "gibson-workloads.gibson.sandbox.setec.federation.bundleEndpointURL="], "set sandbox.setec.federation.bundleEndpointURL"),
    ("a federation value with the fleet in the install domain",
     ["--set", "gibson-workloads.gibson.sandbox.setec.spiffeID="], "the federation would do nothing"),
    ("an endpoint ID outside the fleet domain",
     ["--set", "gibson-workloads.gibson.sandbox.setec.federation.endpointSPIFFEID=spiffe://other.example/spire/server"],
     "endpointSPIFFEID must be a SPIFFE ID in the fleet domain"),
    ("the bundle endpoint on with no peer",
     ["--set-json", "global.networkPolicy.spireBundleEndpointFrom.namespaces=[]"], "names no peer"),
    ("an empty endpoint ID with the https_spiffe profile",
     ["--set-string", "gibson-workloads.gibson.sandbox.setec.federation.endpointSPIFFEID="],
     'must be a SPIFFE ID in the fleet domain "fleet.example.org" with the https_spiffe profile, got ""'),
    # The install domain is read from the baseline render, so no file holds it.
    ("the install domain as the fleet domain",
     ["--set-string", "gibson-workloads.gibson.sandbox.setec.spiffeID=spiffe://{own}/ns/setec-fleet/sa/setec-frontend"],
     "the federation would do nothing"),
]


def render(extra=None, fixture=False):
    args = ["helm", "template", "gibson", os.path.join(ROOT, "helm/gibson"), "--namespace", "gibson",
            "-f", os.path.join(ROOT, "helm/gibson/values-baseline.yaml"),
            "-f", os.path.join(ROOT, "helm/testdata/render-inputs/gibson.yaml")]
    if fixture:
        args += ["-f", FIXTURE]
    args += extra or []
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        return None, out.stderr
    return [d for d in yaml.safe_load_all(out.stdout) if isinstance(d, dict)], ""


def find(docs, kind, name):
    return [d for d in docs if d.get("kind") == kind and d["metadata"]["name"] == name]


def domain(spiffe_id):
    m = re.match(r"^spiffe://([^/]+)", spiffe_id or "")
    return m.group(1) if m else ""


def facts(docs):
    daemon = find(docs, "ClusterSPIFFEID", "gibson-platform-daemon")
    ftd = find(docs, "ClusterFederatedTrustDomain", "gibson-setec-fleet")
    svc = find(docs, "Service", "gibson-server")
    ingress = find(docs, "CiliumNetworkPolicy", "gibson-spire-bundle-endpoint")
    egress = find(docs, "CiliumNetworkPolicy", "gibson-spire-fleet-bundle")
    fleet_id = ""
    for d in docs:
        if d.get("kind") != "ConfigMap":
            continue
        for v in (d.get("data") or {}).values():
            try:
                cfg = yaml.safe_load(v)
            except yaml.YAMLError:
                continue
            sid = (((cfg or {}).get("sandbox") or {}).get("setec") or {}).get("spiffe_id") if isinstance(cfg, dict) else None
            if sid:
                fleet_id = sid
    own = domain(daemon[0]["spec"]["spiffeIDTemplate"]) if daemon else ""
    return {"daemon": daemon, "ftd": ftd, "svc": svc, "ingress": ingress, "egress": egress,
            "fleet_id": fleet_id, "own": own}


def check_base(docs):
    f = facts(docs)
    out = []
    if len(f["daemon"]) != 1:
        return ["the render holds no ClusterSPIFFEID gibson-platform-daemon"]
    if "federatesWith" in f["daemon"][0]["spec"]:
        out.append("the baseline daemon registration has federatesWith")
    if f["ftd"]:
        out.append("the baseline renders ClusterFederatedTrustDomain gibson-setec-fleet")
    if any(p.get("name") == "federation" for s in f["svc"] for p in s["spec"].get("ports", [])):
        out.append("the baseline SPIRE Service has a federation port")
    if f["ingress"] or f["egress"]:
        out.append("the baseline renders a SPIRE federation policy")
    return out


def check_fixture(docs):
    f = facts(docs)
    out = []
    if len(f["daemon"]) != 1 or len(f["ftd"]) != 1 or len(f["svc"]) != 1:
        return [f"the fixture render holds {len(f['daemon'])} daemon registrations, {len(f['ftd'])} "
                f"ClusterFederatedTrustDomain gibson-setec-fleet and {len(f['svc'])} SPIRE Services, want one each"]
    want = domain(f["fleet_id"])
    if not want or want == f["own"]:
        return [f"the daemon config names fleet {f['fleet_id']!r}, not a fleet in another trust domain: the fixture tests nothing"]
    ftd = f["ftd"][0]["spec"]
    if ftd.get("trustDomain") != want:
        out.append(f"the ClusterFederatedTrustDomain is for {ftd.get('trustDomain')!r}, and the daemon dials a fleet in {want!r}")
    if ftd.get("className") != f["daemon"][0]["spec"].get("className"):
        out.append("the ClusterFederatedTrustDomain and the daemon registration have two class names")
    if f["daemon"][0]["spec"].get("federatesWith") != [want]:
        out.append(f"the daemon federatesWith is {f['daemon'][0]['spec'].get('federatesWith')}, want [{want!r}]")
    ports = {p.get("name"): p.get("port") for p in f["svc"][0]["spec"].get("ports", [])}
    if "federation" not in ports:
        out.append("the SPIRE Service has no federation port")
    if len(f["ingress"]) != 1:
        out.append("no policy admits the peers on the bundle endpoint")
    else:
        rules = f["ingress"][0]["spec"].get("ingress") or []
        got = {(r["fromEndpoints"][0]["matchLabels"].get("k8s:io.kubernetes.pod.namespace"),
                r["toPorts"][0]["ports"][0]["port"]) for r in rules if r.get("fromEndpoints")}
        if ("spire-fleet", str(ports.get("federation"))) not in got:
            out.append(f"the bundle endpoint policy admits {sorted(got)}, not spire-fleet on {ports.get('federation')}")
    if len(f["egress"]) != 1:
        out.append("no policy opens the bundle endpoint of the fleet domain to the SPIRE server")
    else:
        rules = f["egress"][0]["spec"].get("egress") or []
        got = {(r["toEndpoints"][0]["matchLabels"].get("k8s:io.kubernetes.pod.namespace"),
                r["toPorts"][0]["ports"][0]["port"]) for r in rules if r.get("toEndpoints")}
        if ("spire-fleet", "8443") not in got:
            out.append(f"the fleet bundle policy opens {sorted(got)}, not spire-fleet on 8443")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    base, err = render()
    fix, err2 = render(fixture=True)
    if base is None or fix is None:
        print(f"FAIL: helm template failed:\n{err or err2}", file=sys.stderr)
        return 1
    if a.selftest:
        bad = 0

        def case(label, findings):
            nonlocal bad
            ok = bool(findings)
            print(f"  {'ok  ' if ok else 'FAIL'} FAILING FIXTURE: {label}")
            bad += 0 if ok else 1

        d = copy.deepcopy(fix); del find(d, "ClusterSPIFFEID", "gibson-platform-daemon")[0]["spec"]["federatesWith"]
        case("no federatesWith on the daemon", check_fixture(d))
        d = copy.deepcopy(fix); find(d, "ClusterFederatedTrustDomain", "gibson-setec-fleet")[0]["spec"]["trustDomain"] = "x.example"
        case("a federation for another domain than the fleet", check_fixture(d))
        d = [x for x in copy.deepcopy(fix) if x["metadata"]["name"] != "gibson-spire-fleet-bundle"]
        case("no egress to the fleet bundle endpoint", check_fixture(d))
        d = [x for x in copy.deepcopy(fix) if x["metadata"]["name"] != "gibson-spire-bundle-endpoint"]
        case("no ingress to the bundle endpoint", check_fixture(d))
        d = copy.deepcopy(base); find(d, "ClusterSPIFFEID", "gibson-platform-daemon")[0]["spec"]["federatesWith"] = ["x.example"]
        case("a federatesWith on the baseline", check_base(d))
        for label, ok in (("the baseline", not check_base(base)), ("the fixture", not check_fixture(fix))):
            print(f"  {'ok  ' if ok else 'FAIL'} {label} render passes")
            bad += 0 if ok else 1
        if bad:
            print(f"check-spire-federation selftest: {bad} case(s) failed", file=sys.stderr)
            return 1
        print("check-spire-federation selftest: 7 cases passed")
        return 0
    findings = [f"baseline: {x}" for x in check_base(base)] + [f"fixture: {x}" for x in check_fixture(fix)]
    own = facts(base)["own"]
    for label, extra, reason in REFUSED:
        docs, err = render([x.replace("{own}", own) for x in extra], fixture=True)
        if docs is not None:
            findings.append(f"FAILING FIXTURE: {label} rendered, and it must fail the render")
        elif reason not in err:
            findings.append(f"FAILING FIXTURE: {label} failed the render for another reason than {reason!r}")
    for f in findings:
        print(f"FAIL: {f}", file=sys.stderr)
    if findings:
        return 1
    print(f"spire-federation: the baseline federates nothing, the fixture federates the fleet domain both ways, "
          f"and {len(REFUSED)} bad values fail the render")
    return 0


if __name__ == "__main__":
    sys.exit(main())
