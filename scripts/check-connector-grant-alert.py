#!/usr/bin/env python3
"""check-connector-grant-alert.py: an unrevoked connector grant raises an alert, and Prometheus scrapes its metric.

The connector-operator exports gibson_connector_unrevoked_grants. The chart
must alert on it and must scrape the operator, or the alert never fires.
The check reads each golden render that has the monitoring API (the
`withcaps` files) and fails when:

  - no PrometheusRule alert reads gibson_connector_unrevoked_grants,
  - no ServiceMonitor selects a Service that selects the connector-operator
    pods, or
  - that Service has no port named metrics.

It fails as blind when no withcaps golden file exists.

  check-connector-grant-alert.py             exit 1 on a finding
  check-connector-grant-alert.py --selftest  prove each finding fails
"""
import glob
import os
import sys

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join("helm", "testdata", "golden")
METRIC = "gibson_connector_unrevoked_grants"
POD = {"app.kubernetes.io/name": "gibson-connector-operator"}


def subset(want: dict, have: dict) -> bool:
    return bool(want) and all(have.get(k) == v for k, v in want.items())


def judge(docs: list) -> list[str]:
    docs = [d for d in docs if isinstance(d, dict)]
    out = []
    alerts = [r for d in docs if d.get("kind") == "PrometheusRule"
              for g in (d.get("spec") or {}).get("groups") or [] for r in g.get("rules") or []
              if r.get("alert") and METRIC in str(r.get("expr"))]
    if not alerts:
        out.append(f"no PrometheusRule alert reads {METRIC}")
    services = [d for d in docs if d.get("kind") == "Service"
                and subset((d.get("spec") or {}).get("selector") or {}, dict(POD, **{"app.kubernetes.io/component": "connector-operator"}))]
    monitors = [d for d in docs if d.get("kind") == "ServiceMonitor"]
    scraped = [s for s in services for m in monitors
               if subset((((m.get("spec") or {}).get("selector") or {}).get("matchLabels") or {}),
                         (s.get("metadata") or {}).get("labels") or {})]
    if not scraped:
        out.append("no ServiceMonitor selects a Service of the connector-operator pods")
    elif not any(p.get("name") == "metrics" for s in scraped for p in (s.get("spec") or {}).get("ports") or []):
        out.append("the scraped connector-operator Service has no port named metrics")
    return out


def audit(root: str) -> tuple[list[str], int]:
    bad, seen = [], 0
    for f in sorted(glob.glob(os.path.join(root, GOLDEN, "*withcaps*.yaml"))):
        seen += 1
        bad += [f"{os.path.basename(f)}: {x}" for x in judge(list(yaml.safe_load_all(open(f))))]
    if not seen:
        bad.append("no withcaps golden render exists: this check is blind")
    return bad, seen


def selftest() -> int:
    labels = dict(POD, **{"app.kubernetes.io/component": "connector-operator"})
    svc = {"kind": "Service", "metadata": {"labels": labels},
           "spec": {"selector": labels, "ports": [{"name": "metrics", "port": 8080}]}}
    sm = {"kind": "ServiceMonitor", "spec": {"selector": {"matchLabels": labels}}}
    rule = {"kind": "PrometheusRule", "spec": {"groups": [{"rules": [{"alert": "A", "expr": f"max({METRIC}) > 0"}]}]}}
    if judge([svc, sm, rule]):
        print(f"SELFTEST FAIL: a complete set must pass, got {judge([svc, sm, rule])}")
        return 1
    other = {"kind": "PrometheusRule", "spec": {"groups": [{"rules": [{"alert": "A", "expr": "up == 0"}]}]}}
    noport = dict(svc, spec={"selector": labels, "ports": [{"name": "http", "port": 8080}]})
    wrongsm = {"kind": "ServiceMonitor", "spec": {"selector": {"matchLabels": {"app": "other"}}}}
    for what, docs in (("no alert", [svc, sm]), ("an alert on another metric", [svc, sm, other]),
                       ("no ServiceMonitor", [svc, rule]), ("a ServiceMonitor for another Service", [svc, wrongsm, rule]),
                       ("no Service", [sm, rule]), ("no metrics port", [noport, sm, rule])):
        if len(judge(docs)) != 1:
            print(f"SELFTEST FAIL: {what} must give one finding, got {judge(docs)}")
            return 1
    print("  ✓ selftest: no alert, an alert on another metric, no or a wrong ServiceMonitor, no Service and no metrics port each fail")
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()
    bad, seen = audit(ROOT)
    if bad:
        print("an unrevoked connector grant raises no alert:", file=sys.stderr)
        for b in bad:
            print(f"  {b}", file=sys.stderr)
        return 1
    print(f"  ✓ connector-grant-alert: {seen} renders alert on {METRIC} and scrape the connector-operator")
    return 0


if __name__ == "__main__":
    sys.exit(main())
