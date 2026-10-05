# Rate-limit alerts

## EdgePerClientQuotaFailingOpen

The edge could not reach the rate-limit service, and it let requests through under the local fuse only. The per-client quotas of signup and sign-in are not enforced.

1. Check the rate-limit pods: `kubectl -n gibson get pods -l app.kubernetes.io/component=ratelimit`.
2. Read the log of the rate-limit service and of its SPIFFE mTLS sidecar.
3. Check redis-stack, which the rate-limit service uses: see [RedisStackUnhealthy](redis.md#redisstackunhealthy).
