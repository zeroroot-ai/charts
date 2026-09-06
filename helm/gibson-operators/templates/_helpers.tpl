{{/*
Expand the name of the chart.
*/}}

{{/*
Create a default fully qualified app name.
*/}}

{{/*
Create chart name and version as used by the chart label.
*/}}

{{/*
Common labels
*/}}

{{/*
Selector labels
*/}}

{{/*
Create the name of the service account to use
*/}}

{{/*
Image pull secrets from global config
*/}}

{{/*
gibson.image — render a `<repo>:<tag>` image reference for a component.

Usage:
  image: {{ include "gibson.image" (dict "registry" (($.Values.global).registry) "repo" .Values.gibson.image.repository "tag" .Values.gibson.image.tag "appVersion" .Chart.AppVersion) | quote }}

Spec: first-deploy-unblock-and-ha R3.3 — chart writes one image string,
fed by Image Updater's `image.tag` write-back path. `image.digest` is no
longer read by the chart (Week 1); a separate helper or flag may
reintroduce digest pinning in Week 2.

Inputs:
  .repo        (string, required) image repository, e.g. ghcr.io/zeroroot-ai/gibson
  .tag         (string, optional) image tag; falls back to .appVersion
  .appVersion  (string, optional) Chart.AppVersion fallback when .tag is empty

The fallback to `appVersion` matches the pre-existing behavior of the
tenant-operator template, where `tag | default .Chart.AppVersion` was the
canonical pattern. Callers SHOULD pass an explicit tag in their values
files and reserve the appVersion fallback for chart smoke tests.
*/}}

{{/* =========================== Service Host Helpers =========================== */}}

{{/*
Redis host

Redis is structural infrastructure (deploy#224 deleted the
`redis.provider` enum and `redis.external` substitution). The operator
always dials the in-chart redis-stack StatefulSet rendered by
gibson-workloads (`<workloads-release>-redis-stack:6379` by convention).
`redis.addr` is the canonical host:port pair the operator dials; helpers
that need just the host strip the port via splitList.
*/}}

{{/*
Redis URL — Spec 3 R11: never embeds the literal password; consumers
inject ${REDIS_PASSWORD} at runtime via the gibson.redisPasswordSecretName
helper. Redis auth is always on (one-code-path epic / deploy#199).
*/}}

{{/*
Redis config validator — fails chart render when `redis.addr` or the
auth-Secret name is unset. Called once per chart from templates that
consume Redis env vars (operator deployment).
*/}}
{{- define "gibson.validateRedis" -}}
{{- $_ := required "redis.addr is required (one-code-path epic / deploy#199): set to the in-chart redis-stack Service, typically `<workloads-release>-redis-stack:6379`." .Values.redis.addr -}}
{{- /* redis.auth — Secret name required. Either passwordSecret or
       existingSecret (legacy alias). */ -}}
{{- $ps := .Values.redis.auth.passwordSecret | default "" -}}
{{- $es := .Values.redis.auth.existingSecret | default "" -}}
{{- if and (eq $ps "") (eq $es "") -}}
{{- fail "redis.auth.passwordSecret (or legacy redis.auth.existingSecret) is required (one-code-path epic / deploy#199): name the Secret that holds the Redis AUTH password. Redis auth is always on." -}}
{{- end -}}
{{- end -}}

{{/*
==============================================================================
TWO-TIER POSTGRES HELPERS — spec helm-eks-readiness-and-pg-split
==============================================================================

The chart is migrating from four per-component Bitnami Postgres aliases
(dashboard-postgresql, tenant-postgresql, fga-postgresql,
zitadel-postgresql) to a two-tier model:

  platformPostgres   → Gibson-owned data: gibson_platform (control + dashboard
                       state) + per-tenant data-plane databases that the daemon
                       CREATEDBs as siblings on the same cluster.

  thirdPartyPostgres → Vendor app data: zitadel, openfga — independent
                       databases on one shared cluster, each owned by its
                       app's user.

Both tiers support an `external.enabled: true` mode that bypasses the
in-chart StatefulSet and points at an externally managed cluster (RDS in
prod, anything in dev that operators want to override).

Phase 1 (this commit): helpers added, no consumer references them yet.
Phase 2: consumers switch from per-alias helpers to these tier helpers.
Phase 3: in-chart consolidated StatefulSets land; the five old aliases are
         deleted; values-kind.yaml routes through the consolidated tiers.
*/}}

