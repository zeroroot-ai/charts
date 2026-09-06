# gibson-workloads

Gibson platform workloads: daemon, dashboard, ext-authz, Envoy gateway, SPIFFE
wiring, observability, and the PlatformBootstrap CR. Must be installed AFTER
`gibson-crds` AND `gibson-operators`.

This README is scoped to operational concerns specific to this chart. Architectural
context lives in `enterprise/docs/ARCHITECTURE.md`; org-level conventions live in
`zeroroot-ai/.github` → `AGENTS.md`.

## Layout

```
helm/gibson-workloads/
├── Chart.yaml                # chart metadata + subchart dependencies (setec, gibson-common)
├── values.yaml               # canonical defaults
├── values-kind.yaml          # kind dev overlay
├── values-aws-prod.yaml      # EKS prod overlay
├── templates/
│   ├── _helpers.tpl          # cross-chart helpers (gibson.fullname, gibson.redis.host, ...)
│   ├── gibson/               # daemon
│   ├── dashboard/            # web UI
│   ├── ext-authz/            # Envoy ExtAuthz + capability-grant API
│   ├── envoy/                # ingress gateway
│   ├── databases/            # redis-stack
│   ├── auth/                 # openbao + sa-identity-map
│   ├── fga-init/             # OpenFGA store + model seed Job
│   ├── observability/        # prometheus + loki + jaeger + grafana
│   ├── spire-server.yaml     # SPIRE server StatefulSet
│   └── tests/                # helm-test Pods (post-install assertions) — see below
├── tests/                    # chart-template lint (bats + helm template) — see below
└── files/                    # static configs (envoy config, grafana dashboards, fga model JSON)
```

## Two test surfaces — don't confuse them

The chart ships **two** kinds of tests, with different purposes and lifecycles:

### `tests/*.bats` — chart-template lint (PR time)

Run via `bats tests/` from `helm/gibson-workloads/`. Drives `helm template`
against the chart with various values and asserts the rendered YAML has the
expected shape. Fast (no cluster needed); runs in CI as part of the
`yaml-helm-lint` workflow.

Use these for: regression-pinning a value gate, asserting a resource is
emitted/suppressed, catching a `helm template` panic.

### `templates/tests/*.yaml` — `helm test` Pods (post-install)

Pods that carry `helm.sh/hook: test` and are run by `helm test gibson-workloads`
against a freshly-installed cluster. They probe the live system end-to-end:

| Pod | Asserts |
|-----|---------|
| `test-service-reachability` | Every first-party Service is reachable from inside the cluster (daemon gRPC, dashboard, ext-authz, Envoy, Redis, Vault, plus sibling-Argo OpenFGA / Zitadel / Neo4j). |
| `test-bootstrap-secrets` | `gibson-openbao-keys`, master-KEK Secret, dashboard OIDC Secret, Zitadel admin PAT, MACHINE_USER PAT Secrets exist and carry non-empty values on their documented keys. |
| `test-bootstrap-configmaps` | `gibson-fga-config` (store_id + model_id), chart-shipped FGA model JSON, `gibson-extauthz-rpc-registry`, reserved-names CM, SA-identity-map CM. |
| `test-fga-tuple-seed` | Canary tuple `(user:platform_operator, member, platform:gibson)` resolves to `allowed=true` via OpenFGA HTTP Check. |
| `test-spire-attestation` | SPIRE workload-API socket is present + SPIRE issues a valid X.509-SVID with a `spiffe://<trust-domain>/` SPIFFE ID to a test pod that matches the daemon's selector. |
| `test-envoy-admin` | Envoy `/ready` returns 200, `gibson_daemon_grpc` cluster has healthy endpoints, `ext_authz` cluster has healthy endpoints, `jwt_authn` JWKS fetch is succeeding. |
| `test-zitadel-auth-probe` | Zitadel OIDC discovery is reachable + the MACHINE_USER PAT exchanges successfully against `/management/v1/info`. |
| `test-data-plane` | Postgres (CNPG -rw), Redis (stack), and Neo4j (Bolt) accept TCP connections from inside the cluster. |

#### Running

```bash
# After `make recreate ENV=kind` has settled:
helm test gibson-workloads --namespace gibson --logs

# Or via the Makefile wrapper:
make verify-release
```

#### Lifecycle

All test Pods carry `helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded`.

- On success, the pod is deleted at the next `helm test` invocation.
- On failure, the pod is **left around** so `kubectl logs <pod>` works for
  diagnosis. Re-running `helm test` recreates it.

A common operator question: "I see a failed `helm-test` Pod — is this stale or
from this run?" Check `kubectl get pod <pod> -o jsonpath='{.metadata.creationTimestamp}'`.

#### Failure-mode signal

Every test pod prefixes hard failures with `::error file=helm-test::` so
GitHub-Actions-style log scrapers and `gh run view --log` pick them out
at-a-glance. The full diagnostic context follows in `[<test-name>]` lines.

#### Adding a new helm-test pod

1. Create `templates/tests/NN-<name>.yaml`. Higher NN runs later (use
   `helm.sh/hook-weight: NN`).
2. Reuse helpers from `templates/tests/_helpers.tpl`:
   - `gibson-workloads.tests.image` (curl-bearing)
   - `gibson-workloads.tests.kubectlImage` (curl + jq + kubectl + openssl)
   - `gibson-workloads.tests.psqlImage`, `gibson-workloads.tests.redisImage`
   - `gibson-workloads.tests.labels`, `gibson-workloads.tests.annotations`
   - `gibson-workloads.tests.errorPrefix` (`::error file=helm-test::`)
3. Use `set -euo pipefail` in any embedded shell.
4. Document the assertion in the leading `{{- /* ... */ -}}` block.
5. Update this README + `docs/runbooks/RUNBOOK-helm-test.md`.
6. If extra RBAC is needed, prefer reusing
   `<release>-test-bootstrap-reader` (already grants get/list on
   secrets+configmaps); only mint a new SA if you need broader scope.

### Override images (air-gapped overlays)

Every test image is overridable via `.Values.tests.*` in an overlay:

```yaml
tests:
  image:           # curl-bearing image (default: curlimages/curl:8.10.1)
    repository: "internal-mirror/curlimages-curl"
    tag: "8.10.1"
  kubectlImage:    # curl + jq + kubectl + openssl
    repository: "internal-mirror/alpine-k8s"
    tag: "1.31.0"
  psqlImage:       # psql binary
    repository: "internal-mirror/bitnami-postgresql"
    tag: "16.4.0-debian-12-r0"
  redisImage:      # redis-cli
    repository: "internal-mirror/redis"
    tag: "7.4.1-alpine"
```

See `values.yaml` `tests:` block for the full set of knobs (bootstrap-Secret
names, postgres host, Zitadel issuer, ...).

## Related

- Slice 5.10 of production-readiness epic: deploy#355.
- Runbook: `docs/runbooks/RUNBOOK-helm-test.md`.
- Org workflow rules: `zeroroot-ai/.github` → `AGENTS.md`.
- Architectural overview: `enterprise/docs/ARCHITECTURE.md`.
