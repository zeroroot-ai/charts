{{/*
Helm chart structural validators.

Spec: unified-identity-and-authorization Phase H/8.4 (Reqs 14.2, 4.7).

Each validator is a named template that emits NOTHING when the chart's
state is correct and `{{ fail "..." }}`s with a precise, actionable message
when something is broken. Invoke once per template that depends on the
invariant — duplicates are safe and cheap.

The legacy validators (`gibson.validateSpire`, `gibson.validateEnvoyGateway`,
`gibson.validateSpiffeRequired`, `gibson.validateEnvoySdsWired`) live in
templates/_helpers.tpl and stay there for backward compat. New validators
land here so the file count stays bounded and the new contract is easy to
discover.

Validators added in Phase H/8.4:
  - gibson.validateAllPathsViaEnvoy
  - gibson.validateRegistryFromSDK
  - gibson.validateKMSConfigured

Validators added in spec zero-trust-hardening (task 5.2):
  - gibson.validateGhcrCredentials
  - dev.networkPolicy.disabled bypass gated on dev.allowed=true (Req 7.2)
  - validateAllPathsViaEnvoy fourth assertion: SaaS multitenancy without
    tenant NetworkPolicy fails (Req 7.3)
*/}}

{{/* =========================================================================
gibson.validateAllPathsViaEnvoy

Fails the render when the daemon's gRPC ports (50051 / 50002 / 50001 /
50100) are exposed outside the Envoy mesh.

The daemon Service is locked to ClusterIP by templates/gibson/service.yaml
(separate guard, fails earlier than this one). This validator catches the
inverse mistake of "I added a NodePort/LoadBalancer Service somewhere
else" or "I set componentIngress.enabled with no Envoy in front of it."

Rules (Envoy is required infrastructure — deploy#200; the gates that used
to inspect envoy.enabled are unconditional now):
  1. .Values.gibson.service.type MUST be ClusterIP (or unset).
  2. .Values.gibson.networkPolicy.enabled MUST be true when the daemon is
     deployed. (Without the policy, anyone in the cluster could reach
     :50051 directly even though Envoy IS the supported path.)
  3. .Values.ingress (the unified platform ingress) MUST NOT have
     gateway-bypassing routes. We can't fully introspect the rendered
     Ingress object from another template, so we approximate: the unified
     ingress's grpc.enabled MUST be false (gRPC traffic must hit Envoy,
     not nginx).

(The previous Rule 3 — `componentIngress.enabled MUST NOT be true` — was
removed in deploy#187 along with the entire `componentIngress` block and
the now-dead `templates/gibson/ingress-grpc.yaml` template, since the
toggle was already forbidden when true and therefore dead code.)

Spec Reqs: 3.4, 3.5.
========================================================================= */}}
{{- define "gibson.validateAllPathsViaEnvoy" -}}
{{- if .Values.gibson.enabled -}}

{{- /* Rule 1 — daemon Service type. */ -}}
{{- $svcType := .Values.gibson.service.type | default "ClusterIP" -}}
{{- if ne $svcType "ClusterIP" -}}
{{- fail (printf "validateAllPathsViaEnvoy: gibson.service.type=%q exposes daemon gRPC ports outside the Envoy mesh. Set gibson.service.type=ClusterIP and route external traffic through Envoy. Spec unified-identity-and-authorization Req 3.4." $svcType) -}}
{{- end -}}

{{- /* Rule 2 — NetworkPolicy (Envoy is unconditionally enabled per deploy#200). */ -}}
{{- $npEnabled := false -}}
{{- with .Values.gibson.networkPolicy -}}
{{- $npEnabled = .enabled -}}
{{- end -}}
{{- /* dev escape hatch: dev.networkPolicy.disabled overrides the requirement,
       but ONLY when dev.allowed=true is set in the same overlay. The undocumented
       dev.allowed flag prevents the bypass from being silently inherited into a
       production overlay (spec zero-trust-hardening Req 7.2). */ -}}
{{- $devDisabled := false -}}
{{- $devAllowed := false -}}
{{- with .Values.dev -}}
{{- $devAllowed = .allowed -}}
{{- with .networkPolicy -}}
{{- $devDisabled = .disabled -}}
{{- end -}}
{{- end -}}
{{- if and $devDisabled (not $devAllowed) -}}
{{- fail "validateAllPathsViaEnvoy: dev.networkPolicy.disabled=true requires dev.allowed=true in the same overlay — the NetworkPolicy bypass is dev-only and must not be silently inherited into production overlays. Either set dev.allowed=true (Kind/dev only) or remove dev.networkPolicy.disabled. Spec zero-trust-hardening Req 7.2." -}}
{{- end -}}
{{- if and (not $npEnabled) (not $devDisabled) -}}
{{- fail "validateAllPathsViaEnvoy: gibson.networkPolicy.enabled=true is required so non-Envoy pods cannot reach the daemon's gRPC ports directly. Set gibson.networkPolicy.enabled=true (or, in a Kind cluster without a NetworkPolicy-aware CNI, set dev.networkPolicy.disabled=true alongside dev.allowed=true to bypass this guard). Spec unified-identity-and-authorization Req 3.5." -}}
{{- end -}}

{{- /* Rule 3 — unified ingress gRPC route incompatible with Envoy. (Previous
       Rule 3 — componentIngress.enabled — removed in deploy#187 along with
       the dead componentIngress block.) */ -}}
{{- if .Values.ingress.enabled -}}
{{- $grpcEnabled := false -}}
{{- with .Values.ingress.grpc -}}
{{- $grpcEnabled = .enabled -}}
{{- end -}}
{{- if $grpcEnabled -}}
{{- fail "validateAllPathsViaEnvoy: ingress.grpc.enabled=true creates a parallel gRPC ingress that bypasses ext-authz. Set ingress.grpc.enabled=false and route gRPC through the Envoy edge listener. Spec unified-identity-and-authorization Req 3.4." -}}
{{- end -}}
{{- end -}}

{{- /* Rule 5 (removed in deploy#756 — platform/tenant scope split).
       Per-tenant isolation (NetworkPolicy + ResourceQuota) is owned by the
       tenant-operator and applied in each TENANT namespace at reconcile time —
       it is NOT a platform-chart concern and must never render into the
       platform namespace (that was the deploy#742 cascade: a tenant
       NetworkPolicy/ResourceQuota in the gibson ns rejected every platform
       init Job). The old chart-side saas.tenantNetworkPolicy assertion is gone;
       cross-chart-check Check 24 enforces "no tenant primitive in the platform
       namespace" instead. */ -}}

{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateGhcrCredentials

Fails the render when the chart pulls images from `ghcr.io/zeroroot-ai/`
(the private GHCR org for Gibson images and the internal authz registry
OCI tag) but the configured imagePullSecrets do not include
`ghcr-credentials`. Without the pull secret, kubelet hits anonymous-pull
quota / 404s on private images and the operator only finds out at first
ImagePullBackOff.

Trigger: extAuthz.image.repository, gibson.image.repository, or
dashboard.image.repository starting with "ghcr.io/zeroroot-ai/".

Ignored: any overlay that explicitly opts out by setting
dev.skipGhcrCredentialsCheck=true (undocumented; for chart-test contexts
that fake the imagePullSecrets list).

Spec zero-trust-hardening Req 11.6.
========================================================================= */}}
{{- define "gibson.validateGhcrCredentials" -}}
{{- if .Values.gibson.enabled -}}
{{- $skip := false -}}
{{- with .Values.dev -}}
{{- $skip = .skipGhcrCredentialsCheck -}}
{{- end -}}
{{- if not $skip -}}

{{- /* Build the set of configured imagePullSecret names from the global
       block (the chart's single helper, gibson.imagePullSecrets, sources
       from .Values.global.imagePullSecrets). */ -}}
{{- $secretNames := list -}}
{{- with .Values.global -}}
{{- range .imagePullSecrets -}}
{{- $secretNames = append $secretNames .name -}}
{{- end -}}
{{- end -}}
{{- $hasGhcr := has "ghcr-credentials" $secretNames -}}

