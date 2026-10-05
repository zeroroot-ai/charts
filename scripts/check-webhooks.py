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
    (zeroroot-ai/hosted bootstrap/eks/gibson/scripts/eks-sweep-admission-webhooks.sh)
    clears service-backed webhook configurations so a destroy cannot wedge
    on a dead endpoint. A url-backed webhook it leaves in place. Every
    webhook in the render must therefore be service-backed.

  fail-closed: no webhook in the render carries `failurePolicy: Ignore`,
    except the two webhooks of the SPIRE controller manager that ADR-0076
    accepts. ACCEPTED_IGNORE names them. An entry that names a webhook that
    no render installs at Ignore fails, so the list cannot go stale.

The guard reads each published profile, the same list that scripts/golden.sh
renders.

  check-webhooks.py             exit 1 on a violation, 0 when clean
  check-webhooks.py --selftest  prove an unprobed Fail webhook, a url-backed one, an Ignore one and a stale exception fail
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The webhooks that can render `failurePolicy: Ignore` (ADR-0076). Keyed by
# webhook name. The chart turns the install and upgrade hooks of the SPIRE
# chart off, so nothing sets these two to Fail: they stay at Ignore.
ACCEPTED_IGNORE = {
    "vclusterfederatedtrustdomain.kb.io": "ADR-0076 accepts Ignore for this webhook of the SPIRE controller manager",
    "vclusterspiffeid.kb.io": "ADR-0076 accepts Ignore for this webhook of the SPIRE controller manager",
}
# The same profiles that scripts/golden.sh renders.
PROFILES = (
    ("values-baseline.yaml",),
    ("values-baseline.yaml", "values-eks.yaml"),
    ("values-baseline.yaml", "values-guest.yaml"),
)


def render(profile: tuple[str, ...] = PROFILES[0]) -> tuple[list[dict], list[dict]]:
    args = ["helm", "template", "gibson", "helm/gibson", "-f", "helm/testdata/render-inputs/gibson.yaml",
            "--namespace", "gibson"]
    for f in profile:
        args += ["-f", f"helm/gibson/{f}"]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    docs = [d for d in yaml.safe_load_all(out) if d]
    return docs, gate_probes(docs)


def gate_probes(docs: list[dict]) -> list[dict]:
    """The probes that the rendered gate Job walks: `config|webhook|namespace|service|port|path` lines."""
    for d in docs:
        if d.get("kind") == "Job" and d["metadata"]["name"] == "gibson-webhook-gate":
            script = d["spec"]["template"]["spec"]["containers"][0]["args"][0]
            body = script.split("<<'PROBES'\n", 1)[1].split("\nPROBES", 1)[0]
            out = []
            for line in body.splitlines():
                config, webhook, namespace, service, port, path = line.strip().split("|")
                out.append({"config": config, "webhook": webhook, "namespace": namespace, "service": service,
                            "port": int(port), "path": path})
            return out
    raise SystemExit("the render has no Job/gibson-webhook-gate: this guard cannot read the probes (this is NOT a pass)")


def ignored(docs: list[dict]) -> set[str]:
    """The names of the webhooks that a render installs at Ignore."""
    return {w.get("name") for d in docs
            if d.get("kind") in ("ValidatingWebhookConfiguration", "MutatingWebhookConfiguration")
            for w in d.get("webhooks") or [] if w.get("failurePolicy") == "Ignore"}


def stale(seen: set[str], accepted: dict) -> list[str]:
    """An accepted exception that no render uses."""
    return [f"ACCEPTED_IGNORE names {name}, and no render installs that webhook at Ignore: delete the entry"
            for name in sorted(accepted) if name not in seen]


def judge_all(accepted: dict = ACCEPTED_IGNORE) -> list[str]:
    out, seen = [], set()
    for profile in PROFILES:
        docs, probes = render(profile)
        seen |= ignored(docs)
        out += [f"[{'+'.join(profile)}] {line}" for line in judge(docs, probes, accepted=accepted)]
    return out + stale(seen, accepted)


def judge(docs: list[dict], probes: list[dict], release_ns: str = "gibson", accepted: dict = ACCEPTED_IGNORE) -> list[str]:
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
            if fp != "Fail" and name not in accepted:
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
SPIRE_FIXTURE = """
apiVersion: admissionregistration.k8s.io/v1
kind: ValidatingWebhookConfiguration
metadata: {name: spire-controller-manager-webhook}
webhooks:
- name: vclusterspiffeid.kb.io
  failurePolicy: Ignore
  clientConfig: {service: {name: spire-webhook, namespace: gibson, path: /validate}}
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
    # The exception list: a listed webhook at Ignore passes, and an entry that
    # no render uses fails.
    spire = [d for d in yaml.safe_load_all(SPIRE_FIXTURE) if d]
    listed = {"vclusterspiffeid.kb.io": "fixture"}
    if judge(spire, [], accepted=listed) or stale(ignored(spire), listed):
        print("SELFTEST FAIL: a listed webhook at Ignore must pass")
        return 1
    if len(judge(spire, [], accepted={})) != 1:
        print("SELFTEST FAIL: the same webhook with no entry must fail as fail-open")
        return 1
    gone = dict(listed, **{"vgone.kb.io": "fixture"})
    if len(stale(ignored(spire), gone)) != 1:
        print("SELFTEST FAIL: an entry for a webhook that the render does not contain must fail as stale")
        return 1
    spire[0]["webhooks"][0]["failurePolicy"] = "Fail"
    if len(stale(ignored(spire), listed)) != 1:
        print("SELFTEST FAIL: an entry for a webhook that renders Fail must fail as stale")
        return 1
    live = judge_all()
    if live:
        print("SELFTEST FAIL: the render violates the webhook contracts:\n  " + "\n  ".join(live))
        return 1
    print("OK: an unprobed Fail webhook, a url-backed one, a fail-open one, a stale probe, a wrong path and a stale "
          f"exception fail; {len(PROFILES)} profiles are clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge_all()
    if got:
        print("❌ admission webhooks:\n  " + "\n  ".join(got))
        return 1
    print(f"✓ webhooks: in {len(PROFILES)} profiles every Fail webhook is probed before activation, every webhook is "
          f"service-backed, and {len(ACCEPTED_IGNORE)} accepted webhooks use Ignore (ADR-0076)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
