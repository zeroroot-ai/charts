# Connector-operator alerts

## ConnectorGrantUnrevoked

A connector was deleted, and the daemon did not revoke its grant before the revoke deadline. The finalizer wrote a record and released the connector. The operator retries the revoke every five minutes. The alert fires when a record stays for one hour. Until the revoke succeeds, the grant of the deleted connector stays live.

1. List the records: `kubectl get configmap -A | grep connector-unrevoked-`.
2. Read the tenant, the connector and the release time in each record: `kubectl -n <ns> get configmap connector-unrevoked-<name> -o yaml`.
3. Read the revoke errors in the operator log: `kubectl -n gibson logs deploy/gibson-connector-operator | grep -i revoke`.
4. If the error is `unavailable` or `deadline_exceeded`, check the daemon: `kubectl -n gibson get pods -l app.kubernetes.io/component=daemon` and its log.
5. If the error is `permission_denied` or `unauthenticated`, check that the daemon allow-list holds the connector-operator SPIFFE ID.
6. Fix the cause. Do not delete a record by hand: the operator deletes it after the revoke succeeds.
