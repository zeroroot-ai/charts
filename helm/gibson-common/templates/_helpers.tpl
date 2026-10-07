{{/*
  gibson-common library helpers.

  A helper lives here when more than one chart names the same thing. The
  drift this prevents is the reason the library exists, not abstraction for
  its own sake: "gibson:50051" against "gibson-workloads:50051" cost
  tenant-operator#70, and clusterIP pins that disagreed across charts cost
  deploy#169 and deploy#171.

  Convention for adding a helper:
    1. Add it here as `{{- define "gibson.helperName" -}}…{{- end -}}`.
    2. Put its documentation in the comment block IMMEDIATELY ABOVE the
       define, and nowhere else. A copy in a consuming chart is how the two
       drift apart, and a header left behind when a define moves is a table
       of contents for a helper the reader cannot find — charts#304 deleted
       118 of those. scripts/check-helper-docs.py fails the build on one.
    3. Say which values keys it reads, and which bug class it locks.
    4. Update consuming charts in the same PR. CI's cross-chart-check
       (deploy#180) refuses a chart that bypasses the helper.

  Never call `lookup`. Under Argo's repo-server render there is no cluster
  context, so `lookup` returns nil with no error and the helper silently
  takes its fallback branch. A `gibson.randomSecret` helper did exactly that
  — read an existing Secret, fall back to randAlphaNum on first install — and
  was deleted in deploy#202 with the rest of the lookup ripout. A Secret that
  must survive a re-render is materialised once by a pre-install Job and read
  by an init container instead.

  Spec: zeroroot-ai/tenant-operator#76 PRD Module 6 / deploy#179.
*/}}

mailpit host — the dev-only delivering SMTP sink that satisfies the daemon's
mailer.RequireDelivering gate on kind. Disabled outside kind
(mailpit.enabled: false).

It lives here, not in gibson-workloads, because two charts name it: the
workloads chart renders the Service, and the operators chart's
tenant-operator dials it as its default SMTP_HOST. The operators chart used
to hardcode "gibson-workloads-mailpit", which is a Service no release
renders — the name is keyed off the RELEASE name, so it is "gibson-mailpit"
for every install (charts#114). One definition, two callers.

Values read: none. Derived from .Release.Name.
*/}}
{{- define "gibson.mailpit.host" -}}
{{- printf "%s-mailpit" .Release.Name }}
{{- end }}

{{/*
  gibson.daemonAddress

  Returns the canonical daemon gRPC dialing address: "<service>:<port>".

  Source of truth: the workloads chart's daemon Service name + port. In a
  unified install the daemon Service is named "gibson" (chart fullname
  default) and listens on 50051; on a split-release install or a custom
  Service name, callers override via .Values.gibson.daemonAddress at the
  consuming-chart level.

  Resolution order:
    1. .Values.gibson.daemonAddress when set (operator escape hatch for
       split-release installs).
    2. "<daemon Service>:50051". The daemon Service is gibson.fullname of
       the workloads chart, under the same release: "<release>-gibson-workloads",
       or the release name alone when it already holds "gibson-workloads".
       Under the umbrella release `gibson` that is "gibson-gibson-workloads".

  Callers in templates:
      env:
        - name: GIBSON_DAEMON_ADDRESS
          value: {{ include "gibson.daemonAddress" . | quote }}

  Values keys read:
    - .Values.gibson.daemonAddress       (the one override)
    - .Release.Name                      (used to construct the default)

  Bug class it locks: the default named "<release>:50051", a Service that no
  release renders, and a second values key hid that by overriding it in
  every install (charts#360).
*/}}
{{- define "gibson.daemonAddress" -}}
{{- $override := "" -}}
{{- if and (hasKey .Values "gibson") .Values.gibson -}}
  {{- $override = .Values.gibson.daemonAddress | default "" -}}
{{- end -}}
{{- if ne $override "" -}}
{{- $override -}}
{{- else -}}
{{- $workloads := "gibson-workloads" -}}
{{- $svc := ternary .Release.Name (printf "%s-%s" .Release.Name $workloads) (contains $workloads .Release.Name) -}}
{{- printf "%s:50051" ($svc | trunc 63 | trimSuffix "-") -}}
{{- end -}}
{{- end -}}

{{/*
  Identity-provider addressing (ADR-0092). Every in-cluster Zitadel client
  CONNECTS to the Zitadel Service by Kubernetes DNS and CLAIMS the public
  host in the x-zitadel-instance-host header. Zitadel selects its instance
  from that header, so no pod needs hostAliases and no identity request meets
  the public edge. These two values are the only names for the two facts.
  Both derive from what an install already has: the release and
  global.domain. No environment sets them.

  gibson.zitadel.url             where to connect: the Zitadel Service
  gibson.zitadel.externalDomain  what to claim: the bare app host. It never
                                 carries a port, because Zitadel stamps a
                                 port in the claimed host into the issuer.
  gibson.zitadel.env             both, as container env entries.

  The context needs .Release and .Values.global.domain.
*/}}
{{- define "gibson.zitadel.url" -}}
{{- printf "http://%s-zitadel.%s.svc.cluster.local:8080" .Release.Name .Release.Namespace -}}
{{- end -}}

{{- define "gibson.zitadel.externalDomain" -}}
{{- include "gibson.appHost" . -}}
{{- end -}}

{{- define "gibson.zitadel.env" -}}
- name: ZITADEL_URL
  value: {{ include "gibson.zitadel.url" . | quote }}
- name: ZITADEL_EXTERNAL_DOMAIN
  value: {{ include "gibson.zitadel.externalDomain" . | quote }}
{{- end -}}

{{/*
  ─────────────────────────────────────────────────────────────────────
  Domain derivation helpers (deploy#630 / ADR two-plane addressing).

  Single source of truth: `global.domain`. Every EXTERNAL hostname the
  platform serves or claims derives from it here — no chart, template,
  or service hardcodes a hostname. This is the "external-identity plane":
  the public origin a service CLAIMS (issuer, redirect URIs, the Host
  header on domain-routed upstreams, TLS SANs, user-facing links).

  The INTRA-CLUSTER plane (how services CONNECT to each other) is
  Kubernetes service DNS and is NOT derived here — see gibson.daemonAddress
  and per-chart service endpoints.

  Values keys read:
    - .Values.global.domain        REQUIRED. e.g. "zeroroot.ai".
    - .Values.global.scheme        optional, default "https".
    - .Values.global.externalPort  optional. e.g. "30443" in kind;
                                    empty (or "443") in prod → no suffix.
  ─────────────────────────────────────────────────────────────────────
*/}}

{{/* gibson.domain — the platform external domain. FAILS if unset (no
     stale fallback; an unset domain must break render loudly). */}}
{{- define "gibson.domain" -}}
{{- if and (hasKey .Values "global") .Values.global .Values.global.domain -}}
{{- .Values.global.domain -}}
{{- else -}}
{{- fail "global.domain is REQUIRED: set the platform external domain in values (deploy#630 — no hardcoded hostname fallback)" -}}
{{- end -}}
{{- end -}}

{{/* gibson.scheme — URL scheme for the external origin (default https). */}}
{{- define "gibson.scheme" -}}
{{- if and (hasKey .Values "global") .Values.global .Values.global.scheme -}}
{{- .Values.global.scheme -}}
{{- else -}}
https
{{- end -}}
{{- end -}}