{{/*
gibson.platformPostgres.host — hostname of the platform tier.

Phase 1+2 default: forwards to the existing dashboard-postgresql alias so
consumers can switch from `gibson.dashboard.dbHost` to
`gibson.platformPostgres.host` byte-identically.

Phase 3 changes the default to `<release>-platform-postgresql` (a new
consolidated StatefulSet that hosts gibson_platform + per-tenant DBs).
*/}}


{{/*
Database / username / password keys default to the existing dashboard-postgresql
alias auth block (database=dashboard, user=dashboard, secret-key
DASHBOARD_DB_PASSWORD). Phase 6 renames `dashboard` → `gibson_platform`.
*/}}





{{- /* Phase 3 in-chart consolidated postgres uses plain postgres image
       without TLS — sslMode default flips to "disable" for kind dev.
       Prod overlays (external.enabled=true) keep "require". */ -}}

{{/*
==============================================================================
TENANT POSTGRES HELPERS — deploy#159

The tenant-operator's data-plane provisioner needs a Postgres role with
CREATEDB privilege. The `gibson_platform` role (used by the daemon for
its own platform data) does NOT have CREATEDB; using it for the operator
causes every tenant's saga step "ProvisionDataPlane.Postgres" to fail
permanently with SQLSTATE 42501 — see deploy#159 for the cascade.

A separate `tenant_admin` role (provisioned by kind-bootstrap and the
EKS overlay) has CREATEDB and a dedicated credentials Secret. These
helpers route the tenant-operator's DATAPLANE_PG_ADMIN_DSN to that
role/Secret pair, keeping it cleanly separated from the daemon's
platform-data path.

Host/port default to the platformPostgres host (same physical
instance), so only username + password Secret differ. SSL mode inherits
from platformPostgres unless explicitly overridden.

Values block (added to gibson-operators/values.yaml + values-kind.yaml):

    tenantPostgres:
      # host/port default to platformPostgres if unset.
      host: ""
      port: 5432
      username: tenant_admin
      passwordSecretName: tenant-admin-postgres-credentials
      passwordSecretKey: password
      sslMode: ""    # defaults to platformPostgres.sslMode
==============================================================================
*/}}

{{- define "gibson.tenantPostgres.host" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- if $cfg.host -}}
{{- $cfg.host -}}
{{- else -}}
{{- /* Same physical instance as platformPostgres by default. */ -}}
{{- include "gibson.platformPostgres.host" . -}}
{{- end -}}
{{- end }}

{{- define "gibson.tenantPostgres.port" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- if $cfg.port -}}
{{- $cfg.port -}}
{{- else -}}
{{- include "gibson.platformPostgres.port" . -}}
{{- end -}}
{{- end }}

{{- define "gibson.tenantPostgres.username" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- $u := $cfg.username | default "tenant_admin" -}}
{{- /* Render-time guard (deploy#159): refuse to render the operator's
       DSN with `gibson_platform`, the role that lacks CREATEDB and
       caused 100% of tenant data-plane provisioning to fail before
       the fix. */ -}}
{{- if eq $u "gibson_platform" -}}
{{- fail (printf "tenantPostgres.username=%q is the wrong role — gibson_platform lacks CREATEDB. The tenant-operator must use tenant_admin (default). See deploy#159." $u) -}}
{{- end -}}
{{- $u -}}
{{- end }}

{{- define "gibson.tenantPostgres.passwordSecretName" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- $cfg.passwordSecretName | default "tenant-admin-postgres-credentials" -}}
{{- end }}

{{- define "gibson.tenantPostgres.passwordSecretKey" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- $cfg.passwordSecretKey | default "password" -}}
{{- end }}

{{- define "gibson.tenantPostgres.sslMode" -}}
{{- $cfg := .Values.tenantPostgres | default dict -}}
{{- if $cfg.sslMode -}}
{{- $cfg.sslMode -}}
{{- else -}}
{{- include "gibson.platformPostgres.sslMode" . -}}
{{- end -}}
{{- end }}

{{/*
gibson.thirdPartyPostgres.<db>.host — host is per-database in Phase 1+2
(forwards to each existing alias) and converges to a shared
`<release>-thirdparty-postgresql` host in Phase 3.

Defaults below mirror the pre-refactor per-alias values
(zitadel-postgresql, fga-postgresql) so consumers
can swap their reference byte-identically. Phase 3 introduces a single
`<release>-thirdparty-postgresql` StatefulSet and the helpers' defaults
swing over.
*/}}

{{/* === ZITADEL === */}}

{{/* === OPENFGA === */}}

{{/*
==============================================================================
LEGACY PER-ALIAS HELPERS — preserved for Phase 1+2 backward compatibility.
Phase 3 deletes the five Bitnami subchart aliases; at that point the
helpers below either retire or rewrite to forward to the tier helpers.
==============================================================================
*/}}

