#!/usr/bin/env python3
"""check-probe-timeouts.py — every probe this chart renders states its timeout.

A probe with no `timeoutSeconds` runs on the Kubernetes default of 1 second.
That default is the tightest value available and nothing at the call site says
so, which is how a probe ends up with it: not because anybody chose 1, but
because whoever wrote the template did not write a number.

One second cost three days. OpenBao's two probes were in the silent group, and
on a loaded 2-core runner /v1/sys/health did not answer inside it:

    Readiness probe failed: Get "http://10.244.0.28:8200/v1/sys/health?...":
    context deadline exceeded (Client.Timeout exceeded while awaiting headers)

A deadline, not a refusal. ESO's ClusterSecretStore is a sync hook, it could
not build a client against an OpenBao that would not answer, Argo spent its
five retries on that one task, and nothing behind it — the daemon included —
was ever created. Three gibson exit tests failed at "Stand up the setec+gvisor
cluster" with no further detail (gibson exit-test-bank run 36922487157).

So: every probe states its timeout. An explicit 1 is a fine answer. The point
is that the number be chosen.

THE VALUES THIS CHART CHOOSES (charts#306)

    tcpSocket                  2   the kernel accepts the connection, nothing
                                   in the process runs
    HTTP answered from memory  3   a health handler that reads no store
    HTTP that waits on a
    dependency                 5   OpenBao's seal, a cold plugin's enrolment,
                                   a startup probe on a JVM recovering a store

WHY THE RENDER AND NOT THE TEMPLATES

A probe inside an `{{- if }}` is invisible to a grep over the templates, and
every probe in this chart that was missing a timeout sat behind one. The render
is the baseline profile with mailpit, stripe-mock and one plugin switched on,
which is the smallest render that contains every probe-bearing workload.

VENDORED CHARTS

A vendored upstream chart is not ours to edit, and only some expose the field
through values. neo4j does, so helm/gibson/values.yaml sets it. The three in
EXEMPT do not, at the versions this chart pins. The exemption is keyed by chart
name, never by a count or a line number, and it is checked in both directions:
an entry that no longer has a gap FAILS, so a chart bump that adds the knob
re-opens the question instead of leaving a stale note behind.

  check-probe-timeouts.py             exit 1 on a probe with no timeoutSeconds
  check-probe-timeouts.py --selftest  prove the fixtures fail and the render is clean
"""
import os
import re
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBES = ("startupProbe", "readinessProbe", "livenessProbe")
PODDED = ("Deployment", "StatefulSet", "DaemonSet", "Job")

# Vendored charts whose templates hard-code the probe and expose no
# timeoutSeconds value, at the version helm/gibson/Chart.yaml pins. Checked in
# both directions: a chart listed here with no gap left is a failure.
EXEMPT = {
    "cnpg": "cloudnative-pg 0.29.0 templates the probes inline and reads only "
            "webhook.{liveness,readiness}Probe.initialDelaySeconds and "
            "webhook.startupProbe.{failureThreshold,periodSeconds}",
    "spire": "spire 0.28.4 (spire-agent, spire-server, "
             "spiffe-oidc-discovery-provider) defines no probe values at all",
    "zitadel": "zitadel 9.34.1 reads enabled, initialDelaySeconds, "
               "periodSeconds and failureThreshold, and no timeout, for both "
               "zitadel and the login UI",
}

RENDER = [
    "helm", "template", "gibson", "helm/gibson",
    "-f", "helm/gibson/values-baseline.yaml",
    "-f", "helm/testdata/render-inputs/gibson.yaml",
    "--namespace", "gibson",
    # The probe-bearing workloads the baseline leaves off. Without these three
    # the render contains no mailpit, stripe-mock or plugin probe at all.
    "--set", "gibson-workloads.mailpit.enabled=true",
    "--set", "gibson-workloads.stripeMock.enabled=true",
    "--set", "gibson-workloads.plugins.probeguard.enabled=true",
    "--set", "gibson-workloads.plugins.probeguard.image.repository=ghcr.io/zeroroot-ai/integrations/probeguard",
    "--set", "gibson-workloads.plugins.probeguard.image.tag=v0.0.0",
]


def render() -> str:
    return subprocess.run(RENDER, cwd=ROOT, capture_output=True, text=True, check=True).stdout


