# Redis alerts

## RedisAuthenticationFailure

The redis-exporter sidecar cannot reach or log in to redis-stack. This rule renders only when `redis.metrics.enabled` is on. A password rotation that failed is the main cause.

1. Read the Redis log: `kubectl -n gibson logs -l app.kubernetes.io/component=redis --tail=200`.
2. Read the rotation state: `kubectl -n gibson get secret gibson-redis-rotation-state`.
3. Read the last runs of the CronJob `gibson-redis-rotation`: `kubectl -n gibson get jobs | grep redis-rotation`.
4. If a manual restart is needed, restart redis-stack first, then the daemon, then the tenant-operator. In a different order, each client that reconnects first gets `WRONGPASS`.

## RedisStackUnhealthy

The redis-stack StatefulSet has no ready replica. Each part that uses Redis fails, the daemon and the tenant-operator among them.

1. Describe the StatefulSet: `kubectl -n gibson describe statefulset/gibson-redis-stack`.
2. Read the pod events and the logs of its init containers `own-data` and `init-acl`.
3. Check the data volume: `kubectl -n gibson get pvc | grep redis`.