{{- /* Inspect the relevant image repositories for the GHCR private prefix. */ -}}
{{- $needsGhcr := false -}}
{{- $offenders := list -}}

{{- $eaRepo := "" -}}
{{- with .Values.extAuthz -}}
{{- with .image -}}
{{- $eaRepo = .repository | default "" -}}
{{- end -}}
{{- end -}}
{{- if hasPrefix "ghcr.io/zeroroot-ai/" $eaRepo -}}
{{- $needsGhcr = true -}}
{{- $offenders = append $offenders (printf "extAuthz.image.repository=%s" $eaRepo) -}}
{{- end -}}

{{- $gRepo := "" -}}
{{- with .Values.gibson -}}
{{- with .image -}}
{{- $gRepo = .repository | default "" -}}
{{- end -}}
{{- end -}}
{{- if hasPrefix "ghcr.io/zeroroot-ai/" $gRepo -}}
{{- $needsGhcr = true -}}
{{- $offenders = append $offenders (printf "gibson.image.repository=%s" $gRepo) -}}
{{- end -}}

{{- $dRepo := "" -}}
{{- with .Values.dashboard -}}
{{- with .image -}}
{{- $dRepo = .repository | default "" -}}
{{- end -}}
{{- end -}}
{{- if hasPrefix "ghcr.io/zeroroot-ai/" $dRepo -}}
{{- $needsGhcr = true -}}
{{- $offenders = append $offenders (printf "dashboard.image.repository=%s" $dRepo) -}}
{{- end -}}

{{- /* Also catch the ext-authz registry OCI artifact pull, which uses the
       same private GHCR org. */ -}}
{{- $regRepo := "" -}}
{{- with .Values.extAuthz -}}
{{- with .registry -}}
{{- $regRepo = .repository | default .registry | default "" -}}
{{- end -}}
{{- end -}}
{{- if hasPrefix "ghcr.io/zeroroot-ai/" $regRepo -}}
{{- $needsGhcr = true -}}
{{- $offenders = append $offenders (printf "extAuthz.registry=%s" $regRepo) -}}
{{- end -}}

{{- if and $needsGhcr (not $hasGhcr) -}}
{{- fail (printf "validateGhcrCredentials: chart pulls from private ghcr.io/zeroroot-ai/ (%s) but global.imagePullSecrets does not include 'ghcr-credentials'. Add `- name: ghcr-credentials` to global.imagePullSecrets or override the image repositories. Spec zero-trust-hardening Req 11.6." (join ", " $offenders)) -}}
{{- end -}}

{{- end -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateRegistryFromSDK

Fails the render when the ext-authz RPC registry ConfigMap isn't sourced
from the gibson release artifact. (The knob is named `sdk.version` for
historical reasons; the OCI artifact it pins is a github.com/zeroroot-ai/gibson
tag, not an SDK release — see helm/gibson-workloads/values.yaml.)

The Phase H/8.6 init-container pull pattern stamps the ConfigMap with
annotation `gibson.zero-day.ai/sdk-version: <ver>` taken from
.Values.sdk.version. We can't introspect another template's output from
inside the helper engine, so we approximate by checking the values that
drive the stamp:

  - .Values.sdk.version MUST be non-empty.

The previous extAuthz.enabled check has been removed: /
deploy#188, ext-authz is structural infrastructure and the
`extAuthz.enabled` toggle was deleted from the chart. ext-authz pod
resources now render unconditionally, so there is no longer a
"daemon-on, ext-authz-off" combination to guard against.

Operators with a fully-air-gapped install can disable this guard by
setting .Values.sdk.bypassRegistryValidation=true (e.g., when they bake
the registry into a custom container image). Discouraged — drift between
the daemon's compiled-in proto registry and the ext-authz YAML is the
exact failure mode this guard catches.

Spec Reqs: 4.7, 14.2.
========================================================================= */}}
{{- define "gibson.validateRegistryFromSDK" -}}
{{- if .Values.gibson.enabled -}}
{{- $sdk := .Values.sdk | default dict -}}
{{- if not $sdk.bypassRegistryValidation -}}

{{- if not $sdk.version -}}
{{- fail "validateRegistryFromSDK: sdk.version is empty — the ext-authz registry ConfigMap (Phase H/8.6) downloads the (rpc → authz) registry from the gibson release artifact pinned by sdk.version. Set sdk.version to a published zeroroot-ai/gibson version (e.g. v0.124.3) or set sdk.bypassRegistryValidation=true for an air-gapped install with a baked-in registry. Spec unified-identity-and-authorization Req 4.7." -}}
{{- end -}}

{{- end -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateKMSConfigured

Fails the render when key_provider.type=aws_kms is selected without the
required AWS-KMS settings populated.

deploy#223 (one-code-path epic): Vault is STRUCTURAL — the in-chart
Vault always renders. The previous `.Values.openbao.enabled` master
switch and `.Values.openbao.external.address` substitution escape hatch
have been deleted. As a result, the legacy fallback rule "either
in-chart Vault or external Vault address" is no longer needed: the
in-chart Vault is ALWAYS present. The only remaining structural
contradiction is selecting `key_provider.type=aws_kms` without
populating the AWS-KMS specifics, which still surfaces as a render
failure here.

Spec Reqs: 5.1, 13.3, 14.2.
========================================================================= */}}
{{- define "gibson.validateKMSConfigured" -}}
{{- if .Values.gibson.enabled -}}

{{- $sec := ((.Values.gibson).config).security | default dict -}}
{{- $kp := $sec.key_provider | default dict -}}
{{- $kpType := "" -}}
{{- if kindIs "map" $kp -}}
{{- $kpType = $kp.type | default "" -}}
{{- else if kindIs "string" $kp -}}
{{- /* Legacy plain-string form: gibson.config.security.key_provider: aws_kms */ -}}
{{- $kpType = $kp -}}
{{- end -}}

{{- if eq $kpType "aws_kms" -}}
  {{- /* aws_kms requires IRSA role ARN + KMS key id. The chart always
         renders the in-chart Vault (deploy#223), so there is no longer
         a "Vault contradiction" failure mode — aws_kms simply takes
         precedence inside the daemon's KeyProvider chain. */ -}}
  {{- $aws := $sec.aws_kms | default ($sec.aws | default dict) -}}
  {{- $roleArn := $aws.role_arn | default "" -}}
  {{- $kmsKeyId := $aws.kms_key_id | default ($aws.secret_arn | default "") -}}
  {{- if not $roleArn -}}
  {{- fail "validateKMSConfigured: gibson.config.security.key_provider.type=aws_kms requires gibson.config.security.aws_kms.role_arn (the IRSA role the daemon assumes for KMS Sign/Decrypt). Set gibson.config.security.aws_kms.role_arn to your IRSA role ARN. Spec first-deploy-unblock-and-ha R6.1." -}}
  {{- end -}}
  {{- if not $kmsKeyId -}}
  {{- fail "validateKMSConfigured: gibson.config.security.key_provider.type=aws_kms requires gibson.config.security.aws_kms.kms_key_id (the KMS key ARN the daemon signs/encrypts with). Spec first-deploy-unblock-and-ha R6.1." -}}
  {{- end -}}
{{- end -}}

