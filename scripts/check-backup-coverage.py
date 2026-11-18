#!/usr/bin/env python3
"""check-backup-coverage.py — no datastore without a backup producer.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origins deploy#1496
and deploy#1547). A datastore is a StatefulSet with a volumeClaimTemplate,
or a CloudNativePG Cluster. Each must have a producer that writes it to the
durable bucket:

  - a CNPG Cluster carries spec.backup (continuous WAL archiving) and a
    ScheduledBackup names it;
  - a StatefulSet's namespace is included by a Velero Schedule in the
    gibson-velero release that backs pod volumes up by default
    (defaultVolumesToFsBackup), and the pod template's
    backup.velero.io/backup-volumes-excludes annotation does not name the
    claim (check-velero-volume-excludes.sh keeps that list exact; this guard
    keeps a claim OUT of it). A claim that is not data of record, such as a
    queue, is named with its reason in scripts/.backup-excluded-claims.txt.
    A line there that no excluded claim matches fails as stale.

Both charts are rendered the way every other offline guard renders them.

  check-backup-coverage.py             exit 1 on an unbacked datastore, 0 when clean
  check-backup-coverage.py --selftest  prove an unbacked StatefulSet and a CNPG Cluster without backup fail
"""
import fnmatch
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXCLUDED = os.path.join(ROOT, "scripts", ".backup-excluded-claims.txt")


def excluded_claims(path: str = EXCLUDED) -> set[str]:
    """`<Kind>/<name>:<claim>` of each line, keyed by content."""
    out = set()
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            out.add(line.split()[0])
    return out


def render(chart: str, values: list[str], ns: str) -> list[dict]:
    args = ["helm", "template", chart, f"helm/{chart}", "--namespace", ns]
    for v in values:
        args += ["-f", v]
    out = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def ns_of(d: dict, default: str) -> str:
    return (d.get("metadata") or {}).get("namespace") or default


def judge(umbrella: list[dict], velero: list[dict], default_ns: str = "gibson",
          allowed: set[str] | None = None) -> list[str]:
    allowed = set() if allowed is None else allowed
    used = set()
    schedules = [d for d in velero if d.get("kind") == "Schedule"]
    scheduled_backups = {d["spec"].get("cluster", {}).get("name") for d in umbrella if d.get("kind") == "ScheduledBackup"}
    out = []

    def velero_covers(ns: str) -> bool:
        for s in schedules:
            t = s["spec"].get("template") or {}
            inc = t.get("includedNamespaces") or ["*"]
            if not t.get("defaultVolumesToFsBackup"):
                continue
            if any(fnmatch.fnmatch(ns, pat) for pat in inc):
                return True
        return False

    for d in umbrella:
        k = d.get("kind")
        name = f"{k}/{(d.get('metadata') or {}).get('name')}"
        if k == "Cluster" and str(d.get("apiVersion", "")).startswith("postgresql.cnpg.io"):
            if not (d.get("spec") or {}).get("backup"):
                out.append(f"{name}: no spec.backup (no WAL archiving to the durable bucket)")
            if d["metadata"]["name"] not in scheduled_backups:
                out.append(f"{name}: no ScheduledBackup names it (no daily base backup)")
        if k == "StatefulSet":
            claims = [v["metadata"]["name"] for v in ((d.get("spec") or {}).get("volumeClaimTemplates") or [])]
            if not claims:
                continue
            ns = ns_of(d, default_ns)
            if not velero_covers(ns):
                out.append(f"{name} (ns {ns}): no Velero Schedule with defaultVolumesToFsBackup includes its namespace")
                continue
            ann = (((d["spec"].get("template") or {}).get("metadata") or {}).get("annotations") or {})
            excluded = {x.strip() for x in ann.get("backup.velero.io/backup-volumes-excludes", "").split(",") if x.strip()}
            for c in claims:
                if c in excluded and f"{name}:{c}" in allowed:
                    used.add(f"{name}:{c}")
                elif c in excluded:
                    out.append(f"{name}: claim {c} is in backup-volumes-excludes, so Velero never backs the data up")
    for stale in sorted(allowed - used):
        out.append(f"{stale}: a line of .backup-excluded-claims.txt that names no excluded claim (stale)")
    return out


