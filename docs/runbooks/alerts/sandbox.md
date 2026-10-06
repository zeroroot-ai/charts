# Sandbox alerts

These alerts watch the sandboxed dispatch of the daemon to setec.

## SandboxColdStartLatencyHigh

The p95 of a cold-start dispatch stays above 500 ms. A cold start launches a new microVM.

1. Check the CPU use of the sandbox-host nodes: `kubectl top nodes -l setec.zeroroot.ai/sandbox-host=true`.
2. Read the log of the setec frontend: `kubectl -n setec-system logs deploy/setec-frontend --tail=200`.
3. Check the warm pool of setec in its log.

## SandboxWarmStartLatencyHigh

The p95 of a warm-start dispatch stays above 100 ms. A warm start reuses a prepared microVM.

1. Check the warm pool of setec in the frontend log.
2. Check the CPU use of the sandbox-host nodes, as for [SandboxColdStartLatencyHigh](#sandboxcoldstartlatencyhigh).

## SandboxDispatchSLOBreach

At least one percent of sandboxed dispatches take more than 10 seconds. This is the user-facing objective. Missions with untrusted content wait.

1. Check whether the cold-start or the warm-start alert fires too, and follow that section.
2. Read the daemon log for the slow dispatches: `kubectl -n gibson logs statefulset/gibson-gibson-workloads | grep -i sandbox`.

## SandboxUnavailable

The health probe of the daemon reports the setec frontend as down for most of five minutes. Missions with untrusted content fail closed with `sandbox_unavailable`. Missions with trusted content continue.

1. Check setec: `kubectl -n setec-system get pods`.
2. Read the frontend log and its events.
3. Check that the daemon reaches the frontend Service (the setec endpoint of the chart values).

## SandboxSpotEvictionStorm

The sandbox health stays `degraded`: spot interruptions cancel detonations faster than the on-demand fallback replaces the nodes.

1. Check the node churn: `kubectl get nodes -l setec.zeroroot.ai/sandbox-host=true`.
2. To raise the on-demand cap, change `gibson-workloads.setec.spotEvictionFallback.onDemandMax` in the GitOps values through a pull request. Revert it when the spot pool recovers.

## SandboxQuotaExceededRate

setec rejects sandboxed dispatches with `quota_exceeded` at more than 0.1 per second for 15 minutes. One tenant above its cap is the usual cause.

1. Find the tenant: `sum by (tenant) (rate(gibson_sandbox_detonation_total{outcome="quota_exceeded"}[5m]))`.
2. Check the missions of that tenant before a change to its limit.