{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateCertManagerCRDs

Spec first-deploy-unblock-and-ha R7.18 + deploy#201 / epic deploy#186:
cert-manager is REQUIRED infrastructure for the chart's TLS plumbing. Every
TLS material traces back to a KMS-rooted authority via cert-manager + a
ClusterIssuer (awspca-issuer in prod, vault in kind).

The `.Values.certManager.enabled` toggle is gone — there is no
"bring-your-own / skip cert-manager" path. This validator instead asserts
that at least one issuer is enabled (a misconfiguration where every issuer
is off would render no ClusterIssuer + no Certificate, leaving every
SPIFFE-consuming mount unbacked at runtime).

Operators MUST install the cert-manager CRDs before installing this chart:
  kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml
========================================================================= */}}
{{- define "gibson.validateCertManagerCRDs" -}}
{{- if .Values.gibson.enabled -}}
{{- $cm := .Values.certManager | default dict -}}
{{- $issuers := $cm.issuers | default dict -}}
{{- $le := $issuers.letsencrypt | default dict -}}
{{- $ss := $issuers.selfsigned | default dict -}}
{{- $awspca := $issuers.awspca | default dict -}}
{{- $vault := $issuers.vault | default dict -}}
{{- if not (or $le.enabled $ss.enabled $awspca.enabled $vault.enabled) -}}
{{- fail "validateCertManagerCRDs: at least one cert-manager issuer must be enabled (certManager.issuers.{letsencrypt,selfsigned,awspca,vault}.enabled). cert-manager is REQUIRED infrastructure (deploy#201) — install cert-manager (kubectl apply -f https://github.com/cert-manager/cert-manager/releases/latest/download/cert-manager.yaml) and enable an issuer for your environment (vault for kind, awspca for prod, letsencrypt for ingress-only setups). Spec first-deploy-unblock-and-ha R7.18 + epic one-code-path." -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateExternalSecretsCRDs — DELETED

ESO is required infrastructure (one-code-path #203); the chart-wide
ESO on/off + required toggles were removed along with this validator.
Callers (gibson StatefulSet) no longer include this define.
========================================================================= */}}

{{/* =========================================================================
gibson.validateTenantStoresConfigured

Fails the render when the daemon is enabled but one or more of the four
data-plane stores is not configured.

Spec: database-per-tenant-data-plane Phase I Task 9.4, Requirement 16.2.

The daemon requires all three stores to be reachable at startup:
  - Postgres: per-tenant relational storage (missions, findings, credentials)
  - Neo4j:    per-tenant knowledge graph (GraphRAG)
  - Redis:    per-tenant logical-DB keyspace (missions, events, state)

Each store may be supplied as either:
  a) An in-chart subchart (e.g. neo4j.enabled=true) — the chart renders the
     store in-cluster.
  b) An explicit external DSN/URI in the dataPlane block (e.g.
     dataPlane.postgres.adminDSN="postgres://...").

If neither is set for any store, the daemon would fail at runtime on first
tenant access.  This validator surfaces the configuration error at helm
install/upgrade time.

Invocation: included from templates/_helpers.tpl validation chain, which is
itself called from the daemon StatefulSet template so any chart render that
includes the daemon hits the check.
========================================================================= */}}
{{- define "gibson.validateTenantStoresConfigured" -}}
{{- if .Values.gibson.enabled -}}

{{- /* dev escape hatch: dev.dataPlane.disabled=true bypasses the validator.
       Use only for Kind dev clusters where in-chart data-plane stores are not
       deployed yet.  Production and staging overlays MUST NOT set this flag. */ -}}
{{- $devDisabled := false -}}
{{- with .Values.dev -}}
{{- with .dataPlane -}}
{{- $devDisabled = .disabled -}}
{{- end -}}
{{- end -}}
{{- if $devDisabled -}}
{{- /* bail out early — validation skipped for dev. */ -}}
{{- else -}}

{{- $dp := .Values.dataPlane | default dict -}}

{{- /* ---- Postgres -------------------------------------------------------- */ -}}
{{- /* one-code-path/198: the platform Postgres is structurally required.
       platformPostgres.host MUST resolve to a non-empty value — directly or via
       platformPostgres.external.host when external.enabled=true. The
       gibson.platformPostgres.host helper fails render LOUD when neither is
       set. This validator additionally accepts the legacy data-plane host
       and the legacy tenant-postgresql alias to keep upgrade paths green for
       one release cycle. The in-chart-StatefulSet toggle arm is GONE
       (one-code-path epic deploy#186). */ -}}
{{- $pg := $dp.postgres | default dict -}}
{{- $pgInChart := false -}}
{{- with (index .Values "tenant-postgresql") -}}
{{- $pgInChart = .enabled -}}
{{- end -}}
{{- $pgHost := and $pg.host (ne $pg.host "") -}}
{{- $pp := .Values.platformPostgres | default dict -}}
{{- $ppHost := and (hasKey $pp "host") (ne (toString ($pp.host | default "")) "") -}}
{{- $ppExternal := and ($pp.external | default dict).enabled (($pp.external).host) -}}
{{- if not (or $pgInChart $pgHost $ppHost $ppExternal) -}}
{{- fail (printf "validateTenantStoresConfigured: gibson.enabled=true requires a Postgres data-plane store. Provide one of:\n  a) Consolidated tier (in-cluster):  set platformPostgres.host=\"<cluster-pg-endpoint>\" (preferred — kind: kind-bootstrap CNPG)\n  b) Consolidated tier (external):    set platformPostgres.external.enabled=true + platformPostgres.external.host=\"<rds-endpoint>\"\n  c) Legacy in-chart Postgres:        set tenant-postgresql.enabled=true\n  d) Legacy external Postgres:        set dataPlane.postgres.host=\"<rds-endpoint>\" (port/admin_database/admin_username/admin_password_secret_ref filled in)\nSpec: per-tenant-data-plane-completion Requirements 6.1, 7.1; one-code-path epic deploy#186.") -}}
{{- end -}}

{{- /* ---- Neo4j ----------------------------------------------------------- */ -}}
{{- /* Per-tenant model: tenant_mode must be "instance" (per-tenant StatefulSet
       provisioned by the tenant-operator) or "multi-db" (shared Enterprise
       cluster with tenant_<id> databases). Vector path is N/A under this spec
       — removed. Spec per-tenant-data-plane-completion Tasks 11-22, Req 5. */ -}}
{{- $n4j := $dp.neo4j | default dict -}}
{{- $tenantMode := "" -}}
{{- with .Values.neo4j -}}
{{- $tenantMode = .tenant_mode | default "" -}}
{{- end -}}
{{- if not (or (eq $tenantMode "instance") (eq $tenantMode "multi-db")) -}}
{{- fail (printf "validateTenantStoresConfigured: gibson.enabled=true requires neo4j.tenant_mode to be either \"instance\" or \"multi-db\" (got %q).\n  - instance:  per-tenant Neo4j Community StatefulSets, provisioned by tenant-operator (default for kind/dev)\n  - multi-db:  shared Neo4j Enterprise cluster with tenant_<id> databases (prod-Enterprise migration path)\nSpec: per-tenant-data-plane-completion Requirement 5." $tenantMode) -}}
{{- end -}}
{{- if eq $tenantMode "instance" -}}
{{- $tenantNeo4jTag := "" -}}
{{- with .Values.tenantNeo4j -}}
{{- with .image -}}
{{- $tenantNeo4jTag = .tag | default "" -}}
{{- end -}}
{{- end -}}
{{- /* The tenant-operator is structural infrastructure (one-code-path
       deploy#186 / deploy#188 closed by #303); there is no .enabled toggle
       on it. The previous `tenantOperator.enabled=true` assertion has been
       deleted along with the value. */ -}}
{{- if not $tenantNeo4jTag -}}
{{- fail "validateTenantStoresConfigured: neo4j.tenant_mode=\"instance\" requires tenantNeo4j.image.tag to be set (the per-tenant StatefulSet image comes from this value — via the operator's Go-built resources today; via the tenant-neo4j-template ConfigMap once Task 22 renders it, deploy#1437).\nSpec: per-tenant-data-plane-completion Tasks 22, 23." -}}
{{- end -}}
{{- else if eq $tenantMode "multi-db" -}}
{{- $sharedURI := "" -}}
{{- with .Values.neo4j -}}
{{- $sharedURI = .shared_cluster_uri | default "" -}}
{{- end -}}
{{- if not $sharedURI -}}
{{- fail "validateTenantStoresConfigured: neo4j.tenant_mode=\"multi-db\" requires neo4j.shared_cluster_uri to point at the shared Neo4j Enterprise cluster (e.g. \"neo4j+s://aura-cluster.example.com:7687\").\nSpec: per-tenant-data-plane-completion Requirement 5.6." -}}
{{- end -}}
{{- end -}}

{{- /* ---- Redis ----------------------------------------------------------- */ -}}
{{- /* Redis is structural infrastructure (deploy#224 deleted
       the `redis.provider` enum and `redis.external` substitution). The
       chart always renders the in-cluster redis-stack StatefulSet; the
       gibson.redis.host helper hard-fails render via `| required` on an
       empty redis.addr, which covers the only remaining knob. Per-tenant
       Redis ACL user provisioning runs through `dataPlane.redis.adminURL`,
       independently required by the data-plane saga (no validator gate
       here — its absence is surfaced loudly at the operator-side step). */ -}}

{{- end -}}{{/* end else (not devDisabled) */}}
{{- end -}}{{/* end if gibson.enabled */}}
{{- end -}}{{/* end define */}}

{{/* =========================================================================
gibson.validateKindRootTokenSafety

Refuses to render the chart when `dataPlane.openbao.kindRootToken=true` AND
the overlay marks itself as production via `global.environment=prod`. The
kindRootToken flag materialises Vault's chart-known dev-mode root token
"root" into <release>-openbao-keys; that's safe in kind dev but a
high-severity credential leak in production.

Spec: tenant-operator-saga-capabilities Requirements 2.1 + NFR Security.
========================================================================= */}}
{{- define "gibson.validateKindRootTokenSafety" -}}
{{- $kindRoot := default false ((.Values.dataPlane).openbao).kindRootToken -}}
{{- $env := default "" (.Values.global).environment -}}
{{- if and $kindRoot (eq $env "prod") -}}
{{- fail (printf "validateKindRootTokenSafety: refusing to render — dataPlane.openbao.kindRootToken=true (which writes the Vault dev-mode root token \"root\" into the cluster) is incompatible with global.environment=%q. Production overlays MUST create the openbao admin Secret out-of-band with a periodic token before `helm install` and leave kindRootToken=false. Spec tenant-operator-saga-capabilities Requirement 2.1." $env) -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateNoLatestTags

Spec 2 R13 — fails the render when any image string in the chart's well-known
image-tag values resolves to :latest. The render-side guard catches misconfig
before deploy; the CI chart-audit gate (.github/workflows/chart-audit.yaml)
greps the rendered output for the same regression on every PR.
========================================================================= */}}
{{/* =========================================================================
gibson.validateIdpDiscoveryURL

Spec: tier-2-host-aliases-cluster-dns (Requirement 2 + Security NFR).

Validates `idp.zitadel.discoveryURL`. The field is OPTIONAL — empty is the
correct value when discovery should fall back to `idp.zitadel.issuer`.
When NON-empty, the value must be a hostname the daemon can reach from
inside the cluster, OR be exactly equal to `idp.zitadel.issuer` (the
escape hatch for operators who want to disable the in-cluster split for
their environment).

Allow-list (matched by hostname, after parsing scheme/port off):
  1. ends with `.svc.cluster.local`
  2. ends with `.svc`
  3. is exactly `localhost`
  4. equals `idp.zitadel.issuer` verbatim (string compare on the full URL
     including scheme + port)

Why so strict: without this rail, an operator typo or a malicious values
override could point the daemon's IdP admin client at an attacker-
controlled `/.well-known/openid-configuration` document, which would
hand it an attacker-controlled token endpoint. The discovery URL is
strictly a within-cluster shortcut; it is NEVER appropriate to set it
to an arbitrary external URL.

Implemented note (matches design.md committed allow-list verbatim):
  - The fourth case (equal-to-issuer) preserves the `discoveryURL ==
    issuer` use case where an operator deliberately disables the
    in-cluster split — the validator passes that through because
    structurally it is identical to leaving discoveryURL empty.
  - We do NOT permit wildcard external hostnames; doing so would defeat
    the security purpose of this rail.
========================================================================= */}}
{{- define "gibson.validateIdpDiscoveryURL" -}}
{{- if .Values.gibson.enabled -}}
{{- $idp := .Values.idp | default dict -}}
{{- $z := $idp.zitadel | default dict -}}
{{- $du := $z.discoveryURL | default "" -}}
{{- if ne $du "" -}}
{{- $issuer := $z.issuer | default "" -}}
{{- /* Strip scheme so the suffix match works on the host portion. */ -}}
{{- $afterScheme := $du -}}
{{- if hasPrefix "https://" $afterScheme -}}
{{- $afterScheme = trimPrefix "https://" $afterScheme -}}
{{- else if hasPrefix "http://" $afterScheme -}}
{{- $afterScheme = trimPrefix "http://" $afterScheme -}}
{{- end -}}
{{- /* Strip path. */ -}}
{{- $hostPort := $afterScheme -}}
{{- if contains "/" $hostPort -}}
{{- $hostPort = (splitList "/" $hostPort) | first -}}
{{- end -}}
{{- /* Strip port. */ -}}
{{- $host := $hostPort -}}
{{- if contains ":" $host -}}
{{- $host = (splitList ":" $host) | first -}}
{{- end -}}
{{- /* Allow-list cases. */ -}}
{{- $ok := false -}}
{{- if hasSuffix ".svc.cluster.local" $host -}}{{- $ok = true -}}{{- end -}}
{{- if hasSuffix ".svc" $host -}}{{- $ok = true -}}{{- end -}}
{{- if eq $host "localhost" -}}{{- $ok = true -}}{{- end -}}
{{- if eq $du $issuer -}}{{- $ok = true -}}{{- end -}}
{{- if not $ok -}}
{{- fail (printf "validateIdpDiscoveryURL: idp.zitadel.discoveryURL=%q does not match the in-cluster allow-list. Permitted forms:\n  1. *.svc.cluster.local (e.g. https://gibson-envoy.gibson.svc.cluster.local:443)\n  2. *.svc                (e.g. https://gibson-envoy.gibson.svc:443)\n  3. localhost\n  4. exactly equal to idp.zitadel.issuer (operator opt-out of the in-cluster split)\nLeaving discoveryURL empty falls back to idp.zitadel.issuer. Setting it to an arbitrary external URL is rejected — that path would let the daemon's IdP admin client be redirected at an attacker-controlled OIDC discovery document. Spec tier-2-host-aliases-cluster-dns Requirement 2 + Security NFR." $du) -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "gibson.validateNoLatestTags" -}}
{{- $offenders := list -}}
{{- range $alias := list "dashboard-postgresql" "tenant-postgresql" "fga-postgresql" "zitadel-postgresql" -}}
{{- $sub := index $.Values $alias | default dict -}}
{{- $img := $sub.image | default dict -}}
{{- $tag := $img.tag | default "" -}}
{{- if eq $tag "latest" -}}
{{- $offenders = append $offenders (printf "%s.image.tag=latest" $alias) -}}
{{- end -}}
{{- end -}}
{{- /* SPIRE in-pod Postgres */ -}}
{{- $spirePg := ((.Values.spire).postgresql) | default dict -}}
{{- $spirePgImg := $spirePg.image | default dict -}}
{{- if eq ($spirePgImg.tag | default "") "latest" -}}
{{- $offenders = append $offenders "spire.postgresql.image.tag=latest" -}}
{{- end -}}
{{- if $offenders -}}
{{- fail (printf "validateNoLatestTags: refusing to render — image tags resolved to ':latest' for: %s. Pin to a concrete tag (Spec first-deploy-unblock-and-ha R13)." (join ", " $offenders)) -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateSetecTier

Spec setec-sandbox-prod-default Task 34 / R2.B.3 / R2.C.4. Rule (b) revised
by deploy#1080 — see the COST GUARD note above `setec:` in values.yaml for
the full analysis.

Two-rule policy:
  (a) `setec.tier == "demo"` is REJECTED. The demo tier was deprecated on
      arrival per the design's deviation note: a single real-KVM
      production tier is cheap enough (the user explicitly accepted
      ~$200-300/mo) and a stub-mode demo tier would weaken NFR-S1 (hardware
      isolation as a non-negotiable property).
  (b) `setec.nodeSelector` / `setec.tolerations` MUST be empty (any
      setec.enabled=true, any tier). These values flow straight through to
      the Setec subchart's top-level nodeSelector/tolerations — which pin
      the OPERATOR Deployment's own Pod, not dynamically-created Sandbox
      Pods (setec's chart templates/deployment.yaml vs. the
      RuntimeClass-driven scheduling in templates/runtime-classes.yaml).
      Pinning the operator onto the tainted, Karpenter scale-to-zero
      sandbox-host NodePool (eks/gibson/karpenter.tf) would
      boot a ~$4/hr metal node on every helm upgrade/ArgoCD sync and it would
      never consolidate
      (the operator, a permanent Deployment, never lets the node go empty).
      This rule was previously the OPPOSITE (required nodeSelector to pin
      sandbox-host) before deploy#1080 discovered the cost-guard bug;
      earlier renders never caught it because setec.enabled was false in
      every overlay until then.

The validator only fires when `setec.enabled == true`. Kind/dev overlays
that disable the Setec subchart entirely skip both rules — they never
reach the SANDBOXED dispatch path.

Real Sandbox-Pod placement onto sandbox-host nodes is NOT this chart's
concern: it is driven by the runtimeAgent DaemonSet's node-capability
labels (consumed by each RuntimeClass's own `scheduling.nodeSelector`) and,
per-class, a SandboxClass CR's `spec.nodeSelector`
(opensource/setec api/v1alpha1). Neither of those has a matching
Tolerations mechanism today — tracked as a known gap in
zeroroot-ai/setec, filed alongside deploy#1080.

Run by templates/gibson/statefulset.yaml's validation chain (alongside
the other gibson.validate* validators).
========================================================================= */}}
{{- define "gibson.validateSetecTier" -}}
{{- $setec := .Values.setec | default dict -}}
{{- if $setec.enabled -}}

{{- /* Rule (a): demo tier is deprecated. */ -}}
{{- $tier := $setec.tier | default "" -}}
{{- if eq $tier "demo" -}}
{{- fail "validateSetecTier: setec.tier=demo is deprecated; the chart only supports the production tier (real-KVM Firecracker microVMs on bare-metal). The demo tier was rejected at design time per the spec's deviation callout — a stub-mode tier would weaken NFR-S1 (hardware isolation). Set setec.tier=production (the default) or set setec.enabled=false. Spec setec-sandbox-prod-default Task 34 / R2.B.3." -}}
{{- end -}}

{{- /* Rule (b): the operator must NOT be pinned to the sandbox-host taint
       — see the COST GUARD note above `setec:` in values.yaml. */ -}}
{{- $ns := $setec.nodeSelector | default dict -}}
{{- $tolerations := $setec.tolerations | default list -}}
{{- if or (gt (len $ns) 0) (gt (len $tolerations) 0) -}}
{{- fail (printf "validateSetecTier: setec.nodeSelector and setec.tolerations must be empty (got nodeSelector=%v, tolerations=%v). These values pin the Setec OPERATOR Deployment itself, not Sandbox Pods — setting them to the sandbox-host taint/label boots the Karpenter scale-to-zero metal NodePool permanently (the operator never lets it go empty for consolidation). Real Sandbox-Pod placement is driven by RuntimeClass scheduling.nodeSelector + a SandboxClass's spec.nodeSelector, not this chart value. Spec setec-sandbox-prod-default Task 34 / R2.C.4, revised deploy#1080." (toString $ns) (toString $tolerations)) -}}
{{- end -}}

{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateSetecContainment

Makes the sandbox containment posture unskippable rather than a values
default someone can quietly flip.

Sandboxes run untrusted code. Three things have to hold for that to be
confined, and each of them is a values key that renders fine when wrong:

  (1) `setec.webhook.enabled` — with the admission webhook off, a
      SandboxClass's allowedNetworkModes, the runc dev-only gate, and the
      tenant-label check never run. The constraints still render; they
      just do not bind.
  (2) `setec.netpol.reservedCIDRs` non-empty — this is the address space
      subtracted from every permissive egress rule the operator
      generates. Empty means every rule resolves to a bare 0.0.0.0/0.
  (3) at least one SandboxClass declaring an explicit
      `defaultNetworkMode` — a Sandbox that omits spec.network inherits
      it, and a class that states nothing leaves those Sandboxes at
      deny-all, which silently breaks the workloads rather than
      confining them.
  (4) `setec.sandboxNamespaces` non-empty, unless
      `setec.rbac.allowClusterWideSandboxWrite` is explicitly true. The
      list does two things at once: it is where the namespace-wide
      default-deny NetworkPolicy renders (podSelector: {}, so a Pod
      created in the namespace by a route other than the operator is
      selected by a policy rather than left unselected — an unselected
      Pod is allow-all in Kubernetes), and it is where the operator's
      Pod-write RBAC is bound instead of cluster-wide. Cluster-wide
      `pods: create` is a cluster-admin path on its own, so taking it is
      a decision that has to be stated.
  (5) a CA source for the admission webhook — either
      `setec.webhook.certManager.enabled` or `setec.webhook.caBundle`.
      Without one the rendered ValidatingWebhookConfiguration carries no
      caBundle, the API server cannot verify the webhook, and with
      failurePolicy Fail (which (1) already requires) every Sandbox and
      SandboxClass write is rejected. That is the failure mode where the
      chart installs cleanly and nothing works.
  (6) every SandboxClass names a backend that is ENABLED in
      `setec.runtimes`. A disabled backend is not a degraded backend: no
      RuntimeClass renders for it and the admission webhook rejects any
      SandboxClass referencing it, so every launch into that class fails.
      This one renders cleanly and is invisible until dispatch —
      deploy#1105 was exactly this shape, with a kata-qemu SandboxClass
      against a chart that shipped `runtimes.kata-qemu.enabled: false`.

The vendored subchart enforces (2), (4) and (5) at render time as well,
and the operator enforces (2) again at startup. Those three are kept here
anyway, and the reason is specific rather than general: the setec chart
is a COPY. A re-vendor from a setec revision that has not landed those
guards, or a hand-edit to the vendored tree, silently removes them, and
the failure is invisible — the chart renders, installs, and is wrong. A
gate in this repo's own template survives that. (1) and (3) exist nowhere
else at all: this is their only gate.

Only fires when `setec.enabled == true`.
========================================================================= */}}
{{- define "gibson.validateSetecContainment" -}}
{{- $setec := .Values.setec | default dict -}}
{{- if $setec.enabled -}}

