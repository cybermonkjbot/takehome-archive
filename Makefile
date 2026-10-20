SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

IMAGES := web-app:0.1.0 api:0.1.0 collector:0.1.0
TRIVY_SEVERITY := CRITICAL,HIGH
TRIVY_IGNORE := .trivyignore.yaml
SBOM_OUTPUT := build/source-inventory.cyclonedx.json

BIN_TRIVY := $(shell command -v trivy 2>/dev/null)
BIN_BANDIT := $(shell command -v bandit 2>/dev/null)
BIN_PIP_AUDIT := $(shell command -v pip-audit 2>/dev/null)
BIN_BUN := $(shell command -v bun 2>/dev/null)
BIN_DOCKER := $(shell command -v docker 2>/dev/null)

.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: start
start: ## One-script start for the full local stack (API + Web)
	@./start.sh

.PHONY: start-k8s
start-k8s: ## One-script deploy and port-forward on Kubernetes
	@./start.sh k8s

.PHONY: check
check: sast scan-deps sbom ## Run source checks (requires Bandit, pip-audit, Trivy, Bun, and PyYAML)

.PHONY: build-web
build-web: ## Build the web dashboard assets
	@bun run build

.PHONY: check-tools
check-tools: ## Check CLI tool availability
	@printf "bun: %s | python3: %s | docker: %s | trivy: %s | bandit: %s | pip-audit: %s\n" \
		"$$(command -v bun || echo 'no')" \
		"$$(command -v python3 || echo 'no')" \
		"$$(command -v docker || echo 'no')" \
		"$$(command -v trivy || echo 'no')" \
		"$$(command -v bandit || echo 'no')" \
		"$$(command -v pip-audit || echo 'no')"

.PHONY: sast
sast: ## Run Bandit on Python and parse Kubernetes YAML
	@test -n "$(BIN_BANDIT)" || { echo "Error: bandit is required; syntax compilation is not a SAST substitute."; exit 1; }
	@bandit -r api/ collector/ datastore/ -ll -ii
	@python3 -c "import glob, yaml; [list(yaml.safe_load_all(open(f))) for f in glob.glob('k8s/**/*.yaml', recursive=True)]"

.PHONY: scan-deps
scan-deps: ## Scan Python dependencies and verify Bun lockfile
	@test -n "$(BIN_PIP_AUDIT)" || { echo "Error: pip-audit is required."; exit 1; }
	@test -n "$(BIN_TRIVY)" || { echo "Error: trivy is required."; exit 1; }
	@test -n "$(BIN_BUN)" || { echo "Error: bun is required to verify bun.lock."; exit 1; }
	@pip-audit -r datastore/requirements.lock --desc --disable-pip
	@trivy fs --scanners vuln --exit-code 1 datastore/requirements.lock
	@trivy fs --scanners vuln --exit-code 1 bun.lock
	@bun pm hash >/dev/null

.PHONY: scan-images
scan-images: ## Scan built container images with Trivy against .trivyignore.yaml
	@test -n "$(BIN_TRIVY)" || { echo "Error: trivy is required to scan images."; exit 1; }
	@for img in $(IMAGES); do \
		trivy image --exit-code 1 --severity $(TRIVY_SEVERITY) --ignorefile $(TRIVY_IGNORE) --pkg-types os,library "$$img"; \
	done

.PHONY: sbom
sbom: ## Generate a source-filesystem CycloneDX inventory with Trivy
	@test -n "$(BIN_TRIVY)" || { echo "Error: trivy is required to generate an SBOM."; exit 1; }
	@mkdir -p build
	@trivy fs --format cyclonedx --output $(SBOM_OUTPUT) .

