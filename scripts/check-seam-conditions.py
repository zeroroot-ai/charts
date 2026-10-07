#!/usr/bin/env python3
"""check-seam-conditions.py: each dependency condition of a chart is a named seam (charts#381).

ADR-0087: a seam selects WHOSE service runs, never WHETHER one runs. Five
dependencies are seams: cert-manager, External Secrets, external-dns,
CloudNativePG and setec. The monitoring CRDs of gibson-crds are the monitoring
seam of the same ADR. No other dependency has a `condition:`.

The check reads each helm/*/Chart.yaml and fails when:

  - a dependency has a `condition:` that SEAMS does not name,
  - a SEAMS entry names a dependency or a condition that no Chart.yaml has,
  - the condition of a seam does not default to true in the values.yaml of
    its chart, or
  - the setec seam does not select whose fleet runs.

The setec seam (ADR-0087) is off when gibson.sandbox.setec.address names a
fleet outside this cluster. The check renders the umbrella with the seam off
four times. An empty address, the address of the in-chart frontend, and an
empty spiffeID must fail in validateSetecDispatch. An outside address must render, and the daemon
config must carry it.

  check-seam-conditions.py             exit 1 on a finding
  check-seam-conditions.py --selftest  prove each finding fails
"""
import glob
import os
import subprocess
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (chart, dependency, condition): the reason. Keyed by content, never by line.
SEAMS = {
    ("gibson", "cert-manager", "certManager.enabled"):
        "operator seam: a cluster that runs cert-manager uses its own",
    ("gibson", "external-secrets", "externalSecrets.enabled"):
        "operator seam: a cluster that runs External Secrets uses its own",
    ("gibson", "external-dns", "externalDns.enabled"):
        "operator seam: a cluster that runs external-dns uses its own",
    ("gibson", "cloudnative-pg", "cnpg.enabled"):
        "operator seam: a cluster that runs CloudNativePG uses its own",
    ("gibson-workloads", "setec", "setec.enabled"):
        "fleet seam: off means gibson.sandbox.setec.address names a fleet outside this cluster",
    ("gibson-crds", "prometheus-operator-crds", "prometheus-operator-crds.enabled"):
        "monitoring seam: a cluster that runs the Prometheus operator owns these CRDs",
}

SETEC_OFF = "gibson-workloads.setec.enabled"
SETEC_REFUSAL = "validateSetecDispatch"
SETEC_ADDRESS = "gibson-workloads.gibson.sandbox.setec.address"
SETEC_SPIFFE_ID = "gibson-workloads.gibson.sandbox.setec.spiffeID"


def load_charts(root):
    """{chart: (dependencies, values)} for each helm/<chart>/Chart.yaml."""
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "helm", "*", "Chart.yaml"))):
        chart_dir = os.path.dirname(path)
        with open(path) as f:
            meta = yaml.safe_load(f) or {}
        values = {}
        vpath = os.path.join(chart_dir, "values.yaml")
        if os.path.exists(vpath):
            with open(vpath) as f:
                values = yaml.safe_load(f) or {}
        out[os.path.basename(chart_dir)] = (meta.get("dependencies") or [], values)
    return out


def dig(values, dotted):
    cur = values
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def chart_findings(charts):
    findings = []
    seen = set()
    for chart, (deps, values) in charts.items():
        for dep in deps:
            cond = dep.get("condition")
            if not cond:
                continue
            key = (chart, dep.get("name"), cond)
            seen.add(key)
            if key not in SEAMS:
                findings.append(
                    f"{chart}/Chart.yaml: dependency {dep.get('name')!r} has condition {cond!r}, "
                    "and it is not a named seam. A seam selects whose service runs, never "
                    "whether one runs (ADR-0087). Remove the condition, or name the seam in "
                    "scripts/check-seam-conditions.py with its reason.")
                continue
            if dig(values, cond) is not True:
                findings.append(
                    f"{chart}/values.yaml: the seam condition {cond!r} defaults to "
                    f"{dig(values, cond)!r}. Each seam defaults to true (ADR-0087).")
    for key in SEAMS:
        if key not in seen:
            findings.append(
                f"SEAMS names {key[0]}/{key[1]} with condition {key[2]!r}, and no Chart.yaml "
                "has it. Delete the stale entry.")
    return findings


OUTSIDE_ADDRESS = "setec.fleet.example.com:443"
OUTSIDE_SPIFFE_ID = "spiffe://fleet.example.com/platform/setec-frontend"

# (case name, address, spiffeID, expect the render to pass)
SETEC_CASES = [
    ("no endpoint", "", OUTSIDE_SPIFFE_ID, False),
    ("the in-chart frontend", "setec-frontend.setec-system.svc.cluster.local:50051", OUTSIDE_SPIFFE_ID, False),
    ("an outside fleet with no spiffeID", OUTSIDE_ADDRESS, "", False),
    ("a fleet outside this cluster", OUTSIDE_ADDRESS, OUTSIDE_SPIFFE_ID, True),
]


