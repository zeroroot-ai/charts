#!/usr/bin/env python3
"""check-smtp-host-resolves.py — an in-cluster SMTP_HOST names a Service the release renders.

The tenant-operator's SMTP_HOST default was the literal
"gibson-workloads-mailpit". The Service the workloads chart renders is named
from the release: "gibson-mailpit" for `helm install gibson`. So the default
named nothing, and every welcome email on a kind install failed:

    ERROR welcome email send failed; retrying on the next reconcile
      error: dial tcp: lookup gibson-workloads-mailpit on 10.96.0.10:53:
             server misbehaving
    Event Warning WelcomeEmailFailed on Tenant primary

Nothing caught it, because nothing compared the dialer's host to the Services
in the same render (charts#114).

Scope: a BARE host — no dot, no colon, no scheme — is an in-cluster Service
name and must exist in the render. A dotted name is an external relay (SES on
AWS, an enclave's own Exchange or Postfix in an air gap) and is out of scope;
a prod overlay sets one through `tenantOperator.smtp.host`.

  check-smtp-host-resolves.py             exit 1 on a host no Service answers, 0 when clean
  check-smtp-host-resolves.py --selftest  prove a dangling host fails, a Service-backed one passes, and an external relay is ignored
"""
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")
NAMESPACE = "gibson"
HOST_ENVS = ("SMTP_HOST",)


def render() -> list[dict]:
    """The baseline, with the in-cluster sink switched on.

    mailpit defaults off and only values-kind.yaml flips it on, so the
    baseline alone renders no mailpit Service and the operator's default
    host is inert there. The agreement this guard polices is between the
    dialer and the Service, so it renders the profile where both exist.
    """
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml",
         "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--set", "gibson-workloads.mailpit.enabled=true",
         "--namespace", NAMESPACE],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def pod_template(d: dict) -> dict:
    return d["spec"]["jobTemplate"]["spec"]["template"] if d["kind"] == "CronJob" else d["spec"]["template"]


def service_names(docs: list[dict]) -> set[str]:
    return {d["metadata"]["name"] for d in docs if d.get("kind") == "Service"}


def is_in_cluster(host: str) -> bool:
    """A bare name is a Service. A dotted name is an external relay."""
    return bool(host) and "." not in host and ":" not in host and "/" not in host


def host_envs(docs: list[dict]) -> list[tuple[str, str, str, str]]:
    """(kind, workload, env name, value) for every literal host env we police."""
    found = []
    for d in docs:
        if d.get("kind") not in WORKLOADS:
            continue
        spec = pod_template(d).get("spec") or {}
        for container in (spec.get("containers") or []) + (spec.get("initContainers") or []):
            for env in container.get("env") or []:
                if env.get("name") in HOST_ENVS and "value" in env:
                    found.append((d["kind"], d["metadata"]["name"], env["name"], env["value"]))
    return found


def check(docs: list[dict]) -> list[str]:
    services = service_names(docs)
    problems = []
    for kind, name, env, value in host_envs(docs):
        if not is_in_cluster(value):
            continue
        if value not in services:
            problems.append(
                f"{kind}/{name} sets {env}={value!r}, and no Service in the render answers to it. "
                f"Derive it from the helper that names the Service, never from a literal."
            )
    return problems


def selftest() -> int:
    docs = render()
    services = sorted(service_names(docs))
    failures = 0

    real = host_envs(docs)
    if not real:
        print(f"::error::[selftest] the render has no {HOST_ENVS[0]} at all — this guard polices nothing")
        failures += 1

    def workload(env_value: str) -> dict:
        return {"kind": "Deployment", "metadata": {"name": "planted"},
                "spec": {"template": {"spec": {"containers": [
                    {"name": "c", "env": [{"name": "SMTP_HOST", "value": env_value}]}]}}}}

    # A bare host no Service answers must fail. This is charts#114 itself.
    planted = docs + [workload("gibson-workloads-mailpit")]
    if not any("gibson-workloads-mailpit" in p for p in check(planted)):
        print("::error::[selftest] a dangling in-cluster SMTP_HOST was not caught")
        failures += 1

    # A bare host backed by a Service must pass.
    if services:
        planted = docs + [workload(services[0])]
        if any("planted" in p for p in check(planted)):
            print(f"::error::[selftest] a Service-backed SMTP_HOST ({services[0]}) was wrongly flagged")
            failures += 1

    # An external relay is out of scope, in every shape a substrate uses.
    for external in ("email-smtp.us-east-1.amazonaws.com", "smtp.internal.example.mil", "127.0.0.1"):
        if any("planted" in p for p in check(docs + [workload(external)])):
            print(f"::error::[selftest] external relay {external} was wrongly flagged")
            failures += 1

    # An unset host is not a dangling one.
    if any("planted" in p for p in check(docs + [workload("")])):
        print("::error::[selftest] an empty SMTP_HOST was wrongly flagged")
        failures += 1

    if failures:
        print(f"[selftest] {failures} assertion(s) failed")
        return 1
    print("SELFTEST PASS")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        if selftest() != 0:
            return 1

    docs = render()
    problems = check(docs)
    if problems:
        for p in problems:
            print(f"::error::{p}")
        return 1

    policed = [v for *_, v in host_envs(docs) if is_in_cluster(v)]
    print(f"✓ smtp-host-resolves: {len(policed)} in-cluster host(s) each name a Service the release renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
