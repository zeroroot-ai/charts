# Purge the last backup of a deleted tenant

A tenant delete takes one last backup of the tenant namespace. Velero deletes it after 30 days (ADR-0075). A tenant owner can ask for an earlier purge. This page is the procedure for that purge, on each install type.

## Before you start

1. Get the request of the tenant owner in writing.
2. Confirm that the person who asks held the Owner role of that tenant. Use the membership records of the platform, not the word of the request.
3. Write down the tenant name and the date of the request in your change record.

## Purge the backup

1. Set the kube context to the cluster of the deleted tenant.
2. Run `make purge-tenant-backup TENANT=<tenant name>` from a checkout of `charts`. The `hosted` estate runs its own make verb, which calls the same script.
3. If the script says that the tenant name has more than one last backup, read the uid of each backup in its output. Get the uid of the deleted tenant from your change record or from the audit log. Then run `make purge-tenant-backup TENANT=<tenant name> TENANT_UID=<uid>`.
4. Wait for the line `backup <name> is deleted`.

## What the script refuses

The script deletes nothing and exits with an error in each of these cases:

- no tenant name, or a name that is not a Kubernetes name;
- a tenant that still exists;
- no last backup of that tenant;
- more than one last backup of that tenant name, with no `TENANT_UID`;
- a backup without the label `gibson.zeroroot.ai/backup-type=final`.

## If the delete fails

The script prints the error of the Velero `DeleteBackupRequest`. Read the Velero log: `kubectl -n velero logs deploy/velero --tail=200`. Fix the cause, for example the bucket credential, and run the script again. Velero then deletes the backup and its data in the bucket.
