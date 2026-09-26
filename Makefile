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

.PHONY: help chart-deps chart-deps-retry golden golden-update render-diff baseline-up baseline-verify postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes iam-admin-pat-escrow seed-passwords login-brand \
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

postgres-archive-names: ## A recovered Postgres reads the old archive and writes a new one, never the same
	@./scripts/check-postgres-archive-names.sh

reaper-fails-closed: ## The orphan reaper deletes nothing when it cannot prove who created a user
	@./scripts/check-orphan-reaper-fails-closed.sh

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
envoy-anchor: ## Every subchart pinning Envoy's ClusterIP gets the discovered value, not the shipped kind default
	@./scripts/check-envoy-anchor-one-value.sh

.PHONY: workflows
workflows: ## The workflow files are valid: a bad expression is rejected at dispatch with no jobs and no log
	@./scripts/check-workflows.sh

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

reloader-namespaced: ## Reloader reads Secrets in its own namespace only: KUBERNETES_NAMESPACE set, no ClusterRole
	@python3 scripts/check-reloader-namespaced.py --selftest
	@python3 scripts/check-reloader-namespaced.py

image-registry: ## Every image this repository names is on ghcr.io (charts#17: check-image-registry + check-no-docker-io)
	@python3 scripts/check-image-registry.py --selftest
	@python3 scripts/check-image-registry.py

mirror-digests: ## Every mirror image in every rendered profile carries its digest
	@python3 scripts/check-mirror-digests.py --selftest
	@python3 scripts/check-mirror-digests.py

orphan-templates: ## No Helm named template that nothing invokes (charts#17)
	@python3 scripts/check-orphan-templates.py --selftest
	@python3 scripts/check-orphan-templates.py

probes: ## No smoke or verify probe whose failure is swallowed by || true (charts#17)
	@python3 scripts/check-probes.py --selftest
	@python3 scripts/check-probes.py

netpol-coverage: ## No workload in the release namespace without a NetworkPolicy (charts#17)
	@python3 scripts/check-netpol-coverage.py --selftest
	@python3 scripts/check-netpol-coverage.py

daemon-netpol-admits-callers: ## Every in-cluster caller of the daemon is admitted by its NetworkPolicy (charts#153)
	@python3 scripts/check-daemon-netpol-admits-callers.py --selftest
	@python3 scripts/check-daemon-netpol-admits-callers.py

edge-strips-instance-headers: ## The Envoy edge never forwards a client's Zitadel instance-selection headers (charts#161, ADR-0092)
	@python3 scripts/check-edge-strips-instance-headers.py --selftest
	@python3 scripts/check-edge-strips-instance-headers.py

hostnames: ## No literal platform hostname; every host derives from global.domain (charts#17)
	@python3 scripts/check-hostnames.py --selftest
	@python3 scripts/check-hostnames.py

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

.PHONY: owner-credential-readers owner-credential-readers-live
owner-credential-readers: ## Only bootstrap reads the Zitadel owner credentials: no new mount, env or RBAC read of iam-admin-pat, iam-admin or the System API key
	@python3 scripts/check-owner-credential-readers.py --selftest
	@python3 scripts/check-owner-credential-readers.py

owner-credential-readers-live: ## The same check against the current kube context, with kubectl auth can-i for every ServiceAccount
	@python3 scripts/check-owner-credential-readers.py --live

daemon-sa-binding: ## The tenant-operator binds the daemon's real ServiceAccount to gibson-connector-creds, per tenant namespace only (gibson#137)
	@python3 scripts/check-daemon-sa-binding.py --selftest
	@python3 scripts/check-daemon-sa-binding.py

fixture-flag-follows-runner: ## The daemon's GIBSON_TEST_FIXTURES_ENABLED follows gibson.e2eRunner.enabled, on and off (gibson#14)
	@python3 scripts/check-fixture-flag-follows-runner.py --selftest
	@python3 scripts/check-fixture-flag-follows-runner.py

webhooks: ## Every Fail webhook is probed before activation, every webhook is service-backed, none fail open (charts#17)
	@python3 scripts/check-webhooks.py --selftest
	@python3 scripts/check-webhooks.py

edge-config-identical: ## The Envoy edge renders identically on every substrate, one edge (ADR-0011, charts#17)
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

vendor-operators: ## Re-vendor the third-party CRDs from the pinned sub-charts
	@python3 scripts/vendor-operator-crds.py

check: golden attribution cloud-free chart-deps-retry substrate-overlays subchart-overrides zitadel-lockstep login-brand tool-image secret-plumbing backup-coverage extauthz-transport servicemonitor-tls envoy-admin-loopback workload-rbac owner-credential-readers daemon-sa-binding fixture-flag-follows-runner webhooks edge-config-identical hook-jobs-sh kubeconform image-registry mirror-digests orphan-templates probes netpol-coverage daemon-netpol-admits-callers edge-strips-instance-headers hostnames reloader-namespaced archive-bucket-required postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes iam-admin-pat-escrow reaper-fails-closed seed-passwords set-secret-env helm-record-size values-no-duplicate-keys baseline-up-one-path rungs workflows envoy-anchor ## Everything that runs without a cluster
	@printf "$(GREEN)  ✓$(NC) check: all offline gates passed\n"

baseline-up: ## Install onto the current kube context
	@./scripts/baseline-up.sh

baseline-verify: ## Prove the install came up
	@./scripts/baseline-verify.sh