{{/*
Dashboard PostgreSQL host (dedicated instance for Better Auth + tenant provisioning)
*/}}

{{/*
Tenant data-plane admin PostgreSQL host (dedicated instance for the daemon's
CREATEDB-bootstrap admin pool — Spec per-tenant-data-plane-completion Task 25).
*/}}

{{/*
Tenant data-plane admin PostgreSQL host resolver — accepts an explicit override
in dataPlane.postgres.host (e.g. RDS in prod) and falls back to the in-chart
tenant-postgresql Service when the in-chart subchart is enabled.
*/}}

{{/*
MinIO service host (Bitnami subchart name pattern: <release>-minio)
*/}}

{{/*
Jaeger host
*/}}

{{/*
Prometheus host
*/}}

{{/*
Grafana host
*/}}

{{/*
Loki host
*/}}

{{/*
Dashboard PostgreSQL host helper (resolves to dashboard-postgresql service)
*/}}

{{/*
Vault host
*/}}
{{/*
Dashboard host
*/}}

{{/* =========================== Secret Name Helpers =========================== */}}

{{/*
LLM secrets name
*/}}

{{/*
LLM secrets name (alias for compatibility)
*/}}

{{/*
Database secrets name
*/}}

{{/*
Database secrets name (alias for compatibility)
*/}}

{{/*
Dashboard secrets name
*/}}

{{/*
NOTE: a `gibson.randomSecret` helper used to live here. It called `lookup`
to read an existing Secret and fall back to `randAlphaNum` on first install.
It was unreferenced (no template ever invoked it) and used `lookup`, which
silently returns nil under Argo's repo-server render (no cluster context).
Deleted in deploy#202 as part of the one-code-path lookup ripout. Where
the preserve-secret pattern is still needed for an in-use Secret, it is
being replaced in follow-up issues (see deploy#186) with a pre-install
Job + init-container read so the Secret is materialised once and consumed
deterministically.
*/}}

{{/* =========================== gRPC Port Helpers =========================== */}}

{{/*
Canonical gRPC port for the Gibson daemon.
Single source of truth consumed by:
  - templates/gibson/configmap.yaml  (daemon.grpc_address)
  - templates/gibson/statefulset.yaml (grpc containerPort)
  - templates/gibson/service.yaml    (grpc port + targetPort backfill)
  - templates/dashboard/deployment.yaml (GIBSON_DAEMON_URL)

Rendered as a bare integer (no quotes, no colon prefix).
See .spec-workflow/specs/spiffe-helm-integration/.
*/}}

{{/*
Dashboard → Daemon URL.

Scheme is tied to whether the daemon ACTUALLY serves mTLS, NOT to whether
SPIRE is deployed (which is now always, deploy#201). The daemon's mTLS
listener is gated on .Values.gibson.auth.spiffe being a populated map
(trustDomain + workloadAPISocket); see templates/gibson/configmap.yaml line
86. SPIRE is required for every consumer (tenant-operator workload
identity, ext-authz mTLS, the SPIRE OIDC discovery provider's own TLS
cert) and may be deployed without the daemon serving mTLS. (The historical
spiffe-jwks-exporter consumer was removed by spec service-acting-auth
Task 17; the dashboard spiffe-helper sidecar never existed outside a
never-invoked template and was deleted by deploy#1456.)

When the daemon DOES serve mTLS:
  - scheme = https
  - host  = <fullname>.<namespace>.svc.cluster.local (DNS form so the
            daemon's serving cert SAN matches `gibson.<ns>.svc.cluster.local`)

When the daemon does NOT serve mTLS:
  - scheme = http
  - host  = <fullname> (short Service name; saves a DNS hop in plain h2c)

History: this helper was reverted in commit 9495509 because the surrounding
state was broken (daemon SPIFFE was off, dashboard had no mTLS client). With
spec in-cluster-mtls-restoration Phase 1 (Task 3) the daemon serves mTLS in
every overlay including Kind, and Track A / Track B (Tasks 6-13) wire the
dashboard's mTLS or JWT-SVID client respectively. The helper is correct in
the new world — re-landed by Task 4.

Note: callers that route through Envoy (gibson-admin-client.ts and, post
Track B, gibson-client.ts) ignore this URL entirely — they dial Envoy at
ADMIN_ENVOY_BASE_URL with a JWT-SVID. This helper feeds GIBSON_DAEMON_URL /
GIBSON_API_URL, which gibson-client.ts uses for the direct path. After Track
B + the soak (Phase 9) GIBSON_DAEMON_URL is removed and so is this helper's
last consumer.
*/}}

