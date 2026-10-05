#!/usr/bin/env python3
"""gen-env-readers.py — vendor each first-party service's env reader set.

ADR-0094 layer 2. The chart injects env vars into containers whose source lives
in other repos, so the consumer side of that contract cannot be read at PR time
without checking out three repos. Instead each service's reader set is extracted
here and committed under `helm/contracts/`, and `check-env-consumed.py` compares
against the committed copy. A nightly job re-runs this and fails on drift, so
the vendored copy cannot silently age.

Each service is read AT THE TAG THE CHART PINS (scripts/pinned_source.py), never
at its `main`. The contract describes the code inside the pinned image. Read at
`main`, it went stale with every merge in the service repo and the nightly was
red on the day after each sync.

Extraction is deliberately literal. For Go it takes the string arguments to
`os.Getenv`, `os.LookupEnv` and the repo-local `envOr`/`envBool`/`envInt`
helpers. For the dashboard it takes the `REQUIRED_ENV` and `OPTIONAL_ENV` blocks
in `src/lib/env-validator.ts` plus every `process.env.NAME`. A name built at
runtime from a prefix is not extractable and would be a false "unread", which is
why no service in this estate constructs env names dynamically: `cmd/ext-authz`
reads 27 literal names, and that is checked by this script finding them.

  gen-env-readers.py --write            refresh helm/contracts/
  gen-env-readers.py --check            exit 1 when the committed copy is stale
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from pinned_source import pinned_tag, tree_at

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "helm" / "contracts"

# Sibling clones. Each service names the repo that builds its image. A service
# is listed only when the chart renders one of its images: `billing` was listed
# with no rendered image, so its contract governed no container and its
# checkout was the one reason the nightly held a long-lived personal token.
SERVICES: dict[str, dict] = {
    "gibson": {"repo": "gibson", "lang": "go",
               "images": ["gibson", "ext-authz", "tenant-operator", "platform-operator",
                          "connector-operator", "gibson-bootstrap-runner",
                          "internal-authz-registry", "spiffe-jwks-exporter"]},
    "dashboard": {"repo": "dashboard", "lang": "ts", "images": ["dashboard"]},
    "setec": {"repo": "setec", "lang": "go",
              "images": ["setec", "setec-frontend", "setec-node-agent",
                         "setec-runtime-agent", "setec-installer", "setec-keepalive"]},
    "docs-site": {"repo": "docs-site", "lang": "sh", "images": ["docs-site"]},
}

# Deliberately OVER-APPROXIMATING: every env-shaped string literal in the
# service's source, not only the arguments to a known list of helpers.
#
# Enumerating helpers was the first design and it was wrong in the dangerous
# direction. `cmd/ext-authz` reads 27 literal EXT_AUTHZ_* names, and a list of
# os.Getenv/envOr/envBool/envInt caught 20 of them: seven more go through
# `durationOr` and `intOr`, repo-local helpers the list did not know. A reader
# set that is MISSING a name makes the gate report a live env var as dead, and
# deleting one of those is how the first attempt at the values gate rendered the
# first-admin Job with `image: ":"`.
#
# Over-approximating costs recall — a constant that merely looks like an env name
# counts as a reader — and buys the property that matters: anything this gate
# reports as unread is unread.
ENV_SHAPED = re.compile(r'''['"`]([A-Z][A-Z0-9_]{2,})['"`]''')
# A validated-env object reads names as PROPERTIES, not string literals:
# `env.ADMIN_ENVOY_BASE_URL`. Without this the dashboard's whole config layer
# looks like it reads nothing. Matching a bare property is loose, which is the
# safe direction here.
ENV_PROP = re.compile(r'\.([A-Z][A-Z0-9_]{2,})\b')
TS_PROCESS = re.compile(r'process\.env\.([A-Z][A-Z0-9_]*)')
TS_PROCESS_IDX = re.compile(r'process\.env\[\s*"([A-Z][A-Z0-9_]*)"')
TS_BLOCK = re.compile(r'"([A-Z][A-Z0-9_]*)"')
SH_READ = re.compile(r'\$\{?([A-Z][A-Z0-9_]*)')

SKIP_DIRS = {".git", "node_modules", ".next", "vendor", "dist", "build", ".worktrees",
             ".turbo", "testdata", ".venv", "__pycache__", "out", ".cache"}


def _walk(base: Path, exts: tuple[str, ...]):
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(exts):
                yield Path(dirpath) / f


def _strip_comments(text: str, ext: str) -> str:
    if ext in (".go", ".ts", ".tsx", ".js", ".mjs"):
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"(^|\s)//[^\n]*", "", text)
    elif ext in (".sh", ".bash"):
        text = re.sub(r"(^|\s)#[^\n]*", "", text)
    return text


def readers(service: str, repo_root: Path) -> set[str]:
    """Env names this service's code reads. Comments are stripped first: a name
    mentioned only in a comment is not a reader, and counting one is how
    LOKI_URL survived the first pass of this audit."""
    spec = SERVICES[service]
    found: set[str] = set()
    if spec["lang"] == "go":
        for p in _walk(repo_root, (".go",)):
            if p.name.endswith("_test.go"):
                continue
            t = _strip_comments(p.read_text(errors="replace"), ".go")
            found |= set(ENV_SHAPED.findall(t)) | set(ENV_PROP.findall(t))
    elif spec["lang"] == "ts":
        for p in _walk(repo_root, (".ts", ".tsx", ".mjs", ".js")):
            if ".test." in p.name or ".spec." in p.name:
                continue
            t = _strip_comments(p.read_text(errors="replace"), p.suffix)
            found |= (set(TS_PROCESS.findall(t)) | set(TS_PROCESS_IDX.findall(t))
                      | set(ENV_SHAPED.findall(t)) | set(ENV_PROP.findall(t)))
        # the declared contract blocks, which are the authority for this service
        ev = repo_root / "src" / "lib" / "env-validator.ts"
        if ev.exists():
            t = _strip_comments(ev.read_text(errors="replace"), ".ts")
            for block in ("REQUIRED_ENV", "OPTIONAL_ENV"):
                m = re.search(block + r"\s*=\s*\[(.*?)\n\]", t, re.S)
                if m:
                    found |= set(TS_BLOCK.findall(m.group(1)))
    elif spec["lang"] == "sh":
        for p in _walk(repo_root, (".sh", ".bash")):
            found |= set(SH_READ.findall(_strip_comments(p.read_text(errors="replace"), ".sh")))
    return found


def render(service: str, names: set[str], tag: str) -> str:
    spec = SERVICES[service]
    return (
        f"# {service}-env-readers.txt — GENERATED by scripts/gen-env-readers.py.\n"
        "# Do not hand-edit. Refresh with `make env-contract-sync`.\n"
        "#\n"
        f"# Every env var the {service} source reads, extracted from literal\n"
        "# arguments only, with comments stripped. check-env-consumed.py fails the\n"
        "# build when the chart injects a name into one of this service's\n"
        f"# containers that is absent here. Images: {', '.join(spec['images'])}.\n"
        "#\n"
        f"# Source: zeroroot-ai/{spec['repo']} @ {tag}\n"
        + "".join(f"{n}\n" for n in sorted(names))
    )


def main() -> int:
    write = "--write" in sys.argv
    check = "--check" in sys.argv
    if not (write or check):
        print(__doc__)
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    missing: list[str] = []
    for service in sorted(SERVICES):
        repo_name = SERVICES[service]["repo"]
        tag = pinned_tag(repo_name)
        with tree_at(repo_name, tag) as tree:
            names = readers(service, Path(tree))
        if not names:
            missing.append(f"{service}: extracted zero env readers from {repo_name} {tag}, which cannot be right")
            continue
        text = render(service, names, tag)
        target = OUT / f"{service}-env-readers.txt"
        if write:
            target.write_text(text)
            print(f"  wrote {target.relative_to(ROOT)}  ({len(names)} names at {tag})")
        else:
            if not target.exists():
                stale.append(f"{target.relative_to(ROOT)} is missing")
            elif target.read_text() != text:
                stale.append(f"{target.relative_to(ROOT)} differs from {repo_name} at {tag}")
    if missing:
        for m in missing:
            print("::error::" + m)
        return 2
    if stale:
        for s in stale:
            print("::error::stale env contract: " + s + " — run `make env-contract-sync`")
        return 1
    print("ok: every vendored env-reader set matches its source repo at the pinned tag")
    return 0


if __name__ == "__main__":
    sys.exit(main())
