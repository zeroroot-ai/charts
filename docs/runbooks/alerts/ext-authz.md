# ext-authz alerts

## ExtAuthzRefusesCallsRedisDown

ext-authz could not read or write the replay state of component tokens in Redis, so it refused component calls with `Unavailable` (ADR-0045). Every component call fails until Redis answers.

1. Check redis-stack: see [RedisStackUnhealthy](redis.md#redisstackunhealthy).
2. Check the ext-authz pods: `kubectl -n gibson get pods -l app.kubernetes.io/component=ext-authz`.
3. Read the ext-authz log for `replay state`.
4. Check that the Redis password Secret of ext-authz is the one that redis-stack uses.
5. Check the NetworkPolicy between ext-authz and Redis on port 6379.
