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
Redis host — the in-chart redis-stack Service.

Redis is structural infrastructure (deploy#224 deleted the
`redis.provider` enum and `redis.external` substitution). The chart
always renders the in-cluster redis-stack StatefulSet; consumers dial
`<release>-redis-stack` on `redis.service.port`. Customers wanting a
managed Redis become a separate product question, not a chart toggle.
*/}}

{{/*
Redis URL — Spec 3 R11: never embeds the literal password; consumers
inject ${REDIS_PASSWORD} at runtime via the gibson.redisPasswordSecretName
helper. Redis auth is always on (one-code-path epic / deploy#199).
*/}}

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
stripe-mock host — dev-only stub that replaces api.stripe.com in kind so
the tenant-operator readyz Stripe probe passes without a live Stripe key.
Disabled in production overlays (stripeMock.enabled: false).
*/}}
{{- define "gibson.stripeMock.host" -}}
{{- printf "%s-stripe-mock" .Release.Name }}
{{- end }}

{{/*
mailpit host — dev-only delivering SMTP sink that satisfies the daemon's
mailer.RequireDelivering gate on kind (signup-email-delivery, gibson#1228
companion). Disabled outside kind (mailpit.enabled: false).
*/}}
{{- define "gibson.mailpit.host" -}}
{{- printf "%s-mailpit" .Release.Name }}
{{- end }}

{{/*
gibson.emailSmtpSecret.name — the K8s Secret name the daemon's SMTP
credentials ExternalSecret materialises (gibson.email.smtp.externalSecret).
*/}}
{{- define "gibson.emailSmtpSecret.name" -}}
{{- printf "%s-email-smtp" .Release.Name }}
{{- end }}

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
Impersonation signing key Secret name. The daemon mounts the
GIBSON_IMPERSONATION_KEY env var from this Secret; key persistence is
required (gibson#103) so tokens issued before a restart remain valid and
HA replicas agree on signatures.
*/}}
{{- define "gibson.impersonationSecret.name" -}}
{{- printf "%s-impersonation-key" (include "gibson.fullname" .) }}
{{- end }}

{{/*
Capability-Grant JWT signing key Secret name (GHSA-3957, gibson#1288).

Release-prefixed like the impersonation key: nothing outside this chart
references it by literal string — the daemon reaches it through a volume
mount, not a values-supplied *SecretRef — so the prefix is safe and keeps two
releases in one namespace from colliding.
*/}}
{{- define "gibson.cgSigningKeySecret.name" -}}
{{- printf "%s-cg-signing-key" (include "gibson.fullname" .) }}
{{- end }}

{{/*
Stripe credentials Secret name.

This name is REFERENCED by literal string `gibson-stripe-credentials` in
helm/gibson-workloads/values-kind.yaml (dashboard.billing.stripeSecretKeySecretRef)
and consumed by the dashboard Deployment's wait-for-stripe-secrets init
container and STRIPE_SECRET_KEY / STRIPE_WEBHOOK_SECRET env entries.

Do NOT prefix with .Release.Name — the value in dashboard.billing.*SecretRef
is the literal Secret name and is intentionally environment-stable.
*/}}
{{- define "gibson.stripeSecrets.name" -}}
gibson-stripe-credentials
{{- end }}

{{/*
Billing-webhook shared-secret Secret name (deploy#1314).

Materialised by templates/secrets/billing-webhook-secret.yaml with the single
key GIBSON_BILLING_WEBHOOK_SECRET. Both ends of the SetTenantBillingActive hop
reference it by this literal name — the daemon StatefulSet's secretKeyRef and,
once dashboard#1016 is decided, the caller workload — so it is intentionally
NOT release-prefixed and environment-stable, exactly like the Stripe Secret.
*/}}
{{- define "gibson.billingWebhookSecret.name" -}}
gibson-billing-webhook-secret
{{- end }}

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
gibson.validateSpire — no-op stub kept for caller backward compat.

