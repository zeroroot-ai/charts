#!/usr/bin/env python3
"""check-secret-contract.py: the secret contract holds, in both directions.

helm/gibson/secret-contract.yaml is the one secret contract of the chart
(charts#433). It moved here from zeroroot-ai/hosted with this check, which
hosted wrote in hosted#350. hosted now vendors the contract at the pinned
chart version and checks its own consumers against it.

Five assertions, over the committed golden renders (helm/testdata/golden/,
which `make golden` keeps equal to the chart) and the OpenBao seed table:

  1. FORWARD. Every backend key an ExternalSecret reads is declared under
     `secrets` or `operator_produced`.
  2. PRODUCER REALITY. An entry with `producer: openbao-seeder` is a key of
     helm/gibson-workloads/files/openbao-seed-keys.txt.
  3. VICE-VERSA. Every key the seed table mints is declared.
  4. CONSUMER REALITY. Every `secrets` entry has a consumer: a rendered
     ExternalSecret reads it, a `runtime:` consumer reads it, or a
     `chart-gate:<target>` names a real target of this Makefile that renders
     the gated object. An entry whose consumers are in hosted (`gitops:`,
     `staging:`) is checked there, because this repo cannot read hosted.
  5. Every `operator_produced` entry is referenced by a rendered object, a
     `runtime:` consumer, or a consumer in hosted.

Exemptions live in scripts/.secret-contract-exemptions.txt, one per line as
`<assertion>:<name> <reason>`, keyed by name and never by line number. An
exemption whose target no longer fails is reported, so the list cannot rot.

  check-secret-contract.py             exit 1 on a contract violation
  check-secret-contract.py --selftest  prove each assertion fails, then check the tree
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = Path("helm") / "gibson" / "secret-contract.yaml"
EXEMPTIONS = Path("scripts") / ".secret-contract-exemptions.txt"
# A consumer in zeroroot-ai/hosted. hosted checks it against its vendored copy.
HOSTED = ("gitops:", "staging:")


def load_exemptions(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        spec, _, reason = line.partition(" ")
        out[spec.strip()] = reason.strip()
    return out


def seed_keys(root: Path) -> set[str]:
    f = root / "helm" / "gibson-workloads" / "files" / "openbao-seed-keys.txt"
    if not f.exists():
        return set()
    out = set()
    for line in f.read_text().splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.add(line.split()[0])
    return out


def _remote_ref_keys(docs) -> dict[str, set[str]]:
    """remoteRef/extract key -> the ExternalSecret names that consume it."""
    refs: dict[str, set[str]] = {}
    for d in docs:
        if not isinstance(d, dict) or d.get("kind") != "ExternalSecret":
            continue
        name = (d.get("metadata") or {}).get("name") or "<unnamed>"
        spec = d.get("spec") or {}
        keys = []
        for e in spec.get("data") or []:
            k = (e.get("remoteRef") or {}).get("key")
            if k:
                keys.append(k)
        for e in spec.get("dataFrom") or []:
            k = (e.get("extract") or {}).get("key")
            if k:
                keys.append(k)
        for k in keys:
            # gitops manifests prefix the backend path; the contract names the
            # bare key, so the last segment is what matches.
            refs.setdefault(k.rsplit("/", 1)[-1], set()).add(name)
    return refs


def _mounted_names(docs) -> set[str]:
    """Every string in a rendered manifest that names a Kubernetes Secret.

    An operator_produced entry is a Secret minted at runtime, not sourced from
    a backend, so it can never appear as a remoteRef key. Its consumer is a
    workload that mounts it or reads a key from it, which is what this finds.
    """
    out: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("secretName", "existingSecret") and isinstance(v, str) and v:
                    out.add(v)
                elif k in ("secretKeyRef", "secretRef") and isinstance(v, dict):
                    n = v.get("name")
                    if isinstance(n, str) and n:
                        out.add(n)
                elif k == "secret" and isinstance(v, dict):
                    n = v.get("secretName") or v.get("name")
                    if isinstance(n, str) and n:
                        out.add(n)
                elif k == "secrets" and isinstance(v, list):
                    for e in v:
                        if isinstance(e, dict) and isinstance(e.get("name"), str):
                            out.add(e["name"])
                else:
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for d in docs:
        if isinstance(d, dict):
            walk(d)
    return out


def _safe_docs(path: Path):
    try:
        return list(yaml.safe_load_all(path.read_text(errors="replace")))
    except yaml.YAMLError:
        return []


def golden_docs(root: Path):
    golden = root / "helm" / "testdata" / "golden"
    for f in sorted(golden.glob("*.yaml")) if golden.is_dir() else []:
        yield from _safe_docs(f)


def make_targets(root: Path) -> set[str]:
    """Every `make` target name in the Makefile.

    Read, not invoked: the question is only whether the named proof exists. A
    Makefile that cannot be read yields an empty set, and every chart-gate
    marker then fails rather than passing silently.
    """
    mk = root / "Makefile"
    if not mk.is_file():
        return set()
    out: set[str] = set()
    for line in mk.read_text(errors="replace").split("\n"):
        if not line or line[0].isspace() or line.startswith((".", "#")):
            continue
        head = line.split(":", 1)[0].strip() if ":" in line else ""
        if head and "=" not in head and " " not in head:
            out.add(head)
    return out


def _consumers(e: dict) -> list[str]:
    return [str(c) for c in e.get("consumers") or []]


def violations(root: Path) -> tuple[list[str], list[str]]:
    """(failures, stale exemptions)"""
    contract = yaml.safe_load((root / CONTRACT).read_text()) or {}
    backend = {e["name"]: e for e in (contract.get("secrets") or []) if e.get("name")}
    opprod = {e["name"]: e for e in (contract.get("operator_produced") or [])
              if isinstance(e, dict) and e.get("name")}
    # `producers` names the runtime-written Secrets (make secret-plumbing).
    declared = set(backend) | set(opprod) | set((contract.get("producers") or {}))

    docs = list(golden_docs(root))
    seeded = seed_keys(root)
    consumed = _remote_ref_keys(docs)
    if not consumed or not seeded:
        return ([f"[blind] the golden renders hold {len(consumed)} ExternalSecret keys and the seed table "
                 f"{len(seeded)} keys: this check read nothing"], [])
    mounted = _mounted_names(docs)
    targets = make_targets(root)
    exempt = load_exemptions(root / EXEMPTIONS)
    used: set[str] = set()
    fail: list[str] = []

    def report(assertion: str, name: str, msg: str) -> None:
        spec = f"{assertion}:{name}"
        if spec in exempt:
            used.add(spec)
            return
        fail.append(f"[{assertion}] {msg}")

    # 1. FORWARD
    for key, holders in sorted(consumed.items()):
        if key not in declared:
            report("forward", key, f"{key}: consumed by ExternalSecret {sorted(holders)} and declared by "
                                   f"nothing in {CONTRACT}, so the install depends on a key no producer owns")
    # 2. PRODUCER REALITY
    for name, e in sorted(backend.items()):
        if e.get("producer") == "openbao-seeder" and name not in seeded:
            report("producer", name, f"{name}: says producer: openbao-seeder, but openbao-seed-keys.txt does not mint it")
    # 3. VICE-VERSA
    for key in sorted(seeded):
        if key not in declared:
            report("viceversa", key, f"{key}: minted by the OpenBao seed table and declared by nothing in {CONTRACT}")
    # 4. CONSUMER REALITY
    for name, e in sorted(backend.items()):
        if name in consumed:
            continue
        cons = _consumers(e)
        gates = [c.split(":", 1)[1] for c in cons if c.startswith("chart-gate:")]
        missing = [g for g in gates if g not in targets]
        if missing:
            report("consumer", name, f"{name}: declares chart-gate:{missing[0]}, and the Makefile has no "
                                     f"`make {missing[0]}` target. The marker claims a proof that does not exist.")
            continue
        if gates or any(c.startswith("runtime:") or c.startswith(HOSTED) for c in cons):
            continue
        report("consumer", name, f"{name}: declares consumers {cons} and no rendered ExternalSecret reads it")
    # 5. operator_produced referenced
    for name, e in sorted(opprod.items()):
        if name in consumed or name in mounted:
            continue
        if any(c.startswith("runtime:") or c.startswith(HOSTED) for c in _consumers(e)):
            continue
        report("opprod", name, f"{name}: listed under operator_produced and referenced by nothing")

    stale = [f"{s}  ({exempt[s] or 'no reason given'})" for s in exempt if s not in used]
    return fail, sorted(stale)


# ---------------------------------------------------------------- selftest ---

def _fixture(tmp: Path, *, contract: dict, seed: str, golden: str,
             targets: tuple[str, ...] = ("cg-rotation-window",)) -> Path:
    (tmp / "scripts").mkdir(parents=True)
    (tmp / CONTRACT).parent.mkdir(parents=True)
    (tmp / "helm" / "gibson-workloads" / "files").mkdir(parents=True)
    (tmp / "helm" / "testdata" / "golden").mkdir(parents=True)
    (tmp / CONTRACT).write_text(yaml.safe_dump(contract))
    (tmp / "helm" / "gibson-workloads" / "files" / "openbao-seed-keys.txt").write_text(seed)
    (tmp / "helm" / "testdata" / "golden" / "values-baseline.bare.yaml").write_text(golden)
    (tmp / "Makefile").write_text("".join(f"{t}: ## fixture\n\t@true\n" for t in targets))
    return tmp


def _es(name: str, key: str) -> str:
    return ("apiVersion: external-secrets.io/v1\nkind: ExternalSecret\n"
            f"metadata:\n  name: {name}\nspec:\n  data:\n"
            f"    - secretKey: x\n      remoteRef:\n        key: {key}\n---\n")


def selftest() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        root = _fixture(
            tmp / "one",
            contract={
                "secrets": [
                    {"name": "seeded-and-read", "producer": "openbao-seeder", "consumers": ["chart:x/good"]},
                    {"name": "claims-seeder", "producer": "openbao-seeder", "consumers": ["chart:x/good"]},
                    {"name": "no-consumer", "producer": "openbao-seeder", "consumers": []},
                    {"name": "runtime-only", "producer": "operator", "consumers": ["runtime:something"]},
                    {"name": "window-only", "producer": "operator-supplied",
                     "consumers": ["chart-gate:cg-rotation-window"]},
                    {"name": "hosted-only", "producer": "operator-supplied", "consumers": ["gitops:arc"]},
                ],
                "operator_produced": [{"name": "opprod-unused", "producer": "cert-manager", "consumers": []}],
            },
            seed="seeded-and-read key:pw\nno-consumer key:pw\nundeclared-seed key:pw\n",
            golden=_es("good", "seeded-and-read") + _es("rogue", "never-declared") + _es("reads", "claims-seeder"),
        )
        fail, stale = violations(root)
        got = sorted(f.split("]")[0].lstrip("[") for f in fail)
        want = ["consumer", "forward", "opprod", "producer", "viceversa"]
        if got != want or stale:
            print(f"SELFTEST FAIL: want one of each assertion {want}, got {got}\n  " + "\n  ".join(fail))
            return 1
        if any("window-only" in f or "hosted-only" in f or "runtime-only" in f for f in fail):
            print("SELFTEST FAIL: a chart-gate, a hosted and a runtime consumer each satisfy the consumer assertion")
            return 1
        blind = _fixture(tmp / "blind", contract={"secrets": []}, seed="", golden="")
        if not any(f.startswith("[blind]") for f in violations(blind)[0]):
            print("SELFTEST FAIL: a tree with no renders and no seed table must fail as blind")
            return 1
        gate = _fixture(tmp / "gate", contract={"secrets": [
            {"name": "ghost-gate", "producer": "operator-supplied", "consumers": ["chart-gate:no-such-target"]}]},
            seed="seeded-and-read key:pw\n", golden=_es("good", "seeded-and-read"), targets=())
        if not any("ghost-gate" in f and "no `make no-such-target`" in f for f in violations(gate)[0]):
            print("SELFTEST FAIL: a chart-gate marker naming no real target was accepted")
            return 1
        (root / EXEMPTIONS).write_text(
            "forward:never-declared tracked in a follow-up\n"
            "producer:claims-seeder tracked in a follow-up\n"
            "viceversa:undeclared-seed tracked in a follow-up\n"
            "opprod:opprod-unused tracked in a follow-up\n"
            "consumer:no-consumer tracked in a follow-up\n"
            "forward:gone-away target no longer fails\n")
        fail3, stale3 = violations(root)
        if fail3 or len(stale3) != 1 or not stale3[0].startswith("forward:gone-away"):
            print(f"SELFTEST FAIL: exemptions silence each assertion and a stale one is reported, got {fail3} {stale3}")
            return 1

    fail, stale = violations(ROOT)
    if fail or stale:
        print("SELFTEST FAIL: the tree violates the secret contract:\n  " + "\n  ".join(fail + stale))
        return 1
    print("✅ self-test: each of the five assertions fails on a planted fixture; a runtime, a chart-gate and a "
          "hosted consumer are accepted; a chart-gate with no target fails; a blind tree fails; an exemption silences and a stale "
          "one fails; the tree is clean")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    fail, stale = violations(ROOT)
    for f in fail:
        print("::error::" + f)
    for s in stale:
        print("::error::stale exemption, delete the line: " + s)
    if fail or stale:
        return 1
    contract = yaml.safe_load((ROOT / CONTRACT).read_text()) or {}
    docs = list(golden_docs(ROOT))
    print(f"ok: secret-contract is consistent in both directions: {len(contract.get('secrets') or [])} "
          f"backend secrets, {len(contract.get('operator_produced') or [])} runtime Secrets, "
          f"{len(_remote_ref_keys(docs))} keys read by rendered ExternalSecrets, "
          f"{len(seed_keys(ROOT))} seeded keys")
    return 0


if __name__ == "__main__":
    sys.exit(main())