{{/* =========================== SPIRE Precondition =========================== */}}

{{/*
gibson.validateSpire — placeholder validator in the operators chart. SPIRE
is required infrastructure (deploy#201) but the SPIRE server itself is
managed by the workloads chart (or a sibling Application), so this
operators-chart copy of the validator does nothing today. Kept for symmetry
with the workloads chart's version.
*/}}

{{/*
gibson.waitForSpireSocket — init container that blocks pod start until the
SPIRE agent's Workload API socket is present on the node. See the workloads
chart's helper of the same name for the canonical documentation; this copy
exists so the operators chart's tenant-operator + platform-operator
deployments can render the same init container without depending on a
helper from a different chart. deploy#201 / epic deploy#186.
*/}}

{{/* =========================== FGA Config Gate =========================== */}}

{{/*
gibson.waitForFgaConfig — init container that blocks pod start until the
gibson-fga-config ConfigMap is populated with a non-empty store_id key.

The gibson-fga-init Job (templates/fga-init/job.yaml in gibson-workloads)
runs as a regular Job alongside pods — NOT a pre-install helm hook — so pods
that read FGA_STORE_ID / FGA_MODEL_ID from the ConfigMap can schedule before
the Job writes it. Without this init container the env vars would be empty at
container-start time, causing the binary to exit 1 with "FGA store_id not
configured".

This init container replaces the previous `optional: true` pattern on env
configMapKeyRef refs (deploy#190 M4). Because it blocks the main container
until the ConfigMap exists, kubelet re-evaluates the configMapKeyRef env vars
at the moment the main container starts — by that point gibson-fga-config is
guaranteed to contain a non-empty store_id.

Requires: the pod's ServiceAccount must have `configmaps: get` in the release
namespace. The tenant-operator SA has this via the release-namespace-rbac Role.

Hard 300s timeout (matches the fga-init Job's activeDeadlineSeconds). If the
ConfigMap hasn't appeared in 5 minutes, the fga-init Job itself has
failed/timed-out and manual intervention is required.

Invoke via: {{ include "gibson.waitForFgaConfig" . | nindent 8 }}
under the pod's initContainers list.
*/}}

{{/*
gibson.validateEnvoyGateway — no-op stub kept for caller backward compat.

Spec: zitadel-envoy-gateway-migration, task 12 (Requirements 3.7, 5.5).

This helper historically failed the render when the daemon required the
Envoy gateway path (gibson.config.identity.requireEnvoy=true) but
extAuthz.enabled was false. Both Envoy (deploy#200) and ext-authz
(deploy#188; the `extAuthz.enabled` toggle was deleted as part of the
one-code-path epic) are now structural / unconditionally-deployed
infrastructure. There is no longer any combination of values that can
satisfy requireEnvoy=true without also deploying both pieces, so the
helper has nothing to fail on.

The define is retained as a no-op so callers
(templates/gibson/statefulset.yaml) keep rendering without change. Safe
to remove once no caller invokes it.
*/}}

{{/*
gibson.validateSpiffeRequired — fails the render when gibson.auth.spiffe is
null/empty in any overlay. Memorialises the "SPIFFE stays ON" invariant from
memory feedback_spiffe_mtls_required.md as a structural chart guard.

Spec: in-cluster-mtls-restoration, Component 2 / Requirement 1.

PHASE 0 STATE (Task 1): NO-OP. The body is comment-only so the failure
message is committed BEFORE activation in Task 18. Any chart render that
happens between Task 1 and Task 18 behaves identically with or without this
helper. Once Task 18 lands, the comment is replaced with a real `fail` call
and any overlay that nulls gibson.auth.spiffe stops rendering.

Invocation will land alongside activation in Task 18 (templates/_pre-render.yaml
or the daemon statefulset, same place gibson.validateEnvoyGateway is invoked).
Until then, defining the helper without invoking it is intentional.
*/}}

{{/*
gibson.validateEnvoySdsWired — fails the render when gibson.auth.spiffe is
populated BUT the rendered Envoy daemon cluster lacks the SDS
UpstreamTlsContext. (Envoy is unconditionally enabled — deploy#200.)

Catches the inverse mistake of "I disabled SDS to debug something but forgot
to disable daemon SPIFFE too" — exactly the failure mode that produced commit
1d11963 ("kind overlay disables daemon SPIFFE mTLS + reverts envoy upstream
TLS"). Without this guard, daemon SPIFFE on + Envoy SDS off renders cleanly
but every gateway-routed RPC fails at runtime with "no certificate".

Spec: in-cluster-mtls-restoration, Component 9 / Requirement 2.

PHASE 0 STATE (Task 2): NO-OP. The body is comment-only so the failure
message is committed BEFORE activation in Task 19. Once Task 19 lands the
body is replaced with a real `fail` invocation that inspects the rendered
Envoy configmap for the transport_socket block on the gibson_daemon_grpc
cluster. The activated check is a render-time string match (helm template
output piped through yq) — no live cluster dependency.
*/}}

