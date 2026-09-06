#!/usr/bin/env python3
"""Generate the air-gap image manifest from the chart, not from memory.

deploy#1171. `images.txt` and `images.yaml` were hand-maintained, and by the
time anyone looked, not one first-party entry matched the chart: the umbrella
was pinned at 0.104.0 against a repo at 0.110.x, `gibson:v0.35.1` /
`dashboard:v1.11.0` / `ext-authz:0.2.2` named tags the chart had long since
digest-pinned past, `tenant-operator:latest` was a tag the chart no longer
emits, and docs-site, setec, setec-runtime-agent, zitadel-login, www and the
whole `mirror/*` family were simply absent. An air-gapped install from that
list cannot come up — and the failure surfaces in a customer's disconnected
environment, which is the worst place to find out.

A hand-list of a rendered artifact will always drift. This renders the chart
and derives the list, so the only way to be wrong is for the chart to be wrong.

    ./generate-image-list.py            # rewrite images.txt + images.yaml
    ./generate-image-list.py --check    # fail if the committed files are stale

`--check` is what CI runs; it regenerates in memory and diffs.

WHY IT RENDERS EVERY PROFILE. Images differ per profile — saas-only workloads,
the setec stack on staging, dev-only surfaces. The air-gap list is a superset
by definition: a mirror that omits an image because the profile the operator
happens to run does not use it is a mirror that breaks the next profile.

WHY IT REBUILDS DEPENDENCIES FIRST. `charts/*.tgz` is gitignored build output.
`helm template` on the umbrella renders whatever is sitting in that directory,
which may have been packaged from another branch — this repo has produced false
render evidence that way before. The three `helm dependency update` calls are
ordered because gibson-workloads' own gibson-common dependency must exist
before the umbrella can package gibson-workloads at all; without it the render
fails outright with `no template "gibson.fullname"`.

WHY IT ALSO READS GIBSON'S COMPONENT CATALOG. The chart renders the platform,
not the work it dispatches. The daemon carries a component catalog
(gibson: internal/platform/componentcatalog/manifests/*.yaml, embedded in the
binary) that names the images setec pulls at dispatch time: the tool runner,
the CVE triage agent, the two zerocool sandboxes and the OSV MCP server. None
of them appears in any `helm template`, so a mirror built from the render
alone comes up and then dispatches nothing. The `dispatchTime` group is
derived from those manifests at the gibson commit recorded in
gibson-catalog.ref. A plain run moves that pin to gibson's current main and
rewrites the file; `--check` reads the committed pin, so CI is deterministic
and the pin moves only when the manifest is regenerated (release time).

DIGESTS ARE NOT RESOLVED HERE. Almost every image is now private
(ghcr.io/zeroroot-ai/*, including the mirror), so pinning needs registry auth
and a network round trip per image. That stays in resolve-digests.sh, run at
mirror time after `docker login ghcr.io`, exactly as the README describes. This
generator's job is that the *set* is right and complete.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
IMAGES_TXT = HERE / "images.txt"
IMAGES_YAML = HERE / "images.yaml"
MANIFEST = ROOT / ".release-please-manifest.json"
CATALOG_REF = HERE / "gibson-catalog.ref"
GIBSON_REPO = "zeroroot-ai/gibson"
GIBSON_CATALOG_DIR = "internal/platform/componentcatalog/manifests"
GITHUB_API = "https://api.github.com"
OCIREPOSITORY = ROOT / "bigbang" / "package" / "ocirepository.yaml"

# Ordered: gibson-common must exist before gibson-workloads can be packaged,
# and both subcharts before the umbrella.
DEP_CHARTS = ["helm/gibson-operators", "helm/gibson-workloads", "helm/gibson"]

# Every profile a consumer can install from this repo. The manifest is the
# union. The SaaS profiles (ci/values-staging, ci/values-saas) live in the
# hosted repo now and are not part of the air-gap package.
# The substrate and guest files are overlays: they render only on top of
# values-vanilla.yaml, the way the README installs them.
VANILLA = "helm/gibson/values-vanilla.yaml"
PROFILES = [
    ("self-hosted", [VANILLA]),
    ("eks", [VANILLA, "helm/gibson/values-eks.yaml"]),
    ("gke", [VANILLA, "helm/gibson/values-gke.yaml"]),
    ("aks", [VANILLA, "helm/gibson/values-aks.yaml"]),
    ("guest", [VANILLA, "helm/gibson/values-guest.yaml"]),
]

IMAGE_LINE_RX = re.compile(r'^\s+(?:image|customImage):\s*"?([^"\s]+)"?\s*$')

FIRST_PARTY_RX = re.compile(r"^ghcr\.io/zeroroot-ai/(?!mirror/)")
MIRROR_RX = re.compile(r"^ghcr\.io/zeroroot-ai/mirror/")

# Per-repository prose. The SET of images is derived; what each one is FOR is
# not derivable, so it is kept here and merged in. An image with no entry still
# lands in the manifest — with an empty role, which reads as a prompt to add
# one rather than as a silent omission.
NOTES: dict[str, dict[str, str]] = {
    "ghcr.io/zeroroot-ai/gibson": {"role": "daemon (run/execute + platform RPCs)"},
    "ghcr.io/zeroroot-ai/dashboard": {"role": "web UI (Next.js)"},
    "ghcr.io/zeroroot-ai/ext-authz": {
        "role": "Envoy ext_authz (auth edge — DO NOT replace with Istio AuthZ)"
    },
    "ghcr.io/zeroroot-ai/tenant-operator": {"role": "tenant operator"},
    "ghcr.io/zeroroot-ai/platform-operator": {
        "role": "platform operator (Zitadel OIDC apps, FGA aggregate root)"
    },
    "ghcr.io/zeroroot-ai/gibson-belief-sidecar": {"role": "ECS-brain belief field sidecar"},
    "ghcr.io/zeroroot-ai/docs-site": {"role": "customer documentation site"},
    "ghcr.io/zeroroot-ai/zitadel-login": {
        "role": "branded Login V2 fork (thin fork of ghcr.io/zitadel/zitadel-login)"
    },
    "ghcr.io/zeroroot-ai/setec": {"role": "setec microVM operator (untrusted-exec boundary)"},
    "ghcr.io/zeroroot-ai/setec-frontend": {"role": "setec frontend (daemon dispatch endpoint)"},
    "ghcr.io/zeroroot-ai/setec-runtime-agent": {"role": "setec in-guest runtime agent"},
    "ghcr.io/zeroroot-ai/billing": {
        "role": "closed Stripe entitlements provider (OPTIONAL — bypassable on-prem)",
        "hardening": "closed seam; federal/air-gap typically disables billing (no-op provider)",
    },
    "ghcr.io/zeroroot-ai/mirror/envoy": {"role": "edge proxy (auth chain)"},
    "ghcr.io/zeroroot-ai/mirror/ratelimit": {"role": "shared per-client edge quota service"},
    "ghcr.io/zeroroot-ai/mirror/coraza-proxy-wasm": {"role": "Edge WAF module: Coraza + OWASP CRS as an Envoy WASM filter"},
    "ghcr.io/zeroroot-ai/mirror/alpine-k8s": {"role": "kubectl/curl/jq job + init tooling"},
    "ghcr.io/zeroroot-ai/mirror/kubectl": {"role": "kubectl for hook Jobs and helm tests"},
    "ghcr.io/zeroroot-ai/mirror/alpine": {"role": "shell base for init containers"},
    "ghcr.io/zeroroot-ai/mirror/busybox": {"role": "shell base for wait-for init containers"},
    "ghcr.io/zeroroot-ai/mirror/curl": {"role": "probe/wait-for tooling"},
    "ghcr.io/zeroroot-ai/mirror/neo4j": {"role": "graph store"},
    "ghcr.io/zeroroot-ai/mirror/bitnami-postgresql": {"role": "psql client for setup Jobs"},
    "ghcr.io/zeroroot-ai/mirror/redis": {"role": "redis CLI for setup Jobs and helm tests"},
    "ghcr.io/zeroroot-ai/mirror/redis-stack-server": {
        "role": "Redis (RediSearch catalog/vector store)"
    },
    "ghcr.io/zeroroot-ai/mirror/redis-exporter": {"role": "Redis metrics exporter"},
    "ghcr.io/zeroroot-ai/mirror/openfga": {"role": "OpenFGA authz"},
    "ghcr.io/zeroroot-ai/mirror/openbao": {"role": "OpenBao (per-tenant secrets / static seal)"},
    "ghcr.io/zeroroot-ai/mirror/wait4x": {"role": "zitadel login readiness init container"},
    "ghcr.io/zeroroot-ai/mirror/aws-cli": {"role": "Redis backup CronJob"},
    "ghcr.io/zeroroot-ai/mirror/jaeger-all-in-one": {"role": "tracing (optional)"},
    "ghcr.io/zeroroot-ai/mirror/prometheus": {"role": "metrics (optional)"},
    "ghcr.io/zeroroot-ai/mirror/grafana": {"role": "dashboards (optional)"},
    "ghcr.io/zeroroot-ai/mirror/loki": {"role": "logs (optional)"},
    "ghcr.io/zeroroot-ai/mirror/promtail": {"role": "log shipping (optional)"},
    "ghcr.io/zeroroot-ai/mirror/stripe-mock": {
        "role": "Stripe mock",
        "hardening": "DEV/TEST ONLY — exclude from a federal mirror",
    },
    "ghcr.io/zeroroot-ai/mirror/mailpit": {
        "role": "SMTP capture",
        "hardening": "DEV/TEST ONLY — exclude from a federal mirror",
    },
    # Dispatch-time images, from gibson's component catalog.
    "ghcr.io/zeroroot-ai/gibson-executor": {
        "role": "tool runner: one image hosting every CLI parser (nmap, nuclei, trivy, ...)"
    },
    "ghcr.io/zeroroot-ai/cve-triage": {"role": "CVE triage agent"},
    "ghcr.io/zeroroot-ai/zerocool-agent": {"role": "zerocool sandbox agent (opencode)"},
    "ghcr.io/zeroroot-ai/zerocool-claude-agent": {
        "role": "zerocool sandbox agent (Claude Code)",
        "hardening": "needs api.anthropic.com or a claude.ai login at run time; no in-perimeter option",
    },
    "ghcr.io/stackloklabs/osv-mcp/server": {"role": "OSV MCP server (connector: osv)"},
    "ghcr.io/zitadel/zitadel": {"role": "Zitadel IdP (core)"},
    "ghcr.io/spiffe/spire-server": {"role": "SPIRE server (SVID issuance)"},
    "ghcr.io/spiffe/spire-agent": {"role": "SPIRE agent"},
    "ghcr.io/spiffe/spire-controller-manager": {"role": "SPIRE controller manager"},
    "ghcr.io/spiffe/spiffe-helper": {"role": "SVID sidecar helper"},
    "ghcr.io/spiffe/spiffe-csi-driver": {"role": "SPIFFE workload API CSI driver"},
    "ghcr.io/spiffe/oidc-discovery-provider": {"role": "SPIRE OIDC discovery (JWKS for OpenBao)"},
    "ghcr.io/stakater/reloader": {"role": "configmap/secret reloader"},
    "ghcr.io/cloudnative-pg/cloudnative-pg": {"role": "CloudNativePG operator"},
    "registry.k8s.io/sig-storage/csi-node-driver-registrar": {
        "role": "CSI node driver registrar (spire csi-driver dependency)"
    },
    "docker.io/smallstep/step-cli": {
        "role": "spire chart helm-test hook",
        "hardening": "helm test only — not a runtime workload",
    },
    "busybox": {
        "role": "spire chart helm-test hook",
        "hardening": "helm test only — not a runtime workload",
    },
    "cgr.dev/chainguard/bash": {
        "role": "spire chart helm-test hook",
        "hardening": "helm test only — not a runtime workload",
    },
    "cgr.dev/chainguard/min-toolkit-debug": {
        "role": "spire chart helm-test hook",
        "hardening": "helm test only — not a runtime workload",
    },
    # deploy#1343 removed the Bitnami redis subchart outright, so
    # `registry-1.docker.io/bitnami/redis:latest` no longer renders and has no
    # entry here. It was the last mutable-tag reference in the manifest. Do not
    # re-add a role entry without re-adding a consumer first.
}

# A repository whose reference carries a mutable tag is called out rather than
# silently listed: an air-gap mirror of a moving tag is not reproducible.
MUTABLE_TAGS = {"latest", "main", "master", "edge", "stable"}


def run(cmd: list[str], cwd: pathlib.Path) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout[-4000:])
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"command failed: {' '.join(cmd)}")
    return proc.stdout


def chart_version() -> str:
    """The released chart version, cross-checked against the package's own pin.

    Both are release-please-managed (`.release-please-manifest.json` and the
    `x-release-please-version` annotation on ocirepository.yaml's `ref.tag`),
    so they cannot disagree unless someone hand-edited one. If they ever do,
    the image list would describe a different chart than the package deploys —
    which is deploy#1171 in miniature — so fail loudly rather than pick one.
    """
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["."]
    m = re.search(r'^\s*tag:\s*"([^"]+)"', OCIREPOSITORY.read_text(encoding="utf-8"), re.M)
    if not m:
        raise SystemExit(f"no ref.tag found in {OCIREPOSITORY.relative_to(ROOT)}")
    if m.group(1) != version:
        raise SystemExit(
            f"version drift: {MANIFEST.name} says {version}, "
            f"{OCIREPOSITORY.relative_to(ROOT)} pins {m.group(1)}. "
            "Both are release-please-managed — do not hand-edit either."
        )
    return version


def build_dependencies() -> None:
    for chart in DEP_CHARTS:
        run(["helm", "dependency", "update", chart], cwd=ROOT)


def render_images() -> dict[str, set[str]]:
    """repository -> set of references, unioned over every install profile."""
    found: dict[str, set[str]] = {}
    for name, values in PROFILES:
        out = run(
            [
                "helm", "template", "gibson", "helm/gibson",
                *[arg for v in values for arg in ("--values", v)],
                "--namespace", "gibson",
                # Rendering helm-test hooks too: an air-gapped operator running
                # `helm test` needs those images mirrored as well.
                "--include-crds",
            ],
            cwd=ROOT,
        )
        for line in out.splitlines():
            m = IMAGE_LINE_RX.match(line)
            if not m:
                continue
            ref = m.group(1)
            if "{{" in ref or not ref:
                continue
            found.setdefault(repo_of(ref), set()).add(ref)
        del name
    return found


def github_get(path: str) -> bytes:
    """One GitHub REST call. A token is optional for the public gibson repo but
    lifts the anonymous rate limit; CI passes github.token as GH_TOKEN."""
    req = urllib.request.Request(
        GITHUB_API + path,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "generate-image-list"},
    )
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.URLError as exc:
        raise SystemExit(f"GitHub API {path}: {exc}") from exc


def committed_catalog_ref() -> str:
    if not CATALOG_REF.exists():
        raise SystemExit(f"{CATALOG_REF.relative_to(ROOT)} is missing; run without --check to create it")
    ref = CATALOG_REF.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise SystemExit(f"{CATALOG_REF.relative_to(ROOT)} must hold one 40-hex commit sha, got {ref!r}")
    return ref


def current_catalog_ref() -> str:
    """gibson's main HEAD, the pin a plain run records."""
    data = json.loads(github_get(f"/repos/{GIBSON_REPO}/commits/main"))
    return data["sha"]


def catalog_images(ref: str) -> dict[str, set[str]]:
    """repository -> set of references named by gibson's component catalog at ref."""
    listing = json.loads(github_get(f"/repos/{GIBSON_REPO}/contents/{GIBSON_CATALOG_DIR}?ref={ref}"))
    found: dict[str, set[str]] = {}
    for entry in listing:
        if not entry["name"].endswith(".yaml"):
            continue
        body = github_get(f"/repos/{GIBSON_REPO}/contents/{GIBSON_CATALOG_DIR}/{entry['name']}?ref={ref}")
        raw = json.loads(body)
        text = base64.b64decode(raw["content"]).decode("utf-8")
        for line in text.splitlines():
            m = IMAGE_LINE_RX.match(line)
            if m and m.group(1):
                found.setdefault(repo_of(m.group(1)), set()).add(m.group(1))
    if not found:
        raise SystemExit(f"no image references found in {GIBSON_REPO}@{ref}:{GIBSON_CATALOG_DIR}")
    return found


def repo_of(ref: str) -> str:
    return re.split(r"[:@]", ref, maxsplit=1)[0] if not ref.startswith("sha256") else ref


def tag_of(ref: str) -> str:
    body = ref.split("@", 1)[0]
    _, _, tail = body.rpartition(":")
    return tail if ":" in body and "/" not in tail else ""


def classify(repo: str) -> str:
    if FIRST_PARTY_RX.match(repo):
        return "firstParty"
    if MIRROR_RX.match(repo):
        return "mirrored"
    return "thirdParty"


# Deliberate exclusions, stated in the generated output so the next reader does
# not conclude the list is short by accident. Each of these HAS been checked
# against the current chart.
EXCLUSIONS = """\
# DELIBERATELY ABSENT — checked, not forgotten:
#
#   ghcr.io/zeroroot-ai/{billing,www}
#       SaaS-only. They ship in helm/saas-overlay/*, which this Big Bang
#       package does not deploy — it deploys the umbrella chart only. billing
#       is the closed seam and is bypassable on-prem (no-op provider); www is
#       absent from any self-hosted install by design.
#
#   ghcr.io/zeroroot-ai/mirror/velero{,-plugin-for-aws}
#       Velero ships as its own release, helm/gibson-velero, in its own
#       namespace, for the RBAC reason that chart's Chart.yaml states. This
#       package deploys the umbrella chart only, so its images are not in this
#       render. An air-gapped operator who wants backups mirrors
#       ghcr.io/zeroroot-ai/mirror/velero:v1.18.1 and
#       ghcr.io/zeroroot-ai/mirror/velero-plugin-for-aws:v1.14.2 as well; the
#       versions are pinned in helm/gibson-velero/values.yaml.
#
#   ghcr.io/zeroroot-ai/internal-authz-registry
#       An OCI artifact, not a runtime image, and the air-gap path does not
#       fetch it: the default path reads the registry from the daemon over
#       mTLS, and an air-gapped install uses sdk.bypassRegistryValidation +
#       sdk.embeddedRegistry (a mounted ConfigMap).
"""

HEADER_TXT = """\
# Flat air-gap mirror list for the Gibson Big Bang package.
#
# GENERATED — do not hand-edit. Regenerate with:
#     ./bigbang/images/generate-image-list.py
# CI (`airgap-image-list`) fails the build when this file is stale, because a
# hand-maintained copy of a rendered artifact always drifts, and it drifts
# invisibly: the failure lands in a disconnected customer environment
# (deploy#1171).
#
# Derived from `helm template` over EVERY ci/ profile (staging, saas,
# self-hosted), so the list is a superset — a mirror that omits an image
# because one profile does not use it breaks the next profile.
#
# Digests are NOT resolved here. Almost every image is private now
# (ghcr.io/zeroroot-ai/*, mirror included), so pinning needs registry auth:
# run `./resolve-digests.sh` after `docker login ghcr.io` at mirror time.
#
# The dispatchTime group is not rendered by the chart: it is read from the
# daemon's component catalog (zeroroot-ai/gibson, see gibson-catalog.ref),
# because setec pulls those images at dispatch time, not at install time.
#
# Mirror loop. `cosign copy` moves the signature alongside the image, which
# `crane copy` does not, so an air-gapped verifier can still check it:
#   while read -r img; do [ "${img#\\#}" = "$img" ] || continue
#     cosign copy "$img" "registry.il.example.mil/${img#*/}"; done < images.txt
#
""" + EXCLUSIONS

HEADER_YAML = """\
---
# Structured air-gap image manifest for the Gibson Big Bang package.
#
# GENERATED — do not hand-edit. Regenerate with:
#     ./bigbang/images/generate-image-list.py
# CI (`airgap-image-list`) fails the build when this file is stale
# (deploy#1171).
#
# `role` and `hardening` are prose and cannot be derived from a render, so they
# come from the NOTES table in the generator. The SET of images is derived: an
# image with no note still appears here, with an empty role, which reads as a
# prompt rather than as a silent omission.
#
# digest: intentionally empty. Almost every image is private, so pinning needs
# registry auth — run ./resolve-digests.sh at mirror time.
#
# The dispatchTime group is not rendered by the chart: it is read from the
# daemon's component catalog (zeroroot-ai/gibson, see gibson-catalog.ref),
# because setec pulls those images at dispatch time, not at install time.
#
""" + EXCLUSIONS

SECTION_TITLES = {
    "firstParty": "First-party platform images (private; ELv2 + the closed billing seam).",
    "mirrored": (
        "Re-mirrored upstreams under ghcr.io/zeroroot-ai/mirror (private).\n"
        "# Upstream sources live in zeroroot-ai/.github :: mirror-list.yaml — mirror\n"
        "# from there when standing up a fresh pull-through mirror."
    ),
    "thirdParty": (
        "Third-party images pulled directly by subcharts (public upstream).\n"
        "# The SPIRE/SPIFFE entries ARE the platform auth edge preserved by this\n"
        "# package — they are not replaced by Istio."
    ),
    "dispatchTime": (
        "Dispatch-time images (private first-party plus one public upstream).\n"
        "# Named by the daemon's component catalog, pulled by setec when a mission\n"
        "# dispatches a tool, an agent or a connector. Absent from every render."
    ),
}

GROUP_ORDER = ("firstParty", "mirrored", "thirdParty", "dispatchTime")


def build_txt(version: str, groups: dict[str, list[str]]) -> str:
    out = [HEADER_TXT, "", "# Helm OCI chart (the deployable artifact)",
           f"ghcr.io/zeroroot-ai/charts/gibson:{version}", ""]
    for kind in GROUP_ORDER:
        refs = groups.get(kind, [])
        if not refs:
            continue
        out.append("# " + SECTION_TITLES[kind].replace("\n# ", "\n# "))
        for ref in refs:
            note = NOTES.get(repo_of(ref), {})
            if "hardening" in note:
                out.append(f"# {note['hardening']}")
            out.append(ref)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def build_yaml(version: str, groups: dict[str, list[str]], catalog_ref: str) -> str:
    lines = [HEADER_YAML, "", "chart:",
             f"  - ref: ghcr.io/zeroroot-ai/charts/gibson:{version}",
             '    digest: ""',
             "    role: umbrella-helm-chart", "",
             "# The gibson commit whose component catalog the dispatchTime group is",
             "# derived from (bigbang/images/gibson-catalog.ref).",
             f"gibsonCatalogRef: {catalog_ref}"]
    for kind in GROUP_ORDER:
        refs = groups.get(kind, [])
        if not refs:
            continue
        lines.append("")
        lines.append("# " + SECTION_TITLES[kind])
        lines.append(f"{kind}:")
        for ref in refs:
            note = NOTES.get(repo_of(ref), {})
            lines.append(f"  - ref: {ref}")
            lines.append('    digest: ""')
            lines.append(f'    role: "{note.get("role", "")}"')
            hardening = note.get("hardening")
            # A moving tag is not mirrorable reproducibly. Say so — unless the
            # note already says it, so the line does not repeat itself.
            if (
                (tag_of(ref) in MUTABLE_TAGS or tag_of(ref) == "")
                and "@sha256:" not in ref
                and "MUTABLE" not in (hardening or "")
            ):
                shown = tag_of(ref) or "latest (no tag given)"
                mutable = (
                    f"MUTABLE TAG `:{shown}` — a federal build MUST pin a digest"
                )
                hardening = f"{hardening}; {mutable}" if hardening else mutable
            if hardening:
                lines.append(f'    hardening: "{hardening}"')
    return "\n".join(lines).rstrip() + "\n"


def generate(catalog_ref: str) -> tuple[str, str]:
    version = chart_version()
    by_repo = render_images()
    groups: dict[str, list[str]] = {}
    for repo, refs in by_repo.items():
        groups.setdefault(classify(repo), []).extend(sorted(refs))
    for refs in catalog_images(catalog_ref).values():
        groups.setdefault("dispatchTime", []).extend(sorted(refs))
    for kind in groups:
        groups[kind] = sorted(set(groups[kind]))
    return build_txt(version, groups), build_yaml(version, groups, catalog_ref)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="fail if the committed files differ from a fresh render")
    ap.add_argument("--skip-deps", action="store_true",
                    help="skip `helm dependency update` (only when charts/ is known fresh)")
    args = ap.parse_args(argv)

    if not args.skip_deps:
        build_dependencies()

    if args.check:
        catalog_ref = committed_catalog_ref()
    else:
        catalog_ref = current_catalog_ref()
        CATALOG_REF.write_text(catalog_ref + "\n", encoding="utf-8")

    txt, yml = generate(catalog_ref)

    if args.check:
        stale = [
            p.relative_to(ROOT)
            for p, want in ((IMAGES_TXT, txt), (IMAGES_YAML, yml))
            if p.read_text(encoding="utf-8") != want
        ]
        if stale:
            print("", file=sys.stderr)
            for p in stale:
                print(f"stale: {p}", file=sys.stderr)
            print(
                "\n[airgap-image-list] the air-gap manifest no longer matches the chart.\n\n"
                "Regenerate and commit:\n"
                "    ./bigbang/images/generate-image-list.py\n\n"
                "This is generated output — an air-gapped install is built from it, and\n"
                "a missing image surfaces in a disconnected customer environment\n"
                "(deploy#1171).\n",
                file=sys.stderr,
            )
            return 1
        print("[airgap-image-list] OK — the air-gap manifest matches the chart")
        return 0

    IMAGES_TXT.write_text(txt, encoding="utf-8")
    IMAGES_YAML.write_text(yml, encoding="utf-8")
    n = sum(1 for ln in txt.splitlines() if ln and not ln.startswith("#"))
    print(f"[airgap-image-list] wrote images.txt + images.yaml ({n} images)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
