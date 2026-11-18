# InferRoute

Cache-aware routing and disaggregated scaling for LLM inference on Kubernetes
(CSE 4207 final project).

A local testbed: three simulated vLLM replicas behind the llm-d router (EPP),
fronted by Envoy, on a single-node kind cluster. No GPU needed.

```
client ──► :30080 Envoy ──ext_proc──► EPP (active policy) ──► picks a replica
                 └────────── forwards request ─────────────► vllm-sim × 3
```

## Prerequisites

- Docker Desktop (give it 5–6 GB of memory under Settings → Resources)
- [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation), `kubectl`, `make`, `curl`, Python 3
- Windows: run everything from WSL2 or Git Bash

## Quick start

```bash
make up                 # cluster + replicas + router, policy = queue-cache-aware
make smoke              # one request through the gateway -> JSON reply
make spread N=30        # 30 requests -> how many each replica served
make status             # pods + active policy
make down               # delete the cluster
```

If image loading fails, set the platform explicitly: `make up ARCH=amd64`
(Intel/AMD) or `make up ARCH=arm64` (Apple Silicon).

## Switching routing policy

```bash
scripts/set-policy.sh list
CONTEXT=kind-inferroute scripts/set-policy.sh round-robin
make spread N=30
CONTEXT=kind-inferroute scripts/set-policy.sh queue-aware
make spread N=30
```

See [policies/README.md](policies/README.md) for what each policy does.

## Layout

| Path | What |
|---|---|
| `deploy/kind-config.yaml` | kind cluster, maps NodePort 30080 to the host |
| `deploy/backends.yaml` | 3 llm-d-inference-sim replicas (fake vLLM) |
| `deploy/epp.yaml` | InferencePool, llm-d router (EPP) + Envoy, gateway Service |
| `policies/` | the three routing policies (EPP configs) |
| `scripts/set-policy.sh` | switch the active policy on a running cluster |
| `scripts/spread.sh` | send N requests, count requests per replica |

## Versions

llm-d-inference-sim v0.9.2 · llm-d router (EPP) v0.9.0 · Envoy 1.33.2 ·
Gateway API Inference Extension CRDs v1.5.0

## Acknowledgements

The Envoy + EPP sidecar layout and simulator flags follow the working
v0.9.0 setup in [nsega/inference-lab](https://github.com/nsega/inference-lab) (MIT).