{{- /* (1) Admission must be on, and fail-closed. */ -}}
{{- $webhook := $setec.webhook | default dict -}}
{{- if not $webhook.enabled -}}
{{- fail "validateSetecContainment: setec.webhook.enabled must be true. Sandboxes run untrusted code; with the admission webhook off, a SandboxClass's allowedNetworkModes, the runc dev-only gate and the tenant-label check are advisory only — they render but never bind. Set setec.webhook.enabled=true (and keep failurePolicy=Fail), or set setec.enabled=false." -}}
{{- end -}}
{{- $fp := $webhook.failurePolicy | default "" -}}
{{- if ne $fp "Fail" -}}
{{- fail (printf "validateSetecContainment: setec.webhook.failurePolicy=%q must be \"Fail\". Ignore admits a Sandbox unchecked whenever the operator is unreachable, which is exactly when a constraint bypass matters. The subchart runs 2 replicas with a PodDisruptionBudget so fail-closed admission does not block launches during a restart." $fp) -}}
{{- end -}}
{{- $certManager := $webhook.certManager | default dict -}}
{{- if and (not $certManager.enabled) (not $webhook.caBundle) -}}
{{- fail "validateSetecContainment: setec.webhook needs a CA source: set setec.webhook.certManager.enabled=true with an issuerRef, or supply setec.webhook.caBundle (base64 PEM) alongside a Secret holding the serving cert. With neither, the rendered ValidatingWebhookConfiguration carries no caBundle, the API server cannot verify the webhook's serving certificate, and with failurePolicy=Fail every Sandbox and SandboxClass write is rejected — the chart installs cleanly and no Sandbox ever launches." -}}
{{- end -}}

