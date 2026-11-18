# InferRoute — local LLM-aware routing testbed on kind.
#
#   make up        create the cluster and deploy replicas + router (~3 min first time)
#   make smoke     send one request through the gateway
#   make spread    send N requests and show how many each replica served
#   make status    list pods
#   make experiment  run Experiment 1 (all 3 policies, same traffic)
#   make analyze RUN=results/<dir>  summary table + charts for one run
#   make down      delete the cluster
#
# Switch routing policy with scripts/set-policy.sh (see policies/README.md).

CLUSTER     := inferroute
NS          := inferroute
CTX         := kind-$(CLUSTER)
KCTL        := kubectl --context $(CTX) -n $(NS)
GATEWAY_URL := http://localhost:30080
POLICY      ?= queue-cache-aware

# Gateway API Inference Extension CRDs (defines the InferencePool type).
GIE_CRDS := https://github.com/kubernetes-sigs/gateway-api-inference-extension/releases/download/v1.5.0/manifests.yaml

IMAGES := \
	ghcr.io/llm-d/llm-d-inference-sim:v0.9.2 \
	ghcr.io/llm-d/llm-d-router-endpoint-picker:v0.9.0 \
	envoyproxy/envoy:distroless-v1.33.2

# Docker image platform: arm64 on Apple Silicon, amd64 on Intel/AMD.
# Override if detection is wrong: make up ARCH=amd64
ARCH ?= $(shell uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')

.PHONY: up down images smoke spread status logs experiment analyze

up: images
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || \
		kind create cluster --config deploy/kind-config.yaml --wait 120s
	@# Load images from the host instead of pulling inside the cluster.
	@# Newer Docker (containerd image store) breaks `kind load docker-image`
	@# for multi-arch images, so there we save a single-platform archive.
	@# Older Docker has no `docker save --platform`; plain kind load works there.
	@if docker save --help 2>/dev/null | grep -q -- '--platform'; then \
		for img in $(IMAGES); do \
			f=/tmp/$$(echo $$img | tr '/:' '__').tar; \
			docker save --platform linux/$(ARCH) $$img -o $$f && \
			kind load image-archive --name $(CLUSTER) $$f && rm -f $$f || exit 1; \
		done; \
	else \
		for img in $(IMAGES); do \
			kind load docker-image --name $(CLUSTER) $$img || exit 1; \
		done; \
	fi
	kubectl --context $(CTX) apply -f $(GIE_CRDS)
	kubectl --context $(CTX) create namespace $(NS) --dry-run=client -o yaml | \
		kubectl --context $(CTX) apply -f -
	$(KCTL) apply -f deploy/backends.yaml
	$(KCTL) create configmap epp-config \
		--from-file=config.yaml=policies/$(POLICY).yaml \
		--dry-run=client -o yaml | $(KCTL) apply -f -
	$(KCTL) apply -f deploy/epp.yaml
	$(KCTL) annotate deploy epp inferroute/policy=$(POLICY) --overwrite
	$(KCTL) wait --for=condition=available --timeout=300s deploy/vllm-sim deploy/epp
	@echo "gateway: $(GATEWAY_URL)   policy: $(POLICY)"

images:
	@for img in $(IMAGES); do docker pull -q --platform linux/$(ARCH) $$img; done

down:
	kind delete cluster --name $(CLUSTER) || true

smoke:
	curl -sf -X POST $(GATEWAY_URL)/v1/chat/completions \
	  -H 'Content-Type: application/json' \
	  -d '{"model":"meta-llama/Llama-3.1-8B-Instruct","messages":[{"role":"user","content":"ping"}]}' \
	  | python3 -m json.tool

spread:
	CONTEXT=$(CTX) NAMESPACE=$(NS) ./scripts/spread.sh $(or $(N),30)

status:
	$(KCTL) get pods -o wide
	@echo "policy: $$(CONTEXT=$(CTX) NAMESPACE=$(NS) ./scripts/set-policy.sh current)"

logs:
	$(KCTL) logs deploy/epp -c epp --tail=50

experiment:
	CONTEXT=$(CTX) NAMESPACE=$(NS) ./scripts/run_experiment.sh

analyze:
	@test -n "$(RUN)" || (echo "usage: make analyze RUN=results/<run-dir>" && exit 1)
	python3 analysis/analyze.py $(RUN)
