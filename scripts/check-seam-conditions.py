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
  - a shipped profile (helm/gibson/values-*.yaml) turns the setec seam off.

The setec seam selects whose fleet runs. A fleet in another cluster needs the
endpoint pair of ADR-0087 (gibson.sandbox.setec.address and spiffeID), and
that pair is not built. So the render refuses setec.enabled=false today. The
check renders the umbrella with the seam off and fails when the render passes,
or when it fails for a reason other than validateSetecDispatch.

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
        "fleet seam: off means the fleet runs in another cluster (not built, the render refuses off)",
    ("gibson-crds", "prometheus-operator-crds", "prometheus-operator-crds.enabled"):
        "monitoring seam: a cluster that runs the Prometheus operator owns these CRDs",
}

SETEC_OFF = "gibson-workloads.setec.enabled"
SETEC_REFUSAL = "validateSetecDispatch"


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


def profile_findings(profiles):
    """profiles: {file name: parsed values}. A shipped profile never turns setec off."""
    findings = []
    for name, values in profiles.items():
        if dig(values or {}, SETEC_OFF) is False:
            findings.append(
                f"{name}: sets {SETEC_OFF}=false. Each install runs one sandbox fleet, and a "
                "fleet in another cluster is not built (ADR-0087).")
    return findings


def load_profiles(root):
    out = {}
    for path in sorted(glob.glob(os.path.join(root, "helm", "gibson", "values-*.yaml"))):
        with open(path) as f:
            out[os.path.relpath(path, root)] = yaml.safe_load(f) or {}
    return out


def render_setec_off(root):
    cmd = ["helm", "template", "gibson", os.path.join(root, "helm", "gibson"),
           "-f", os.path.join(root, "helm", "gibson", "values-baseline.yaml"),
           "-f", os.path.join(root, "helm", "testdata", "render-inputs", "gibson.yaml"),
           "--namespace", "gibson", "--set", f"{SETEC_OFF}=false"]
    p = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return p.returncode, p.stderr


def render_findings(returncode, stderr):
    if returncode == 0:
        return [f"the umbrella renders with {SETEC_OFF}=false. The render must refuse it "
                "until the endpoint pair of ADR-0087 is built: the daemon would dial a "
                "setec frontend that does not exist."]
    if SETEC_REFUSAL not in stderr:
        tail = stderr.strip().splitlines()[-1:] or ["(no stderr)"]
        return [f"the render with {SETEC_OFF}=false fails, but not in {SETEC_REFUSAL}: "
                f"{tail[0]}. The check cannot prove the refusal."]
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
    if not profile_findings({"values-guest.yaml": {"gibson-workloads": {"setec": {"enabled": False}}}}):
        print("  ✗ selftest: a profile that turns setec off does not fail")
        failed = True
    if profile_findings({"values-guest.yaml": {"certManager": {"enabled": False}}}):
        print("  ✗ selftest: a profile that turns an operator seam off fails")
        failed = True
    if not render_findings(0, ""):
        print("  ✗ selftest: a render that accepts setec off does not fail")
        failed = True
    if not render_findings(1, "Error: some other template error"):
        print("  ✗ selftest: a render that fails for another reason does not fail")
        failed = True
    if render_findings(1, f"Error: {SETEC_REFUSAL}: the daemon requires setec.enabled=true"):
        print("  ✗ selftest: the expected refusal fails")
        failed = True
    if failed:
        return 1
    print("  ✓ selftest: an unnamed condition, a stale entry, a false default, a profile "
          "with setec off and a render that accepts setec off each fail")
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
    findings = chart_findings(charts) + profile_findings(load_profiles(ROOT))
    findings += render_findings(*render_setec_off(ROOT))
    for f in findings:
        print(f"FAIL: {f}")
    if findings:
        return 1
    print(f"  ✓ seam-conditions: {len(SEAMS)} named seams, each defaults to true, no profile "
          "turns setec off, and the render refuses setec off")
    return 0


if __name__ == "__main__":
    sys.exit(main())