{{- /* (2) Reserved ranges must be stated. */ -}}
{{- $netpol := $setec.netpol | default dict -}}
{{- $reserved := $netpol.reservedCIDRs | default list -}}
{{- if eq (len $reserved) 0 -}}
{{- fail "validateSetecContainment: setec.netpol.reservedCIDRs must not be empty. It is the address space subtracted from every permissive Sandbox egress rule; empty means each rule resolves to a bare 0.0.0.0/0 and a Sandbox can reach the control plane. Include this account's VPC CIDR alongside the RFC1918 / link-local / CGNAT defaults." -}}
{{- end -}}
{{- $resolvers := $netpol.resolvers | default list -}}
{{- if eq (len $resolvers) 0 -}}
{{- fail "validateSetecContainment: setec.netpol.resolvers must not be empty. Sandbox Pods resolve names through these addresses instead of cluster DNS, which is what stops a Sandbox enumerating in-cluster Services by name." -}}
{{- end -}}

{{- /* (3) Every rendered class must state its posture explicitly. */ -}}
{{- $classes := ($setec.sandboxClasses | default dict) -}}
{{- if not $classes.enabled -}}
{{- fail "validateSetecContainment: setec.sandboxClasses.enabled must be true. A Sandbox that names no class resolves to the cluster-default SandboxClass, and that class is what supplies the egress posture for a Sandbox that declares no spec.network. With no classes rendered, every such Sandbox falls through to deny-all." -}}
{{- end -}}
{{- $list := $classes.classes | default list -}}
{{- if eq (len $list) 0 -}}
{{- fail "validateSetecContainment: setec.sandboxClasses.classes must declare at least one class." -}}
{{- end -}}
{{- $defaults := 0 -}}
{{- range $list -}}
{{- $spec := .spec | default dict -}}
{{- if not $spec.defaultNetworkMode -}}
{{- fail (printf "validateSetecContainment: SandboxClass %q declares no spec.defaultNetworkMode. A Sandbox in this class that omits spec.network would resolve to deny-all, which breaks the workload rather than confining it. State the posture: external-only for workloads that must reach external endpoints, egress-allow-list for a declared destination set, none for no network." (.name | default "<unnamed>")) -}}
{{- end -}}
{{- if $spec.default -}}{{- $defaults = add1 $defaults -}}{{- end -}}
{{- end -}}
{{- if ne $defaults 1 -}}
{{- fail (printf "validateSetecContainment: exactly one SandboxClass must carry spec.default: true (found %d). With none, a Sandbox that names no class fails to resolve; with more than one, the operator refuses to default until the ambiguity is resolved." $defaults) -}}
{{- end -}}

