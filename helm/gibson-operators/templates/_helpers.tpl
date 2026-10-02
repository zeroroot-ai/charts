{{/*
  the operators chart's own helpers: the Redis validator and the tenant
  Postgres accessors.

  Anything more than one chart names lives in gibson-common, documented at
  its define and nowhere else (charts#304). This file held hundreds of lines of
  headers for helpers that moved there; scripts/check-helper-docs.py now
  fails the build on a header with no define under it.
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
