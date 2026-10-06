# Alert runbooks

Each alert rule of the chart names one section of a page in this folder, in its `runbook_url` annotation. `scripts/check-alert-runbooks.py` fails on a rule with no `runbook_url`, and on a URL whose page or section does not exist.

The commands in these pages read the cluster. They change nothing. A fix goes through the chart and the GitOps tree, never through a change to a live object.

| Page | Alerts |
|---|---|
| [tenant-operator.md](tenant-operator.md) | The tenant-operator |
| [dashboard-auth.md](dashboard-auth.md) | Sign-in through the dashboard |
| [redis.md](redis.md) | redis-stack |
| [sandbox.md](sandbox.md) | Sandbox dispatch through setec |
| [ext-authz.md](ext-authz.md) | The Redis dependency of ext-authz |
| [ratelimit.md](ratelimit.md) | The per-client quota of the edge |
| [plugin-rotation.md](plugin-rotation.md) | Secret rotation to plugins |

In the commands, `gibson` is the release namespace. Use your own namespace if it is different.