{{- /* (4) Sandbox namespaces must be named, or the cluster-wide grant taken deliberately. */ -}}
{{- $rbac := $setec.rbac | default dict -}}
{{- $clusterWide := $rbac.allowClusterWideSandboxWrite | default false -}}
{{- $sandboxNs := $setec.sandboxNamespaces | default list -}}
{{- if and (not $clusterWide) (eq (len $sandboxNs) 0) -}}
{{- fail "validateSetecContainment: setec.sandboxNamespaces must not be empty. It names the namespaces Sandboxes run in, and it decides two things at once. It is where the namespace-wide default-deny NetworkPolicy renders — podSelector: {}, every Pod, because the per-Sandbox policies select on setec.zeroroot.ai/sandbox and a Pod created in the namespace by any other route carries no such label, is selected by no policy, and is therefore unrestricted (Kubernetes treats an unselected Pod as allow-all, not deny-all). And it is where the operator's Pod- and NetworkPolicy-write RBAC is bound with a RoleBinding instead of cluster-wide; cluster-wide `pods: create` is a cluster-admin path on its own. Either list your Sandbox namespaces, or set setec.rbac.allowClusterWideSandboxWrite=true to take the cluster-wide grant on the record." -}}
{{- end -}}
{{- if has ($setec.namespace | default "setec-system") $sandboxNs -}}
{{- fail (printf "validateSetecContainment: setec.sandboxNamespaces contains %q, which is setec.namespace — the namespace this chart installs the operator, the frontend and the node agents into. The baseline policy denies all traffic for every Pod in the namespaces it names, so listing the operator's own namespace would cut off the operator itself. Give Sandboxes a namespace of their own." ($setec.namespace | default "setec-system")) -}}
{{- end -}}

{{- /* (6) Every class must name a backend this cluster actually has. */ -}}
{{- $runtimes := $setec.runtimes | default dict -}}
{{- $enabledBackends := list -}}
{{- range $name, $cfg := $runtimes -}}
{{- if (default dict $cfg).enabled -}}{{- $enabledBackends = append $enabledBackends $name -}}{{- end -}}
{{- end -}}
{{- range $list -}}
{{- $spec := .spec | default dict -}}
{{- $backend := ($spec.runtime | default dict).backend | default "" -}}
{{- if and $backend (not (has $backend $enabledBackends)) -}}
{{- fail (printf "validateSetecContainment: SandboxClass %q names runtime.backend=%q, which is not enabled in setec.runtimes (enabled: %s). A disabled backend is not a slower backend — templates/runtime-classes.yaml renders no RuntimeClass for it and the admission webhook rejects every SandboxClass that references it, so each launch into this class fails at dispatch while the chart renders and installs cleanly. Set setec.runtimes.%s.enabled=true, or point the class at an enabled backend. deploy#1105." (.name | default "<unnamed>") $backend (join ", " (default (list "<none>") $enabledBackends)) $backend) -}}
{{- end -}}
{{- end -}}

