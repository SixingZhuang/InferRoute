# Results: 20261118-090609

## TTFT by policy (seconds)

| Policy | OK / sent | P50 | P95 | P99 | Max |
|---|---|---|---|---|---|
| round-robin | 737 / 737 | 1.57 | 12.65 | 14.67 | 16.29 |
| queue-aware | 737 / 737 | 2.07 | 7.58 | 8.83 | 9.71 |
| queue-cache-aware | 737 / 737 | 1.65 | 5.68 | 6.79 | 7.54 |

## P95 TTFT by request type (seconds)

| Policy | short | shared-long | unique-long |
|---|---|---|---|
| round-robin | 12.04 | 12.69 | 13.40 |
| queue-aware | 7.30 | 7.80 | 8.92 |
| queue-cache-aware | 5.31 | 5.75 | 6.91 |

## Replica load and prefix-cache hit rate

| Policy | Requests per replica (largest first) | Prefix-cache hit rate |
|---|---|---|
| round-robin | 35% / 35% / 30% | 26% |
| queue-aware | 34% / 34% / 33% | 28% |
| queue-cache-aware | 35% / 33% / 32% | 42% |
