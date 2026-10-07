# Tenant-operator alerts

## TenantOperatorOrphansAccumulating

The orphan reaper strips more than five finalizers in one hour. One strip after a test teardown or an operator restart is normal. A steady rate means that a controller makes child objects without an owner reference, or that a saga step fails.

1. Read the reaper lines in the operator log: `kubectl -n gibson logs deploy/gibson-tenant-operator | grep -i orphan`.
2. Note the kinds of the stripped objects.
3. Find the controller that creates those objects, and check that it sets an owner reference.

## TenantOperatorStuckTerminatingNamespaces

A `tenant-*` namespace stays in `Terminating` longer than the grace period of the reaper.

1. List the namespaces: `kubectl get ns | grep Terminating`.
2. Read the finalizers: `kubectl get ns <ns> -o jsonpath='{.spec.finalizers}'`.
3. List what is left in the namespace: `kubectl api-resources --verbs=list --namespaced -o name | xargs -n1 kubectl -n <ns> get --ignore-not-found --show-kind`.
4. For a finalizer that the reaper does not know, fix the controller that owns it. Do not edit the namespace.

## TenantOperatorFinalBackupFailed

A tenant delete stopped because the last backup of the tenant did not complete. The delete removed nothing, and the operator retries on the next pass.

1. Read the `reason` label of `gibson_tenant_operator_final_backup_failures_total`.
2. If the reason is `backup_failed` or `timeout`, read the Velero Backup: `kubectl -n velero get backups.velero.io -l gibson.zeroroot.ai/backup-kind=final` and `velero backup describe <name> --details`.
3. If the reason is `create` or `read`, check the Velero API: `kubectl -n velero get pods` and the operator log for the Velero error.
4. If the reason is `read_namespace`, check that the tenant namespace exists: `kubectl get ns -l gibson.zeroroot.ai/tenant`.
5. If the reason is `audit`, check the daemon as in [TenantOperatorDaemonCallsFailing](#tenantoperatordaemoncallsfailing).
6. Fix the cause in the chart or in the producer. The operator takes the backup again on its next pass.

## TenantOperatorDaemonCallsFailing

The calls of the tenant-operator to the daemon (`gibson.daemon.operator.v1.DaemonOperatorService`, SPIFFE mTLS) fail repeatedly. Tenant admin work and pending provisioning stall, and a new signup can stay without access to its workspace.

1. Read the `outcome` label of `gibson_tenant_operator_daemon_call_errors_total`.
2. If the outcome is `permission_denied` or `unauthenticated`, go to [TenantOperatorDaemonCallsDenied](#tenantoperatordaemoncallsdenied).
3. If the outcome is `unavailable` or `deadline_exceeded`, check the daemon: `kubectl -n gibson get pods -l app.kubernetes.io/component=daemon` and its log.
4. Check that the operator pod has its SPIFFE socket: `kubectl -n gibson logs deploy/gibson-tenant-operator | grep -i spiffe`.

## TenantOperatorDaemonCallsDenied

The daemon answers `permission_denied` or `unauthenticated` to the tenant-operator. The deny is deterministic and does not heal by itself.

1. Read the daemon log for the denied method: `kubectl -n gibson logs statefulset/gibson-gibson-workloads | grep -i -E "denied|unauthenticated"`.
2. Check that the daemon allow-list holds the tenant-operator SPIFFE ID: the env `GIBSON_SPIFFE_ALLOWED_PEER_IDS` of the daemon StatefulSet.
3. Check the FGA tuples of the platform operator for the operator service account.
4. Fix the value in the chart or the tuple in its producer. Then let the operator retry.
