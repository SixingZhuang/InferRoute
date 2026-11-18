# Routing policies

Each file is an llm-d-router (v0.9.0) `EndpointPickerConfig`. The EPP loads
one of them from the `epp-config` ConfigMap; `scripts/set-policy.sh` switches
between them.

| Policy | Signals used | Picks |
|---|---|---|
| `round-robin` | none | a random replica (load-blind baseline; llm-d has no strict round-robin picker) |
| `queue-aware` | waiting-queue length | the replica with the shortest queue |
| `queue-cache-aware` | queue length + estimated prefix-cache match | the highest of `2*queue + 3*prefix` |

Policies 2 and 3 differ only by the prefix-cache term, so Experiment 1 can
attribute any difference to cache awareness. Tune the weights in
`queue-cache-aware.yaml` to explore the queue-vs-cache trade-off.

```bash
scripts/set-policy.sh list
scripts/set-policy.sh queue-aware
scripts/set-policy.sh current
```

Assumes the EPP Deployment `epp` in namespace `inferroute` mounts ConfigMap
`epp-config` and starts with `--config-file=/etc/epp/config.yaml`. Override
with `NAMESPACE`, `CONFIGMAP`, `DEPLOYMENT`, `CONTEXT` env vars.

Config structure (plugin set, `dataLayer` section, prefix-producer
parameters) follows the working v0.9.0 setup in
[nsega/inference-lab](https://github.com/nsega/inference-lab) (MIT).