{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateEnvoyTlsNotSelfSigned

Seals the deprecated in-chart self-signed Envoy edge cert path. The
historical `envoy.tls.selfSigned: true` flag minted material inside the
chart at render time — under Argo's offline render that produced fresh
material on every reconcile, breaking TLS for every consumer that had
cached the prior cert (dashboard pod, Envoy edge listeners, the daemon's
IdP admin client). deploy#124 moved the cert to cert-manager exclusively;
deploy#125 (this validator) seals the old path so it cannot be
re-introduced by accident.

Failure message names every replacement value an operator should set and
links the parent issue (deploy#123) so the migration is discoverable from
the error alone.

Spec: deploy#123 (envoy-cert-unify) / deploy#125.
========================================================================= */}}
{{- define "gibson.validateEnvoyTlsNotSelfSigned" -}}
{{- $tls := (.Values.envoy).tls | default dict -}}
{{- if $tls.selfSigned -}}
{{- fail "validateEnvoyTlsNotSelfSigned: envoy.tls.selfSigned=true is forbidden (deploy#125 / parent deploy#123). The Envoy edge TLS Secret gibson-envoy-tls is owned EXCLUSIVELY by the cert-manager Certificate in templates/cert-manager/certificate.yaml. The old in-chart inline-mint path was deleted in deploy#124 — under Argo's offline render it produced fresh cert material on every reconcile, breaking every consumer that had cached the prior cert. To migrate: set envoy.tls.selfSigned=false AND certManager.envoyEdge.enabled=true AND certManager.envoyEdge.issuer to one of: letsencrypt-prod | letsencrypt-staging | selfsigned-ca (kind). See zeroroot-ai/deploy#123 for the unification rationale." -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateObservabilityRetiredToggles

Fails the render when an overlay still sets one of the five booleans that
deploy#313 retired in favour of the `observability.provider` enum:

  observability.enabled | prometheus.enabled | grafana.enabled
  loki.enabled | promtail.enabled

Why a hard fail and not a silent ignore (deploy#1199 b). Four of the five
were inert: they survived in values.yaml and in values-aws-prod.yaml long
after the templates reading them were deleted, so the prod overlay set
them all to `false` and still rendered the complete in-chart
prometheus + grafana + loki + promtail stack. An operator reading the
overlay saw observability switched off while the cluster ran it.

The fifth, `loki.enabled`, was worse than inert. One consumer still read
it — the daemon's GIBSON_LOKI_URL in templates/gibson/statefulset.yaml —
and it disagreed with the enum that decides whether an in-chart Loki
exists. On prod that inverted the intent exactly: Loki was DEPLOYED (the
enum defaulted to in-chart) and the daemon was told NOT to query it, so
the estate ran a log store nothing read. That site now keys off
`observability.provider`, and the boolean is retired with the rest.

Dead config that reads as configuration is worse than no config: it earns
trust it cannot honour. Ignoring a stale key reproduces exactly that
failure, so the retired names are a render error carrying the
replacement. `jaeger.enabled` is NOT in this set —
templates/observability/jaeger-deployment.yaml still gates on it, so it
remains a live knob.

Spec: deploy#1199 (b). Parent enum: deploy#313.
========================================================================= */}}
{{- define "gibson.validateObservabilityRetiredToggles" -}}
{{- $retired := list
      (list "observability.provider" ((.Values.observability | default dict).provider))
      (list "observability.enabled" ((.Values.observability | default dict).enabled))
      (list "prometheus.enabled"    ((.Values.prometheus    | default dict).enabled))
      (list "grafana.enabled"       ((.Values.grafana       | default dict).enabled))
      (list "loki.enabled"          ((.Values.loki          | default dict).enabled))
      (list "promtail.enabled"      ((.Values.promtail      | default dict).enabled))
-}}
{{- $set := list -}}
{{- range $entry := $retired -}}
{{- if not (kindIs "invalid" (index $entry 1)) -}}
{{- $set = append $set (printf "%s=%v" (index $entry 0) (index $entry 1)) -}}
{{- end -}}
{{- end -}}
{{- if $set -}}
{{- fail (printf "validateObservabilityRetiredToggles: %s. These keys are retired. observability.provider went with the in-chart prometheus/grafana/loki/promtail stack, which the chart no longer ships in ANY profile: it emits ServiceMonitors, PrometheusRules and grafana_dashboard ConfigMaps for a stack the cluster already runs, each gated on .Capabilities.APIVersions.Has. The other five were retired in deploy#313 and NO template has read them since — setting them changes nothing, which is how values-aws-prod.yaml came to render the entire in-chart prometheus/grafana/loki/promtail stack while declaring observability disabled (deploy#1199 b). Delete them and set observability.provider instead: 'in-chart' deploys the stack in-cluster; 'external-grafana-cloud' deploys none of it and leaves the ServiceMonitors for an external scraper. Note jaeger.enabled is still live and is not affected." (join ", " $set)) -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.envoy.validateRateLimitBucket

Fails the render when an `envoy.rateLimit.<name>` token bucket is missing,
zero, negative, fractional, inverted or effectively unbounded.

Why this is a hard fail and not a default (deploy#1189): every degenerate
shape of a token bucket renders as valid YAML and produces an Envoy config
that Envoy itself accepts, while throttling nothing. A limiter that is
present but inert is worse than an absent one, because the route table then
LOOKS protected. This repo already has that class on record (deploy#1220,
`| default true`), so the numbers are REQUIRED — values.yaml carries the
documented defaults, and an overlay that nulls or mangles one gets a build
failure, not a silent bypass.

Rejected shapes and what each would mean at runtime:

  missing / null       -> Envoy renders `max_tokens:` with no value; helm
                          emits it as null and Envoy rejects OR (worse, when
                          the whole bucket is absent) the filter renders with
                          no token_bucket at all, which DISABLES it.
  tokensPerFill: 0     -> the bucket drains once and never refills: every
                          request after the first burst 429s, forever.
  maxTokens: 0         -> the bucket is empty at all times: 100% 429.
  fractional / string  -> silently truncated by `int64`, so the operator's
                          intent and the rendered number differ.
  maxTokens <
    tokensPerFill      -> Envoy caps the bucket at max_tokens, so the
                          configured refill rate is unachievable and the
                          effective rate is a number nobody wrote down.
  > 1000000 per fill   -> not a limit. 1M rps through one Envoy process is
                          unreachable, so this is "disabled" spelled as a
                          number. Say it out loud in the diff instead.
  fillIntervalSeconds
    > 3600             -> a refill window measured in hours is a one-shot
                          quota, not a rate limit.

Usage:
  {{- include "gibson.envoy.validateRateLimitBucket" (dict "name" "preAuthRegister" "bucket" $b) -}}
========================================================================= */}}
{{- define "gibson.envoy.validateRateLimitBucket" -}}
{{- $name := .name -}}
{{- $b := .bucket -}}
{{- $where := printf "envoy.rateLimit.%s" $name -}}
{{- if not (kindIs "map" $b) -}}
{{- fail (printf "validateRateLimitBucket: %s is missing or is not a map. The Envoy edge rate limiter has no `enabled` knob by design (deploy#1189) — every bucket is structural and must carry maxTokens, tokensPerFill and fillIntervalSeconds. Restore the block from the chart's values.yaml rather than removing it; an absent bucket renders a local_ratelimit filter with no token_bucket, which Envoy accepts and treats as DISABLED." $where) -}}
{{- end -}}
{{- range $field := list "maxTokens" "tokensPerFill" "fillIntervalSeconds" -}}
{{- $v := index $b $field -}}
{{- if kindIs "invalid" $v -}}
{{- fail (printf "validateRateLimitBucket: %s.%s is unset. All three of maxTokens, tokensPerFill and fillIntervalSeconds are REQUIRED — a partially-specified bucket renders an Envoy token_bucket that throttles nothing while the route table looks protected (deploy#1189)." $where $field) -}}
{{- end -}}
{{- if kindIs "bool" $v -}}
{{- fail (printf "validateRateLimitBucket: %s.%s=%v is a boolean, not a count. This limiter is not switchable — see the note in values.yaml." $where $field $v) -}}
{{- end -}}
{{- /* int64 of a non-numeric string is 0, and int64 of a fraction truncates;
       comparing the round-trip string form catches both without needing to
       enumerate helm's numeric kinds. */ -}}
{{- if ne (printf "%v" $v) (printf "%v" (int64 $v)) -}}
{{- fail (printf "validateRateLimitBucket: %s.%s=%v is not a whole number. Helm would truncate it with int64 and Envoy would enforce a rate nobody configured." $where $field $v) -}}
{{- end -}}
{{- if lt (int64 $v) 1 -}}
{{- fail (printf "validateRateLimitBucket: %s.%s=%v must be >= 1. Zero is not 'no limit' — an empty or never-refilling bucket 429s every request on that route, which is a self-inflicted outage, not a relaxed limit (deploy#1189)." $where $field $v) -}}
{{- end -}}
{{- end -}}
{{- $max := int64 $b.maxTokens -}}
{{- $fill := int64 $b.tokensPerFill -}}
{{- $interval := int64 $b.fillIntervalSeconds -}}
{{- if lt $max $fill -}}
{{- fail (printf "validateRateLimitBucket: %s has maxTokens=%v < tokensPerFill=%v. Envoy caps the bucket at max_tokens, so the refill rate you wrote can never be reached and the effective limit is an emergent number nobody reviewed. Set maxTokens >= tokensPerFill (maxTokens is the burst allowance; tokensPerFill/fillIntervalSeconds is the sustained rate)." $where $max $fill) -}}
{{- end -}}
{{- if gt $fill 1000000 -}}
{{- fail (printf "validateRateLimitBucket: %s.tokensPerFill=%v is effectively unbounded. One Envoy process cannot serve 1e6 requests per fill interval, so this is the limiter switched off, written as a number that reads like a limit. If the intent really is 'do not throttle this route', delete the route's bucket in a reviewed diff and justify it (deploy#1189)." $where $fill) -}}
{{- end -}}
{{- if gt $interval 3600 -}}
{{- fail (printf "validateRateLimitBucket: %s.fillIntervalSeconds=%v exceeds 3600. A refill window measured in hours is a one-shot quota, not a rate limit — the bucket drains and the route is hard-down until the next fill." $where $interval) -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.envoy.validateHttp2MaxConcurrentStreams

Fails the render when envoy.rateLimit.http2MaxConcurrentStreams is unset or
outside 1..65535.

Envoy's downstream default is 2147483647 — one HTTP/2 connection may open
effectively unlimited concurrent streams, which is the cheapest amplification
an attacker has at a TLS edge. Restoring that value through this knob is
indistinguishable in a diff from tuning it, so the upper bound is enforced
here: anything above 65535 is the default wearing a number.
========================================================================= */}}
{{- define "gibson.envoy.validateHttp2MaxConcurrentStreams" -}}
{{- $v := . -}}
{{- if kindIs "invalid" $v -}}
{{- fail "validateHttp2MaxConcurrentStreams: envoy.rateLimit.http2MaxConcurrentStreams is unset. It is required — omitting it restores Envoy's downstream default of 2147483647 concurrent streams per connection (deploy#1189)." -}}
{{- end -}}
{{- if or (kindIs "bool" $v) (ne (printf "%v" $v) (printf "%v" (int64 $v))) -}}
{{- fail (printf "validateHttp2MaxConcurrentStreams: envoy.rateLimit.http2MaxConcurrentStreams=%v is not a whole number." $v) -}}
{{- end -}}
{{- if or (lt (int64 $v) 1) (gt (int64 $v) 65535) -}}
{{- fail (printf "validateHttp2MaxConcurrentStreams: envoy.rateLimit.http2MaxConcurrentStreams=%v must be between 1 and 65535. Envoy's downstream default is 2147483647; a value near it is not a bound at all (deploy#1189)." $v) -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateEntitlementsCoherence

GHSA-455w — fails the render when the entitlements seam is wired half-way.

 gives the platform two deployment profiles, and the daemon reads them
from two independent knobs:

  gibson.entitlementsEndpoint  — dial a remote EntitlementsService (SaaS)
  gibson.entitlementsRequired  — fail CLOSED when that seam does not wire

Set together they are the SaaS profile. Both unset they are the self-hosted
profile, and the OSS unlimited ConfigProvider is the correct answer —
says on-prem must work with no billing backend at all, so `required` must stay
false there and this validator never fires.

The dangerous shape is the third one: endpoint set, required false. The daemon
does `enforceBilling := entitlements.Required()` and short-circuits
withholdPendingTenant when that is false, so the fail-closed quota check
gibson#1270 landed never runs. A transiently-unavailable or misconfigured
entitlements-svc then silently degrades to unlimited — paid tiers provision with
no billing record, in a paying environment, with nothing in the logs that reads
as a failure. Fail-OPEN is the one posture a billing seam must never take.

That shape is not hypothetical: it is what helm/gibson-workloads/values.yaml
defaults to (required: false), and it is what any overlay produces by wiring the
endpoint and forgetting the flag. The umbrella's signup-seam.bats guards the
CI profile fixtures; this guards every real overlay, including ones this repo
never sees.

The inverse (required true, endpoint empty) is also incoherent — the daemon
fails closed with nothing to dial — and fails here too.

Invoked from templates/gibson/statefulset.yaml, where both values are consumed.
========================================================================= */}}
{{- define "gibson.validateEntitlementsCoherence" -}}
{{- $endpoint := trim (default "" .Values.gibson.entitlementsEndpoint) -}}
{{- $required := .Values.gibson.entitlementsRequired -}}
{{- if and $endpoint (not (eq $required true)) -}}
{{- fail (printf "validateEntitlementsCoherence: gibson.entitlementsEndpoint is set (%q) but gibson.entitlementsRequired is %v. That is the SaaS profile with the fail-closed guard OFF: the daemon computes enforceBilling from entitlementsRequired, so withholdPendingTenant is skipped and a failed or unavailable entitlements-svc silently provisions paid tiers with no billing record (GHSA-455w, gibson#1270 §5). Set gibson.entitlementsRequired: true alongside the endpoint. If you meant the self-hosted profile, clear gibson.entitlementsEndpoint instead — on-prem is supposed to run with no billing backend and the unlimited ConfigProvider is correct there." $endpoint $required) -}}
{{- end -}}
{{- if and (eq $required true) (not $endpoint) -}}
{{- fail "validateEntitlementsCoherence: gibson.entitlementsRequired is true but gibson.entitlementsEndpoint is empty. The daemon would fail closed on every provision with no EntitlementsService to dial — a total signup outage, not a safe default. Set the endpoint, or set entitlementsRequired: false for the self-hosted profile." -}}
{{- end -}}
{{- end -}}

{{/* =========================================================================
gibson.validateSetecDispatch

Guards the daemon→setec-frontend leg (deploy#1106).

`gibson.sandbox.enabled` is not a preference. When it is true the daemon
routes every UNTRUSTED tool call through setec and, under the default
setec-only dispatch shape, DENIES the call rather than running it in-process
when that route is unavailable (gibson internal/engine/harness/
dispatchpolicy). So a render that turns it on against a frontend nobody is
installing does not degrade — it takes untrusted tooling offline, and it does
so at the first tool call rather than at install.

Two ways to get there, both of which render clean YAML:

  (1) `gibson.sandbox.enabled` with `setec.enabled=false`, or with the setec
      subchart on but `setec.frontend.enabled=false`. Every derived value —
      the address, the TLS serverName, the client cert — is computed from a
      frontend that will not exist. This is also what makes the
      cert-manager Certificate and the ESO mirror in
      templates/setec/daemon-client-secret.yaml unresolvable: their source
      Secret is never minted, the ExternalSecret sits in SecretSyncedError,
      and the daemon Pod stays Pending on a missing volume.

  (2) `gibson.sandbox.enabled` with no `setec.tenant`. config.SandboxConfig's
      own Validate() rejects an empty tenant, so the daemon exits at startup.
      Failing here names the values key instead.

An install pointing at a setec frontend this chart does not provision is a
real deployment, and it is not this one: it would supply its own
sandbox.setec.address, mtls paths and client Secret, and it can have a values
knob when someone actually runs it. Speculating a second codepath for it now
is what forbids.
========================================================================= */}}
{{- define "gibson.validateSetecDispatch" -}}
{{- $sbx := (.Values.gibson).sandbox | default dict -}}
{{- if $sbx.enabled -}}
{{- $setec := .Values.setec | default dict -}}
{{- if not $setec.enabled -}}
{{- fail "validateSetecDispatch: gibson.sandbox.enabled=true requires setec.enabled=true. The daemon's whole sandbox config — address, TLS serverName, the client keypair it mounts — is derived from the setec subchart's frontend, and with the subchart off none of it exists. Under the default setec-only dispatch shape an unreachable sandbox backend does not fall back to in-process execution; it denies the call, so this renders cleanly and takes untrusted tooling offline at the first invocation. Install setec, or set gibson.sandbox.enabled=false." -}}
{{- end -}}
{{- $frontend := $setec.frontend | default dict -}}
{{- if not $frontend.enabled -}}
{{- fail "validateSetecDispatch: gibson.sandbox.enabled=true requires setec.frontend.enabled=true. The frontend IS the daemon's gRPC endpoint into setec — the operator alone serves no Launch RPC. With it off the daemon dials a Service that is never created, and templates/setec/daemon-client-secret.yaml has no source Secret to mirror, so the daemon Pod stays Pending on a volume that never appears." -}}
{{- end -}}
{{- $tenant := ($sbx.setec | default dict).tenant | default "" -}}
{{- if not $tenant -}}
{{- fail "validateSetecDispatch: gibson.sandbox.enabled=true requires gibson.sandbox.setec.tenant. It is the setec tenant every Launch is attributed to and there is no defensible default; the daemon's own config.SandboxConfig.Validate() refuses to start without it, so leaving it empty trades a render error that names the key for a CrashLoopBackOff that does not." -}}
{{- end -}}
{{- end -}}
{{- end -}}
