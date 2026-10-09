#!/usr/bin/env python3
"""check-belief-trainer-image.py: the tenant operator gets the image of the belief trainer (gibson#31).

The tenant operator writes a belief trainer CronJob into each tenant namespace
(gibson#616). It reads the image from BELIEF_TRAINER_IMAGE and exits at start
when the value is empty, so a chart that does not set it stops every install.

The check reads each golden render and fails when:

  1. the tenant-operator Deployment sets no BELIEF_TRAINER_IMAGE, or an empty one,
  2. the value is not the image of the daemon StatefulSet. The default trainer
     is the gibson image of the daemon: it holds the belief-trainer binary and
     the trainer RPCs of the same release. Two pins that drift apart give a
     trainer of another release. The hosted platform sets its own trainer image
     in its overlay, which is not a golden profile.

  check-belief-trainer-image.py             exit 1 on a finding
  check-belief-trainer-image.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden", "values-*.yaml")
ENV = "BELIEF_TRAINER_IMAGE"


def containers(doc: dict) -> list[dict]:
    return (((doc.get("spec") or {}).get("template") or {}).get("spec") or {}).get("containers") or []


def judge(docs: list[dict]) -> list[str]:
    op = [d for d in docs if d.get("kind") == "Deployment"
          and d["metadata"]["name"] == "gibson-tenant-operator"]
    daemon = [d for d in docs if d.get("kind") == "StatefulSet"
              and (d["metadata"].get("labels") or {}).get("app.kubernetes.io/component") == "daemon"]
    if len(op) != 1 or len(daemon) != 1:
        return [f"found {len(op)} tenant-operator Deployments and {len(daemon)} daemon StatefulSets, want 1 each"]
    env = {e["name"]: e.get("value") for c in containers(op[0]) for e in c.get("env") or []}
    got = env.get(ENV)
    if not got:
        return [f"the tenant-operator sets no {ENV}: the operator exits at start without it"]
    want = next((c["image"] for c in containers(daemon[0]) if c.get("name") == "gibson"), None)
    if got != want:
        return [f"the tenant-operator sets {ENV}={got}, the daemon runs {want}: set "
                "gibson-operators.tenantOperator.beliefTrainer.image to the pin of gibson-workloads.gibson.image"]
    return []


def load(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all(open(path)) if isinstance(d, dict) and d.get("metadata")]


def selftest() -> int:
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN)))
    if not files:
        print("selftest: no golden render")
        return 1
    base = load(files[0])
    if judge(base):
        print(f"selftest: the golden {files[0]} is not clean: {judge(base)}")
        return 1

    def mutate(fn) -> list[dict]:
        docs = yaml.safe_load(yaml.safe_dump(base))
        for d in docs:
            if d.get("kind") == "Deployment" and d["metadata"]["name"] == "gibson-tenant-operator":
                for c in containers(d):
                    fn(c)
        return docs

    cases = {
        "no value": mutate(lambda c: c.__setitem__("env", [e for e in c.get("env") or [] if e["name"] != ENV])),
        "empty value": mutate(lambda c: [e.__setitem__("value", "") for e in c.get("env") or [] if e["name"] == ENV]),
        "another release": mutate(lambda c: [e.__setitem__("value", "ghcr.io/zeroroot-ai/gibson:v0.1.0")
                                             for e in c.get("env") or [] if e["name"] == ENV]),
    }
    bad = [name for name, docs in cases.items() if not judge(docs)]
    if bad:
        print(f"selftest: these fixtures passed and must fail: {bad}")
        return 1
    print(f"selftest: {len(cases)} fixtures fail as they must")
    return 0


def main() -> int:
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    files = sorted(glob.glob(os.path.join(ROOT, GOLDEN)))
    if not files:
        print("::error::no golden render to check")
        return 1
    fail = []
    for f in files:
        fail += [f"{os.path.relpath(f, ROOT)}: {m}" for m in judge(load(f))]
    for m in fail:
        print(f"::error::{m}")
    if fail:
        return 1
    print(f"OK: the tenant-operator sets {ENV} to the daemon image in {len(files)} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
