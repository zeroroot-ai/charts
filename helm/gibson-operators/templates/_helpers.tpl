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

The role uses the same physical instance as platformPostgres, so host,
port and SSL mode are the platformPostgres values. No values key changes
them: the `tenantPostgres` block these helpers once read was never
declared, and no overlay set it.
==============================================================================
*/}}

{{- define "gibson.tenantPostgres.host" -}}
{{- include "gibson.platformPostgres.host" . -}}
{{- end }}

{{- define "gibson.tenantPostgres.port" -}}
{{- include "gibson.platformPostgres.port" . -}}
{{- end }}

{{- /* The key of the tenant_admin Secret that holds its login role, which the
       OpenBao database engine issues (ADR-0171). */ -}}
{{- define "gibson.tenantPostgres.usernameSecretKey" -}}
username
{{- end }}

{{- define "gibson.tenantPostgres.passwordSecretName" -}}
tenant-admin-postgres-credentials
{{- end }}

{{- define "gibson.tenantPostgres.passwordSecretKey" -}}
password
{{- end }}

{{- define "gibson.tenantPostgres.sslMode" -}}
{{- include "gibson.platformPostgres.sslMode" . -}}
{{- end }}
