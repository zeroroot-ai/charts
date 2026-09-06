# gibson-crds Makefile — sync CRDs from sibling operator repos.
#
# Usage:
#   make sync-crds          # copies CRDs from tenant-operator + platform-operator
#   make lint               # helm lint
#   make template           # helm template (sanity)

CHART_DIR := $(CURDIR)
TENANT_OP_CRDS := ../../../platform/gibson/operators/tenant/config/crd/bases
PLATFORM_OP_CRDS := ../../../platform/gibson/operators/platform/config/crd/bases
CONNECTOR_OP_CRDS := ../../../platform/gibson/operators/connector/config/crd/bases

.PHONY: sync-crds lint template clean

sync-crds:
	@echo "→ Syncing tenant-operator CRDs"
	@for f in $(TENANT_OP_CRDS)/*.yaml; do \
	  name=$$(basename $$f); \
	  cp $$f templates/$$name; \
	  echo "  copied $$name"; \
	done
	@echo "→ Syncing platform-operator CRDs"
	@for f in $(PLATFORM_OP_CRDS)/*.yaml; do \
	  name=$$(basename $$f); \
	  cp $$f templates/$$name; \
	  echo "  copied $$name"; \
	done
	@echo "→ Syncing connector-operator CRDs"
	@for f in $(CONNECTOR_OP_CRDS)/*.yaml; do \
	  name=$$(basename $$f); \
	  cp $$f templates/$$name; \
	  echo "  copied $$name"; \
	done
	@echo "→ Adding helm.sh/resource-policy: keep to every CRD"
	@for f in templates/*.yaml; do \
	  $(MAKE) -s _annotate FILE=$$f; \
	done

# Internal: insert resource-policy annotation into a CRD if not present.
_annotate:
	@grep -q "resource-policy" $(FILE) || \
	  sed -i 's|annotations:|annotations:\n    "helm.sh/resource-policy": keep|' $(FILE) 2>/dev/null || \
	  sed -i '/^metadata:/a\  annotations:\n    "helm.sh/resource-policy": keep' $(FILE)

lint:
	helm lint $(CHART_DIR)

template:
	helm template gibson-crds $(CHART_DIR)

clean:
	@rm -f templates/*.yaml
