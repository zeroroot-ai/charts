#!/usr/bin/env python3
"""check-fixture-flag-follows-runner.py — the daemon's fixture flag follows the runner toggle.

gibson.e2eRunner.enabled is the exit-test profile's one toggle: it renders the
runner's identity and sets GIBSON_TEST_FIXTURES_ENABLED=true on the daemon.
The flag is the daemon's runtime gate for the mock LLM and the runner's
tenant membership. The exit-test workflows set it through a gibson.extraEnv
key the chart never had, so it never reached the daemon and every happy path
stopped at the dispatch gate (gibson#14).

This guard renders the umbrella with the toggle off and on and fails when the
flag is present while the runner is off, or absent while the runner is on.

It also renders every values file the umbrella ships and fails when any of them
puts the flag on any container (charts#332). A shipped profile is one a real
installer applies, and the flag admits test fixtures into a tenant. An exit
test turns the runner on at dispatch, with --set or a file outside helm/gibson,
never in a shipped file. gibson held this check as cmd/fixtures-lint, where no
lane ran it and no workflow could read this repo, so gibson#507 deleted it.

  check-fixture-flag-follows-runner.py             exit 1 on a mismatch
  check-fixture-flag-follows-runner.py --selftest  prove a stray flag, a missing flag
                                                   and a shipped file that sets it fail
"""
import copy
import glob
import os
import tempfile
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = "GIBSON_TEST_FIXTURES_ENABLED"


BASELINE = "helm/gibson/values-baseline.yaml"


def helm_template(extra: list[str]) -> list[dict]:
    args = [
        "helm", "template", "gibson", "helm/gibson",
        "-f", BASELINE,
        "-f", "helm/testdata/render-inputs/gibson.yaml",
        "--namespace", "gibson",
    ] + extra
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def render(runner_on: bool) -> list[dict]:
    return helm_template(["--set", f"gibson-workloads.gibson.e2eRunner.enabled={'true' if runner_on else 'false'}"])


def shipped_profiles() -> list[str]:
    """Every values file the umbrella ships on top of the baseline."""
    found = sorted(glob.glob(os.path.join(ROOT, "helm/gibson/values-*.yaml")))
    return [os.path.relpath(f, ROOT) for f in found if os.path.relpath(f, ROOT) != BASELINE]


def flag_carriers(docs: list[dict]) -> list[str]:
    """Each workload container whose env names the flag, whatever its value."""
    out = []
    for d in docs:
        pod = (((d.get("spec") or {}).get("template") or {}).get("spec") or {})
        if d.get("kind") == "CronJob":
            pod = ((((d.get("spec") or {}).get("jobTemplate") or {}).get("spec") or {}).get("template") or {}).get("spec") or {}
        for c in (pod.get("containers") or []) + (pod.get("initContainers") or []):
            if any(e.get("name") == FLAG for e in c.get("env") or []):
                out.append(f"{d.get('kind')}/{d['metadata']['name']} container {c.get('name')}")
    return out


def judge_profile(profile: str, docs: list[dict]) -> list[str]:
    return [f"{profile} puts {FLAG} on {w}: a shipped profile would admit test fixtures into a tenant"
            for w in flag_carriers(docs)]


def judge_shipped() -> list[str]:
    profiles = shipped_profiles()
    if not profiles:
        return ["no helm/gibson/values-*.yaml beside the baseline: the check found nothing to read"]
    out = judge_profile(BASELINE, helm_template([]))
    for prof in profiles:
        out += judge_profile(prof, helm_template(["-f", prof]))
    return out


def daemon_env(docs: list[dict]) -> dict | None:
    for d in docs:
        if d.get("kind") != "StatefulSet":
            continue
        if d.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/component") != "daemon":
            continue
        for c in d["spec"]["template"]["spec"].get("containers", []):
            if c.get("name") == "gibson":
                return {e["name"]: e.get("value") for e in c.get("env", []) if "name" in e}
    return None


def judge(env_off: dict | None, env_on: dict | None) -> list[str]:
    out = []
    if env_off is None or env_on is None:
        return ["no daemon StatefulSet container named gibson in the render"]
    if FLAG in env_off:
        out.append(f"{FLAG} is set with e2eRunner off: a production profile would run the fixtures")
    if env_on.get(FLAG) != "true":
        out.append(f"{FLAG} is not \"true\" with e2eRunner on: the fixture daemon registers no mock LLM and seeds no runner tenancy")
    return out


def selftest() -> int:
    env_off = daemon_env(render(False))
    env_on = daemon_env(render(True))
    if judge(env_off, env_on):
        print("selftest: the shipped chart must pass before the fixtures run", file=sys.stderr)
        return 1
    stray = copy.deepcopy(env_off)
    stray[FLAG] = "true"
    if not judge(stray, env_on):
        print("selftest: a flag set while the runner is off must fail", file=sys.stderr)
        return 1
    missing = copy.deepcopy(env_on)
    del missing[FLAG]
    if not judge(env_off, missing):
        print("selftest: a missing flag while the runner is on must fail", file=sys.stderr)
        return 1
    # A shipped file that turns the runner on is the charts#332 shape. The
    # fixture goes through the same render a real profile does.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as fx:
        fx.write("gibson-workloads:\n  gibson:\n    e2eRunner:\n      enabled: true\n")
        fx.flush()
        if not judge_profile("fixture", helm_template(["-f", fx.name])):
            print("selftest: a values file that turns the runner on must fail", file=sys.stderr)
            return 1
    print("check-fixture-flag-follows-runner selftest PASSED")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    problems = judge(daemon_env(render(False)), daemon_env(render(True)))
    problems += judge_shipped()
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print(f"check-fixture-flag-follows-runner PASSED ({len(shipped_profiles())} shipped profiles carry no {FLAG})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
