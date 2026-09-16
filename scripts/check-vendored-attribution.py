#!/usr/bin/env python3
"""Guard: every verbatim third-party redistribution carries its attribution.

helm/gibson-operator-crd-files/files/crds/*.yaml are CustomResourceDefinitions
copied verbatim out of cert-manager, External Secrets and CloudNativePG. All
three are Apache-2.0, and section 4 requires the license and the attribution
notices to travel with a redistribution. Before the charts repo is public that
is a compliance obligation; it is also the first thing an Iron Bank onboarding
review asks for.

The failure this guards is quiet. `scripts/vendor-operator-crds.py` writes the
header, so the only way a file loses it is an edit to that script or a
hand-written file dropped into the directory — neither of which any other guard
notices, and both of which ship silently.

Checks, once per run:
  0. NOTICE names the same vendored-CRD directory this guard reads. The two
     drifted apart once already: NOTICE kept an old path while this script read
     the real one, so the guard passed and the attribution artifact pointed at a
     directory that does not exist.

Checks, per vendored file:
  1. an `# Upstream: <url>` line
  2. a `# License:  <id>` line
  3. LICENSES/<id>.txt exists and is non-empty
  4. the file's chart name appears in NOTICE

Modes:
  (default)    ENFORCE — exit 1 on any finding
  --selftest   prove the guard fails on a header-stripped copy (exit 2 if not)

Exit: 0 clean · 1 a redistribution is missing its attribution · 2 self-test broken
"""

from __future__ import annotations

import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
CRDS = ROOT / "helm" / "gibson-operator-crd-files" / "files" / "crds"
LICENSES = ROOT / "LICENSES"
NOTICE = ROOT / "NOTICE"

# The directory above, written the way NOTICE has to write it.
CRDS_REL = "helm/gibson-operator-crd-files/files/crds/"

UPSTREAM_RE = re.compile(r"^#\s*Upstream:\s*(\S+)", re.M)
LICENSE_RE = re.compile(r"^#\s*License:\s*(\S+)", re.M)
# Any vendored-CRD directory NOTICE names. Every match must be CRDS_REL.
NOTICE_CRDS_RE = re.compile(r"helm/[A-Za-z0-9._-]+/files/crds/")


def audit(
    crds: pathlib.Path,
    licenses: pathlib.Path,
    notice: pathlib.Path,
    crds_rel: str,
) -> list[str]:
    problems: list[str] = []
    notice_text = notice.read_text() if notice.exists() else ""
    if not notice_text.strip():
        problems.append("NOTICE is missing or empty — Apache-2.0 section 4 needs it")
    named = sorted(set(NOTICE_CRDS_RE.findall(notice_text)))
    if named != [crds_rel]:
        problems.append(
            f"NOTICE names {named or 'no'} vendored-CRD directory, but this guard "
            f"reads '{crds_rel}'. The two must be the same path."
        )
    files = sorted(crds.glob("*.yaml"))
    if not files:
        problems.append(f"{crds} holds no vendored CRDs — did the path move?")
    for f in files:
        head = "\n".join(f.read_text().split("\n")[:20])
        rel = f.relative_to(ROOT) if ROOT in f.parents else f.name
        up = UPSTREAM_RE.search(head)
        lic = LICENSE_RE.search(head)
        if not up:
            problems.append(f"{rel}: no `# Upstream:` line. Regenerate with `make vendor-operators`.")
        if not lic:
            problems.append(f"{rel}: no `# License:` line. Regenerate with `make vendor-operators`.")
            continue
        text = licenses / f"{lic.group(1)}.txt"
        if not text.exists() or not text.read_text().strip():
            problems.append(f"{rel}: declares {lic.group(1)} but LICENSES/{lic.group(1)}.txt is missing or empty")
        if notice_text and f.stem not in notice_text:
            problems.append(f"{rel}: '{f.stem}' is not attributed anywhere in NOTICE")
    return problems


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)
        crds = d / "crds"; crds.mkdir()
        lic = d / "LICENSES"; lic.mkdir()
        (lic / "Apache-2.0.txt").write_text("Apache License\nVersion 2.0\n")
        rel = "helm/vendored/files/crds/"
        good_notice = f"{rel} holds these copies.\ncert-manager Apache-2.0\n"
        notice = d / "NOTICE"; notice.write_text(good_notice)

        good = ("# Upstream: https://example.invalid/x\n"
                "# License:  Apache-2.0 — full text in LICENSES/Apache-2.0.txt\n---\n")
        (crds / "cert-manager.yaml").write_text(good)
        if audit(crds, lic, notice, rel):
            print("SELFTEST BROKEN: a complete header was reported as a problem", file=sys.stderr)
            return 2

        # fixture 1: header stripped
        (crds / "cert-manager.yaml").write_text("---\nkind: CustomResourceDefinition\n")
        if not audit(crds, lic, notice, rel):
            print("SELFTEST BROKEN: a stripped header passed", file=sys.stderr)
            return 2

        # fixture 2: license named but its text absent
        (crds / "cert-manager.yaml").write_text(
            "# Upstream: https://example.invalid/x\n# License:  GPL-3.0\n---\n")
        if not audit(crds, lic, notice, rel):
            print("SELFTEST BROKEN: a license with no retained text passed", file=sys.stderr)
            return 2

        # fixture 3: attributed nowhere in NOTICE
        (crds / "unlisted.yaml").write_text(good)
        if not any("NOTICE" in p for p in audit(crds, lic, notice, rel)):
            print("SELFTEST BROKEN: a component absent from NOTICE passed", file=sys.stderr)
            return 2
        (crds / "unlisted.yaml").unlink()
        (crds / "cert-manager.yaml").write_text(good)

        # fixture 4: NOTICE names a different directory than the one read
        notice.write_text(good_notice.replace(rel, "helm/stale-name/files/crds/"))
        if not any("must be the same path" in p for p in audit(crds, lic, notice, rel)):
            print("SELFTEST BROKEN: a NOTICE path that drifted passed", file=sys.stderr)
            return 2

        # fixture 5: NOTICE names no vendored-CRD directory at all
        notice.write_text("cert-manager Apache-2.0\n")
        if not any("must be the same path" in p for p in audit(crds, lic, notice, rel)):
            print("SELFTEST BROKEN: a NOTICE with no path passed", file=sys.stderr)
            return 2

    print("check-vendored-attribution: selftest passed (guard can fail)")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    rc = selftest()
    if rc:
        return rc
    problems = audit(CRDS, LICENSES, NOTICE, CRDS_REL)
    if problems:
        print("check-vendored-attribution: FAIL", file=sys.stderr)
        for p in problems:
            print(f"  ❌ {p}", file=sys.stderr)
        return 1
    n = len(list(CRDS.glob("*.yaml")))
    print(f"check-vendored-attribution: OK — {n} vendored redistribution(s) carry upstream, license and NOTICE attribution")
    return 0


if __name__ == "__main__":
    sys.exit(main())
