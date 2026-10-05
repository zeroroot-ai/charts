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

.PHONY: upgrade-pair openbao-one-replica zitadel-claimed-host contract-pins config-contract-sync config-consumed smtp-tls-mode email-smtp-external-secret openbao-login-diagnosis help values-consumed env-consumed env-contract-sync env-contract-fresh chart-deps chart-deps-retry golden golden-update render-diff baseline-up baseline-verify postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes velero-no-hooks iam-admin-pat-escrow seed-passwords login-brand \
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

substrate-overlays: ## values-eks/gke/aks set only substrate keys: load balancer, storage class, region, DNS-01, DNS provider, workload identity
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

cnpg-netpol-covers-jobs: ## Every pod CNPG creates, bootstrap Jobs included, has an egress-allowing NetworkPolicy
	@./scripts/check-cnpg-netpol-covers-jobs.sh

.PHONY: values-no-duplicate-keys
values-no-duplicate-keys: ## No values file declares a key twice (YAML keeps the last and drops the first, silently)
	@./scripts/check-values-no-duplicate-keys.sh

.PHONY: envoy-anchor
smtp-host-resolves: ## An in-cluster SMTP_HOST names a Service the release renders (charts#114)
	@./scripts/check-smtp-host-resolves.py --selftest

envoy-anchor: ## Every subchart pinning Envoy's ClusterIP gets the discovered value, not the shipped kind default
	@./scripts/check-envoy-anchor-one-value.sh

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

netpol-before-hooks: ## Every hook Job's NetworkPolicy applies in an earlier Argo wave than the Job
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

config-contract-sync: ## Regenerate helm/contracts/gibson-config-keys.txt from the sibling gibson clone, at the pinned tag
	@python3 scripts/gen-config-keys.py --write

config-consumed: ## Every config key the ConfigMap renders is one the gibson daemon binds; viper drops the rest in silence (gibson#599)
	@python3 scripts/check-config-consumed.py --selftest

contract-pins: ## Every vendored contract names the release tag the chart pins for its service (charts#303)
	@python3 scripts/pinned_source.py --selftest
	@python3 scripts/pinned_source.py --check-headers

env-contract-fresh: ## The vendored contracts match their source repos at the pinned tags (needs sibling clones with tags)
	@python3 scripts/gen-env-readers.py --check
	@python3 scripts/gen-config-keys.py --check

probes: ## No smoke or verify probe whose failure is swallowed by || true (charts#17)
	@python3 scripts/check-probes.py --selftest
	@python3 scripts/check-probes.py

probe-timeouts: ## Every container probe the chart renders states its timeoutSeconds, never the 1-second default (charts#306)
	@python3 scripts/check-probe-timeouts.py --selftest
	@python3 scripts/check-probe-timeouts.py

openbao-login-diagnosis: ## A failed OpenBao kubernetes-auth login names which step failed, not just 403 (charts#330)
	@./scripts/check-openbao-login-diagnosis.sh

cg-rotation-window: ## The CG signing-key rotation window renders the previous key, and steady state does not (charts#316)
	@python3 scripts/check-cg-rotation-window.py --selftest
	@python3 scripts/check-cg-rotation-window.py

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

netpol-coverage: ## No workload in the release namespace without a NetworkPolicy (charts#17)
	@python3 scripts/check-netpol-coverage.py --selftest
	@python3 scripts/check-netpol-coverage.py

daemon-netpol-admits-callers: ## Every in-cluster caller of the daemon is admitted by its NetworkPolicy (charts#153)
	@python3 scripts/check-daemon-netpol-admits-callers.py --selftest
	@python3 scripts/check-daemon-netpol-admits-callers.py

edge-strips-instance-headers: ## The Envoy edge never forwards a client's Zitadel instance-selection headers (charts#161, ADR-0092)
	@python3 scripts/check-edge-strips-instance-headers.py --selftest
	@python3 scripts/check-edge-strips-instance-headers.py

edge-jwt-payload-unforgeable: ## A client can never supply x-jwt-payload: jwt_authn strips it on every route before ext_authz reads it
	@python3 scripts/check-edge-jwt-payload-unforgeable.py --selftest
	@python3 scripts/check-edge-jwt-payload-unforgeable.py

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

.PHONY: node-heap-tracks-limit
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

daemon-sa-binding: ## The tenant-operator binds the daemon's real ServiceAccount to gibson-connector-creds, per tenant namespace only (gibson#137)
	@python3 scripts/check-daemon-sa-binding.py --selftest
	@python3 scripts/check-daemon-sa-binding.py

zitadel-claimed-host: ## No claimed Zitadel host carries a port, and no caller forges Host with curl (charts#162, ADR-0092)
	@python3 scripts/check-zitadel-claimed-host.py --selftest
	@python3 scripts/check-zitadel-claimed-host.py

openbao-one-replica: ## The render fails above one OpenBao replica, because the chart stores on a file (hosted#400)
	@./scripts/check-openbao-one-replica.sh

fixture-flag-follows-runner: ## GIBSON_TEST_FIXTURES_ENABLED follows gibson.e2eRunner.enabled, and no shipped values file sets it (gibson#14, charts#332)
	@python3 scripts/check-fixture-flag-follows-runner.py --selftest
	@python3 scripts/check-fixture-flag-follows-runner.py

webhooks: ## Every Fail webhook is probed before activation, every webhook is service-backed, none fail open (charts#17)
	@python3 scripts/check-webhooks.py --selftest
	@python3 scripts/check-webhooks.py

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

check: golden attribution cloud-free smtp-host-resolves chart-deps-retry substrate-overlays subchart-overrides zitadel-lockstep oidcclient-roles instance-admin-roles-scoped signin-policy login-brand tool-image secret-plumbing backup-coverage extauthz-transport servicemonitor-tls envoy-admin-loopback workload-rbac owner-credential-readers operator-rbac-fresh cnpg-superuser-secret secret-reads-granted daemon-sa-binding fixture-flag-follows-runner webhooks edge-config-identical hook-jobs-sh kubeconform image-registry mirror-digests orphan-templates values-consumed env-consumed config-consumed contract-pins probes probe-timeouts helper-docs referenced-paths-exist cg-rotation-window email-smtp-external-secret smtp-tls-mode edge-rate-limits netpol-coverage daemon-netpol-admits-callers edge-strips-instance-headers edge-misdirected-authority edge-access-log-no-credentials node-heap-tracks-limit hostnames app-url-links reloader-namespaced archive-bucket-required postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes velero-no-hooks iam-admin-pat-escrow seed-passwords set-secret-env helm-record-size values-no-duplicate-keys baseline-up-one-path rungs workflows upgrade-pair envoy-anchor platform-owner-values no-owner-password-secret edge-zitadel-routes edge-jwt-payload-unforgeable netpol-before-hooks openbao-login-diagnosis zitadel-claimed-host openbao-one-replica ## Everything that runs without a cluster
	@printf "$(GREEN)  ✓$(NC) check: all offline gates passed\n"

baseline-up: ## Install onto the current kube context
	@./scripts/baseline-up.sh

baseline-verify: ## Prove the install came up
	@./scripts/baseline-verify.sh
