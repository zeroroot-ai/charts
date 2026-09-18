#!/usr/bin/env python3
"""check-workload-rbac.py — every grant to a platform ServiceAccount is on an allowlist keyed by content.

Rebuilds a guard lost in the 2026-09-04 split (charts#17, origins deploy#1210
and deploy#1363). RBAC is the blast radius: what a compromised workload can
do is exactly the rules bound to its ServiceAccount. This guard renders the
baseline profile, folds every RoleBinding and ClusterRoleBinding whose
subject is a ServiceAccount into (subject, role, digest of the role's
rules), and compares that set with helm/gibson/rbac-allowlist.yaml. A grant
that is not on the list, or whose rules changed (the digest moves), fails
with the new line to add, so widening any workload's authority is a visible,
reviewed edit to the allowlist and never a side effect of a template change.
An allowlist line the render no longer produces fails too, so the list
cannot go stale.

The digest is over the role's rules (apiGroups, resources, resourceNames,
verbs, nonResourceURLs), sorted, so a comment or a reorder does not move it
and a verb does.

  check-workload-rbac.py             exit 1 on an unlisted, changed or stale grant, 0 when clean
  check-workload-rbac.py --selftest  prove a widened rule and an unlisted grant fail
  check-workload-rbac.py --print     print the current render's lines (to seed or update the allowlist)
"""
import hashlib
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALLOWLIST = os.path.join(ROOT, "helm", "gibson", "rbac-allowlist.yaml")


def render() -> list[dict]:
    out = subprocess.run(
        ["helm", "template", "gibson", "helm/gibson",
         "-f", "helm/gibson/values-baseline.yaml", "-f", "helm/testdata/render-inputs/gibson.yaml",
         "--namespace", "gibson"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def rules_digest(rules: list) -> str:
    norm = []
    for r in rules or []:
        norm.append({
            "apiGroups": sorted(r.get("apiGroups") or []),
            "resources": sorted(r.get("resources") or []),
            "resourceNames": sorted(r.get("resourceNames") or []),
            "verbs": sorted(r.get("verbs") or []),
            "nonResourceURLs": sorted(r.get("nonResourceURLs") or []),
        })
    norm.sort(key=lambda x: json.dumps(x, sort_keys=True))
    return hashlib.sha256(json.dumps(norm, sort_keys=True).encode()).hexdigest()[:16]


def grants(docs: list[dict], default_ns: str = "gibson") -> dict[str, str]:
    """(subject ns/sa -> role kind/name [binding ns]) -> rules digest."""
    roles = {}
    for d in docs:
        if d.get("kind") in ("Role", "ClusterRole"):
            ns = (d["metadata"].get("namespace") or default_ns) if d["kind"] == "Role" else ""
            roles[(d["kind"], ns, d["metadata"]["name"])] = rules_digest(d.get("rules"))
    out = {}
    for d in docs:
        if d.get("kind") not in ("RoleBinding", "ClusterRoleBinding"):
            continue
        ref = d.get("roleRef") or {}
        bns = d["metadata"].get("namespace") or default_ns if d["kind"] == "RoleBinding" else ""
        key = (ref.get("kind"), bns if ref.get("kind") == "Role" else "", ref.get("name"))
        digest = roles.get(key, "external")
        for s in d.get("subjects") or []:
            if s.get("kind") != "ServiceAccount":
                continue
            subj = f"{s.get('namespace') or default_ns}/{s['name']}"
            scope = f"in {bns}" if d["kind"] == "RoleBinding" else "cluster-wide"
            out[f"{subj} -> {ref.get('kind')}/{ref.get('name')} {scope}"] = digest
    return out


def load_allowlist() -> dict[str, str]:
    data = yaml.safe_load(open(ALLOWLIST)) or {}
    return {k: str(v) for k, v in (data.get("grants") or {}).items()}


def judge(current: dict[str, str], allowed: dict[str, str]) -> list[str]:
    out = []
    for k, digest in sorted(current.items()):
        if k not in allowed:
            out.append(f"unlisted grant: {k!r}: {digest}")
        elif allowed[k] != digest:
            out.append(f"rules changed: {k!r}: allowlisted {allowed[k]}, rendered {digest}")
    for k in sorted(allowed):
        if k not in current:
            out.append(f"stale allowlist entry (nothing renders it): {k!r}")
    return out


FIXTURE = """
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: reader, namespace: gibson}
rules:
- apiGroups: ['']
  resources: [secrets]
  verbs: [get, list]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: reader, namespace: gibson}
roleRef: {kind: Role, name: reader}
subjects:
- {kind: ServiceAccount, name: app, namespace: gibson}
"""


def selftest() -> int:
    docs = [d for d in yaml.safe_load_all(FIXTURE) if d]
    cur = grants(docs)
    key = "gibson/app -> Role/reader in gibson"
    if list(cur) != [key]:
        print(f"SELFTEST FAIL: want one grant {key!r}, got {cur}")
        return 1
    if judge(cur, dict(cur)):
        print("SELFTEST FAIL: an allowlist equal to the render must pass")
        return 1
    # THE FIXTURE THIS EXISTS FOR: one more verb moves the digest.
    docs[0]["rules"][0]["verbs"].append("delete")
    widened = grants(docs)
    got = judge(widened, dict(cur))
    if len(got) != 1 or not got[0].startswith("rules changed"):
        print(f"SELFTEST FAIL: a widened rule must fail as 'rules changed', got {got}")
        return 1
    got = judge(widened, {})
    if len(got) != 1 or not got[0].startswith("unlisted grant"):
        print(f"SELFTEST FAIL: an unlisted grant must fail, got {got}")
        return 1
    got = judge({}, dict(cur))
    if len(got) != 1 or not got[0].startswith("stale allowlist entry"):
        print(f"SELFTEST FAIL: a stale entry must fail, got {got}")
        return 1
    live = judge(grants(render()), load_allowlist())
    if live:
        print("SELFTEST FAIL: the render and the allowlist disagree:\n  " + "\n  ".join(live))
        return 1
    print("OK: a widened rule, an unlisted grant and a stale entry fail; the render matches the allowlist")
    return 0


def main() -> int:
    if "--print" in sys.argv:
        for k, v in sorted(grants(render()).items()):
            print(f"  {k!r}: {v}")
        return 0
    if "--selftest" in sys.argv:
        return selftest()
    got = judge(grants(render()), load_allowlist())
    if got:
        print("❌ workload RBAC drifted from helm/gibson/rbac-allowlist.yaml (update the line for a deliberate change):\n  " + "\n  ".join(got))
        return 1
    print("✓ workload-rbac: every grant to a ServiceAccount is on the allowlist with its rules digest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
