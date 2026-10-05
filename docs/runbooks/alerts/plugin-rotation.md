# Plugin secret rotation alerts

The synthetic monitor `tests/plugin/rotation-monitor.sh` in the `hosted` repo rotates a secret and measures how long a plugin takes to see it.

## PluginRotationPropagationP95HighSlo

The 10-minute p95 of `gibson_plugin_rotation_propagation_seconds` for one plugin stays above 5 seconds.

1. Check the emission latency of the rotation event: `gibson_secrets_rotation_emit_seconds`.
2. Check the backlog of the component callback stream: `gibson_component_callback_lag_seconds`.
3. Check the state of the plugin: `gibson_plugin_state{plugin="<plugin>"}`.
4. Compare with the log of the synthetic monitor for the same rotation.

## PluginRotationPropagationNoSamples

The synthetic monitor produced no sample in two hours.

1. Check the CronJob of the monitor and its last runs.
2. Read the log of the last run.
3. Check that Prometheus scrapes the monitor target.
