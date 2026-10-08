#!/usr/bin/env python3
"""check-alert-series-scraped.py: each series an alert rule reads is scraped (charts#515).

A rule that reads a series which no scrape object collects can never fire,
and nothing in the render says so. Six rules were blind that way: the
dashboard had no scrape object, the tenant-operator had none, and the
ext-authz scrape object named a Service port that does not exist.

The check reads each golden render that holds PrometheusRules and, for each
series that a rule expression reads (a recording rule of the same render is
not a series to scrape):

  1. finds its producer from PRODUCERS, by name prefix. A name with no known
     producer fails, so a new series names its producer here,
  2. finds a ServiceMonitor or PodMonitor of the render that selects a pod of
     that producer, through a Service port or a pod port,
  3. resolves the scraped port to a container port number, and requires the
     pod label gibson.zeroroot.ai/metrics-port to name that number, so the
     metrics network rule admits the scraper there (D76).

EXTERNAL names a prefix whose producer runs outside the release, with its
reason.

  check-alert-series-scraped.py             exit 1 on a finding
  check-alert-series-scraped.py --selftest  prove each finding fails
"""
import glob
import os
import re
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
RELEASE_NS = "gibson"
LABEL = "gibson.zeroroot.ai/metrics-port"
COMPONENT = "app.kubernetes.io/component"
POD_KINDS = {"Deployment", "StatefulSet", "DaemonSet"}

# Series name prefix -> the app.kubernetes.io/component of the pod that serves it.
PRODUCERS = (
    ("dashboard_", "dashboard"),
    ("extauthz_", "ext-authz"),
    ("gibson_tenant_operator_", "tenant-operator"),
    ("gibson_connector_", "connector-operator"),
    ("gibson_audit_", "daemon"),
)
# Series name prefix -> why no scrape object of the release collects it.
EXTERNAL = {
    "kube_": "kube-state-metrics of the cluster monitoring stack, outside the release (ADR-0087)",
}

PROMQL_WORDS = set("""
by without on ignoring group_left group_right and or unless bool offset
sum min max avg count stddev stdvar topk bottomk quantile count_values group
rate irate increase delta idelta deriv predict_linear resets changes
abs ceil floor round exp ln log2 log10 sqrt clamp clamp_min clamp_max sgn
histogram_quantile histogram_count histogram_sum vector scalar time timestamp
absent absent_over_time label_replace label_join sort sort_desc
avg_over_time min_over_time max_over_time sum_over_time count_over_time
quantile_over_time stddev_over_time last_over_time present_over_time
day_of_month day_of_week hour minute month year days_in_month
atan2 sin cos tan asin acos atan sinh cosh tanh asinh acosh atanh deg rad pi
inf nan
""".split())


def series(expr: str) -> set[str]:
    named = set(re.findall(r'__name__\s*=\s*"([^"]+)"', str(expr)))
    e = re.sub(r'"(?:\\.|[^"\\])*"', "", str(expr))
    e = re.sub(r"\{[^}]*\}", "", e)
    e = re.sub(r"\[[^\]]*\]", "", e)
    e = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", e)
    out = set()
    for m in re.finditer(r"(?<![\w.])([a-zA-Z_:][a-zA-Z0-9_:]*)\s*(\()?", e):
        name, call = m.group(1), m.group(2)
        if call or name.lower() in PROMQL_WORDS:
            continue
        out.add(name)
    return out | named


def ns_of(d: dict) -> str:
    return (d.get("metadata") or {}).get("namespace") or RELEASE_NS


def selects(sel: dict, labels: dict) -> bool:
    ml = (sel or {}).get("matchLabels") or {}
    return bool(ml) and all(labels.get(k) == v for k, v in ml.items())


def pods(docs: list) -> list[tuple[str, str, dict, list]]:
    out = []
    for d in docs:
        if d.get("kind") in POD_KINDS:
            t = d["spec"].get("template") or {}
            out.append((d["metadata"]["name"], ns_of(d), (t.get("metadata") or {}).get("labels") or {},
                        (t.get("spec") or {}).get("containers") or []))
    return out


def port_number(containers: list, ref) -> int | None:
    for c in containers:
        for p in c.get("ports") or []:
            if ref in (p.get("name"), p.get("containerPort")) or str(ref) == str(p.get("containerPort")):
                return int(p["containerPort"])
    return None


def scraped_ports(docs: list, ns: str, labels: dict, containers: list) -> list[int]:
    """The container ports of the pod that a scrape object of the render reads."""
    out = []
    for m in docs:
        # The Prometheus operator looks for targets in the namespace of the
        # monitor unless the monitor names others, or selects any namespace.
        nsel = (m.get("spec") or {}).get("namespaceSelector") or {}
        if not nsel.get("any") and ns not in (nsel.get("matchNames") or [ns_of(m)]):
            continue
        if m.get("kind") == "PodMonitor" and selects((m.get("spec") or {}).get("selector"), labels):
            for ep in m["spec"].get("podMetricsEndpoints") or []:
                n = port_number(containers, ep.get("port") or ep.get("targetPort"))
                if n:
                    out.append(n)
        if m.get("kind") != "ServiceMonitor":
            continue
        for s in docs:
            if s.get("kind") != "Service" or ns_of(s) != ns:
                continue
            ssel = (s.get("spec") or {}).get("selector") or {}
            if not ssel or any(labels.get(k) != v for k, v in ssel.items()):
                continue
            if not selects((m.get("spec") or {}).get("selector"), (s.get("metadata") or {}).get("labels") or {}):
                continue
            sports = {p.get("name"): p for p in (s.get("spec") or {}).get("ports") or []}
            for ep in m["spec"].get("endpoints") or []:
                sp = sports.get(ep.get("port"))
                if sp is None:
                    continue
                n = port_number(containers, sp.get("targetPort", sp.get("port")))
                if n:
                    out.append(n)
    return out


