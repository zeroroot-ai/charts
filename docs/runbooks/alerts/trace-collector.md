# Trace collector alerts

The agentgateway proxy sends one span for each request to the trace collector `gibson-trace-collector`. The collector keeps each span in a persistent queue on its PVC and sends it to the daemon, which records the model call (charts#816).

## TraceExportFailing

The collector failed to send spans to the daemon for 10 minutes. The queue keeps the spans, so no model call is lost yet. The record of model calls and the token counts fall behind.

1. Check the daemon pods: `kubectl -n gibson get pods -l app.kubernetes.io/component=daemon`.
2. Check the collector pod: `kubectl -n gibson get pods -l app.kubernetes.io/component=trace-collector`.
3. Read the log of the `envoy` container of the collector. A TLS error means the SPIFFE mTLS to the daemon fails: check that the daemon accepts the SVID `platform/otel-collector` (`GIBSON_TRACE_COLLECTOR_SVID`).
4. Read the log of the `collector` container. It logs at warn and never logs a span body.

## TraceQueueFilling

The persistent queue of the collector holds more than half of its bytes for 10 minutes. When it is full, the collector refuses new spans and the gateway drops them, so model calls go unrecorded.

1. Do the steps of [TraceExportFailing](#traceexportfailing). A queue fills when the export fails or is slower than the gateway traffic.
2. Read `otelcol_exporter_queue_size` and `otelcol_exporter_queue_capacity` of the exporter `otlp/daemon`. Both are in bytes.
3. If the export works and the queue still grows, raise `traceCollector.queue.capacityMiB` and `traceCollector.queue.size` together. The render fails when `capacityMiB` x 2 + 512 passes the PVC size.

## TraceExportDelayed

A span waits more than 15 minutes in the queue before the daemon gets it, for 10 minutes. The rule estimates the wait: the bytes in the queue divided by the bytes that leave the queue each second. The record of model calls and the token counts are that late.

1. If TraceExportFailing also fires, do its steps first. The export is down, not slow.
2. Read the CPU and memory of the daemon pods. A slow trace listener drains the queue slowly.
3. Read `otelcol_exporter_queue_size` (bytes) and `rate(otelcol_exporter_enqueue_size_bytes_sum[10m])` (bytes in each second) of the exporter `otlp/daemon`. If the traffic in is higher than the daemon can take for a long time, scale the daemon.
