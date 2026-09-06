# Security policy

## Reporting a vulnerability

**Do not open a public issue.**

Report privately through GitHub Security Advisories:
[Report a vulnerability](https://github.com/zeroroot-ai/charts/security/advisories/new)

## What to expect

| | |
|---|---|
| Acknowledgement | within 3 working days |
| Initial assessment | within 10 working days |
| Fix or mitigation plan | communicated with the assessment |

If you have not heard back within 3 working days, assume the report did not
reach us and escalate through any other channel you have. Silence is a failure
on our side, not a decision.

## Scope

This repository is the install chart. Findings in what the chart RENDERS are in scope

 over-broad RBAC, a secret written to a ConfigMap, a NetworkPolicy that does not constrain, a privileged container that need not be.:The images the chart references are private and live elsewhere; findings in the images belong to the repository that builds them. A misconfiguration in your own values file is not a chart finding.

## Out of scope

- Findings in a deployment you control that come from your own configuration
- Anything requiring a privileged position we already assume hostile
- Automated scanner output with no demonstrated impact. A CVE in a dependency we do not reach is not a finding; show the path

## Safe harbour

We will not pursue or support legal action against anyone who reports in good
faith under this policy, stays within scope, and does not access, modify or
retain data belonging to anyone else.
