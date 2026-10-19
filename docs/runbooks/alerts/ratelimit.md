# Rate-limit alerts

## EdgePerClientQuotaFailingOpen

The edge could not reach the rate-limit service, and it let requests through under the local fuse only. The per-client quotas of login and the other fail-open routes are not enforced. POST /signup fails closed instead and raises [EdgeSignupQuotaFailingClosed](#edgesignupquotafailingclosed) (D92).

1. Check the rate-limit pods: `kubectl -n gibson get pods -l app.kubernetes.io/component=ratelimit`.
2. Read the log of the rate-limit service and of its SPIFFE mTLS sidecar.
3. Check redis-stack, which the rate-limit service uses: see [RedisStackUnhealthy](redis.md#redisstackunhealthy).

## EdgeSignupQuotaFailingClosed

The edge got no answer from the rate-limit service for a signup, and it refused the signup with 503. POST /signup fails closed (D92), so new signups fail while this lasts. The other routes fail open and raise EdgePerClientQuotaFailingOpen instead.

1. Check the rate-limit pods: `kubectl -n gibson get pods -l app.kubernetes.io/component=ratelimit`.
2. Read the log of the rate-limit service and of its SPIFFE mTLS sidecar.
3. Check redis-stack, which the rate-limit service uses: see [RedisStackUnhealthy](redis.md#redisstackunhealthy).
4. Read the edge counters `envoy_cluster_upstream_rq_timeout` and `envoy_cluster_upstream_cx_connect_ms` of the cluster `ratelimit_grpc`. A timeout with no fault in the service means the stage 1 timeout (`envoy.rateLimit.perClient.signupTimeoutMs`) is too short for the measured latency.