The SPIRE server is now rendered by exactly one mechanism: the umbrella
chart's own `spire` sub-chart dependency (helm/gibson/Chart.yaml). A
standalone gitops Argo Application (apps/prod/spire.yaml) briefly held
that role after deploy#751 (PRD deploy#750) removed the in-chart SPIRE
server template and its `dev.externallyManagedSpire` toggle (split brain,
deploy#742/#743) — that Application was itself retired with the
single-main gitops collapse. With a
single owner there is no in-chart-vs-external choice left for this
validator to police; single-ownership is enforced structurally by
cross-chart-check Check 19 (component-ownership). SPIRE remains REQUIRED
(deploy#201): every SPIFFE-consuming pod's wait-for-spire-socket init
container fails fast if the agent socket is absent.
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

{{/* =========================== SPIFFE Socket Wait Init =========================== */}}

{{/*
gibson.waitForSpireSocket — init container that blocks pod start until the
SPIRE agent's Workload API socket is present on the node. Every SPIFFE-
consuming pod (daemon, ext-authz, tenant-operator, dashboard) renders this
ahead of its main container so a missing/restarting SPIRE agent produces a
visible Init state instead of a generic CreateContainerConfigError or
silent SPIFFE-Workload-API-unavailable retry loop.

Spec one-code-path (deploy#201 / epic deploy#186): SPIRE is required
infrastructure; the .Values.spire.enabled toggle is gone. This init
container is the runtime equivalent of "fail at boot if a dependency is
missing".

Hard 60s timeout. The agent socket appears within seconds of the SPIRE
agent DaemonSet pod becoming Ready on the node; if it isn't there after a
minute, the SPIRE control plane is broken and we want the pod to fail-fast
so kubelet retries (and so the operator's "pod stuck in Init" alert fires)
instead of waiting 5+ minutes.

The socket path is the chart's canonical `/run/spire/agent/spire-agent.sock`
which is fixed by the SPIRE Helm chart's hostPath bind mount.

Invoke via: {{ include "gibson.waitForSpireSocket" . | nindent 8 }}
under a pod template's `spec.initContainers:` list. The caller MUST also
declare the `spire-agent-socket` volume with the matching mount path.
*/}}

{{/* =========================== FGA Config Gate =========================== */}}

{{/*
gibson.waitForFgaConfig — init container that blocks pod start until the
gibson-fga-config ConfigMap is populated with a non-empty store_id key.

The gibson-fga-init Job (templates/fga-init/job.yaml) runs as a regular Job
alongside pods — it is NOT a pre-install helm hook — so pods that read
EXT_AUTHZ_FGA_STORE_ID / EXT_AUTHZ_FGA_MODEL_ID (or their equivalents) from
the ConfigMap can schedule before the Job writes the ConfigMap. Without this
init container the env vars would be empty at container-start time, causing
the binary to exit 1 with "FGA store_id not configured".

This init container replaces the previous `optional: true` pattern on those
env refs (deploy#190 M4). Because it blocks the main container until the
ConfigMap exists, kubelet re-evaluates the configMapKeyRef env vars at the
moment the main container starts — by that point gibson-fga-config is
guaranteed to contain a non-empty store_id.

Requires: the pod's ServiceAccount must have `configmaps: get` in the
release namespace. The tenant-operator SA already has this via the
release-namespace-rbac Role; ext-authz gets it via the new
gibson-ext-authz-fga-config-reader Role (templates/ext-authz/fga-config-rbac.yaml).

Hard 300s timeout PER ATTEMPT — the kubelet restarts the init container on
failure, so the effective wait is unbounded and survives a slow fga-init.
(The fga-init Job's own activeDeadlineSeconds is 1200s; see
templates/fga-init/job.yaml.)

Invoke via: {{ include "gibson.waitForFgaConfig" . | nindent 8 }}
under the pod's initContainers list. No extra volumes needed (uses in-cluster
SA token via automountServiceAccountToken default).
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


{{/*
gibson.envoyEdge.caRequired — TRUE when consumers must mount the Envoy
edge CA into their trust store, FALSE when the system trust bundle is
sufficient.

The decision is keyed on the cert-manager Issuer that signs gibson-envoy-tls
(certManager.envoyEdge.issuer):

  - selfsigned-ca        → TRUE: the self-signed root is NOT in any system
                                 bundle; consumers must mount it explicitly
                                 (kind / on-prem self-hosted).
  - letsencrypt-*        → FALSE: Let's Encrypt's intermediates chain to
                                  ISRG Root X1 which IS in every modern
                                  system trust store; no consumer mount.
  - awspca-issuer        → FALSE: AWS Private CA chains to a root the
                                  workload's IAM trust policy already
                                  trusts (assumed).
  - openbao-issuer         → TRUE:  openbao-issued certs chain to the in-
                                  cluster Vault PKI root; consumers must
                                  mount it explicitly.

Used by dashboard (NODE_EXTRA_CA_CERTS) and the daemon (SSL_CERT_DIR /
the /etc/ssl/envoy-ca mount). Single decision point per deploy#126 —
no consumer template re-derives the same rule inline. PRD deploy#337
Wave 5 slice C.
*/}}
{{- define "gibson.envoyEdge.caRequired" -}}
{{- $issuer := (.Values.certManager.envoyEdge).issuer | default "" -}}
{{- if or (eq $issuer "selfsigned-ca") (eq $issuer "openbao-issuer") -}}
true
{{- else -}}
false
{{- end -}}
{{- end }}

{{/*
=============================================================================
Setec dispatch — one derivation point for the daemon→frontend leg
(deploy#1106).

Everything below is derived from `setec.*` rather than restated under
`gibson.sandbox.setec.*`, because the two must agree exactly and there is no
render-time error when they do not: a wrong address is a dial timeout at the
first sandboxed tool call, and a wrong serverName is a handshake failure at
the same moment. Both surface hours after the deploy, in gibson's logs, as a
setec problem. An overlay that runs setec now sets `gibson.sandbox.enabled`
and the tenant, and nothing else.
=============================================================================
*/}}

{{/*
gibson.setecFullname — the setec subchart's object-name prefix.
Canonically `setec` via setec.fullnameOverride; falls back to the
release-derived name the subchart would otherwise pick.
*/}}
{{- define "gibson.setecFullname" -}}
{{- .Values.setec.fullnameOverride | default (printf "%s-setec" .Release.Name) -}}
{{- end }}

{{/*
gibson.setecNamespace — the namespace the setec subchart installs into.
NOT the release namespace; the frontend Deployment, its Service and its TLS
Secrets all live here.
*/}}
{{- define "gibson.setecNamespace" -}}
{{- .Values.setec.namespace | default "setec-system" -}}
{{- end }}

{{/*
gibson.setecFrontendName — the frontend Service / Deployment name.
*/}}
{{- define "gibson.setecFrontendName" -}}
{{- (.Values.setec.frontendService).name | default (printf "%s-frontend" (include "gibson.setecFullname" .)) -}}
{{- end }}

{{/*
gibson.setecFrontendAddress — host:port the daemon dials.
*/}}
{{- define "gibson.setecFrontendAddress" -}}
{{- printf "%s.%s.svc.cluster.local:%d" (include "gibson.setecFrontendName" .) (include "gibson.setecNamespace" .) (int ((.Values.setec.frontendService).port | default 50051)) -}}
{{- end }}

{{/*
gibson.setecFrontendServerName — the TLS serverName the daemon verifies.

Must be a name the frontend's server certificate actually carries. That cert
is minted by templates/setec/frontend-tls.yaml with commonName
`<frontend>.<ns>.svc` and dnsNames covering the short, two-label, `.svc` and
`.svc.cluster.local` forms — the `.svc` form is used here because it is what
setec's own round-trip test defaults to (gibson
internal/engine/harness/setec_roundtrip_setec_test.go), so both callers
verify the same name.
*/}}
{{- define "gibson.setecFrontendServerName" -}}
{{- printf "%s.%s.svc" (include "gibson.setecFrontendName" .) (include "gibson.setecNamespace" .) -}}
{{- end }}

{{/*
gibson.setecClientSecretName — the release-namespace Secret holding the
daemon's client keypair plus the CA that signed the frontend's server cert.
*/}}
{{- define "gibson.setecClientSecretName" -}}
{{- (.Values.gibson.sandbox.setec).clientSecretName | default "gibson-setec-client-tls" -}}
{{- end }}

{{/*
gibson.setecMtlsMountPath — where that Secret is mounted in the daemon pod.
A sibling of /etc/gibson, not a subpath of it: kubelet rejects a mount that
targets a path already occupied by another volume, and the `config`
ConfigMap already owns /etc/gibson (same reason /etc/gibson-kek is a
sibling).
*/}}
{{- define "gibson.setecMtlsMountPath" -}}
{{- (.Values.gibson.sandbox.setec).mtlsMountPath | default "/etc/gibson-setec-mtls" -}}
{{- end }}

{{/*
gibson.envoy.wafDirectives — read a files/coraza/<chain>.conf and emit the
JSON array of directive lines the Coraza WASM filter takes in its
`directives_map` (Edge WAF, deploy#1658). Comment and blank lines are
dropped; a backslash-continued line is joined with the next, because Coraza
reads each array element as one directive. Values from envoy.waf are
substituted (paranoia level, anomaly thresholds) so the numbers live in ONE
place. Usage: include "gibson.envoy.wafDirectives" (dict "ctx" . "file" "files/coraza/browser.conf")
*/}}
{{- define "gibson.envoy.wafDirectives" -}}
{{- $raw := tpl (.ctx.Files.Get .file) .ctx -}}
{{- $lines := list -}}
{{- $acc := "" -}}
{{- range (splitList "\n" $raw) -}}
{{- $t := trim . -}}
{{- if or (eq $t "") (hasPrefix "#" $t) -}}
{{- else if hasSuffix "\\" $t -}}
{{- $acc = printf "%s%s " $acc (trimSuffix "\\" $t) -}}
{{- else -}}
{{- $lines = append $lines (printf "%s%s" $acc $t) -}}
{{- $acc = "" -}}
{{- end -}}
{{- end -}}
{{- toJson $lines -}}
{{- end -}}
