#!/usr/bin/env python3
"""check-substrate-overlays.py — a substrate overlay sets only what is true because of the substrate.

A plain Kubernetes cluster is the supported self-hosted target; eks, gke and
aks are specialisations of it. values-eks.yaml, values-gke.yaml and
values-aks.yaml may therefore set only the keys that exist because the
cluster is that substrate: load-balancer annotations on the edge Service,
storage classes, the provider region, the DNS-01 solver and issuer for the
edge certificate, the DNS provider external-dns publishes to, and the
workload-identity annotations on ServiceAccounts. Anything else in an
overlay is a posture change hiding in a substrate file, which is how a
resource request, a replica count or a feature flag once diverged between
clouds. The EKS and AKS overlays have named this guard in their headers
since the split; it exists now.

  check-substrate-overlays.py             exit 1 on a key outside the allowlist, 0 when clean
  check-substrate-overlays.py --selftest  prove a resource request in an overlay fails
"""
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OVERLAYS = ["values-eks.yaml", "values-gke.yaml", "values-aks.yaml"]

# Key-path prefixes a substrate overlay may set. Keyed by content: a new
# substrate need is a new line here with the reason it is substrate-bound.
ALLOWED = (
    "gibson-workloads.envoy.service.annotations",          # load balancer, client-IP preservation
    "gibson-workloads.gibson.persistence.storageClassName",  # storage class
    "gibson-workloads.openbao.persistence.storageClass",
    "gibson-workloads.redis.master.persistence",
    "gibson-workloads.aws.region",                            # provider region
    "gibson-operators.aws.region",
    "gibson-workloads.certManager.envoyEdge.issuer",        # DNS-01 solver and issuer for the edge cert
    "gibson-workloads.certManager.issuers",
    "external-dns.provider",                                  # the DNS provider external-dns publishes to
    "external-dns.env",
    "gibson-workloads.gibson.serviceAccount.annotations",   # workload identity (IRSA, GKE WI, AKS WI)
    "gibson-workloads.dashboard.serviceAccount.annotations",
    "gibson-workloads.extAuthz.serviceAccount.annotations",
    "gibson-operators.tenantOperator.serviceAccount.annotations",
)


def leaf_paths(d, prefix=""):
    if isinstance(d, dict) and d:
        for k, v in d.items():
            yield from leaf_paths(v, f"{prefix}.{k}" if prefix else str(k))
    else:
        yield prefix


def violations(overlay: dict) -> list[str]:
    out = []
    for p in sorted(set(leaf_paths(overlay))):
        if not any(p == a or p.startswith(a + ".") for a in ALLOWED):
            out.append(p)
    return out


def judge(root: str) -> list[str]:
    out = []
    for f in OVERLAYS:
        path = os.path.join(root, "helm", "gibson", f)
        if not os.path.exists(path):
            continue
        for p in violations(yaml.safe_load(open(path)) or {}):
            out.append(f"{f}: {p} is not a substrate key")
    return out


def selftest() -> int:
    bad = {"gibson-workloads": {"envoy": {"service": {"annotations": {"a": "b"}}}, "gibson": {"resources": {"requests": {"cpu": "2"}}}}, "global": {"domain": "x"}}
    got = violations(bad)
    if got != ["gibson-workloads.gibson.resources.requests.cpu", "global.domain"]:
        print(f"SELFTEST FAIL: want the request and the domain flagged, not the annotation, got {got}")
        return 1
    live = judge(ROOT)
    if live:
        print("SELFTEST FAIL: a substrate overlay sets a non-substrate key:\n  " + "\n  ".join(live))
        return 1
    print("OK: a resource request and a domain in an overlay fail, substrate keys pass, the three overlays are clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(ROOT)
    if got:
        print("❌ substrate overlays set keys that are not substrate-bound (move them to a profile, or add the key here with its reason):\n  " + "\n  ".join(got))
        return 1
    print("✓ substrate-overlays: eks, gke and aks set only substrate keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