def render_setec_off(root, address, spiffe_id):
    cmd = ["helm", "template", "gibson", os.path.join(root, "helm", "gibson"),
           "-f", os.path.join(root, "helm", "gibson", "values-baseline.yaml"),
           "-f", os.path.join(root, "helm", "testdata", "render-inputs", "gibson.yaml"),
           "--namespace", "gibson", "--set", f"{SETEC_OFF}=false",
           "--set-string", f"{SETEC_ADDRESS}={address}",
           "--set-string", f"{SETEC_SPIFFE_ID}={spiffe_id}"]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return p.returncode, p.stdout, p.stderr


def first_error(stderr):
    for line in stderr.splitlines():
        if line.startswith("Error"):
            return line
    return stderr.strip().splitlines()[-1] if stderr.strip() else "(no stderr)"


def render_findings(case, address, want_pass, returncode, stdout, stderr):
    if want_pass:
        if returncode != 0:
            tail = first_error(stderr)
            return [f"{case}: the render with {SETEC_OFF}=false and address {address!r} "
                    f"fails: {tail}. The seam must select an outside fleet (ADR-0087)."]
        if address not in stdout:
            return [f"{case}: the render passes, but no rendered object carries the "
                    f"address {address!r}. The daemon would not dial the outside fleet."]
        return []
    if returncode == 0:
        return [f"{case}: the render with {SETEC_OFF}=false and address {address!r} passes. "
                "It must refuse it: the daemon would dial a frontend that does not exist."]
    if SETEC_REFUSAL not in stderr:
        tail = first_error(stderr)
        return [f"{case}: the render fails, but not in {SETEC_REFUSAL}: {tail}. "
                "The check cannot prove the refusal."]
    return []


def selftest():
    full = {}
    for (chart, dep, cond) in SEAMS:
        deps, values = full.setdefault(chart, ([], {}))
        deps.append({"name": dep, "condition": cond})
        cur = values
        parts = cond.split(".")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = True
    cases = [
        ("the full seam list passes", full, False),
        ("an unnamed condition fails",
         {**full, "gibson-velero": ([{"name": "velero", "condition": "velero.enabled"}], {})}, True),
        ("a stale seam entry fails",
         {k: v for k, v in full.items() if k != "gibson-crds"}, True),
        ("a seam that defaults to false fails",
         {**full, "gibson": (full["gibson"][0],
                             {**full["gibson"][1], "certManager": {"enabled": False}})}, True),
    ]
    failed = False
    for name, charts, want in cases:
        got = bool(chart_findings(charts))
        if got != want:
            print(f"  ✗ selftest: {name}: finding={got}, want {want}")
            failed = True
    render_cases = [
        ("an accepted empty endpoint fails", ("c", "", False, 0, "", ""), True),
        ("a refusal for another reason fails", ("c", "", False, 1, "", "Error: other"), True),
        ("the expected refusal passes", ("c", "", False, 1, "", f"Error: {SETEC_REFUSAL}: x"), False),
        ("a refused outside fleet fails", ("c", OUTSIDE_ADDRESS, True, 1, "", f"Error: {SETEC_REFUSAL}"), True),
        ("an outside fleet the config drops fails", ("c", OUTSIDE_ADDRESS, True, 0, "kind: X", ""), True),
        ("an outside fleet in the config passes", ("c", OUTSIDE_ADDRESS, True, 0, f"address: {OUTSIDE_ADDRESS}", ""), False),
    ]
    for name, args, want in render_cases:
        got = bool(render_findings(*args))
        if got != want:
            print(f"  ✗ selftest: {name}: finding={got}, want {want}")
            failed = True
    if failed:
        return 1
    print("  ✓ selftest: an unnamed condition, a stale entry, a false default, an accepted "
          "empty or in-cluster endpoint, a refused outside fleet and a dropped address each fail")
    return 0


def main():
    if sys.argv[1:] == ["--selftest"]:
        return selftest()
    if sys.argv[1:]:
        print(__doc__)
        return 2
    charts = load_charts(ROOT)
    if not any(deps for deps, _ in charts.values()):
        print("FAIL: no Chart.yaml with dependencies found; the check read nothing")
        return 1
    findings = chart_findings(charts)
    for case, address, spiffe_id, want_pass in SETEC_CASES:
        findings += render_findings(case, address, want_pass, *render_setec_off(ROOT, address, spiffe_id))
    for f in findings:
        print(f"FAIL: {f}")
    if findings:
        return 1
    print(f"  ✓ seam-conditions: {len(SEAMS)} named seams, each defaults to true; with setec off "
          "the render refuses no endpoint, the in-chart frontend and no spiffeID, and renders an outside fleet")
    return 0


if __name__ == "__main__":
    sys.exit(main())
