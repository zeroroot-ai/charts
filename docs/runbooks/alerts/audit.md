# Audit write alerts

The daemon writes each audit record to the `audit_log` table of the platform Postgres. An audit write never drops (gibson#838): the daemon retries, and the caller waits.

## AuditWriteErrors

A write to the `audit_log` table failed. The owner decision of 2026-10-05 pages on any audit write error.

1. Read the daemon log for `audit`: `kubectl -n gibson logs statefulset/gibson-gibson-workloads | grep -i audit`.
2. Check the platform Postgres: `kubectl -n gibson get cluster.postgresql.cnpg.io` and the status of its primary.
3. Check the storage of the primary: `kubectl -n gibson get pvc -l cnpg.io/cluster`.
4. Check the migration state of the daemon in its start log. A migration that changed `audit_log` under a running daemon shows here.
5. Fix the cause in the chart or in the producer. The daemon writes the record again by itself.
