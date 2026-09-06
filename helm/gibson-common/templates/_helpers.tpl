{{/*
  gibson-common library helpers.

  Two helpers today; both eliminate a cross-chart drift class observed in
  production cluster bringup:

  - gibson.daemonAddress: the canonical daemon gRPC address. Every dialing
    pod (tenant-operator, dashboard, plugins) MUST use this. Drift between
    "gibson:50051" and "gibson-workloads:50051" caused tenant-operator#70.

  - gibson.hostAliases: the canonical hostAliases block for every
    in-cluster pod that needs to dial Envoy by its public hostnames
    (app.<domain>, api.<domain>, www.<domain>, docs.<domain>). Drift
    between values-kind.yaml clusterIP pins across charts caused
    deploy#169 / #171.

  Convention for adding a new helper:
    1. Add it here as `{{- define "gibson.helperName" -}}…{{- end -}}`.
    2. Document the values keys it reads at the top of the define block.
    3. Document the bug class it eliminates — helpers exist to lock
       invariants, not to add abstraction for its own sake.
    4. Update consuming charts in the same PR (CI's cross-chart-check
       — deploy#180 — will refuse a chart that bypasses the helper).

  Spec: zeroroot-ai/tenant-operator#76 PRD Module 6 / deploy#179.
*/}}

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
    2. .Values.tenantOperator.daemonGrpcAddress when set (legacy key in
       the operators chart; kept for backward compat during the migration).
    3. "<release-fullname-of-workloads>:50051" — the workloads chart's
       conventional Service name + the daemon's listen port.

  Callers in templates:
      env:
        - name: GIBSON_DAEMON_ADDRESS
          value: {{ include "gibson.daemonAddress" . | quote }}

  Values keys read:
    - .Values.gibson.daemonAddress       (preferred override)
    - .Values.tenantOperator.daemonGrpcAddress (legacy override)
    - .Release.Name                      (used to construct the default)
*/}}
{{- define "gibson.daemonAddress" -}}
{{- $override := "" -}}
{{- if and (hasKey .Values "gibson") .Values.gibson -}}
  {{- $override = .Values.gibson.daemonAddress | default "" -}}
{{- end -}}
{{- if and (eq $override "") (hasKey .Values "tenantOperator") .Values.tenantOperator -}}
  {{- $override = .Values.tenantOperator.daemonGrpcAddress | default "" -}}
{{- end -}}
{{- if ne $override "" -}}
{{- $override -}}
{{- else -}}
{{- printf "%s:50051" (default "gibson" .Release.Name) -}}
{{- end -}}
{{- end -}}

{{/*
  gibson.hostAliases

  Emits the hostAliases block (yaml subtree) that pins the public Envoy
  hostnames (app.<domain>, api.<domain>, www.<domain>, docs.<domain>) to
  the Envoy Service's ClusterIP. Required for any pod that dials Envoy by its
  public hostname (dashboard signin callback, daemon OIDC discovery,
  tenant-operator Zitadel admin client, plugins).

  Emits nothing (just the leading whitespace/comment marker) when:
    - .Values.envoy.service.clusterIP is unset (production / EKS — no
      hostAlias pin needed because public DNS resolves to the public
      ALB; only dev/kind needs the pin).

  Envoy is required infrastructure (deploy#200); there is no `.enabled`
  toggle. The only knob is whether to pin a hostAlias (kind) or rely on
  DNS (prod), which the clusterIP value controls.

  Usage in a Deployment / StatefulSet template:
      spec:
        template:
          spec:
            {{- include "gibson.hostAliases" . | nindent 6 }}
            securityContext: …

  The helper emits the WHOLE `hostAliases:` key when present, so the
  caller is responsible only for nindent and for choosing the surrounding
  insertion point. When disabled, the helper emits no output at all so
  the surrounding template stays valid.

  Values keys read:
    - .Values.envoy.service.clusterIP
    - .Values.global.domain (REQUIRED — see gibson.domain; no fallback)

  Note: `auth.<domain>` is NOT emitted (the platform domain normalization
  epic, deploy#630, retired the `auth.` subdomain in favor of a single
  `app.<domain>` host; slice S12 / deploy#642 removed it from here — this
  note used to describe that removal as pending, it has since landed).
*/}}
{{- define "gibson.hostAliases" -}}
{{- if and (hasKey .Values "envoy") .Values.envoy -}}
{{- if and (hasKey .Values.envoy "service") .Values.envoy.service.clusterIP -}}
hostAliases:
  - ip: {{ .Values.envoy.service.clusterIP | quote }}
    hostnames:
      - {{ include "gibson.appHost" . | quote }}
      - {{ include "gibson.apiHost" . | quote }}
      - {{ include "gibson.wwwHost" . | quote }}
      - {{ include "gibson.docsHost" . | quote }}
{{- end -}}
{{- end -}}
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

