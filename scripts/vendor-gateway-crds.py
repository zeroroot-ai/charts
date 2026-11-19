#!/usr/bin/env python3
"""Vendor the agentgateway CRDs and the Gateway API CRDs into gibson-crds (charts#815).

The umbrella installs the agentgateway controller as a subchart and renders a
Gateway, an AgentgatewayParameters and AgentgatewayPolicy objects. Helm
refuses a CRD and a CR of that CRD in one release (deploy#1627), so the CRDs
install with gibson-crds, through the first-party subchart

    helm/gibson-gateway-crd-files/files/crds/<source>.yaml

whose templates/crds.yaml includes them verbatim through .Files.Get. Each CRD
gets `helm.sh/resource-policy: keep`, like every other CRD gibson-crds ships.
It is a subchart, not files of gibson-crds, because a Helm release record
stores the files of the top-level chart as well as the manifest, under a
1 MiB cap (helm/gibson-operator-crds/Chart.yaml).

ONE SOURCE OF TRUTH for agentgateway. The version comes from the agentgateway
dependency of helm/gibson/Chart.yaml. The CRDs come from the chart
agentgateway-crds of the same version, and the chart digest must match the
pin below. Bump the controller and --check fails until this script runs.

The Gateway API has no chart. Its CRDs come from the release asset
standard-install.yaml of GATEWAY_API_VERSION, and the file must match the
sha256 below. The asset also holds a ValidatingAdmissionPolicy
(safe-upgrades), which is not a CRD and which this script does not vendor,
and three L4 route CRDs that GATEWAY_API_SKIP leaves out.

Modes:
    (default)    regenerate helm/gibson-gateway-crd-files/files/crds/*.yaml
    --check      exit 1 when a committed file differs from a fresh render
                 (compared as parsed YAML)
    --selftest   prove --check fails on a mutated copy (exit 2 if it cannot)

Needs helm and network access to the two upstream sources.
Exit: 0 ok · 1 drift or missing input · 2 self-test broken
"""

from __future__ import annotations

import copy
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import urllib.request

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
UMBRELLA = ROOT / "helm" / "gibson"
DEST = ROOT / "helm" / "gibson-gateway-crd-files" / "files" / "crds"
KEEP = {"helm.sh/resource-policy": "keep"}

AGW_CRDS_CHART = "oci://cr.agentgateway.dev/charts/agentgateway-crds"
# The digest of the chart agentgateway-crds of the pinned version. A version
# tag of an OCI chart can move; the digest cannot.
AGW_CRDS_DIGEST = {
    "v1.7.0": "sha256:abf0327cd44f89bc263d86f155db4094166cb2e6b920805bcf879d426ecd6d91",
}
GATEWAY_API_VERSION = "v1.6.3"
GATEWAY_API_URL = (
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/"
    f"{GATEWAY_API_VERSION}/standard-install.yaml"
)
GATEWAY_API_SHA256 = "356c2b8e286a964dc20995435659ad1095386329974793af10bbfe3eff977fe5"

# Gateway API CRDs this script does not vendor. The platform renders no L4
# route, and a Helm release record has a 1 MiB cap that these three would
# move closer (scripts/check-helm-record-size.sh). The agentgateway
# controller opens TCPRoute and TLSRoute informers that wait for the CRD
# (kclient.NewDelayedInformer), and it reads no UDPRoute.
GATEWAY_API_SKIP = {
    "tcproutes.gateway.networking.k8s.io",
    "tlsroutes.gateway.networking.k8s.io",
    "udproutes.gateway.networking.k8s.io",
}

UPSTREAM = {
    "agentgateway": ("Apache-2.0", "https://github.com/agentgateway/agentgateway"),
    "gateway-api": ("Apache-2.0", "https://github.com/kubernetes-sigs/gateway-api"),
}


def agentgateway_version() -> str:
    chart = yaml.safe_load((UMBRELLA / "Chart.yaml").read_text())
    for d in chart.get("dependencies", []):
        if d["name"] == "agentgateway":
            return d["version"]
    sys.exit("ERROR: helm/gibson/Chart.yaml lists no agentgateway dependency (charts#815)")


def crds_of(text: str) -> list[dict]:
    docs = []
    for d in yaml.safe_load_all(text):
        if not isinstance(d, dict) or d.get("kind") != "CustomResourceDefinition":
            continue
        d = copy.deepcopy(d)
        md = d.setdefault("metadata", {})
        ann = md.get("annotations") or {}
        ann.update(KEEP)
        md["annotations"] = ann
        docs.append(d)
    docs.sort(key=lambda d: d["metadata"]["name"])
    return docs


def render_agentgateway(version: str) -> list[dict]:
    want = AGW_CRDS_DIGEST.get(version)
    if not want:
        sys.exit(f"ERROR: no chart digest pinned for agentgateway-crds {version}. "
                 "Add it to AGW_CRDS_DIGEST after you check it.")
    with tempfile.TemporaryDirectory() as td:
        subprocess.run(["helm", "pull", AGW_CRDS_CHART, "--version", version, "-d", td],
                       check=True, capture_output=True, text=True)
        tgz = next(pathlib.Path(td).glob("*.tgz"))
        got = "sha256:" + hashlib.sha256(tgz.read_bytes()).hexdigest()
        if got != want:
            sys.exit(f"ERROR: agentgateway-crds {version} has digest {got}, the pin is {want}")
        out = subprocess.run(["helm", "template", "gibson-crds", str(tgz), "--namespace", "gibson"],
                             check=True, capture_output=True, text=True).stdout
    docs = crds_of(out)
    if not docs:
        sys.exit(f"ERROR: agentgateway-crds {version} rendered no CustomResourceDefinition")
    return docs


