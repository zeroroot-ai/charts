# Dashboard sign-in alerts

## DashboardSignInErrorRateHigh

More than one percent of sign-in attempts fail over five minutes.

1. Read the dominant reason: `sum by (reason) (rate(dashboard_login_error_total[5m]))`.
2. Read the dashboard log: `kubectl -n gibson logs deploy/gibson-dashboard --tail=200`.
3. If the reason points at the daemon, check the daemon pod and the edge.
4. If the reason points at Zitadel, check `kubectl -n gibson get pods -l app.kubernetes.io/name=zitadel`.

## DashboardSignInDaemonUnreachable

A sign-in failed because the membership lookup of the dashboard did not reach the daemon. The person saw `/login/error?reason=daemon_unavailable`.

1. Check the daemon: `kubectl -n gibson get pods -l app.kubernetes.io/component=daemon`.
2. Check Envoy: `kubectl -n gibson get pods -l app.kubernetes.io/component=envoy` and its log for the upstream of the daemon.
3. Read the dashboard log for `ListMyMemberships` and the gRPC code (`Unavailable` or `DeadlineExceeded`).
4. Check the Hubble flows between the dashboard and the daemon for a dropped flow: `hubble observe --from-label app.kubernetes.io/component=dashboard --verdict DROPPED`.

## DashboardFGAUnreachable

The membership lookup of the dashboard fails because OpenFGA does not answer. Sign-in sends users to `/login/error`.

1. Check OpenFGA: `kubectl -n gibson get pods -l app.kubernetes.io/name=openfga`.
2. Read the OpenFGA log and the daemon log for `ListMyMemberships`.
3. Check the database of OpenFGA: `kubectl -n gibson get cluster.postgresql.cnpg.io`.

## DashboardSignInLatencyBudgetBurn

The p95 sign-in latency stays above the 1.5 second objective.

1. Compare the latency of the membership lookup with the total sign-in latency.
2. Check the upstream health of the edge: `kubectl -n gibson logs deploy/gibson-envoy --tail=200 | grep -i upstream`.
3. Check the Zitadel pods and their CPU use: `kubectl -n gibson top pods`.

## DashboardWorkloadSvidFallback

The dashboard calls the daemon with its workload SVID instead of the user token. Per-user authorization is off for those calls, and the audit names the wrong subject. A pod start increments the counter once, and `for: 10m` absorbs that.

1. Read the reason in the dashboard log: `kubectl -n gibson logs deploy/gibson-dashboard | grep -i fallback`.
2. Check that the dashboard pod mounts the SPIFFE Workload API socket.
3. Check that the dashboard sends the user token: a missing session token is a sign-in fault, not a transport fault.