{{/* =========================== Component Health Probes =========================== */}}

{{/*
Component health port definition for container spec.
Renders the named port entry for the health endpoint.
*/}}

{{/*
Component liveness and readiness probes.
Uses httpGet against the SDK health endpoints on the configured health port.

Usage: {{ include "gibson.health.probes" $root }}
*/}}

{{/*
Render an annotations block from a map value. Intended for ServiceAccount
templates that accept an optional .Values.<component>.serviceAccount.annotations
map (primarily used for IRSA role-arn injection). Usage:

  {{- with .Values.dashboard.serviceAccount.annotations }}
  annotations:
    {{- include "gibson.irsaAnnotations" . | nindent 4 }}
  {{- end }}

The template itself does not emit the `annotations:` key — the caller
includes it so `{{- with }}` correctly no-ops when the map is empty.
*/}}

{{/* =========================== Zero-Trust-Hardening Traceability =========================== */}}

{{/*
gibson.zeroTrustHardeningVersion — render the schema version of the
zero-trust-hardening remediation that is baked into this chart copy.

Bumped by spec maintainers when a follow-up wave of the spec lands a chart
change (rare). The current value is "1" — the initial Wave 1 cut shipped by
spec zero-trust-hardening tasks 5.2-5.5. The label
`zeroroot.ai/zero-trust-hardening-version` is rendered by
templates/gibson/statefulset.yaml on the daemon workload (and ONLY the
daemon workload — other workloads do NOT carry this label, deliberately,
so an operator can use a label selector to confirm the daemon fleet
specifically is on the post-fix configuration).

Operator usage:
  kubectl get sts gibson -o jsonpath='{.metadata.labels}' | grep zero-trust
  kubectl get sts -A -l zeroroot.ai/zero-trust-hardening-version=1

The value is a string ("1"), not a number — Kubernetes label values must
be strings; the consumer template MUST quote the value.
*/}}

{{/* =========================== HA Spread Helper (Week 2 §3 R8) ========================= */}}

{{/*
gibson.spread — emit pod topologySpreadConstraints + affinity blocks for an HA
component. Reads per-component overrides from
.Values.<component>.{topologySpreadConstraints, podAntiAffinity, affinity,
nodeSelector, tolerations} and falls back to chart-wide HA defaults.

Usage:
  {{- include "gibson.spread" (dict "ctx" . "component" "gibson") | nindent 6 }}

Emits these top-level pod-spec keys when set (each on its own line; the
caller decides indentation via nindent on the include):
  - affinity:
  - topologySpreadConstraints:
  - nodeSelector:
  - tolerations:

Replicas <= 1 still emit constraints with whenUnsatisfiable=ScheduleAnyway
so a future replica bump gets the constraints automatically.
*/}}

{{/* =========================== PriorityClass Helper (Week 2 §3 R18) =================== */}}

{{/*
gibson.priorityClassName — return the priorityClassName for a component.
Components are tiered:
  - critical: daemon, ext-authz, envoy, openfga, spire-server  → gibson-platform-critical (900)
  - platform: dashboard, tenant-operator                       → gibson-platform (500)
  - tenant:   tenant-neo4j                                     → tenant-neo4j (100, existing)

The chart renders the gibson-platform-critical and gibson-platform
PriorityClass objects from templates/operations/priority-classes.yaml
(unconditionally, like the existing tenant-neo4j PC).

Usage:
  priorityClassName: {{ include "gibson.priorityClassName" (dict "component" "gibson") }}
*/}}

{{/* =========================== Redis Password Secret Helper (Week 2 §2 R11) =========== */}}

{{/*
gibson.redisPasswordSecretName — name of the Secret that holds the Redis
auth password.
  - When .Values.redis.auth.passwordSecret is set, return that.
  - Otherwise return the chart-managed `<release>-redis-stack` Secret name.
*/}}

{{/*
gibson.redisPasswordSecretKey — Secret key for the Redis password.
  - When .Values.redis.auth.passwordSecretKey is set, return that.
  - Otherwise return "redis-password" (the chart-managed default).
*/}}

