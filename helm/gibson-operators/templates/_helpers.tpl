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

{{/*
gibson-operators.spireOidcJwksURL: the JWKS URL of the SPIRE OIDC provider.
An overlay value (vault.jwtAuth.spireOidcJwksURL) wins. The default names the
provider Service of this release in full, with the cluster domain the provider
itself reads (global.k8s.clusterDomain, default cluster.local). The issuer
stays the short host because it is the iss claim. The connector-operator
reads the provider namespace from this URL and refuses any host that is not
<service>.<namespace>.svc (gibson operators/connector ProxyAuth, charts#706).
*/}}
{{- define "gibson-operators.spireOidcJwksURL" -}}
{{- $domain := (((.Values.global).k8s).clusterDomain) | default "cluster.local" -}}
{{- ((.Values.vault).jwtAuth).spireOidcJwksURL | default (printf "https://%s-spiffe-oidc-discovery-provider.%s.svc.%s/keys" .Release.Name .Release.Namespace $domain) -}}
{{- end -}}

{{/*
gibson-operators.clusterCIDRs joins global.networkPolicy.clusterCIDRs with
commas for CONNECTOR_CLUSTER_CIDRS. The render fails on an empty list and on
an entry that is not a CIDR (gibson-operators.validCIDR). The connector
operator parses each entry again at start.
*/}}
{{- define "gibson-operators.clusterCIDRs" -}}
{{- $cidrs := ((.Values.global).networkPolicy | default dict).clusterCIDRs | default list -}}
{{- if not (kindIs "slice" $cidrs) -}}
{{- fail "global.networkPolicy.clusterCIDRs must be a list of CIDRs." -}}
{{- end -}}
{{- if empty $cidrs -}}
{{- fail "global.networkPolicy.clusterCIDRs is required: set the pod range and the Service range of the cluster, for example [10.244.0.0/16, 10.96.0.0/12] on kind or [10.42.0.0/16, 10.43.0.0/16] on k3s. The connector operator does not start without them." -}}
{{- end -}}
{{- range $cidrs -}}
{{- if ne (include "gibson-operators.validCIDR" .) "true" -}}
{{- fail (printf "global.networkPolicy.clusterCIDRs: %v is not a CIDR. Give an IPv4 address with a prefix of 0 to 32, or an IPv6 address with a prefix of 0 to 128." .) -}}
{{- end -}}
{{- end -}}
{{- join "," $cidrs -}}
{{- end -}}

{{/*
gibson-operators.ciliumDaemonSetName and gibson-operators.ciliumDaemonSetNamespace
name the Cilium agent DaemonSet (global.cilium.daemonSet). The tenant operator
and the connector operator read them as CILIUM_DAEMONSET_NAME and
CILIUM_DAEMONSET_NAMESPACE. Each one reads the DaemonSet at start and refuses to
start below the Cilium release that fixes CVE-2024-47825. The Role of
cilium-read-rbac.yaml follows both values.
*/}}
{{- define "gibson-operators.ciliumDaemonSetName" -}}
{{- (((.Values.global).cilium).daemonSet).name | default "cilium" -}}
{{- end -}}

{{- define "gibson-operators.ciliumDaemonSetNamespace" -}}
{{- (((.Values.global).cilium).daemonSet).namespace | default "kube-system" -}}
{{- end -}}

{{/*
gibson-operators.validCIDR returns "true" for an IPv4 or an IPv6 CIDR. Sprig
has no CIDR parser, so it checks each part. IPv4: four decimal octets of 0 to
255 and a prefix of 0 to 32, with no leading zero. IPv6: groups of one to
four hex digits, at most one "::", eight groups without "::" and fewer with
it, and a prefix of 0 to 128. An IPv6 address with an IPv4 tail is refused.
tests/cluster-cidrs.bats holds the fixtures.
*/}}
{{- define "gibson-operators.validCIDR" -}}
{{- $ok := false -}}
{{- if kindIs "string" . -}}
{{- if regexMatch "^(0|[1-9][0-9]{0,2})(\\.(0|[1-9][0-9]{0,2})){3}/(0|[1-9][0-9]?)$" . -}}
{{- $parts := splitList "/" . -}}
{{- $ok = le (atoi (index $parts 1)) 32 -}}
{{- range splitList "." (index $parts 0) -}}
{{- if gt (atoi .) 255 -}}{{- $ok = false -}}{{- end -}}
{{- end -}}
{{- else if regexMatch "^[0-9A-Fa-f:]+/(0|[1-9][0-9]{0,2})$" . -}}
{{- $parts := splitList "/" . -}}
{{- $addr := index $parts 0 -}}
{{- $double := sub (len (splitList "::" $addr)) 1 -}}
{{- $groups := list -}}
{{- range splitList ":" (replace "::" ":" $addr) -}}
{{- if . -}}{{- $groups = append $groups . -}}{{- end -}}
{{- end -}}
{{- $ok = and (le (atoi (index $parts 1)) 128) (le $double 1) (not (contains ":::" $addr)) -}}
{{- if eq $double 0 -}}
{{- $ok = and $ok (eq (len $groups) 8) (not (hasPrefix ":" $addr)) (not (hasSuffix ":" $addr)) -}}
{{- else -}}
{{- $rest := replace "::" "" $addr -}}
{{- $ok = and $ok (le (len $groups) 7) (not (hasPrefix ":" $rest)) (not (hasSuffix ":" $rest)) -}}
{{- end -}}
{{- range $groups -}}
{{- if gt (len .) 4 -}}{{- $ok = false -}}{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $ok -}}
{{- end -}}