def judge(docs: list) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    recorded, wanted = set(), {}
    for d in docs:
        if d.get("kind") != "PrometheusRule":
            continue
        for g in (d.get("spec") or {}).get("groups") or []:
            for r in g.get("rules") or []:
                if r.get("record"):
                    recorded.add(r["record"])
                for s in series(r.get("expr", "")):
                    wanted.setdefault(s, set()).add(r.get("alert") or r.get("record"))
    bad, all_pods = [], pods(docs)
    for name in sorted(set(wanted) - recorded):
        rules = ", ".join(sorted(wanted[name]))
        if any(name.startswith(p) for p in EXTERNAL):
            continue
        comp = next((c for p, c in PRODUCERS if name.startswith(p)), None)
        if comp is None:
            bad.append(f"{name} ({rules}): no producer is known. Name its pod in PRODUCERS, or delete the rule")
            continue
        targets = [p for p in all_pods if p[2].get(COMPONENT) == comp]
        if not targets:
            bad.append(f"{name} ({rules}): no pod with {COMPONENT}={comp} in the render")
            continue
        ok = False
        for _, ns, labels, containers in targets:
            for n in scraped_ports(docs, ns, labels, containers):
                if str(labels.get(LABEL)) == str(n):
                    ok = True
        if not ok:
            bad.append(f"{name} ({rules}): no scrape object reads the {comp} pod on the port that its "
                       f"{LABEL} label opens to the scraper")
    return bad


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*.yaml"))):
        docs = [d for d in yaml.safe_load_all(open(f)) if isinstance(d, dict)]
        if not any(d.get("kind") == "PrometheusRule" for d in docs):
            continue
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(docs)]
    if not seen:
        bad.append("no golden render holds a PrometheusRule: this check is blind")
    return bad, seen


def selftest() -> int:
    def rule(expr):
        return {"kind": "PrometheusRule", "spec": {"groups": [{"rules": [{"alert": "A", "expr": expr}]}]}}

    def pod(comp, port=9464, label=True):
        labels = {COMPONENT: comp, "app": comp}
        if label:
            labels[LABEL] = str(port)
        return {"kind": "Deployment", "metadata": {"name": comp}, "spec": {"template": {
            "metadata": {"labels": labels},
            "spec": {"containers": [{"name": "c", "ports": [{"name": "metrics", "containerPort": port}]}]}}}}

    def svc(comp, pname="metrics"):
        return {"kind": "Service", "metadata": {"name": comp, "labels": {"app": comp}},
                "spec": {"selector": {"app": comp}, "ports": [{"name": pname, "port": 1, "targetPort": "metrics"}]}}

    def sm(comp, port="metrics"):
        return {"kind": "ServiceMonitor", "metadata": {"name": comp},
                "spec": {"selector": {"matchLabels": {"app": comp}}, "endpoints": [{"port": port}]}}

    expr = 'sum(rate(dashboard_signin_total{result="error"}[5m])) by (result) / on() group_left vector(1) > 0.1'
    if series(expr) != {"dashboard_signin_total"}:
        print(f"SELFTEST FAIL: the parser read {series(expr)}")
        return 1
    good = [rule(expr), pod("dashboard"), svc("dashboard"), sm("dashboard"),
            rule("kube_statefulset_status_replicas_ready < 1"),
            {"kind": "PrometheusRule", "spec": {"groups": [{"rules": [
                {"record": "slo:x", "expr": "rate(dashboard_signin_total[5m])"},
                {"alert": "B", "expr": "slo:x > 1"}]}]}}]
    if judge(good):
        print(f"SELFTEST FAIL: a scraped series must pass, got {judge(good)}")
        return 1
    any_ns = [rule(expr), pod("dashboard"), svc("dashboard"),
              dict(sm("dashboard"), metadata={"name": "d", "namespace": "other"},
                   spec=dict(sm("dashboard")["spec"], namespaceSelector={"any": True}))]
    if judge(any_ns):
        print(f"SELFTEST FAIL: a monitor with namespaceSelector.any scrapes each namespace, got {judge(any_ns)}")
        return 1
    failing = (
        # The charts#515 shapes.
        ("a series with no scrape object", [rule(expr), pod("dashboard")]),
        ("a scrape object that names a port the Service lacks",
         [rule(expr), pod("dashboard"), svc("dashboard"), sm("dashboard", port="health")]),
        ("a scraped port that the network rule does not open",
         [rule(expr), pod("dashboard", label=False), svc("dashboard"), sm("dashboard")]),
        ("a series with no known producer", [rule("gibson_sandbox_health < 1")]),
        ("a producer with no pod", [rule("extauthz_x_total > 0")]),
        ("a monitor in another namespace",
         [rule(expr), pod("dashboard"), svc("dashboard"), dict(sm("dashboard"), metadata={"name": "d", "namespace": "other"})]),
        ("a series named only by __name__", [rule('rate({__name__="gibson_sandbox_health"}[5m]) > 0')]),
    )
    for what, docs in failing:
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: an unscraped series, a missing Service port, a closed port, an unknown producer and a "
          "missing pod fail; a monitor of any namespace passes")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("an alert rule reads a series that no scrape object collects (charts#515):", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ alert-series-scraped: each series an alert rule reads is scraped, in {seen} renders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