def render_gateway_api() -> list[dict]:
    with urllib.request.urlopen(GATEWAY_API_URL, timeout=60) as r:  # noqa: S310 (a fixed https URL)
        body = r.read()
    got = hashlib.sha256(body).hexdigest()
    if got != GATEWAY_API_SHA256:
        sys.exit(f"ERROR: {GATEWAY_API_URL} has sha256 {got}, the pin is {GATEWAY_API_SHA256}")
    docs = [d for d in crds_of(body.decode()) if d["metadata"]["name"] not in GATEWAY_API_SKIP]
    if not docs:
        sys.exit(f"ERROR: {GATEWAY_API_URL} holds no CustomResourceDefinition")
    return docs


def sources() -> dict[str, tuple[str, list[dict]]]:
    v = agentgateway_version()
    return {
        "agentgateway": (f"agentgateway-crds {v} ({AGW_CRDS_CHART})", render_agentgateway(v)),
        "gateway-api": (f"Gateway API {GATEWAY_API_VERSION} standard-install.yaml", render_gateway_api()),
    }


def dump(name: str, origin: str, docs: list[dict]) -> str:
    license, upstream = UPSTREAM[name]
    header = (
        f"# GENERATED by scripts/vendor-gateway-crds.py from {origin}.\n"
        "# Do not edit. Change the pin, run `make vendor-gateway-crds`, and commit\n"
        "# the result.\n"
        "#\n"
        f"# Upstream: {upstream}\n"
        f"# License:  {license} — full text in LICENSES/{license}.txt, attribution in NOTICE.\n"
        "# This file is a VERBATIM redistribution of that project's CRDs.\n"
    )
    body = yaml.safe_dump_all(docs, sort_keys=False, width=1 << 20, allow_unicode=True,
                              explicit_start=True)
    return header + body


def load_committed(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    return [d for d in yaml.safe_load_all(path.read_text()) if isinstance(d, dict)]


def drift(fresh: dict[str, tuple[str, list[dict]]], dest: pathlib.Path) -> list[str]:
    problems = []
    for name, (origin, docs) in fresh.items():
        path = dest / f"{name}.yaml"
        committed = load_committed(path)
        if committed != docs:
            problems.append(f"{path.name} differs from {origin} "
                            f"({len(committed)} committed vs {len(docs)} rendered CRDs)")
    extra = sorted(p.name for p in dest.glob("*.yaml") if p.stem not in fresh)
    problems += [f"{e} is not produced by this script" for e in extra]
    return problems


def selftest() -> int:
    fresh = sources()
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        for name, (origin, docs) in fresh.items():
            (d / f"{name}.yaml").write_text(dump(name, origin, docs))
        if drift(fresh, d):
            print("GUARD BROKEN: a fresh copy reads as drift", file=sys.stderr)
            return 2
        origin, docs = fresh["agentgateway"]
        (d / "agentgateway.yaml").write_text(dump("agentgateway", origin, docs[1:]))
        if not drift(fresh, d):
            print("GUARD BROKEN: a dropped CRD was not detected", file=sys.stderr)
            return 2
        mutated = copy.deepcopy(docs)
        mutated[0]["metadata"]["annotations"].pop("helm.sh/resource-policy")
        (d / "agentgateway.yaml").write_text(dump("agentgateway", origin, mutated))
        if not drift(fresh, d):
            print("GUARD BROKEN: a dropped keep annotation was not detected", file=sys.stderr)
            return 2
        (d / "agentgateway.yaml").write_text(dump("agentgateway", origin, docs))
        (d / "stray.yaml").write_text("---\n")
        if not drift(fresh, d):
            print("GUARD BROKEN: a hand-written file was not detected", file=sys.stderr)
            return 2
    print("✅ selftest passed: --check detects a dropped CRD, a dropped keep annotation and a stray file")
    return 0


def main(argv: list[str]) -> int:
    if "--selftest" in argv:
        return selftest()
    fresh = sources()
    if "--check" in argv:
        problems = drift(fresh, DEST)
        if problems:
            print("❌ vendored gateway CRDs are stale:")
            for p in problems:
                print(f"   {p}")
            print("   Run `make vendor-gateway-crds` and commit helm/gibson-gateway-crd-files/files/crds/.")
            return 1
        print("✅ vendored gateway CRDs match the pinned agentgateway release and Gateway API asset")
        return 0
    DEST.mkdir(parents=True, exist_ok=True)
    for name, (origin, docs) in fresh.items():
        path = DEST / f"{name}.yaml"
        path.write_text(dump(name, origin, docs))
        print(f"  wrote {path.relative_to(ROOT)} ({len(docs)} CRDs from {origin})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
