# Audit export alerts

The daemon writes each audit record to `audit/<tenant>/` in the durable bucket, with an object lock (ADR-0113). Retention removes only the rows it exported.

## AuditExportLagging

The oldest audit record that is not in the bucket is older than one hour. The audit table grows until the export runs.

1. Read the daemon log for `audit export`.
2. Check the `GIBSON_AUDIT_EXPORT_*` values of the daemon. An empty bucket stops the export at start.
3. Check the bucket credential in the `bringup-keyring` Secret.
4. If the export also fails, see [AuditExportErrors](#auditexporterrors).

## AuditExportErrors

A write of an audit range to the bucket failed. The daemon writes the range again, and the lag grows until a write succeeds.

1. Read the daemon log for `audit export` and the error of the store.
2. Check that the lock mode and the lock days match the bucket policy under `audit/` (`gibson.auditExport.lockMode` and `lockDays`).
3. Check that the bucket has object lock turned on.
4. Check that the store answers from the daemon pod, through its NetworkPolicy.
