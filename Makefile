# InferRoute — local LLM-aware routing testbed on kind.
#
#   make up        create the cluster and deploy replicas + router (~3 min first time)
#   make smoke     send one request through the gateway
#   make spread    send N requests and show how many each replica served
#   make status    list pods
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

.PHONY: up down images smoke spread status logs

up: images
	@kind get clusters 2>/dev/null | grep -qx $(CLUSTER) || \
		kind create cluster --config deploy/kind-config.yaml --wait 120s
	@# Load images from the host instead of pulling inside the cluster.
	@# `kind load docker-image` fails with Docker's containerd image store,
	@# so save a single-platform archive and load that.
	@for img in $(IMAGES); do \
		f=/tmp/$$(echo $$img | tr '/:' '__').tar; \
		docker save --platform linux/$(ARCH) $$img -o $$f && \
		kind load image-archive --name $(CLUSTER) $$f && rm -f $$f; \
	done
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