{{/* gibson.externalPortSuffix — ":<port>" when a non-default external
     port is configured, else empty. Centralizes the kind :30443 quirk. */}}
{{- define "gibson.externalPortSuffix" -}}
{{- $port := "" -}}
{{- if and (hasKey .Values "global") .Values.global .Values.global.externalPort -}}
{{- $port = .Values.global.externalPort | toString -}}
{{- end -}}
{{- if and (ne $port "") (ne $port "443") -}}:{{ $port }}{{- end -}}
{{- end -}}

{{/* gibson.appHost / apiHost / wwwHost — the public ingress hostnames.
     Bare hosts (no scheme, no port) — for hostAliases, SANs, Host header. */}}
{{- define "gibson.appHost" -}}{{ printf "app.%s" (include "gibson.domain" .) }}{{- end -}}
{{- define "gibson.apiHost" -}}{{ printf "api.%s" (include "gibson.domain" .) }}{{- end -}}
{{- define "gibson.wwwHost" -}}{{ printf "www.%s" (include "gibson.domain" .) }}{{- end -}}
{{- define "gibson.docsHost" -}}{{ printf "docs.%s" (include "gibson.domain" .) }}{{- end -}}

{{/* gibson.apiEndpoint — "api.<domain>:<port>", the host:port form for
     gRPC clients that must name an explicit port. The agent callback uses
     it: a sandboxed agent reaches the platform through the public edge
     like any off-cluster client, never through an in-cluster Service
     name it is not permitted to resolve. */}}
{{- define "gibson.apiEndpoint" -}}
{{- $port := "443" -}}
{{- if and (hasKey .Values "global") .Values.global .Values.global.externalPort -}}
{{- $port = .Values.global.externalPort | toString -}}
{{- end -}}
{{- printf "%s:%s" (include "gibson.apiHost" .) $port -}}
{{- end -}}

{{/* gibson.externalOrigin — the canonical public origin everything flows
     through: "<scheme>://app.<domain>[:port]". This is the OIDC issuer,
     the dashboard base URL, and the redirect-URI prefix. */}}
{{- define "gibson.externalOrigin" -}}
{{- printf "%s://%s%s" (include "gibson.scheme" .) (include "gibson.appHost" .) (include "gibson.externalPortSuffix" .) -}}
{{- end -}}

{{/* gibson.apiOrigin — the public origin of the API plane:
     "<scheme>://api.<domain>[:port]". This is GIBSON_PUBLIC_URL — the base
     the daemon stamps into the capability-grant discovery document
     (/.well-known/agent-configuration), the register-endpoint audience, and
     the bootstrap-token issuer. It MUST carry the same external port suffix as
     gibson.externalOrigin; building it from the bare apiHost dropped the
     NodePort on kind, making CG enrollment unreachable (deploy#777). */}}
{{- define "gibson.apiOrigin" -}}
{{- printf "%s://%s%s" (include "gibson.scheme" .) (include "gibson.apiHost" .) (include "gibson.externalPortSuffix" .) -}}
{{- end -}}

{{/* gibson.wwwOrigin — the marketing host origin: "<scheme>://www.<domain>[:port]".
     Wired into the dashboard's WWW_URL; the www/app middleware host split
     (deploy#630 S11) serves marketing here and the product surface at
     gibson.externalOrigin (app.<domain>). */}}
{{- define "gibson.wwwOrigin" -}}
{{- printf "%s://%s%s" (include "gibson.scheme" .) (include "gibson.wwwHost" .) (include "gibson.externalPortSuffix" .) -}}
{{- end -}}

{{/* gibson.docsOrigin — the docs host origin: "<scheme>://docs.<domain>[:port]".
     Wired into the dashboard's DOCS_URL, which the middleware uses as the
     redirect target for /docs (dashboard#989).

     The port suffix matters. DOCS_URL used to be built as
     printf "https://%s" gibson.docsHost — hardcoded scheme, no port — so on
     any install whose edge is not on 443 (kind publishes :30443) the dashboard
     redirected /docs to an origin nothing listens on. Every other external
     origin helper here already appends externalPortSuffix; this one now does
     too, and picks up gibson.scheme rather than assuming https. */}}
{{- define "gibson.docsOrigin" -}}
{{- printf "%s://%s%s" (include "gibson.scheme" .) (include "gibson.docsHost" .) (include "gibson.externalPortSuffix" .) -}}
{{- end -}}

{{/* gibson.oidcIssuer — the OIDC issuer claim. Always the external origin
     (host root, NO path): app.<domain>/auth is served by the dashboard BFF
     proxying the protocol paths to in-cluster Zitadel, so the issuer stays
     a clean origin (deploy#630 / Zitadel Login-V2 pattern). */}}
{{- define "gibson.oidcIssuer" -}}{{ include "gibson.externalOrigin" . }}{{- end -}}

{{/* gibson.zitadelExternalDomain — Zitadel ExternalDomain (bare app host;
     ExternalPort/ExternalSecure are configured separately). */}}
{{- define "gibson.zitadelExternalDomain" -}}{{ include "gibson.appHost" . }}{{- end -}}

{{/* gibson.dashboardCallbackURI — the dashboard's OIDC redirect_uri,
     derived from the external origin. Registered on the gibson-dashboard
     OIDCClient and sent verbatim by Auth.js; the two MUST match. */}}
{{- define "gibson.dashboardCallbackURI" -}}{{ printf "%s/api/auth/callback/zitadel" (include "gibson.externalOrigin" .) }}{{- end -}}

{{/* gibson.tlsSans — the SAN list for the single edge certificate. Includes
     the bare apex: the apex vhost terminates TLS to serve the 301 -> www
     redirect, so it needs a SAN or browsers cert-error before the redirect
     (deploy#683). Order: apex first so it is also commonName-eligible. */}}
{{- define "gibson.tlsSans" -}}
- {{ include "gibson.appHost" . | quote }}
- {{ include "gibson.apiHost" . | quote }}
- {{ include "gibson.wwwHost" . | quote }}
- {{ include "gibson.docsHost" . | quote }}
- {{ include "gibson.domain" . | quote }}
{{- end -}}

{{/* gibson.externalDnsHosts — comma-separated host list for the
     external-dns hostname annotation (apex, www, app, api — the retired
     auth. host is dropped, deploy#642). Apex first (canonical). */}}
{{- define "gibson.externalDnsHosts" -}}
{{- /* www is deliberately absent. The marketing site is an
       off-cluster surface: no chart deploys it in either audience, so a
       cluster must never claim its hostname. Leaving it here meant
       external-dns would take www.<domain> back from the CDN on the next
       standup and black-hole the marketing site. */ -}}
{{- printf "%s,%s,%s,%s" (include "gibson.domain" .) (include "gibson.appHost" .) (include "gibson.apiHost" .) (include "gibson.docsHost" .) -}}
{{- end -}}