{{/* gibson.zitadelHostPort — app host WITH the external port suffix
     ("app.<domain>[:port]"). This is the Host header the operators forge onto
     in-cluster Zitadel requests (they dial Service DNS but Zitadel routes by
     Host), and must match Zitadel's registered ExternalDomain+ExternalPort. */}}
{{- define "gibson.zitadelHostPort" -}}{{ printf "%s%s" (include "gibson.appHost" .) (include "gibson.externalPortSuffix" .) }}{{- end -}}

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

{{- define "gibson.dashboard.daemonURL" -}}
{{- $spiffe := (.Values.gibson.auth).spiffe -}}
{{- $daemonMtls := and $spiffe (kindIs "map" $spiffe) $spiffe.trustDomain $spiffe.workloadAPISocket -}}
{{- $scheme := "http" -}}
{{- if $daemonMtls -}}
{{- $scheme = "https" -}}
{{- end -}}
{{- $host := include "gibson.fullname" . -}}
{{- $ns := .Release.Namespace -}}
{{- $port := include "gibson.grpc.port" . -}}
{{- if $daemonMtls -}}
{{- printf "%s://%s.%s.svc.cluster.local:%s" $scheme $host $ns $port -}}
{{- else -}}
{{- printf "%s://%s:%s" $scheme $host $port -}}
{{- end -}}
{{- end }}

{{- define "gibson.dashboard.dbHost" -}}
{{- if .Values.dashboard.postgres.host }}
{{- .Values.dashboard.postgres.host }}
{{- else }}
{{- include "gibson.dashboard.postgresql.host" . }}
{{- end }}
{{- end }}


{{- define "gibson.dashboard.postgresql.host" -}}
{{- printf "%s-dashboard-postgresql" .Release.Name }}
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

{{- define "gibson.grafana.host" -}}
{{- printf "%s-grafana" .Release.Name }}
{{- end }}

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

{{- define "gibson.irsaAnnotations" -}}
{{- toYaml . -}}
{{- end }}

{{- define "gibson.jaeger.host" -}}
{{- printf "%s-jaeger" .Release.Name }}
{{- end }}

{{- define "gibson.labels" -}}
helm.sh/chart: {{ include "gibson.chart" . }}
{{ include "gibson.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "gibson.llmSecrets.name" -}}
{{- printf "%s-llm-secrets" (include "gibson.fullname" .) }}
{{- end }}

{{- define "gibson.loki.host" -}}
{{- printf "%s-loki" .Release.Name }}
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
       (kind: gibson-platform-postgres-credentials reconciled by
       kind-bootstrap CNPG; eks: ESO-projected Secret pointing at the
       RDS credential Secret in AWS Secrets Manager). */ -}}
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

{{- define "gibson.prometheus.host" -}}
{{- printf "%s-prometheus" .Release.Name }}
{{- end }}

{{- define "gibson.redisPasswordSecretKey" -}}
{{- .Values.redis.auth.passwordSecretKey | default "redis-password" -}}
{{- end }}

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
       no callbackPeerSVIDs and no allowedPeerIDs, so it authorises nothing.
       Do not add anything to it. */ -}}
{{- printf "%s-helm-test" (include "gibson.fullname" .) -}}
{{- end }}

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
{{- if .Values.dataPlane.postgres.host }}
{{- .Values.dataPlane.postgres.host }}
{{- else if (index .Values "tenant-postgresql" "enabled") }}
{{- include "gibson.tenant.postgresql.host" . }}
{{- end }}
{{- end }}

{{- define "gibson.tenant.postgresql.host" -}}
{{- printf "%s-tenant-postgresql" .Release.Name }}
{{- end }}






















