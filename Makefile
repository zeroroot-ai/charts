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

.PHONY: help chart-deps golden golden-update render-diff vanilla-up vanilla-verify postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes \
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
	@echo "  vanilla-up        install onto the CURRENT kube context"
	@echo "  vanilla-verify    prove the install came up"

chart-deps: ## Vendor sub-chart tarballs
	@./scripts/helm-dep-update.sh 2>/dev/null || { \
	  for c in helm/gibson-crds helm/gibson-operators helm/gibson-workloads helm/gibson-velero helm/gibson; do \
	    helm dependency update $$c >/dev/null || exit 1; done; }
	@printf "$(GREEN)  ✓$(NC) chart-deps: vendored sub-charts are current\n"

golden: ## Snapshot test
	@./scripts/golden.sh check

golden-update: ## Regenerate snapshots
	@./scripts/golden.sh update

render-diff: ## Resource-level delta vs a ref
	@./scripts/render-diff.sh $(REF)

attribution: ## Every verbatim redistribution carries its attribution
	@python3 scripts/check-vendored-attribution.py

cloud-free: ## The vanilla profile assumes no cloud
	@./scripts/check-vanilla-is-cloud-free.sh

postgres-archive-names: ## A recovered Postgres reads the old archive and writes a new one, never the same
	@./scripts/check-postgres-archive-names.sh

cnpg-netpol-covers-jobs: ## Every pod CNPG creates, bootstrap Jobs included, has an egress-allowing NetworkPolicy
	@./scripts/check-cnpg-netpol-covers-jobs.sh

velero-volume-excludes: ## Every pod tells Velero which of its volumes are sockets and scratch, never a claim
	@./scripts/check-velero-volume-excludes.sh

subchart-overrides: ## Every hand-overridden subchart image tag is a declared version link (charts#14)
	@python3 scripts/check-subchart-overrides-declared.py --selftest >/dev/null
	@python3 scripts/check-subchart-overrides-declared.py

zitadel-lockstep: ## The ZITADEL server tag and the login fork pin name the same release
	@python3 scripts/check-zitadel-lockstep.py --selftest
	@python3 scripts/check-zitadel-lockstep.py

tool-image: ## No template names the alpine-k8s tool image by hand; it renders gibson.toolImage
	@python3 scripts/check-no-literal-tool-image.py --selftest
	@python3 scripts/check-no-literal-tool-image.py

vendor-operators: ## Re-vendor the third-party CRDs from the pinned sub-charts
	@python3 scripts/vendor-operator-crds.py

check: golden attribution cloud-free subchart-overrides zitadel-lockstep tool-image postgres-archive-names cnpg-netpol-covers-jobs velero-volume-excludes ## Everything that runs without a cluster
	@printf "$(GREEN)  ✓$(NC) check: all offline gates passed\n"

vanilla-up: ## Install onto the current kube context
	@./scripts/vanilla-up.sh

vanilla-verify: ## Prove the install came up
	@./scripts/vanilla-verify.sh