{{/* ============================================================
   Consolidated shared helpers (deploy#797): single home for the
   gibson.* templates formerly duplicated in gibson-operators and
   gibson-workloads. Helm named-templates are global across an
   umbrella; duplicates collided. Edit here only.
   ============================================================ */}}
{{- define "gibson.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}


{{- define "gibson.dashboardSecrets.name" -}}
{{- printf "%s-dashboard-secrets" (include "gibson.fullname" .) }}
{{- end }}


{{- define "gibson.dbSecrets.name" -}}
{{- printf "%s-db-secrets" (include "gibson.fullname" .) }}
{{- end }}

{{- define "gibson.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Canonical gRPC port for the Gibson daemon.
Single source of truth consumed by:
  - templates/gibson/configmap.yaml  (daemon.grpc_address)
  - templates/gibson/statefulset.yaml (grpc containerPort)
  - templates/gibson/service.yaml    (grpc port + targetPort backfill)
  - templates/dashboard/deployment.yaml (GIBSON_API_URL)

Rendered as a bare integer (no quotes, no colon prefix).
See .spec-workflow/specs/spiffe-helm-integration/.
*/}}
{{- define "gibson.grpc.port" -}}
{{- .Values.gibson.service.grpc.port | default 50051 -}}
{{- end }}


{{/*
  gibson.image — render a first-party image reference for a component.

  Call shape: include "gibson.image" (dict "registry" (($.Values.global).registry) "repo" ... "tag" ... "appVersion" ... "digest" ...)

  Digest-pin precedence (deploy#1066 second half): when the caller
  passes a non-empty "digest", it wins over "tag" entirely and the helper
  renders "<repo>@sha256:<digest>" — an immutable reference, the shape prod
  promotion (deploy#784) needs to snapshot staging component digests into a
  digest-pinned umbrella chart version. "digest" may be passed bare hex or
  already "sha256:"-prefixed; either form is accepted. This is additive to
  the pre-existing convention (deploy#789) of embedding a digest directly in
  the tag value ("<tag>@sha256:<digest>") — that form still renders via the
  tag branch below and both satisfy tools/digest-pin-check.

  With no digest, falls back to the original "<repo>:<tag>" behavior (tag
  defaults to .appVersion, then fails closed if still empty) — unchanged.
*/}}
{{- define "gibson.image" -}}
{{- $repo := required "gibson.image: .repo is required" .repo -}}
{{- /*
  Registry substitution. `.registry` moves EVERY first-party image to one host
  in a single value, which is what a customer mirroring the platform into their
  own registry needs. It swaps the host segment; it never rewrites the path, so
  a mirror that preserves the path needs nothing else.

  An image whose path also changes — a rebuild under a different naming scheme
  — is repointed by its own `repository` key instead, which still wins here
  because a repo carrying a host is only host-swapped, not re-prefixed.

  A repo with no host at all (`zeroroot-ai/mirror/x`) gets the registry
  prepended, which is the second shape a few values keys already use.
*/ -}}
{{- $registry := .registry | default "" -}}
{{- if $registry -}}
{{- $head := (splitList "/" $repo) | first -}}
{{- if or (contains "." $head) (contains ":" $head) -}}
{{- $repo = printf "%s/%s" $registry (join "/" (rest (splitList "/" $repo))) -}}
{{- else -}}
{{- $repo = printf "%s/%s" $registry $repo -}}
{{- end -}}
{{- end -}}
{{- $digest := .digest | default "" -}}
{{- if $digest -}}
{{- if not (hasPrefix "sha256:" $digest) -}}
{{- $digest = printf "sha256:%s" $digest -}}
{{- end -}}
{{- printf "%s@%s" $repo $digest -}}
{{- else -}}
{{- $tag := default (default "" .appVersion) .tag -}}
{{- if not $tag -}}
{{- fail (printf "gibson.image: image tag is empty for repo %q — set <component>.image.tag in your values overlay or pass appVersion. See first-deploy-unblock-and-ha R3.3." $repo) -}}
{{- end -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{- define "gibson.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ .name }}
{{- end }}
{{- end }}
{{- end }}

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
{{- define "gibson.irsaAnnotations" -}}
{{- toYaml . -}}
{{- end }}

{{- define "gibson.labels" -}}
helm.sh/chart: {{ include "gibson.chart" . }}
{{ include "gibson.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "gibson.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "gibson.platformPostgres.database" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- $ext.database | default "gibson_platform" -}}
{{- else -}}
{{- $cfg.database | default "gibson_platform" -}}
{{- end -}}
{{- end }}

{{/*
gibson.platformPostgres.host — hostname of the platform tier.

Phase 1+2 default: forwards to the existing dashboard-postgresql alias so
consumers can switch from `gibson.dashboard.dbHost` to
`gibson.platformPostgres.host` byte-identically.

Phase 3 changes the default to `<release>-platform-postgresql` (a new
consolidated StatefulSet that hosts gibson_platform + per-tenant DBs).
*/}}
{{- define "gibson.platformPostgres.host" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- required "platformPostgres.external.host is required when platformPostgres.external.enabled is true" $ext.host -}}
{{- else -}}
{{- /* one-code-path/198: the platform Postgres is structurally required.
       No silent fallback to a synthetic in-chart StatefulSet name — there
       is no in-chart StatefulSet template here. Every overlay MUST set
       platformPostgres.host (kind: kind-bootstrap CNPG; eks: external.host
       via external.enabled=true). Render fails LOUD when the value is
       empty. See one-code-path epic deploy#186. */ -}}
{{- required "platformPostgres.host is required — set it to your platform Postgres endpoint (kind: platform-postgres-rw.gibson.svc.cluster.local; eks: set platformPostgres.external.enabled=true + platformPostgres.external.host=<rds-endpoint>). See one-code-path epic deploy#186." $cfg.host -}}
{{- end -}}
{{- end }}

{{- define "gibson.platformPostgres.passwordSecretKey" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- $ext.passwordSecretKey | default "PLATFORM_DB_PASSWORD" -}}
{{- else -}}
{{- $cfg.passwordSecretKey | default "PLATFORM_DB_PASSWORD" -}}
{{- end -}}
{{- end }}

{{- define "gibson.platformPostgres.passwordSecretName" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- required "platformPostgres.external.passwordSecretName is required when platformPostgres.external.enabled is true" $ext.passwordSecretName -}}
{{- else -}}
{{- /* one-code-path/198: the platform Postgres password Secret is
       structurally required. No silent fallback to a synthetic
       <release>-platform-postgresql Secret name — that Secret is not
       reconciled by this chart. Every overlay MUST name a real Secret
       (gibson-platform-postgres-credentials that CNPG reconciles, or a
       Secret that an ExternalSecret writes from OpenBao, the store on every
       profile, ADR-0014). */ -}}
{{- required "platformPostgres.passwordSecretName is required — set it to the Secret name that holds the platform Postgres password (kind: gibson-platform-postgres-credentials; eks: an ESO-projected Secret). See one-code-path epic deploy#186." $cfg.passwordSecretName -}}
{{- end -}}
{{- end }}

{{- define "gibson.platformPostgres.port" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- $ext.port | default 5432 -}}
{{- else -}}
{{- $cfg.port | default 5432 -}}
{{- end -}}
{{- end }}

{{- define "gibson.platformPostgres.sslMode" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- $ext.sslMode | default "require" -}}
{{- else -}}
{{- $cfg.sslMode | default "disable" -}}
{{- end -}}
{{- end }}

{{- define "gibson.platformPostgres.username" -}}
{{- $cfg := .Values.platformPostgres | default dict -}}
{{- $ext := $cfg.external | default dict -}}
{{- if $ext.enabled -}}
{{- $ext.username | default "gibson_platform" -}}
{{- else -}}
{{- $cfg.username | default "gibson_platform" -}}
{{- end -}}
{{- end }}



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
{{- define "gibson.priorityClassName" -}}
{{- $component := .component -}}
{{- $critical := list "gibson" "extAuthz" "ext-authz" "envoy" "openfga" "spire-server" "spire" -}}
{{- $platform := list "dashboard" "tenantOperator" "tenant-operator" -}}
{{- if has $component $critical -}}
gibson-platform-critical
{{- else if has $component $platform -}}
gibson-platform
{{- else -}}
{{- /* Empty — caller can omit the key. */ -}}
{{- end -}}
{{- end }}

{{/*
gibson.redisPasswordSecretKey — Secret key for the Redis password.
  - When .Values.redis.auth.passwordSecretKey is set, return that.
  - Otherwise return "redis-password" (the chart-managed default).
*/}}
{{- define "gibson.redisPasswordSecretKey" -}}
{{- .Values.redis.auth.passwordSecretKey | default "redis-password" -}}
{{- end }}

{{/*
gibson.redisPasswordSecretName — name of the Secret that holds the Redis
auth password.
  - When .Values.redis.auth.passwordSecret is set, return that.
  - Otherwise return the chart-managed `<release>-redis-stack` Secret name.
*/}}
{{- define "gibson.redisPasswordSecretName" -}}
{{- if .Values.redis.auth.passwordSecret -}}
{{- .Values.redis.auth.passwordSecret -}}
{{- else if .Values.redis.auth.existingSecret -}}
{{/* Sibling-managed Redis (gitops Application) writes its credentials to
     a Secret named via Helm-side `auth.existingSecret`. The bitnami
     redis subchart uses this same field; mirror its semantics so the
     gibson chart's tenant-operator + jobs reach the right Secret. */}}
{{- .Values.redis.auth.existingSecret -}}
{{- else -}}
{{- printf "%s-redis-stack" .Release.Name -}}
{{- end -}}
{{- end }}

{{- define "gibson.selectorLabels" -}}
app.kubernetes.io/name: {{ include "gibson.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "gibson.serviceAccountName" -}}
{{- if .Values.gibson.serviceAccount.create }}
{{- default (include "gibson.fullname" .) .Values.gibson.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.gibson.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
gibson.openbaoAddr: the one address of the in-chart OpenBao, always https
(the listener serves only TLS). The OpenBao clients of every chart read it.
*/}}
{{- define "gibson.openbaoAddr" -}}
{{- printf "https://%s-openbao.%s.svc:8200" .Release.Name .Release.Namespace -}}
{{- end -}}

{{/*
gibson.openbaoTLSSecret: the Secret of the OpenBao listener certificate. Its
key ca.crt is the CA that every OpenBao client trusts.
*/}}
{{- define "gibson.openbaoTLSSecret" -}}
{{- printf "%s-openbao-tls" .Release.Name -}}
{{- end -}}

{{/*
gibson.openbaoCADir: where a client pod mounts the OpenBao CA.
*/}}
{{- define "gibson.openbaoCADir" -}}
/etc/gibson/openbao-ca
{{- end -}}

{{/*
gibson.openbaoCAVolume: a volume that projects only the key ca.crt of the
listener Secret, never its private key. Put it under `volumes:`.
*/}}
{{- define "gibson.openbaoCAVolume" -}}
- name: openbao-ca
  secret:
    secretName: {{ include "gibson.openbaoTLSSecret" . }}
    defaultMode: 0444
    items:
      - key: ca.crt
        path: ca.crt
{{- end -}}

{{/*
gibson.openbaoCAMount: the mount of gibson.openbaoCAVolume. Put it under
`volumeMounts:` of each container that dials OpenBao.
*/}}
{{- define "gibson.openbaoCAMount" -}}
- name: openbao-ca
  mountPath: {{ include "gibson.openbaoCADir" . }}
  readOnly: true
{{- end -}}

{{/*
gibson.openbaoCAEnv: the env that makes each client trust the OpenBao CA. Go
reads SSL_CERT_DIR and keeps the system roots of /etc/ssl/certs, and curl
reads CURL_CA_BUNDLE. Put it under `env:`.
*/}}
{{- define "gibson.openbaoCAEnv" -}}
- name: SSL_CERT_DIR
  value: {{ printf "/etc/ssl/certs:%s" (include "gibson.openbaoCADir" .) | quote }}
- name: CURL_CA_BUNDLE
  value: {{ printf "%s/ca.crt" (include "gibson.openbaoCADir" .) | quote }}
{{- end -}}

{{/*
=============================================================================
Platform ServiceAccount names — the workload-attestation identity set.
=============================================================================
These six names are security-relevant, not cosmetic. Each one appears in
THREE places that must never drift apart:

  1. the pod template's `serviceAccountName`;
  2. the literal `k8s:sa:` workload selector on the matching
     ClusterSPIFFEID (gibson-workloads/templates/spire-server/clusterspiffeids.yaml)
     — the SPIRE agent re-derives the pod's real SA from kubelet at
     attestation time and refuses to issue the SVID on a mismatch;
  3. the PROTECTED_SAS list in the platform identity
     ValidatingAdmissionPolicy (spire-server/platform-identity-admission.yaml).

Helpers exist so all three read from one expression. Changing a name in
values.yaml alone is safe; hardcoding one in a template is not.
gibson-workloads/tests/clusterspiffeid-selectors.bats cross-checks (1)
against (2) at render time.

The daemon's own SA is `gibson.serviceAccountName` above — release-derived
(`gibson.fullname`), NOT the literal "gibson-daemon".
*/}}

{{- define "gibson.envoyServiceAccountName" -}}
{{- (((.Values.envoy | default dict).serviceAccount) | default dict).name | default "gibson-envoy" -}}
{{- end }}

{{- define "gibson.extAuthzServiceAccountName" -}}
{{- (((.Values.extAuthz | default dict).serviceAccount) | default dict).name | default "gibson-ext-authz" -}}
{{- end }}

{{- define "gibson.ratelimitServiceAccountName" -}}
{{- (((.Values.ratelimit | default dict).serviceAccount) | default dict).name | default "gibson-ratelimit" -}}
{{- end }}

{{- define "gibson.dashboardServiceAccountName" -}}
{{- (((.Values.dashboard | default dict).serviceAccount) | default dict).name | default "gibson-dashboard" -}}
{{- end }}

{{- define "gibson.connectorOperatorServiceAccountName" -}}
{{- /* The gibson-operators sub-chart hardcodes this literal in the
       connector-operator Deployment and ClusterRoleBinding. The workloads
       chart's ClusterSPIFFEID and the identity admission policy read it
       here. Keep the two charts in agreement — an
       override here without the matching override there costs the operator
       its SVID and the grant-revoke finalizer its daemon dial. */ -}}
{{- (((.Values.connectorOperator | default dict).serviceAccount) | default dict).name | default "gibson-connector-operator" -}}
{{- end -}}

{{- define "gibson.tenantOperatorServiceAccountName" -}}
{{- /* The gibson-operators sub-chart hardcodes this literal in the
       tenant-operator Deployment, its ClusterRoleBinding, its webhook and
       the OPERATOR_SERVICE_ACCOUNT_NAME env var. Keep the two charts in
       agreement — an override here without the matching override there
       costs the operator its SVID. */ -}}
{{- (((.Values.tenantOperator | default dict).serviceAccount) | default dict).name | default "gibson-tenant-operator" -}}
{{- end }}

{{- define "gibson.platformOperatorServiceAccountName" -}}
{{- /* The gibson-operators sub-chart names the platform-operator
       ServiceAccount from platformOperator.serviceAccount.name with this
       default. The workloads chart's ClusterSPIFFEID and the identity
       admission policy read this name (gibson#583). Keep the two charts in
       agreement. */ -}}
{{- (((.Values.platformOperator | default dict).serviceAccount) | default dict).name | default "gibson-platform-operator" -}}
{{- end }}

{{- define "gibson.openbaoServiceAccountName" -}}
{{- /* CORRECTION: an earlier version of this comment said openbao was a
       sub-chart with "no value in this chart to read". It is in-chart —
       gibson-workloads/templates/auth/openbao-deployment.yaml declares both
       the ServiceAccount and the pod's serviceAccountName, and both
       hardcode `{{ .Release.Name }}-openbao`. Three lines above, this file
       asserts that hardcoding a name in a template is not safe; this helper
       is what makes the two sites agree, so it must render the same
       expression they do. It does. If a name override is ever added for
       openbao, add it here and read the helper from both sites rather than
       changing one of them. */ -}}
{{- printf "%s-openbao" .Release.Name -}}
{{- end }}

{{- define "gibson.helmTestServiceAccountName" -}}
{{- /* The SVID-fetching helm test's own identity. It exists so the test can
       prove SPIRE issues SVIDs WITHOUT holding a platform identity: it used
       to run with the daemon's component label and the daemon's
       ServiceAccount, which made it the daemon as far as the trust domain
       was concerned for as long as it ran, and made the installer a
       principal that had to be allow-listed by the platform-identity
       admission policy. Neither is true now.

       This SA and its ClusterSPIFFEID are deliberately NOT in
       PROTECTED_COMPONENTS / PROTECTED_SAS: `platform/helm-test` appears in
       no spiffe.callbackPeers and no spiffe.allowedPeers, so it authorises nothing.
       Do not add anything to it. */ -}}
{{- printf "%s-helm-test" (include "gibson.fullname" .) -}}
{{- end }}

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
{{- define "gibson.spread" -}}
{{- $ctx := .ctx -}}
{{- $component := .component -}}
{{- $vals := index $ctx.Values $component | default dict -}}
{{- $tsc := $vals.topologySpreadConstraints -}}
{{- $paa := $vals.podAntiAffinity -}}
{{- $aff := $vals.affinity -}}
{{- $ns := $vals.nodeSelector -}}
{{- $tol := $vals.tolerations -}}

{{- /* If no per-component override, emit chart-wide defaults: zone spread +
       soft pod anti-affinity on the component selector. */ -}}
{{- if and (not $tsc) (not $aff) -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: topology.kubernetes.io/zone
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "gibson.selectorLabels" $ctx | nindent 8 }}
        app.kubernetes.io/component: {{ $component }}
affinity:
  podAntiAffinity:
    {{- if eq ($paa | toString) "requiredDuringScheduling" }}
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchLabels:
            {{- include "gibson.selectorLabels" $ctx | nindent 12 }}
            app.kubernetes.io/component: {{ $component }}
        topologyKey: kubernetes.io/hostname
    {{- else }}
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              {{- include "gibson.selectorLabels" $ctx | nindent 14 }}
              app.kubernetes.io/component: {{ $component }}
          topologyKey: kubernetes.io/hostname
    {{- end }}
{{- else }}
{{- if $tsc }}
topologySpreadConstraints:
{{- toYaml $tsc | nindent 2 }}
{{- end }}
{{- if $aff }}
affinity:
{{- toYaml $aff | nindent 2 }}
{{- end }}
{{- end }}
{{- if $ns }}
nodeSelector:
{{- toYaml $ns | nindent 2 }}
{{- end }}
{{- if $tol }}
tolerations:
{{- toYaml $tol | nindent 2 }}
{{- end }}
{{- end }}

{{- define "gibson.tenant.dbHost" -}}
{{- with .Values.dataPlane.postgres.host }}{{ . }}{{ end }}
{{- end }}






















{{/*
  gibson.trueUnlessSet

  Renders "true" when the passed value is UNSET, and the value's own
  string form otherwise. The default-on boolean knob, done correctly.

  Bug class it eliminates (deploy#1220): sprig's `default` switches on
  `empty(given)`, and `empty(false)` is TRUE. So `X | default true`
  returns "true" for BOTH an unset X and an explicit `X: false` — the
  knob renders as a knob, documents itself as a knob, and can never be
  turned off. A knob of that shape once kept the secret store on
  plaintext while the operator believed TLS was on.

  `kindIs "invalid"` is the only predicate that separates nil (unset)
  from false — the same idiom the pdb.yaml templates already use for
  their auto-on-when-replicas>1 gate.

  Usage — value position:
    enabled: {{ include "gibson.trueUnlessSet" (((.Values.gibson.config).metrics).enabled) }}
  Usage — quoted env value:
    value: {{ include "gibson.trueUnlessSet" (...) | quote }}
  Usage — render gate:
    {{- if ne (include "gibson.trueUnlessSet" (...)) "false" }}

  NOTE for a STRUCTURAL resource that must always exist: do not reach for
  this helper — delete the knob instead (one-code-path /).

  Enforced by cross-chart-check's `truthy-default-swallows-false` class,
  which fails the build on any `default`/`coalesce`/`or` carrying a
  truthy boolean literal fallback.
*/}}
{{- define "gibson.trueUnlessSet" -}}
{{- if kindIs "invalid" . -}}true{{- else -}}{{ toString . }}{{- end -}}
{{- end -}}

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

ACTIVE (Task 19 landed). The body calls `fail` when gibson.auth.spiffe is
populated and the gibson_daemon_grpc cluster in files/envoy/envoy.yaml declares
no transport_socket. The check is a render-time match with no live cluster
dependency.
*/}}
{{- define "gibson.validateEnvoySdsWired" -}}
{{- /*
  Phase 6 / Task 19: ACTIVE. When gibson.auth.spiffe is populated
  (Envoy is unconditionally enabled — deploy#200), the gibson_daemon_grpc cluster in files/envoy/envoy.yaml
  MUST declare a transport_socket. Otherwise Envoy → daemon traffic falls
  back to plain h2c against a TLS listener and silently fails at runtime.

  We check the source file (files/envoy/envoy.yaml) rather than the rendered
  configmap because Helm's template engine doesn't let us re-render and
  inspect another template's output mid-render. The configmap renders the
  same file via tpl, so a string match on the source is equivalent for this
  guard's purpose.
*/ -}}
{{- $spiffe := (.Values.gibson.auth).spiffe -}}
{{- $daemonMtls := and $spiffe (kindIs "map" $spiffe) $spiffe.workloadAPISocket -}}
{{- if $daemonMtls -}}
{{- $envoyCfg := .Files.Get "files/envoy/envoy.yaml" -}}
{{- /*
  Marker-based check: rather than a multi-line regex (Helm's regex engine
  is line-oriented for `regexMatch`), we look for two distinct strings:
    - "name: gibson_daemon_grpc" (the cluster declaration)
    - "envoy.transport_sockets.tls" (the SDS-backed socket type)
  Both MUST be present in files/envoy/envoy.yaml for the daemon-cluster
  upstream TLS to be wired. The check is conservative: it doesn't enforce
  the marker is INSIDE the cluster block, only that both exist. A stronger
  per-block check requires either a YAML parser (not available in Helm) or
  splitting the file on cluster boundaries. The conservative check catches
  the regression we care about (someone deleting the transport_socket block
  entirely) without false positives.
*/ -}}
{{- $hasCluster := contains "name: gibson_daemon_grpc" ($envoyCfg | default "") -}}
{{- $hasTls := contains "envoy.transport_sockets.tls" ($envoyCfg | default "") -}}
{{- $hasSdsSecret := contains "tls_certificate_sds_secret_configs" ($envoyCfg | default "") -}}
{{- if not (and $hasCluster $hasTls $hasSdsSecret) -}}
{{- fail "gibson.auth.spiffe populated requires the gibson_daemon_grpc cluster in files/envoy/envoy.yaml to declare a transport_socket: envoy.transport_sockets.tls block backed by SPIRE SDS (tls_certificate_sds_secret_configs). See spec in-cluster-mtls-restoration Component 8/9. Without this, Envoy → daemon dials fail at runtime even though helm template renders cleanly. This is the failure mode that produced commit 1d11963." -}}
{{- end -}}
{{- end -}}
{{- end }}

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
{{- define "gibson.waitForFgaConfig" -}}
- name: wait-for-fga-config
  image: ghcr.io/zeroroot-ai/mirror/kubectl:1.31.4@sha256:64614ef8290f3fb27fed5164b338debeeb79a1e5e26c93eb920770b71abd7c48
  command: ['/bin/bash', '-c']
  args:
    - |
      set -eu
      ns={{ .Release.Namespace | quote }}
      echo "[wait-for-fga-config] waiting for ConfigMap ${ns}/gibson-fga-config (store_id key, timeout: 300s)..."
      for i in $(seq 1 150); do
        val=$(kubectl get configmap -n "${ns}" gibson-fga-config \
              -o jsonpath='{.data.store_id}' 2>/dev/null || true)
        if [ -n "${val}" ]; then
          echo "[wait-for-fga-config] ok (store_id=${val})"
          exit 0
        fi
        echo "[wait-for-fga-config] not ready (attempt ${i}/150), retrying in 2s..."
        sleep 2
      done
      echo "[wait-for-fga-config] ERROR: gibson-fga-config not populated after 300s." \
           "Check: kubectl logs -n ${ns} -l app.kubernetes.io/component=fga-init" >&2
      exit 1
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 65532
    capabilities:
      drop: ["ALL"]
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 100m
      memory: 64Mi
{{- end }}

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
{{- define "gibson.zeroTrustHardeningVersion" -}}
1
{{- end }}

{{/* --- reconciled drifting helpers (deploy#797) --- */}}
{{- define "gibson.redis.host" -}}
{{- /* deploy#797: host of the in-cluster redis-stack Service. redis.addr
       (host[:port]) wins when set (a separate release/remote points at the
       workloads release Service); else computed from this release. */ -}}
{{- (splitList ":" (.Values.redis.addr | default "")) | first | default (printf "%s-redis-stack" .Release.Name) -}}
{{- end }}

{{- define "gibson.redis.addr" -}}
{{- /* deploy#797: full host:port. Soft default replaces the former hard
       `required .Values.redis.addr` so the umbrella (one shared release)
       computes it while standalone operators still pin it explicitly. */ -}}
{{- $port := int (.Values.redis.service.port | default 6379) -}}
{{- .Values.redis.addr | default (printf "%s-redis-stack:%d" .Release.Name $port) -}}
{{- end }}

{{/*
Redis URL — Spec 3 R11: never embeds the literal password; consumers
inject ${REDIS_PASSWORD} at runtime via the gibson.redisPasswordSecretName
helper. Redis auth is always on (one-code-path epic / deploy#199).
*/}}
{{- define "gibson.redis.url" -}}
{{- $port := int (.Values.redis.service.port | default 6379) -}}
{{- printf "redis://:${REDIS_PASSWORD}@%s:%d" (include "gibson.redis.host" .) $port -}}
{{- end }}

{{/*
gibson.validateSpiffeRequired — fails the render when gibson.auth.spiffe is
null/empty in any overlay. Memorialises the "SPIFFE stays ON" invariant from
memory feedback_spiffe_mtls_required.md as a structural chart guard.

Spec: in-cluster-mtls-restoration, Component 2 / Requirement 1.

ACTIVE (Task 18 landed). The body calls `fail` when gibson.auth.spiffe is
absent or missing workloadAPISocket, or when global.spire.trustDomain is
empty, and the daemon statefulset invokes it. It has no escape hatch:
`dev.disableSPIFFE` was deleted (charts#399).
*/}}
{{- define "gibson.validateSpiffeRequired" -}}
{{- /*
  Phase 6 / Task 18: ACTIVE. Fails the render if gibson.auth.spiffe is null
  or missing the required keys. Memorialises memory feedback_spiffe_mtls_required.md
  as a structural chart guard.

  Note: the actual values use camelCase (workloadAPISocket) per
  values.yaml gibson.auth.spiffe block; the daemon configmap renders the
  snake_case form for the daemon binary's config.
*/ -}}
{{- $spiffe := (.Values.gibson.auth).spiffe -}}
{{- if not (and $spiffe (kindIs "map" $spiffe) $spiffe.workloadAPISocket (include "gibson.trustDomain" .)) -}}
{{- fail "gibson.auth.spiffe must be populated in every overlay (gibson.auth.spiffe.workloadAPISocket, and global.spire.trustDomain). See spec in-cluster-mtls-restoration and memory feedback_spiffe_mtls_required.md. Disabling daemon SPIFFE mTLS is not permitted as a debugging shortcut." -}}
{{- end -}}
{{- end }}

{{/*
gibson.waitForSpireSocketImage: the image of the wait-for-spire-socket init
container. Two places run it: the helper below, in each chart pod, and the
tenant-operator, which gives it to each plugin pod it deploys in a
tenant-<t>-plugins namespace (PLUGIN_WAIT_FOR_SPIRE_IMAGE, gibson#815). One
pin keeps the two the same.
*/}}
{{- define "gibson.waitForSpireSocketImage" -}}
ghcr.io/zeroroot-ai/mirror/busybox:1.36@sha256:73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662
{{- end -}}

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
{{- define "gibson.waitForSpireSocket" -}}
- name: wait-for-spire-socket
  image: {{ include "gibson.waitForSpireSocketImage" . }}
  command: ['sh', '-c']
  args:
    - |
      # deploy#201: SPIRE is required infrastructure. Fail-fast (60s) if the
      # Workload API socket is not present on this node — a missing socket
      # means the SPIRE agent DaemonSet is unhealthy and SPIFFE-consuming
      # workloads MUST NOT proceed (their mTLS / JWT-SVID minting would
      # silently hang otherwise).
      SOCK="/run/spire/agent/spire-agent.sock"
      DEADLINE=$(( $(date +%s) + 60 ))
      echo "[wait-for-spire-socket] waiting for ${SOCK} (timeout: 60s)..."
      until [ -S "${SOCK}" ]; do
        if [ $(date +%s) -ge ${DEADLINE} ]; then
          echo "[wait-for-spire-socket] ERROR: ${SOCK} not present after 60s; SPIRE agent DaemonSet is not healthy on this node. Fix the SPIRE control plane before this pod can start." >&2
          exit 1
        fi
        sleep 1
      done
      echo "[wait-for-spire-socket] ok"
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 65532
    capabilities:
      drop: ["ALL"]
  resources:
    requests:
      cpu: 10m
      memory: 16Mi
    limits:
      cpu: 100m
      memory: 32Mi
  volumeMounts:
    - name: spire-agent-socket
      mountPath: /run/spire/agent
      readOnly: true
{{- end }}

{{/* gibson.assertSecretKnobsDeleted — the tombstone for the two-backend world
     (deploy#1733).

     OpenBao is the one External Secrets backend on every substrate, reached
     through one ClusterSecretStore. The selector `global.secretBackend` and
     the AWS path prefix `global.secretPrefix` are DELETED, together with the
     `gibson.secretBackend` validator and the `gibson.secretKeyPrefix` helper.
     A values file that still carries either key selects nothing. Left silent,
     it would read as configuration that works, so the render fails and names
     the key.

     `externalSecrets.clusterSecretStore.create` is deleted with them: it chose
     between this chart and the kind Terraform workspace, and that workspace
     stopped creating a store in deploy#1732. The chart renders the store on
     every profile.

     Included by templates/external-secrets/cluster-secret-store.yaml, which
     every profile renders. */}}
{{- define "gibson.assertSecretKnobsDeleted" -}}
{{- $g := (hasKey .Values "global") | ternary .Values.global dict -}}
{{- range $k := list "secretBackend" "secretPrefix" -}}
{{- if and $g (hasKey $g $k) -}}
{{- fail (printf "global.%s was deleted (deploy#1733). OpenBao is the one External Secrets backend on every substrate, and every remoteRef key is flat under the kv v2 mount `secret`. There is no backend to select and no prefix to set. Remove global.%s from the values file." $k $k) -}}
{{- end -}}
{{- end -}}
{{- $css := ((.Values.externalSecrets).clusterSecretStore) | default dict -}}
{{- if hasKey $css "create" -}}
{{- fail "externalSecrets.clusterSecretStore.create was deleted (deploy#1733). The chart renders the one ClusterSecretStore on every profile; no Terraform workspace creates it any more. Remove the key from the values file." -}}
{{- end -}}
{{- if hasKey $css "kubernetes" -}}
{{- fail "externalSecrets.clusterSecretStore.kubernetes was deleted (deploy#1733). The in-cluster Kubernetes-provider backend is gone; the store reads OpenBao. Remove the key from the values file." -}}
{{- end -}}
{{- end -}}

{{/*
gibson.toolImage — the ONE alpine-k8s tool image (kubectl + sh) that every
first-party Job, init container and gate runs. It used to be a literal in
thirteen templates, ten of them two minors behind the values-level pins
(zeroroot-ai/.github#20, the alpine-k8s version link). The source of truth
is the umbrella's `global.toolImage` (helm/gibson/values.yaml); the copy in
helm/gibson-workloads/values.yaml is the standalone-render default and a
declared consumer of that link, so the drift detector and the fan-out keep
the two equal. `make check` (tool-image) fails any template that carries the
literal again.
*/}}
{{- define "gibson.toolImage" -}}
{{- $t := required "global.toolImage is required: the alpine-k8s tool image every first-party Job runs" ((.Values.global).toolImage) -}}
{{- printf "%s:%s" (required "global.toolImage.repository is required" $t.repository) (required "global.toolImage.tag is required" ($t.tag | toString)) -}}
{{- end -}}

{{/*
gibson.trustDomain: the SPIFFE trust domain of this install (ADR-0164).

One value names it: `global.spire.trustDomain`. The vendored SPIRE chart reads
the same key, so the server, the agents and every ID the chart renders share
one domain. Each install sets its own. The SaaS uses `zeroroot.ai`. The render
fails when the value is empty, or when it is not a valid trust domain name.
*/}}
{{- define "gibson.trustDomain" -}}
{{- $td := (((.Values.global).spire).trustDomain) | default "" | toString -}}
{{- if not $td -}}
{{- fail "global.spire.trustDomain is required: the SPIFFE trust domain of this install (ADR-0164). Each install sets its own." -}}
{{- end -}}
{{- if not (regexMatch "^[a-z0-9]([a-z0-9._-]*[a-z0-9])?$" $td) -}}
{{- fail (printf "global.spire.trustDomain %q is not a SPIFFE trust domain name: use lower-case letters, digits, dots, dashes and underscores" $td) -}}
{{- end -}}
{{- $td -}}
{{- end -}}

{{/*
gibson.spiffeID: the SPIFFE ID of a path in this install.
  {{ include "gibson.spiffeID" (dict "ctx" $ "path" "platform/daemon") }}
*/}}
{{- define "gibson.spiffeID" -}}
spiffe://{{ include "gibson.trustDomain" .ctx }}/{{ .path | trimPrefix "/" }}
{{- end -}}

{{/*
gibson.spiffeIDs: the SPIFFE IDs of a list of paths, joined with commas.
  {{ include "gibson.spiffeIDs" (dict "ctx" $ "paths" .Values.spiffe.callbackPeers) }}
*/}}
{{- define "gibson.spiffeIDs" -}}
{{- $out := list -}}
{{- range $p := .paths -}}
{{- $out = append $out (include "gibson.spiffeID" (dict "ctx" $.ctx "path" $p)) -}}
{{- end -}}
{{- join "," $out -}}
{{- end -}}

{{/*
gibson.registrationKnob: the SIGNUP_SELF_SERVE value of the registration rung
(ADR-0074, charts#374). `registration` names the rung:

  closed    no value: the daemon refuses every signup, and the dashboard
            redirects /signup to /login. A platform admin provisions tenants.
  approval  "approval": anyone may register, and an administrator approves
            each account. No mail transport is needed.
  open      "true": self-serve signup.

The daemon and the dashboard read the same variable, so both take it from
here. The render fails on any other name.
*/}}
{{- define "gibson.registrationKnob" -}}
{{- $rung := .Values.registration | default "" | toString -}}
{{- if eq $rung "open" -}}true
{{- else if eq $rung "approval" -}}approval
{{- else if ne $rung "closed" -}}
{{- fail (printf "registration is %q: use closed, approval or open (ADR-0074)" $rung) -}}
{{- end -}}
{{- end -}}

{{/*
gibson.email: the one mail value of the install, global.email (hosted#223,
ADR-0027), checked and returned as JSON. The daemon, the tenant-operator and
the PlatformBootstrap (Zitadel) read it; nothing else names a mail transport.

  provider  log   the daemon logs each mail. The tenant-operator sends its
                  welcome mail to the in-cluster mailpit (no TLS, no
                  credential). Zitadel gets no SMTP provider.
            smtp  all three send through smtp.host.
  from, fromName  the sender. fromName is required with smtp (Zitadel).
  smtp.host       a host name, or a template that renders one, for example
                  '{{ include "gibson.mailpit.host" . }}' for the in-chart sink.
  smtp.tlsMode    starttls (587), implicit (465) or plaintext.
  smtp.credentials.source
            secretStore  the ExternalSecret <release>-email-smtp reads
                         remoteKey (properties username and password)
            secret       secretName, keys username and password
            none         a relay that takes no credential

The returned credentialsSecret names the Secret that holds username and
password, or is empty.
*/}}
{{- define "gibson.email" -}}
{{- $e := required "global.email is required: the one mail value of the install (hosted#223)." ((.Values.global).email) -}}
{{- $provider := required "global.email.provider is required: log or smtp." $e.provider -}}
{{- if not (has $provider (list "log" "smtp")) -}}
{{- fail (printf "global.email.provider is %q: use log or smtp." $provider) -}}
{{- end -}}
{{- $from := required "global.email.from is required: the sender address of each mail." $e.from -}}
{{- $out := dict "provider" $provider "from" $from "fromName" ($e.fromName | default "") "host" "" "port" "" "tlsMode" "" "configurationSet" "" "credentialsSource" "" "credentialsSecret" "" "remoteKey" "" -}}
{{- if eq $provider "smtp" -}}
{{- $s := required "global.email.smtp is required when global.email.provider is smtp." $e.smtp -}}
{{- $_ := set $out "fromName" (required "global.email.fromName is required when global.email.provider is smtp: Zitadel names the sender." $e.fromName) -}}
{{- $_ := set $out "host" (tpl (required "global.email.smtp.host is required when global.email.provider is smtp." $s.host) .) -}}
{{- $_ := set $out "port" (toString (required "global.email.smtp.port is required when global.email.provider is smtp." $s.port)) -}}
{{- $mode := required "global.email.smtp.tlsMode is required: starttls, implicit or plaintext." $s.tlsMode -}}
{{- if not (has $mode (list "starttls" "implicit" "plaintext")) -}}
{{- fail (printf "global.email.smtp.tlsMode is %q: use starttls (port 587, dial plaintext then upgrade), implicit (port 465, TLS from the first byte) or plaintext (no encryption, for a sink that offers no STARTTLS)." $mode) -}}
{{- end -}}
{{- $_ := set $out "tlsMode" $mode -}}
{{- $_ := set $out "configurationSet" ($s.configurationSet | default "") -}}
{{- $c := required "global.email.smtp.credentials is required when global.email.provider is smtp." $s.credentials -}}
{{- $src := required "global.email.smtp.credentials.source is required: secretStore, secret or none." $c.source -}}
{{- $_ := set $out "credentialsSource" $src -}}
{{- if eq $src "secretStore" -}}
{{- $_ := set $out "remoteKey" (required "global.email.smtp.credentials.remoteKey is required when the source is secretStore." $c.remoteKey) -}}
{{- $_ := set $out "credentialsSecret" (printf "%s-email-smtp" .Release.Name) -}}
{{- else if eq $src "secret" -}}
{{- $_ := set $out "credentialsSecret" (required "global.email.smtp.credentials.secretName is required when the source is secret." $c.secretName) -}}
{{- else if ne $src "none" -}}
{{- fail (printf "global.email.smtp.credentials.source is %q: use secretStore, secret or none." $src) -}}
{{- end -}}
{{- if and (eq $mode "plaintext") (ne $src "none") -}}
{{- fail "global.email.smtp.tlsMode is plaintext and the relay takes a credential: the credential would cross the network in the clear, and net/smtp refuses PlainAuth over a cleartext link. Use starttls or implicit, or credentials.source none for a sink." -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
gibson.assertKeysDeleted: the render fails when a deleted values key is set,
even to false or an empty string. A silent ignore would keep an old setting in an overlay that nobody
reads. `use` names what replaces the keys. charts#392 (the trust domain keys)
and charts#374 (the signup keys) both call it.
  {{ include "gibson.assertKeysDeleted" (dict "ctx" $ "keys" (list "a.b") "use" "registration") }}
*/}}
{{- define "gibson.assertKeysDeleted" -}}
{{- range $k := .keys -}}
{{- $node := $.ctx.Values -}}
{{- $found := true -}}
{{- range $part := splitList "." $k -}}
{{- if and $found (kindIs "map" $node) (hasKey $node $part) -}}
{{- $node = index $node $part -}}
{{- else -}}
{{- $found = false -}}
{{- end -}}
{{- end -}}
{{- if $found -}}
{{- fail (printf "%s was deleted. Use %s." $k $.use) -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
gibson.netLabels: the network labels of one pod (ADR-0165 rule 4, D76).

The chart renders one set of Cilium policies that select pods by these labels.
A new pod gets labels, not a new policy. Input is a dict:

  role          platform | datastore | system. Required.
                platform:  the pod talks to each other platform pod.
                datastore: the pod is a data store. Only its clients reach it.
                system:    a cluster operator. It is in no shared group.
  datastore     postgres | redis | openbao | neo4j. Required with role datastore.
  clients       the data stores that the pod reaches, from the same four names.
  kubeApi       true when the pod calls the Kubernetes API.
  internet      true when the pod reaches any host outside the cluster (the
                Cilium world entity). Use it only when the hosts are not
                known at render time, for example an API address that a
                tenant sets.
  fqdn          the name of an egress host group. The pod reaches only the
                host names of that group (toFQDNs). The groups and their
                hosts are in gibson.egressFqdnGroups (helm/gibson).
  namespaces    true when the pod reaches pods in other namespaces.
  edge          true for the public edge. Each source reaches its listener.
  controlPlane  true for a server that the API server or a node dials
                (an admission webhook, the SPIRE server agent port).
  metricsPort   the port that serves only metrics, one of gibson.metricsPorts.
                The cluster scraper reaches the pod on this port and on no
                other. Never an API port. A pod with no metrics port gets
                no scraper traffic.

  {{- include "gibson.netLabels" (dict "role" "platform" "clients" (list "redis") "kubeApi" true) | nindent 8 }}

scripts/check-secure-pod.py fails on a pod with no role, and on a pod that
reaches a data store or the internet with no label for it. A pod with only
the egress-fqdn label must not reach the world entity.
*/}}
{{- define "gibson.netLabels" -}}
{{- $roles := list "platform" "datastore" "system" -}}
{{- $stores := list "postgres" "redis" "openbao" "neo4j" -}}
{{- if not (has .role $roles) -}}
{{- fail (printf "gibson.netLabels: role %v is not one of %s" .role (join ", " $roles)) -}}
{{- end -}}
gibson.zeroroot.ai/net-role: {{ .role }}
{{- if eq .role "datastore" }}
{{- if not (has .datastore $stores) }}
{{- fail (printf "gibson.netLabels: datastore %v is not one of %s" .datastore (join ", " $stores)) }}
{{- end }}
gibson.zeroroot.ai/datastore: {{ .datastore }}
{{- end }}
{{- range $s := .clients | default list }}
{{- if not (has $s $stores) }}
{{- fail (printf "gibson.netLabels: client of %v: not one of %s" $s (join ", " $stores)) }}
{{- end }}
gibson.zeroroot.ai/client-{{ $s }}: "true"
{{- end }}
{{- if .kubeApi }}
gibson.zeroroot.ai/kube-api: "true"
{{- end }}
{{- if .internet }}
gibson.zeroroot.ai/egress-internet: "true"
{{- end }}
{{- with .fqdn }}
{{- $groups := list "zitadel" "tenant-operator" "cert-manager" "external-dns" "object-store" }}
{{- if not (has . $groups) }}
{{- fail (printf "gibson.netLabels: egress host group %v is not one of %s" . (join ", " $groups)) }}
{{- end }}
gibson.zeroroot.ai/egress-fqdn: {{ . }}
{{- end }}
{{- if .namespaces }}
gibson.zeroroot.ai/egress-namespaces: "true"
{{- end }}
{{- if .edge }}
gibson.zeroroot.ai/ingress-edge: "true"
{{- end }}
{{- if .controlPlane }}
gibson.zeroroot.ai/ingress-control-plane: "true"
{{- end }}
{{- with .metricsPort }}
{{- $ports := include "gibson.metricsPorts" $ | splitList " " }}
{{- if not (has (toString .) $ports) }}
{{- fail (printf "gibson.netLabels: metrics port %v is not one of %s" . (join ", " $ports)) }}
{{- end }}
gibson.zeroroot.ai/metrics-port: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
gibson.metricsPorts: the ports that serve only metrics (D76, the metrics
policy of helm/gibson/templates/network-policies.yaml).

The metrics policy renders one rule for each port. The rule selects the pods
with the label gibson.zeroroot.ai/metrics-port set to that port, and admits
the cluster scraper to that port only. A port here must never be an API
port of a pod that carries its label. scripts/check-metrics-policy.py checks
this on each render.

  8080  the operators of this chart, External Secrets and CloudNativePG
  9090  the daemon, the rate limiter and Reloader
  9187  the Postgres instances
  9402  cert-manager
  9901  the probe listener of the edge (/ready and /stats/prometheus only)
*/}}
{{- define "gibson.metricsPorts" -}}
8080 9090 9187 9402 9901
{{- end -}}
