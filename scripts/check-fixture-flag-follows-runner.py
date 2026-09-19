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

  check-fixture-flag-follows-runner.py             exit 1 on a mismatch
  check-fixture-flag-follows-runner.py --selftest  prove a stray flag and a missing flag fail
"""
import copy
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLAG = "GIBSON_TEST_FIXTURES_ENABLED"


def render(runner_on: bool) -> list[dict]:
    args = [
        "helm", "template", "gibson", "helm/gibson",
        "-f", "helm/gibson/values-baseline.yaml",
        "-f", "helm/testdata/render-inputs/gibson.yaml",
        "--namespace", "gibson",
        "--set", f"gibson-workloads.gibson.e2eRunner.enabled={'true' if runner_on else 'false'}",
    ]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


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
    print("check-fixture-flag-follows-runner selftest PASSED")
    return 0


def main() -> int:
    if "--selftest" in sys.argv[1:]:
        return selftest()
    problems = judge(daemon_env(render(False)), daemon_env(render(True)))
    for p in problems:
        print(f"FAIL: {p}", file=sys.stderr)
    if problems:
        return 1
    print("check-fixture-flag-follows-runner PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