def chart_of(source: str) -> str:
    """The vendored sub-chart a rendered document came from, or "" for ours.

    helm writes `# Source: gibson/charts/<name>/templates/...` for a sub-chart
    and `# Source: gibson/templates/...` for the umbrella. gibson-crds and
    gibson-workloads are ours and are not sub-charts for this purpose.
    """
    m = re.match(r"[^/]+/charts/([^/]+)/", source)
    if not m or m.group(1).startswith("gibson-"):
        return ""
    return m.group(1)


def gaps(rendered: str) -> list[tuple[str, str]]:
    """(chart, "Kind/name:container:probe") for every probe with no timeout."""
    out = []
    for chunk in rendered.split("\n---\n"):
        m = re.search(r"# Source: (\S+)", chunk)
        source = m.group(1) if m else "?"
        try:
            doc = yaml.safe_load(chunk)
        except yaml.YAMLError:
            continue
        if not doc or not isinstance(doc, dict):
            continue
        kind = doc.get("kind")
        if kind == "CronJob":
            pods = [doc["spec"]["jobTemplate"]["spec"]["template"]]
        elif kind in PODDED:
            pods = [doc["spec"]["template"]]
        elif kind == "Pod":
            pods = [doc]
        else:
            continue
        name = (doc.get("metadata") or {}).get("name", "?")
        for pod in pods:
            spec = pod.get("spec") or {}
            for key in ("initContainers", "containers"):
                for c in spec.get(key) or []:
                    for probe in PROBES:
                        p = (c or {}).get(probe)
                        if isinstance(p, dict) and "timeoutSeconds" not in p:
                            out.append((chart_of(source), f"{kind}/{name}:{c.get('name','?')}:{probe}"))
    return out


def judge(found: list[tuple[str, str]]) -> list[str]:
    """Every failure the found gaps imply, in both directions."""
    bad = []
    for chart, ref in found:
        if chart in EXEMPT:
            continue
        where = f"vendored chart {chart}" if chart else "this chart"
        bad.append(f"{ref} ({where}) declares no timeoutSeconds, so it runs on the 1-second default")
    for chart, why in EXEMPT.items():
        if not any(c == chart for c, _ in found):
            bad.append(
                f"EXEMPT names {chart}, but every probe it renders now declares a timeoutSeconds. "
                f"Delete the entry. It was exempt because: {why}"
            )
    return bad


# Two fixtures, both of which must fail: a probe with no timeout, and an
# exemption that has gone stale.
FIXTURE = """
apiVersion: apps/v1
kind: Deployment
metadata: {name: silent}
spec:
  template:
    spec:
      containers:
        - name: app
          readinessProbe: {httpGet: {path: /healthz, port: 8080}}
          livenessProbe: {httpGet: {path: /healthz, port: 8080}, timeoutSeconds: 3}
      initContainers:
        - name: wait
          startupProbe: {tcpSocket: {port: 9000}}
"""


def selftest() -> int:
    got = sorted(gaps("# Source: gibson/templates/x.yaml\n" + FIXTURE))
    want = [("", "Deployment/silent:app:readinessProbe"), ("", "Deployment/silent:wait:startupProbe")]
    if got != sorted(want):
        print(f"SELFTEST FAIL: want exactly the two silent probes (not the one with a timeout), got {got}")
        return 1
    if len(judge(got)) != 2 + len(EXEMPT):
        print("SELFTEST FAIL: two silent probes must be reported, and every exemption with no gap must be reported stale")
        return 1
    stale = judge([(c, "Kind/n:c:readinessProbe") for c in EXEMPT])
    if stale:
        print(f"SELFTEST FAIL: an exemption that still has a gap must be silent, got {stale}")
        return 1
    live = judge(gaps(render()))
    if live:
        print("SELFTEST FAIL: the render is not clean:\n  " + "\n  ".join(live))
        return 1
    print(f"OK: a silent probe fails, a probe with a timeout passes, a stale exemption fails, "
          f"and the render is clean with {len(EXEMPT)} vendored chart(s) exempt")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad = judge(gaps(render()))
    if bad:
        print("❌ probe timeouts:\n  " + "\n  ".join(bad))
        return 1
    print("✓ probe-timeouts: every probe this chart renders states its timeoutSeconds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
