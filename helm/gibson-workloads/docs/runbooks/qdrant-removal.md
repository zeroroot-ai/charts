# Qdrant removal migration runbook

After upgrading the `gibson-workloads` chart to the version that removes Qdrant,
the StatefulSet and its PVC become orphaned resources in existing clusters.

## Manual cleanup steps (run once per cluster after chart upgrade)

1. Verify the Qdrant pod is gone after chart upgrade:
   kubectl get pods -n gibson -l app.kubernetes.io/component=qdrant

2. Delete the orphaned StatefulSet if it still exists:
   kubectl delete statefulset gibson-workloads-qdrant -n gibson --ignore-not-found

3. Delete the orphaned PVC (WARNING: this permanently deletes all Qdrant data):
   kubectl delete pvc storage-gibson-workloads-qdrant-0 -n gibson --ignore-not-found

Note: The Qdrant Go adapter was an unimplemented stub. No production vector data
exists to migrate. Vector storage now uses the existing redis-stack-server instance.
