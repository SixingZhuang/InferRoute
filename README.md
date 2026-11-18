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

## Experiment 1: routing policies under a burst

```bash
pip install -r requirements.txt          # httpx, pandas, matplotlib
make experiment                          # ~6 min: 3 policies x (restart + 80 s of traffic)
make analyze RUN=results/<run-dir>       # summary.md + 3 charts in that folder
```

`make experiment` runs identical traffic (same seed) under each policy,
restarting the replicas in between so every policy starts with cold caches.
Default traffic: 6 req/s for 30 s, a burst of 20 req/s for 20 s, then 6 req/s
for 30 s; 60% short, 30% shared-long (24 tenants' ~600-word system prompts),
10% unique-long (~800-word documents) requests. Change it with `LOADGEN_ARGS`, e.g.
`LOADGEN_ARGS="--phases 6:30,25:20,6:30" make experiment`; see
`python3 load/loadgen.py --help`.

## Layout

| Path | What |
|---|---|
| `deploy/kind-config.yaml` | kind cluster, maps NodePort 30080 to the host |
| `deploy/backends.yaml` | 3 llm-d-inference-sim replicas (fake vLLM) |
| `deploy/epp.yaml` | InferencePool, llm-d router (EPP) + Envoy, gateway Service |
| `policies/` | the three routing policies (EPP configs) |
| `scripts/set-policy.sh` | switch the active policy on a running cluster |
| `scripts/spread.sh` | send N requests, count requests per replica |
| `load/loadgen.py` | open-loop bursty load generator; one CSV row per request (TTFT, latency) |
| `scripts/run_experiment.sh` | run the same traffic under each policy, save CSVs + replica metrics |
| `analysis/analyze.py` | summary table and charts for one run |

## Versions

llm-d-inference-sim v0.9.2 · llm-d router (EPP) v0.9.0 · Envoy 1.33.2 ·
Gateway API Inference Extension CRDs v1.5.0

## Acknowledgements

The Envoy + EPP sidecar layout and simulator flags follow the working
v0.9.0 setup in [nsega/inference-lab](https://github.com/nsega/inference-lab) (MIT).
