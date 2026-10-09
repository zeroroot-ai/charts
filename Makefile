# =============================================================================
# gibson charts — the on-prem product.
#
# This repo is the chart and nothing else. Provisioning a cluster, running the
# hosted estate and everything else the operator does live in `hosted`, which
# is a CONSUMER of this repo: it installs a published, signed OCI chart at a
# pinned version and holds no chart source.
# =============================================================================
GREEN := \033[0;32m
NC    := \033[0m

.PHONY: upgrade-pair reloader-names operator-rbac-sync operator-rbac-covers identity-admission-covers openbao-one-replica zitadel-claimed-host contract-pins config-contract-sync config-consumed smtp-tls-mode email-smtp-external-secret openbao-login-diagnosis help values-consumed env-consumed env-contract-sync env-contract-fresh chart-deps chart-deps-retry golden golden-update render-diff baseline-up baseline-verify postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes velero-no-hooks iam-admin-pat-escrow seed-passwords login-brand \
        check attribution vendor-operators cloud-free

help: ## Show available targets
	@echo "Usage: make <target>"
	@echo ""
	@echo "  chart-deps        vendor the sub-chart tarballs (needs network)"
	@echo "  golden            snapshot test: every profile, bare and with monitoring CRDs"
	@echo "  golden-update     regenerate the snapshots after an intended change"
	@echo "  render-diff       resource-level delta against a ref (default origin/main)"
	@echo "  check             golden + attribution + cloud-free"
	@echo ""
	@echo "  baseline-up        install onto the CURRENT kube context"
	@echo "  baseline-verify    prove the install came up"

chart-deps: ## Vendor sub-chart tarballs, with retries (hosted#147)
	@for c in helm/gibson-operator-crds helm/gibson-crds helm/gibson-operators helm/gibson-workloads helm/gibson-velero helm/gibson; do \
	  ./scripts/helm-dep-update.sh $$c || exit 1; done
	@printf "$(GREEN)  ✓$(NC) chart-deps: vendored sub-charts are current\n"

chart-deps-retry: ## chart-deps retries a failed download and fails after the last attempt
	@./scripts/check-chart-deps-retry.sh

golden: ## Snapshot test
	@./scripts/golden.sh check

golden-update: ## Regenerate snapshots
	@./scripts/golden.sh update

render-diff: ## Resource-level delta vs a ref
	@./scripts/render-diff.sh $(REF)

attribution: ## Every verbatim redistribution carries its attribution
	@python3 scripts/check-vendored-attribution.py

cloud-free: ## The baseline profile assumes no cloud
	@./scripts/check-baseline-is-cloud-free.sh

substrate-overlays: ## values-eks sets only substrate keys: load balancer, storage class, region, DNS-01, DNS provider, workload identity
	@python3 scripts/check-substrate-overlays.py --selftest
	@python3 scripts/check-substrate-overlays.py

archive-bucket-required: ## No profile ships a default archive bucket; the required guards fire without one
	@./scripts/check-archive-bucket-required.sh

platform-owner-values: ## Platform owner install values are required, must differ, and always reach their owner (ADR-0093)
	@./scripts/check-platform-owner-values.sh

no-owner-password-secret: ## No template ever asks a bootstrap binary to write a password (ADR-0093, hosted#202)
	@./scripts/check-no-owner-password-secret.py

postgres-archive-names: ## A recovered Postgres reads the old archive and writes a new one, never the same
	@./scripts/check-postgres-archive-names.sh

seed-passwords: ## Every password the OpenBao seeder mints is argument-safe (letters and digits, no leading '-')
	@./scripts/check-seed-passwords.sh

set-secret-env: ## baseline-set-secret.sh hands the operator's value to the pod as environment, never as script text
	@./scripts/baseline-set-secret.sh --selftest

cnpg-netpol-covers-jobs: ## Every pod CNPG creates, bootstrap Jobs included, reaches the bucket and the primary under the network policy (D76)
	@./scripts/check-cnpg-netpol-covers-jobs.sh

.PHONY: values-no-duplicate-keys
values-no-duplicate-keys: ## No values file declares a key twice (YAML keeps the last and drops the first, silently)
	@./scripts/check-values-no-duplicate-keys.sh

smtp-host-resolves: ## An in-cluster SMTP_HOST names a Service the release renders (charts#114)
	@./scripts/check-smtp-host-resolves.py --selftest

.PHONY: no-pinned-addressing
no-pinned-addressing: ## No hostAliases, no pinned Envoy address, and no public origin as an unexplained dial target (ADR-0092, charts#163)
	@python3 scripts/check-no-pinned-addressing.py --selftest
	@python3 scripts/check-no-pinned-addressing.py

.PHONY: openbao-token-rotation
openbao-token-rotation: ## A service token older than its lifetime or a rotation request gets a successor, and the old one is revoked after its grace (ADR-0171)
	@# The test renders the umbrella, which reads the PACKAGED gibson-workloads
	@# chart. chart-deps tracks dependency sources, not templates, so repack first.
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/openbao-token-rotation.bats

.PHONY: openbao-seed-inputor
keyring-seal-rotation: ## The seal key rotates in the keyring, and the openbao container writes both keys with their own ids (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/keyring-seal-rotation.bats

openbao-seed-inputor: ## The inputor seed kind takes the keyring value when present and the placeholder when not (charts#486)
	@bats tests/openbao-seed-inputor.bats
.PHONY: egress-host-groups-test
egress-host-groups-test: ## An egress host group of the values renders one policy with each host on its port, and a bad group fails the render (D76)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/egress-host-groups.bats
.PHONY: redis-rotation-test
redis-rotation-test: ## The redis password rotation keeps the old password until each client restarted, and recovers when the store already has the new one (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/redis-rotation.bats

.PHONY: openbao-seed-lifetime
openbao-seed-lifetime: ## A seed key older than its lifetime gets a new generated value, and no other key changes (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/openbao-seed-lifetime.bats

.PHONY: edge-extra-routes
edge-extra-routes: ## Each extra edge route states its auth mode and rate-limit class; the baseline has none and no billing object (charts#375)
	@python3 scripts/check-edge-extra-routes.py

.PHONY: workflows
workflows: ## The workflow files are valid: a bad expression is rejected at dispatch with no jobs and no log
	@./scripts/check-workflows.sh

upgrade-pair: ## The upgrade exit test resolves the version to upgrade FROM by version order, from a complete tag list (charts#155)
	@python3 scripts/resolve-upgrade-pair.py --selftest

.PHONY: rungs
rungs: ## Every rung of the profile ladder renders and shrinks the baseline
	@./scripts/check-rungs.sh

.PHONY: baseline-up-one-path
baseline-up-one-path: ## The chart checkout and the published artifact install the same releases, in the same order
	@./scripts/check-baseline-up-one-path.sh

.PHONY: helm-record-size
helm-record-size: ## Every published chart fits in a Helm release record (one Secret, 1 MiB cap)
	@./scripts/check-helm-record-size.sh

velero-volume-excludes: ## Every pod tells Velero which of its volumes are sockets and scratch, never a claim
	@./scripts/check-velero-volume-excludes.sh

velero-no-hooks: ## The Velero Schedule and BackupStorageLocation are ordinary resources, so a chart bump reaches Argo (charts#190)
	@./scripts/check-velero-no-hooks.sh

iam-admin-pat-escrow: ## The Zitadel IAM_OWNER PAT is escrowed to OpenBao and read back by an ExternalSecret, so a restore can bring it back
	@./scripts/check-iam-admin-pat-escrow.sh

subchart-overrides: ## Every hand-overridden subchart image tag is a declared version link (charts#14)
	@python3 scripts/check-subchart-overrides-declared.py --selftest >/dev/null
	@python3 scripts/check-subchart-overrides-declared.py

login-brand: ## The login-branding Job applies the declared brand and re-applies a changed one (ADR-0064)
	@python3 scripts/check-login-brand.py --selftest
	@python3 scripts/check-login-brand.py

zitadel-lockstep: ## The ZITADEL server tag and the login fork pin name the same release
	@python3 scripts/check-zitadel-lockstep.py --selftest
	@python3 scripts/check-zitadel-lockstep.py

oidcclient-roles: ## Every OIDCClient declares its Zitadel roles explicitly — the empty-list default can never silently apply (hosted#191)
	@python3 scripts/check-oidcclient-roles-explicit.py --selftest
	@python3 scripts/check-oidcclient-roles-explicit.py

instance-admin-roles-scoped: ## No OIDCClient outside the bootstrap identity holds an instance-administrator role (hosted#207)
	@python3 scripts/check-instance-admin-roles-scoped.py --selftest
	@python3 scripts/check-instance-admin-roles-scoped.py

signin-policy: ## The first Zitadel instance starts with MFA forced, no external IdPs, no self-registration (ADR-0093)
	@python3 scripts/check-signin-policy.py --selftest
	@python3 scripts/check-signin-policy.py

netpol-before-hooks: ## Every network policy that selects a hook Job applies in an earlier Argo wave than the Job
	@python3 scripts/check-netpol-before-hooks.py --selftest
	@python3 scripts/check-netpol-before-hooks.py

reloader-namespaced: ## Reloader reads Secrets in its own namespace only: KUBERNETES_NAMESPACE set, no ClusterRole
	@python3 scripts/check-reloader-namespaced.py --selftest
	@python3 scripts/check-reloader-namespaced.py

image-registry: ## Every image this repository names is on ghcr.io (charts#17: check-image-registry + check-no-docker-io)
	@python3 scripts/check-image-registry.py --selftest
	@python3 scripts/check-image-registry.py

mirror-digests: ## Every mirror image in every rendered profile carries its digest
	@python3 scripts/check-mirror-digests.py --selftest
	@python3 scripts/check-mirror-digests.py

orphan-templates: ## No Helm named template that nothing invokes, and none invoked that does nothing (charts#17, charts#293)
	@python3 scripts/check-orphan-templates.py --selftest
	@python3 scripts/check-orphan-templates.py

values-consumed: ## No values key that no template reads (ADR-0094, charts#292)
	@python3 scripts/check-values-consumed.py --selftest
	@python3 scripts/check-values-consumed.py

env-consumed: ## No env var injected into a first-party container that the service never reads (ADR-0094, charts#294)
	@python3 scripts/check-env-consumed.py --selftest
	@python3 scripts/check-env-consumed.py

env-contract-sync: ## Refresh helm/contracts/ from each service's sibling clone, at the tag the chart pins
	@python3 scripts/gen-env-readers.py --write
	@python3 scripts/gen-grpc-methods.py --write

config-contract-sync: ## Regenerate helm/contracts/gibson-config-keys.txt from the sibling gibson clone, at the pinned tag
	@python3 scripts/gen-config-keys.py --write

config-consumed: ## Every config key the ConfigMap renders is one the gibson daemon binds; viper drops the rest in silence (gibson#599)
	@python3 scripts/check-config-consumed.py --selftest

contract-pins: ## Every vendored contract names the release tag the chart pins for its service (charts#303)
	@python3 scripts/pinned_source.py --selftest
	@python3 scripts/pinned_source.py --check-headers

operator-rbac-sync: ## Refresh helm/contracts/gibson-operator-rbac-*.yaml from the sibling gibson clone, at the pinned tag
	@python3 scripts/gen-operator-rbac.py --write

operator-rbac-covers: ## The chart grants each gibson operator at least the role its own controllers declare
	@python3 scripts/check-operator-rbac-covers.py --selftest
	@python3 scripts/check-operator-rbac-covers.py

env-contract-fresh: ## The vendored contracts match their source repos at the pinned tags (needs sibling clones with tags)
	@python3 scripts/gen-env-readers.py --check
	@python3 scripts/gen-config-keys.py --check
	@python3 scripts/gen-operator-rbac.py --check
	@python3 scripts/gen-grpc-methods.py --check

probes: ## No smoke or verify probe whose failure is swallowed by || true (charts#17)
	@python3 scripts/check-probes.py --selftest
	@python3 scripts/check-probes.py

probe-timeouts: ## Every container probe the chart renders states its timeoutSeconds, never the 1-second default (charts#306)
	@python3 scripts/check-probe-timeouts.py --selftest
	@python3 scripts/check-probe-timeouts.py

openbao-login-diagnosis: ## A failed OpenBao kubernetes-auth login names which step failed, not just 403 (charts#330)
	@./scripts/check-openbao-login-diagnosis.sh

openbao-system-key-rotation: ## The Zitadel System API key flips to the other slot, then the old slot gets a new key (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/openbao-system-key-rotation.bats

zitadel-masterkey-rotation: ## The masterkey rotation proves the marker, rewraps each Zitadel key in one transaction, and refuses a marker neither key opens (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/zitadel-masterkey-rotation.bats

openbao-pg-engine: ## The database engine issues one login role for each owner of a platform database, and rewrites a connection only when the superuser credential changes (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@bats tests/openbao-pg-engine.bats

cg-rotation-window: ## The CG signing-key set projects its current, next and previous slots always, with no rotation flag (ADR-0171)
	@rm -f helm/gibson/charts/gibson-workloads-*.tgz helm/gibson/.charts.stamp
	@$(MAKE) -s chart-deps >/dev/null
	@python3 scripts/check-cg-rotation-window.py --selftest
	@python3 scripts/check-cg-rotation-window.py
	@bats tests/openbao-cg-key-rotation.bats

email-smtp-external-secret: ## The daemon's SMTP ExternalSecret renders only with the seam on, and reads both halves of the credential (hosted secret-contract chart-gate)
	@python3 scripts/check-email-smtp-external-secret.py --selftest
	@python3 scripts/check-email-smtp-external-secret.py

smtp-tls-mode: ## The tenant operator's SMTP transport is one of three named modes, and plaintext with a username fails the render (gibson#561, gibson#574)
	@python3 scripts/check-smtp-tls-mode.py --selftest
	@python3 scripts/check-smtp-tls-mode.py

edge-rate-limits: ## Every local_ratelimit enforces, no descriptor keys off an untrusted address, and no quota sits above its fuse
	@python3 scripts/check-edge-rate-limits.py --selftest
	@python3 scripts/check-edge-rate-limits.py

helper-docs: ## Every helper's documentation sits immediately above its own define (charts#304)
	@python3 scripts/check-helper-docs.py --selftest
	@python3 scripts/check-helper-docs.py

referenced-paths-exist: ## No comment names a repo-relative file that is not there
	@python3 scripts/check-referenced-paths-exist.py --selftest
	@python3 scripts/check-referenced-paths-exist.py

secure-pod: ## Each rendered pod meets the six rules of a secure pod, or has an entry with a reason (ADR-0165)
	@python3 scripts/check-secure-pod.py --selftest
	@python3 scripts/check-secure-pod.py

.PHONY: hubble-drops
hubble-drops: ## The Hubble flow check of the exit tests fails on a flow that a policy drops (charts#489)
	@python3 scripts/check-hubble-drops.py --selftest

daemon-netpol-admits-callers: ## The network policy lets every in-cluster caller reach the daemon (charts#153, D76)
	@python3 scripts/check-daemon-netpol-admits-callers.py --selftest
	@python3 scripts/check-daemon-netpol-admits-callers.py

edge-strips-instance-headers: ## The Envoy edge never forwards a client's Zitadel instance-selection headers (charts#161, ADR-0092)
	@python3 scripts/check-edge-strips-instance-headers.py --selftest
	@python3 scripts/check-edge-strips-instance-headers.py

edge-jwt-payload-unforgeable: ## A client can never supply x-jwt-payload: jwt_authn strips it on every route before ext_authz reads it
	@python3 scripts/check-edge-jwt-payload-unforgeable.py --selftest
	@python3 scripts/check-edge-jwt-payload-unforgeable.py

.PHONY: edge-grpc-routes
edge-grpc-routes: ## Each gRPC route of the edge names a service of the gibson authz registry (charts#356)
	@python3 scripts/check-edge-grpc-routes.py --selftest
	@python3 scripts/check-edge-grpc-routes.py

.PHONY: purge-tenant-backup-test purge-tenant-backup
purge-tenant-backup-test: ## The purge script deletes one last backup of a deleted tenant and refuses each other case (charts#419)
	@bats tests/purge-tenant-backup.bats

preflight-kvm-test: ## The install preflight fails on a fleet node with no /dev/kvm, and skips the nodes when the setec seam is off (charts#413, ADR-0083)
	@bats tests/preflight-kvm.bats

purge-tenant-backup: ## Delete the last backup of a deleted tenant: make purge-tenant-backup TENANT=<name> [TENANT_UID=<uid>]
	@scripts/purge-tenant-backup.sh "$(TENANT)"

.PHONY: no-render-time-secrets
no-render-time-secrets: ## No first-party template calls lookup or a random function, or renders a Secret with data (ADR-0014, charts#366)
	@python3 scripts/check-no-render-time-secrets.py --selftest
	@python3 scripts/check-no-render-time-secrets.py

.PHONY: trust-domain-literal
trust-domain-literal: ## No file holds the SaaS trust domain as a SPIFFE ID literal, and the render follows global.spire.trustDomain (ADR-0164, charts#392)
	@python3 scripts/check-trust-domain-literal.py --selftest
	@python3 scripts/check-trust-domain-literal.py

.PHONY: optional-references
optional-references: ## Each optional reference and empty default of a template has a recorded reason (ADR-0003, charts#359)
	@python3 scripts/check-optional-references.py --selftest
	@python3 scripts/check-optional-references.py

.PHONY: no-closed-images
no-closed-images: ## The render of each published profile names no closed image (ADR-0074, charts#373)
	@python3 scripts/check-no-closed-images.py --selftest
	@python3 scripts/check-no-closed-images.py

.PHONY: operator-crd-bundle
operator-crd-bundle: ## The CRD bundle holds each kind the templates render, and only operators the umbrella installs (charts#370)
	@python3 scripts/check-operator-crd-bundle.py --selftest
	@python3 scripts/check-operator-crd-bundle.py

.PHONY: alert-runbooks
alert-runbooks: ## Each alert rule names a runbook section under docs/runbooks/alerts/ that exists (charts#410)
	@python3 scripts/check-alert-runbooks.py --selftest
	@python3 scripts/check-alert-runbooks.py

.PHONY: registration-rung
registration-rung: ## One value selects the registration rung, and the daemon and the dashboard both get it (ADR-0074, charts#374)
	@python3 scripts/check-registration-rung.py --selftest
	@python3 scripts/check-registration-rung.py

.PHONY: extauthz-redis
extauthz-redis: ## The ext-authz pod of each profile has the Redis address, the password and the Redis wait (ADR-0045, charts#397)
	@python3 scripts/check-extauthz-redis.py --selftest
	@python3 scripts/check-extauthz-redis.py

.PHONY: seam-conditions
seam-conditions: ## Each dependency condition is a named seam that defaults on, and the render refuses setec off (charts#381)
	@python3 scripts/check-seam-conditions.py --selftest
	@python3 scripts/check-seam-conditions.py
.PHONY: kvm-fleet-selector
kvm-fleet-selector: ## The KVM preflight default selector is the nodeSelector of the setec device plugin (charts#508)
	@python3 scripts/check-kvm-fleet-selector.py --selftest
	@python3 scripts/check-kvm-fleet-selector.py

.PHONY: fga-init-seeds
fga-init-seeds: ## fga-init seeds signup_service for the dashboard service identity and platform_operator for the operators only, and prunes each (charts#485)
	@python3 scripts/check-fga-init-seeds.py --selftest
	@python3 scripts/check-fga-init-seeds.py

.PHONY: fga-tls
fga-tls: ## The daemon dials OpenFGA over TLS with the mounted CA in each render (gibson fix/fga-client-uses-tls)
	@python3 scripts/check-fga-tls.py --selftest
	@python3 scripts/check-fga-tls.py

.PHONY: network-peers
cilium-policy-shape: ## Each Cilium policy of each render has the shape the CRD accepts: no specs under spec (charts#494)
	@python3 scripts/check-cilium-policy-shape.py --selftest
	@python3 scripts/check-cilium-policy-shape.py
.PHONY: cilium-policy-shape

network-peers: ## Each ingress peer names its namespace, each outside host its port, and the object store names the buckets of the install (D76)
	@python3 scripts/check-network-peers.py --selftest
	@python3 scripts/check-network-peers.py
.PHONY: metrics-policy
metrics-policy: ## The cluster scraper reaches only the metrics port of each pod, from one pinned namespace, and never an API port (D76)
	@python3 scripts/check-metrics-policy.py --selftest
	@python3 scripts/check-metrics-policy.py

.PHONY: externalsecret-store-namespace
externalsecret-store-namespace: ## Each ExternalSecret sits in a namespace that its store serves, and no ClusterSecretStore serves every namespace (charts#504)
	@python3 scripts/check-externalsecret-store-namespace.py --selftest
	@python3 scripts/check-externalsecret-store-namespace.py

.PHONY: alert-series-scraped
alert-series-scraped: ## Each series an alert rule reads has a scrape object on a port that the metrics rule opens (charts#515)
	@python3 scripts/check-alert-series-scraped.py --selftest
	@python3 scripts/check-alert-series-scraped.py
.PHONY: openbao-tls
openbao-tls: ## OpenBao serves only TLS from the chart CA, and each client dials https and mounts the CA (ADR-0027)
	@python3 scripts/check-openbao-tls.py --selftest
	@python3 scripts/check-openbao-tls.py

.PHONY: namespace-policy
namespace-policy: ## Each namespace a render creates or fills with pods has a default deny (D76)
	@python3 scripts/check-namespace-policy.py --selftest
	@python3 scripts/check-namespace-policy.py

.PHONY: openbao-policies
openbao-policies: ## No OpenBao policy grants path "*", sudo only on named paths, one policy for each token (ADR-0032)
	@python3 scripts/check-openbao-policies.py --selftest
	@python3 scripts/check-openbao-policies.py

.PHONY: entitlements-identity
entitlements-identity: ## With the entitlements endpoint set, the daemon pins the SPIFFE ID of the entitlements service
	@python3 scripts/check-entitlements-identity.py --selftest
	@python3 scripts/check-entitlements-identity.py

.PHONY: connector-proxy-caller
connector-proxy-caller: ## The connector-operator gets the issuer, the JWKS and the daemon ID that each connector proxy checks
	@python3 scripts/check-connector-proxy-caller.py --selftest
	@python3 scripts/check-connector-proxy-caller.py

.PHONY: connector-grant-alert
connector-grant-alert: ## An unrevoked connector grant raises an alert, and Prometheus scrapes its metric
	@python3 scripts/check-connector-grant-alert.py --selftest
	@python3 scripts/check-connector-grant-alert.py

.PHONY: email-one-value
email-one-value: ## One mail value, global.email: each old mail key fails the render, and the three senders agree (hosted#223)
	@python3 scripts/check-email-one-value.py --selftest
	@python3 scripts/check-email-one-value.py

.PHONY: platform-operator-audit
platform-operator-audit: ## The platform-operator has the daemon env, the SPIRE socket, its identity and a daemon allow-list entry (gibson#583)
	@python3 scripts/check-platform-operator-audit.py --selftest
	@python3 scripts/check-platform-operator-audit.py

.PHONY: secret-contract
secret-contract: ## Every secret has a producer AND a consumer, both directions (charts#433, from hosted#350)
	@python3 scripts/check-secret-contract.py --selftest
	@python3 scripts/check-secret-contract.py

.PHONY: egress-policy-type
egress-policy-type: ## One policy type, Cilium, and each policy rule selects a pod (ADR-0165 rule 4, D76, charts#395)
	@python3 scripts/check-egress-policy-type.py --selftest
	@python3 scripts/check-egress-policy-type.py

.PHONY: alert-rules-test
alert-rules-test: ## Each alert rule with a test in tests/alerts fires as its promtool test says (charts#446)
	@./scripts/check-alert-rules-test.sh --selftest
	@./scripts/check-alert-rules-test.sh

.PHONY: edge-zitadel-routes edge-zitadel-routes-live
edge-zitadel-routes: ## The edge sends only listed routes to Zitadel, and refuses a user's own email or username change (ADR-0093)
	@python3 scripts/check-edge-zitadel-routes.py --selftest
	@python3 scripts/check-edge-zitadel-routes.py

edge-zitadel-routes-live: ## The same requests against a running edge: EDGE_URL=https://app.<domain>[:port] [EDGE_RESOLVE=..] [EDGE_INSECURE=1] [ZITADEL_TOKEN=..]
	@python3 scripts/check-edge-zitadel-routes.py --live

.PHONY: edge-misdirected-authority edge-misdirected-authority-live
edge-misdirected-authority: ## Every browser-facing edge chain answers a coalesced HTTP/2 request (wrong SNI for the :authority) with 421, never 404 or 401
	@python3 scripts/check-edge-misdirected-authority.py --selftest
	@python3 scripts/check-edge-misdirected-authority.py

edge-misdirected-authority-live: ## The same against a running edge: EDGE_URL=https://app.<domain>[:port] API_HOST=api.<domain> [EDGE_RESOLVE=a,b] [EDGE_INSECURE=1]
	@python3 scripts/check-edge-misdirected-authority.py --live
.PHONY: edge-access-log-no-credentials
edge-access-log-no-credentials: ## No Envoy access log records a credential header's value (Authorization keeps its scheme word only, cookies never)
	@python3 scripts/check-edge-access-log-no-credentials.py --selftest
	@python3 scripts/check-edge-access-log-no-credentials.py

.PHONY: daemon-address-qualified
daemon-address-qualified: ## Each daemon address that an operator hands out names its Service, namespace and port (charts#526)
	@python3 scripts/check-daemon-address-qualified.py --selftest
	@python3 scripts/check-daemon-address-qualified.py

.PHONY: node-heap-tracks-limit
.PHONY: belief-trainer-image
belief-trainer-image: ## The tenant operator gets BELIEF_TRAINER_IMAGE, the daemon image by default (gibson#31)
	@python3 scripts/check-belief-trainer-image.py --selftest
	@python3 scripts/check-belief-trainer-image.py

node-heap-tracks-limit: ## Every Node server bounds its V8 heap inside its own memory limit (dashboard#150)
	@python3 scripts/check-node-heap-tracks-limit.py --selftest
	@python3 scripts/check-node-heap-tracks-limit.py

hostnames: ## No literal platform hostname; every host derives from global.domain (charts#17)
	@python3 scripts/check-hostnames.py --selftest
	@python3 scripts/check-hostnames.py

app-url-links: ## An explicit gibson.appUrl override is never the API-plane host or an in-cluster address
	@./scripts/check-app-url-links.sh

secret-plumbing: ## Every Secret a workload references is rendered, or has a recorded runtime producer (charts#17)
	@python3 scripts/check-secret-plumbing.py --selftest
	@python3 scripts/check-secret-plumbing.py

backup-coverage: ## No datastore without a backup producer: CNPG backup + ScheduledBackup, Velero fs-backup for every claim (charts#17)
	@python3 scripts/check-backup-coverage.py --selftest
	@python3 scripts/check-backup-coverage.py

extauthz-transport: ## Every URL ext-authz trusts is https (charts#17)
	@python3 scripts/check-extauthz-transport.py --selftest
	@python3 scripts/check-extauthz-transport.py

servicemonitor-tls: ## Every https ServiceMonitor scrape names its CA and none sets insecureSkipVerify
	@python3 scripts/check-servicemonitor-tls.py --selftest
	@python3 scripts/check-servicemonitor-tls.py

envoy-admin-loopback: ## Envoy admin binds 127.0.0.1; the pod-IP listener on :9901 serves /ready and /stats/prometheus only
	@python3 scripts/check-envoy-admin-loopback.py --selftest
	@python3 scripts/check-envoy-admin-loopback.py

workload-rbac: ## Every grant to a ServiceAccount is on helm/gibson/rbac-allowlist.yaml with its rules digest (charts#17)
	@python3 scripts/check-workload-rbac.py --selftest
	@python3 scripts/check-workload-rbac.py

.PHONY: secret-reads-granted
secret-reads-granted: ## Every `kubectl get secret` in a rendered pod is granted to that pod's ServiceAccount
	@python3 scripts/check-secret-reads-granted.py --selftest
	@python3 scripts/check-secret-reads-granted.py

.PHONY: owner-credential-readers owner-credential-readers-live
owner-credential-readers: ## Only bootstrap reads the Zitadel owner credentials, and no workload can exec into, patch, mint a token for or bind over one that does
	@python3 scripts/check-owner-credential-readers.py --selftest
	@python3 scripts/check-owner-credential-readers.py

owner-credential-readers-live: ## The same check against the current kube context: kubectl auth can-i and a SelfSubjectRulesReview for every ServiceAccount
	@python3 scripts/check-owner-credential-readers.py --live

connector-creds-binding: ## The tenant-operator binds the connector operator's ServiceAccount to gibson-connector-creds per tenant namespace, and the daemon holds no grant (gibson#664) (gibson#137)
	@python3 scripts/check-connector-creds-binding.py --selftest
	@python3 scripts/check-connector-creds-binding.py

zitadel-claimed-host: ## No claimed Zitadel host carries a port, and no caller forges Host with curl (charts#162, ADR-0092)
	@python3 scripts/check-zitadel-claimed-host.py --selftest
	@python3 scripts/check-zitadel-claimed-host.py

openbao-one-replica: ## The render fails above one OpenBao replica, because the chart stores on a file (hosted#400)
	@./scripts/check-openbao-one-replica.sh

identity-admission-covers: ## Every identity the chart registers is one the admission policy protects
	@python3 scripts/check-identity-admission-covers.py --selftest
	@python3 scripts/check-identity-admission-covers.py

reloader-names: ## Every Reloader annotation names a Secret or a ConfigMap that exists (charts#411)
	@python3 scripts/check-reloader-names.py --selftest
	@python3 scripts/check-reloader-names.py

fixture-flag-follows-runner: ## GIBSON_TEST_FIXTURES_ENABLED follows gibson.e2eRunner.enabled, and no shipped values file sets it (gibson#14, charts#332)
	@python3 scripts/check-fixture-flag-follows-runner.py --selftest
	@python3 scripts/check-fixture-flag-follows-runner.py

webhooks: ## Every Fail webhook is probed before activation, every webhook is service-backed, and only the two accepted webhooks use Ignore (charts#17, ADR-0076)
	@python3 scripts/check-webhooks.py --selftest
	@python3 scripts/check-webhooks.py

.PHONY: namespace-label-admission
namespace-label-admission: ## Each namespace label that a network rule trusts has an admission policy that limits its writer (charts#527)
	@python3 scripts/check-namespace-label-admission.py --selftest
	@python3 scripts/check-namespace-label-admission.py

.PHONY: plugin-namespace-role
plugin-namespace-role: ## The tenant-operator may write each object of a plugin instance, and no Secret (gibson#815)
	@python3 scripts/check-plugin-namespace-role.py --selftest
	@python3 scripts/check-plugin-namespace-role.py

.PHONY: spiffeid-selectors
spiffeid-selectors: ## Each ClusterSPIFFEID names its workload selectors, or its name is on the list in the guard (charts#358)
	@python3 scripts/check-spiffeid-selectors.py --selftest
	@python3 scripts/check-spiffeid-selectors.py

edge-config-identical: ## The Envoy edge renders identically on every substrate, one edge (ADR-0079, charts#17)
	@python3 scripts/check-edge-config-identical.py --selftest
	@python3 scripts/check-edge-config-identical.py

hook-jobs-sh: ## A hook Job that runs under sh is POSIX sh (shellcheck sh mode, charts#17)
	@python3 scripts/check-hook-jobs-sh.py --selftest
	@python3 scripts/check-hook-jobs-sh.py

kubeconform: ## The umbrella and velero renders validate against the Kubernetes and CRD schemas (charts#17)
	@./scripts/check-kubeconform.sh --selftest

tool-image: ## No template names the alpine-k8s tool image by hand; it renders gibson.toolImage
	@python3 scripts/check-no-literal-tool-image.py --selftest
	@python3 scripts/check-no-literal-tool-image.py

vendor-operators: ## Re-vendor the third-party CRDs and operator RBAC from the pinned sub-charts
	@python3 scripts/vendor-operator-crds.py
	@python3 scripts/vendor-operator-rbac.py

cnpg-superuser-secret: ## One Secret sets the password of the role postgres: a CNPG initdb owner postgres uses the superuser Secret
	@python3 scripts/check-cnpg-superuser-secret.py --selftest
	@python3 scripts/check-cnpg-superuser-secret.py

operator-rbac-fresh: ## The vendored cert-manager and External Secrets RBAC matches the pinned sub-charts, less the rules REMOVED_RULES names
	@python3 scripts/vendor-operator-rbac.py --selftest
	@python3 scripts/vendor-operator-rbac.py --check

check: golden attribution cloud-free smtp-host-resolves chart-deps-retry substrate-overlays subchart-overrides zitadel-lockstep oidcclient-roles instance-admin-roles-scoped signin-policy login-brand tool-image secret-plumbing backup-coverage extauthz-transport servicemonitor-tls envoy-admin-loopback workload-rbac owner-credential-readers operator-rbac-fresh cnpg-superuser-secret secret-reads-granted connector-creds-binding fixture-flag-follows-runner webhooks edge-config-identical hook-jobs-sh kubeconform image-registry mirror-digests orphan-templates values-consumed env-consumed config-consumed contract-pins probes probe-timeouts helper-docs referenced-paths-exist cg-rotation-window email-smtp-external-secret smtp-tls-mode edge-rate-limits secure-pod daemon-netpol-admits-callers edge-strips-instance-headers edge-misdirected-authority edge-access-log-no-credentials node-heap-tracks-limit belief-trainer-image daemon-address-qualified hostnames app-url-links reloader-namespaced archive-bucket-required postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes velero-no-hooks iam-admin-pat-escrow seed-passwords set-secret-env helm-record-size values-no-duplicate-keys baseline-up-one-path rungs workflows upgrade-pair platform-owner-values no-owner-password-secret edge-zitadel-routes edge-grpc-routes edge-jwt-payload-unforgeable netpol-before-hooks openbao-login-diagnosis zitadel-claimed-host openbao-one-replica identity-admission-covers operator-rbac-covers reloader-names purge-tenant-backup-test preflight-kvm-test no-render-time-secrets spiffeid-selectors trust-domain-literal optional-references no-closed-images operator-crd-bundle alert-runbooks registration-rung extauthz-redis secret-contract egress-policy-type no-pinned-addressing openbao-seed-inputor keyring-seal-rotation openbao-seed-lifetime openbao-system-key-rotation openbao-token-rotation redis-rotation-test egress-host-groups-test hubble-drops fga-tls fga-init-seeds externalsecret-store-namespace metrics-policy network-peers cilium-policy-shape openbao-tls alert-series-scraped namespace-policy openbao-policies entitlements-identity connector-proxy-caller connector-grant-alert email-one-value platform-operator-audit alert-rules-test plugin-namespace-role namespace-label-admission edge-extra-routes zitadel-masterkey-rotation openbao-pg-engine ## Everything that runs without a cluster
	@printf "$(GREEN)  ✓$(NC) check: all offline gates passed\n"

baseline-up: ## Install onto the current kube context
	@./scripts/baseline-up.sh

baseline-verify: ## Prove the install came up
	@./scripts/baseline-verify.sh