FIXTURE_UMBRELLA = """
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: backed, namespace: gibson}
spec:
  volumeClaimTemplates: [{metadata: {name: data}}]
  template: {metadata: {annotations: {backup.velero.io/backup-volumes-excludes: tmp}}}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: excluded, namespace: gibson}
spec:
  volumeClaimTemplates: [{metadata: {name: data}}]
  template: {metadata: {annotations: {backup.velero.io/backup-volumes-excludes: "tmp,data"}}}
---
apiVersion: apps/v1
kind: StatefulSet
metadata: {name: elsewhere, namespace: nowhere}
spec:
  volumeClaimTemplates: [{metadata: {name: data}}]
  template: {metadata: {}}
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: pg-ok, namespace: gibson}
spec: {backup: {barmanObjectStore: {}}}
---
apiVersion: postgresql.cnpg.io/v1
kind: ScheduledBackup
metadata: {name: pg-ok-daily}
spec: {cluster: {name: pg-ok}}
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata: {name: pg-naked, namespace: gibson}
spec: {}
"""
FIXTURE_VELERO = """
apiVersion: velero.io/v1
kind: Schedule
metadata: {name: hourly}
spec:
  template: {includedNamespaces: ["gibson", "tenant-*"], defaultVolumesToFsBackup: true}
"""


def selftest() -> int:
    got = judge([d for d in yaml.safe_load_all(FIXTURE_UMBRELLA) if d], [d for d in yaml.safe_load_all(FIXTURE_VELERO) if d])
    want = ["StatefulSet/excluded: claim data", "StatefulSet/elsewhere (ns nowhere)", "Cluster/pg-naked: no spec.backup", "Cluster/pg-naked: no ScheduledBackup"]
    if len(got) != 4 or not all(any(g.startswith(w) for g in got) for w in want):
        print(f"SELFTEST FAIL: want {want}, got {got}")
        return 1
    # A listed queue claim passes, and a line that names no excluded claim fails as stale.
    fx_u = [d for d in yaml.safe_load_all(FIXTURE_UMBRELLA) if d]
    fx_v = [d for d in yaml.safe_load_all(FIXTURE_VELERO) if d]
    got = judge(fx_u, fx_v, allowed={"StatefulSet/excluded:data", "StatefulSet/backed:data"})
    if any(g.startswith("StatefulSet/excluded: claim data") for g in got) or \
            not any(g.startswith("StatefulSet/backed:data") and "stale" in g for g in got):
        print(f"SELFTEST FAIL: a listed claim must pass and a stale line must fail, got {got}")
        return 1
    live = judge(
        render("gibson", ["helm/gibson/values-baseline.yaml", "helm/testdata/render-inputs/gibson.yaml"], "gibson"),
        render("gibson-velero", ["helm/testdata/render-inputs/gibson-velero.yaml"], "velero"),
        allowed=excluded_claims(),
    )
    if live:
        print("SELFTEST FAIL: the render has an unbacked datastore:\n  " + "\n  ".join(live))
        return 1
    print("OK: an excluded claim, an uncovered namespace, a CNPG Cluster without backup and a stale claim line fail; "
          "a listed queue claim passes; every rendered datastore has a producer")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(
        render("gibson", ["helm/gibson/values-baseline.yaml", "helm/testdata/render-inputs/gibson.yaml"], "gibson"),
        render("gibson-velero", ["helm/testdata/render-inputs/gibson-velero.yaml"], "velero"),
        allowed=excluded_claims(),
    )
    if got:
        print("❌ datastores without a backup producer:\n  " + "\n  ".join(got))
        return 1
    print("✓ backup-coverage: every datastore the charts render has a backup producer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