{{/*
  gibson.trueUnlessSet

  Renders "true" when the passed value is UNSET, and the value's own
  string form otherwise. The default-on boolean knob, done correctly.

  Bug class it eliminates (deploy#1220): sprig's `default` switches on
  `empty(given)`, and `empty(false)` is TRUE. So `X | default true`
  returns "true" for BOTH an unset X and an explicit `X: false` — the
  knob renders as a knob, documents itself as a knob, and can never be
  turned off. `openbao.config.tlsDisable | default true` meant an overlay
  setting `tlsDisable: false` still rendered `tls_disable = true`, so the
  secret store served plaintext while the operator believed TLS was on.

  `kindIs "invalid"` is the only predicate that separates nil (unset)
  from false — the same idiom the pdb.yaml templates already use for
  their auto-on-when-replicas>1 gate.

  Usage — value position:
    tls_disable = {{ include "gibson.trueUnlessSet" ((.Values.openbao.config).tlsDisable) }}
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

{{- define "gibson.validateEnvoyGateway" -}}
{{- /* intentional no-op: requireEnvoy↔ext-authz coupling is now structural */ -}}
{{- end }}

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
{{- $daemonMtls := and $spiffe (kindIs "map" $spiffe) $spiffe.workloadAPISocket $spiffe.trustDomain -}}
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

{{- define "gibson.waitForFgaConfig" -}}
- name: wait-for-fga-config
  image: ghcr.io/zeroroot-ai/mirror/kubectl:1.31.4
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

{{- define "gibson.redis.url" -}}
{{- $port := int (.Values.redis.service.port | default 6379) -}}
{{- printf "redis://:${REDIS_PASSWORD}@%s:%d" (include "gibson.redis.host" .) $port -}}
{{- end }}

{{- define "gibson.validateSpire" -}}
{{- /* intentionally empty */ -}}
{{- end }}

{{- define "gibson.validateSpiffeRequired" -}}
{{- /*
  Phase 6 / Task 18: ACTIVE. Fails the render if gibson.auth.spiffe is null
  or missing the required keys. Memorialises memory feedback_spiffe_mtls_required.md
  as a structural chart guard.

  Note: the actual values use camelCase (workloadAPISocket / trustDomain) per
  values.yaml gibson.auth.spiffe block; the daemon configmap renders the
  snake_case form for the daemon binary's config.
*/ -}}
{{- $spiffe := (.Values.gibson.auth).spiffe -}}
{{- /* dev.disableSPIFFE escape hatch — kind-only; rejected in prod overlays via CI lint */ -}}
{{- if (.Values.dev).disableSPIFFE -}}
{{- else if not (and $spiffe (kindIs "map" $spiffe) $spiffe.workloadAPISocket $spiffe.trustDomain) -}}
{{- fail "gibson.auth.spiffe must be populated in every overlay (gibson.auth.spiffe.workloadAPISocket + gibson.auth.spiffe.trustDomain). See spec in-cluster-mtls-restoration and memory feedback_spiffe_mtls_required.md. Disabling daemon SPIFFE mTLS is not permitted as a debugging shortcut." -}}
{{- end -}}
{{- end }}

{{- define "gibson.waitForSpireSocket" -}}
- name: wait-for-spire-socket
  image: ghcr.io/zeroroot-ai/mirror/busybox:1.36
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

{{/* ---------------------------------------------------------------------
     gibson.netpolEgressDNS — the DNS egress rule every narrowed policy
     needs (deploy#1462).

     Under the namespace default-deny (deploy#1365) a pod selected by a
     policy with policyTypes: [Egress] can reach ONLY what that policy's
     egress rules allow. Replacing `- {}` with named rules therefore
     severs name resolution unless DNS is restored explicitly, and a pod
     that cannot resolve is an outage no render catches.

     kube-dns rather than an ipBlock: the Service ClusterIP is not what
     NetworkPolicy matches — it matches the BACKING POD, so the selector
     names the CoreDNS pods. `k8s-app: kube-dns` is CoreDNS's label on
     both EKS and kind (the Deployment is named coredns; the label is
     retained for compatibility), and `kubernetes.io/metadata.name` is
     applied to every namespace by the apiserver since 1.22, so no
     cluster-specific labelling is assumed.

     UDP *and* TCP :53 — a response larger than the UDP payload limit
     sets TC and the resolver retries over TCP. Allowing only UDP works
     until a record grows, which is the worst possible failure mode.
--------------------------------------------------------------------- */}}
{{- define "gibson.netpolEgressDNS" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: kube-system
      podSelector:
        matchLabels:
          k8s-app: kube-dns
  ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
{{- end -}}
