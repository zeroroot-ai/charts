#!/usr/bin/env python3
"""check-webhooks.py — every admission webhook is probed before it is live, sweepable at teardown, and fails closed.

Rebuilds three guards lost in the 2026-09-04 split (charts#17):

  check-webhook-ordering (deploy#1761): a `failurePolicy: Fail` webhook
    refuses writes from the moment the API server sees it until the pod
    behind it answers, so templates/webhook-gate.yaml posts an
    AdmissionReview to every such webhook before the umbrella activates
    it. `webhookGate.probes` is the list the gate walks. Every Fail webhook
    in the render must have a probe whose service, port and path agree
    with the clientConfig the API server will dial; every probe must name
    a webhook the render installs.

  check-teardown-webhooks (deploy#1592, deploy#1594): the teardown sweep
    (hosted: bootstrap/eks/gibson/scripts/eks-sweep-admission-webhooks.sh)
    clears service-backed webhook configurations so a destroy cannot wedge
    on a dead endpoint. A url-backed webhook it leaves in place. Every
    webhook in the render must therefore be service-backed.

  fail-closed: no webhook in the render carries `failurePolicy: Ignore`,
    except one the vendored chart itself flips to Fail with a post-install
    hook; those are named here with that reason.

  check-webhooks.py             exit 1 on a violation, 0 when clean
  check-webhooks.py --selftest  prove an unprobed Fail webhook, a url-backed one and an Ignore one fail
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Webhooks the vendored chart renders at Ignore and flips to Fail in its own
# post-install/upgrade hook (spire-controller-manager: "Actual value to be
# set by post install/upgrade hooks"). Keyed by webhook name.
FLIPPED_BY_HOOK = {
    "vclusterfederatedtrustdomain.kb.io": "spire-controller-manager sets Fail in its post-install hook",
    "vclusterspiffeid.kb.io": "spire-controller-manager sets Fail in its post-install hook",
}


def render() -> tuple[list[dict], list[dict]]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    probes = ((yaml.safe_load(open(os.path.join(ROOT, "helm", "gibson", "values.yaml"))) or {}).get("webhookGate") or {}).get("probes") or []
    return docs, probes


def judge(docs: list[dict], probes: list[dict], release_ns: str = "gibson") -> list[str]:
    out = []
    hooks = []  # (config, webhook, dial tuple or None, failurePolicy)
    for d in docs:
        if d.get("kind") not in ("ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"):
            continue
        cfg = d["metadata"]["name"]
        for w in d.get("webhooks") or []:
            name = w.get("name")
            cc = w.get("clientConfig") or {}
            svc = cc.get("service")
            dial = (svc.get("name"), svc.get("namespace") or release_ns, int(svc.get("port") or 443), svc.get("path") or "/") if svc else None
            if not svc:
                out.append(f"{cfg}/{name}: url-backed (no clientConfig.service); the teardown sweep cannot clear it")
            fp = w.get("failurePolicy")
            if fp != "Fail" and name not in FLIPPED_BY_HOOK:
                out.append(f"{cfg}/{name}: failurePolicy {fp!r}, a fail-open admission webhook")
            hooks.append((cfg, name, dial, fp))
    # A webhook NAME may appear in both a mutating and a validating
    # configuration of the same name with different paths (cert-manager), so
    # a probe is matched on config + webhook + the exact dial.
    probe_dials = set()
    for p in probes:
        key = (p.get("config"), p.get("webhook"))
        dial = (p.get("service"), p.get("namespace") or release_ns, int(p.get("port") or 443), p.get("path") or "/")
        candidates = [h for h in hooks if (h[0], h[1]) == key]
        if not candidates:
            out.append(f"probe {key[0]}/{key[1]} names a webhook the render does not install")
            continue
        if dial not in {h[2] for h in candidates}:
            out.append(f"probe {key[0]}/{key[1]} dials {dial}, the API server dials {sorted(h[2] for h in candidates if h[2])}")
            continue
        probe_dials.add((key[0], key[1], dial))
    for cfg, name, dial, fp in hooks:
        if fp == "Fail" and dial and (cfg, name, dial) not in probe_dials:
            out.append(f"{cfg}/{name} ({dial[3]}): failurePolicy Fail with no probe in webhookGate.probes; it would be activated unproved")
    return out


FIXTURE = """
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata: {name: probed}
webhooks:
- name: a.example.io
  failurePolicy: Fail
  clientConfig: {service: {name: a-svc, namespace: gibson, port: 443, path: /validate}}
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata: {name: unprobed}
webhooks:
- name: b.example.io
  failurePolicy: Fail
  clientConfig: {service: {name: b-svc, namespace: gibson, path: /validate}}
---
apiVersion: admissionregistration.k8s.io/v1
kind: MutatingWebhookConfiguration
metadata: {name: urlbacked}
webhooks:
- name: c.example.io
  failurePolicy: Fail
  clientConfig: {url: https://elsewhere.example/mutate}
---
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata: {name: open}
webhooks:
- name: d.example.io
  failurePolicy: Ignore
  clientConfig: {service: {name: d-svc, namespace: gibson, path: /validate}}
"""
FIXTURE_PROBES = [
    {"config": "probed", "webhook": "a.example.io", "service": "a-svc", "port": 443, "path": "/validate"},
    {"config": "gone", "webhook": "z.example.io", "service": "z", "port": 443, "path": "/"},
]


def selftest() -> int:
    got = judge([d for d in yaml.safe_load_all(FIXTURE) if d], FIXTURE_PROBES)
    want = ("unprobed/b.example.io (/validate): failurePolicy Fail with no probe", "urlbacked/c.example.io: url-backed", "open/d.example.io: failurePolicy 'Ignore'", "probe gone/z.example.io names a webhook")
    if len(got) != 4 or not all(any(g.startswith(w) for g in got) for w in want):
        print(f"SELFTEST FAIL: want {want}, got {got}")
        return 1
    # a probe that dials the wrong path is caught too
    bad = [dict(FIXTURE_PROBES[0], path="/wrong")]
    got = judge([d for d in yaml.safe_load_all(FIXTURE) if d][:1], bad)
    if len(got) != 2 or not any("dials" in g for g in got) or not any("no probe" in g for g in got):
        print(f"SELFTEST FAIL: a probe on the wrong path must fail as a wrong dial AND leave the webhook unprobed, got {got}")
        return 1
    live = judge(*render())
    if live:
        print("SELFTEST FAIL: the render violates the webhook contracts:\n  " + "\n  ".join(live))
        return 1
    print("OK: an unprobed Fail webhook, a url-backed one, a fail-open one, a stale probe and a wrong path fail; the render is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(*render())
    if got:
        print("❌ admission webhooks:\n  " + "\n  ".join(got))
        return 1
    print("✓ webhooks: every Fail webhook is probed before activation, every webhook is service-backed, none fail open")
    return 0


if __name__ == "__main__":
    sys.exit(main())
