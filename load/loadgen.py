#!/usr/bin/env python3
"""InferRoute load generator: open-loop, bursty, mixed-size chat traffic.

Sends OpenAI-style streaming chat requests to the gateway on a fixed
schedule (open-loop: a request is sent at its scheduled time whether or not
earlier ones have finished), and writes one CSV row per request with its
time-to-first-token (TTFT) and end-to-end latency.

The whole schedule (send times + request contents) is generated up front
from --seed, so every routing policy can be tested with exactly the same
traffic.

Request types (mix set by --mix):
  short        a short question, no system prompt
  shared-long  one of a few long "tenant" system prompts + a short question;
               the system prompt is identical for every request of that
               tenant, so it can be served from a replica's prefix cache
  unique-long  a long user message that never repeats (e.g. "summarize this
               document"); no cache reuse, just a lot of prefill work

Traffic shape (--phases): comma-separated RATE:SECONDS segments, e.g.
  6:30,20:20,6:30  = 6 req/s for 30 s, burst to 20 req/s for 20 s, back to 6.
Arrivals inside a segment are Poisson (random gaps with that average rate).

Example:
  python3 load/loadgen.py --policy queue-aware --out results/queue-aware.csv
"""
import argparse
import asyncio
import csv
import json
import math
import random
import sys
import time
from dataclasses import dataclass, field

import httpx

# Vocabulary for generated text. With the simulator's dummy tokenizer,
# roughly one word = one token.
WORDS = (
    "cloud kubernetes cluster replica routing latency throughput queue cache "
    "prefix token model inference gateway envoy scheduler pod service request "
    "response batch memory compute network storage container deploy scale "
    "metric policy burst load balance student course project report data "
    "system design question answer explain summary document chapter section "
    "result value error timeout retry server client stream window score"
).split()


@dataclass
class Req:
    idx: int
    sched_t: float          # seconds after start when it should be sent
    phase: int              # index of the --phases segment it belongs to
    kind: str               # short | shared-long | unique-long
    tenant: int             # tenant id for shared-long, else -1
    messages: list
    prompt_words: int
    result: dict = field(default_factory=dict)


def words(rng: random.Random, n: int) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n))


def parse_phases(spec: str):
    phases = []
    for seg in spec.split(","):
        rate, secs = seg.split(":")
        phases.append((float(rate), float(secs)))
    return phases


def parse_mix(spec: str):
    mix = {}
    for item in spec.split(","):
        k, v = item.split("=")
        mix[k.strip()] = float(v)
    for k in mix:
        if k not in ("short", "shared-long", "unique-long"):
            sys.exit(f"unknown request type in --mix: {k}")
    total = sum(mix.values())
    return {k: v / total for k, v in mix.items()}


def tenant_prompts(n_tenants: int, n_words: int):
    """Fixed long system prompt per tenant (same text in every run)."""
    prompts = []
    for t in range(n_tenants):
        rng = random.Random(10_000 + t)
        prompts.append(
            f"You are the assistant for tenant {t}. Follow these instructions. "
            + words(rng, n_words)
        )
    return prompts


def build_schedule(args) -> list:
    rng = random.Random(args.seed)
    phases = parse_phases(args.phases)
    mix = parse_mix(args.mix)
    kinds, kind_w = zip(*mix.items())
    tenants = tenant_prompts(args.tenants, args.shared_prefix_words)
    # Zipf-like tenant popularity: tenant 0 is the hottest.
    tenant_w = [1 / (t + 1) ** args.tenant_skew for t in range(args.tenants)]

    reqs, t0 = [], 0.0
    for p_idx, (rate, secs) in enumerate(phases):
        t, end = t0, t0 + secs
        while rate > 0:
            t += rng.expovariate(rate)
            if t >= end:
                break
            kind = rng.choices(kinds, kind_w)[0]
            tenant = -1
            question = words(rng, rng.randint(args.short_min, args.short_max))
            if kind == "short":
                messages = [{"role": "user", "content": question}]
                n = len(question.split())
            elif kind == "shared-long":
                tenant = rng.choices(range(args.tenants), tenant_w)[0]
                messages = [
                    {"role": "system", "content": tenants[tenant]},
                    {"role": "user", "content": question},
                ]
                n = len(tenants[tenant].split()) + len(question.split())
            else:  # unique-long
                # The long document goes in a system message and a short
                # instruction is the last message: the simulator's echo mode
                # replies with the last message, so the answer stays short
                # while the prompt (prefill work) is long.
                doc = words(rng, args.unique_words)
                instruction = "Summarize the document above in three sentences."
                messages = [
                    {"role": "system", "content": "Document: " + doc},
                    {"role": "user", "content": instruction},
                ]
                n = args.unique_words + 1 + len(instruction.split())
            reqs.append(Req(len(reqs), t, p_idx, kind, tenant, messages, n))
        t0 = end
    return reqs


async def send(client: httpx.AsyncClient, args, req: Req, start: float):
    body = {
        "model": args.model,
        "messages": req.messages,
        "max_tokens": args.max_tokens,
        "stream": True,
    }
    sent = time.perf_counter()
    res = req.result
    res["send_lag_s"] = sent - start - req.sched_t   # >0 if the client fell behind
    ttft, chunks, status, err = None, 0, None, ""
    last_data, finish = "", ""
    try:
        async with client.stream("POST", args.target + "/v1/chat/completions",
                                 json=body, timeout=args.timeout) as r:
            status = r.status_code
            if status != 200:
                err = (await r.aread()).decode(errors="replace")[:200]
            else:
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    last_data = data
                    try:
                        chunk = json.loads(data)
                    except ValueError:
                        continue
                    # A server can fail mid-stream: HTTP 200 already sent,
                    # then an {"error": ...} event instead of tokens.
                    if isinstance(chunk, dict) and "error" in chunk:
                        err = "stream error: " + json.dumps(chunk["error"])
                        continue
                    try:
                        choice = chunk["choices"][0]
                    except (KeyError, IndexError, TypeError):
                        continue
                    finish = choice.get("finish_reason") or finish
                    if (choice.get("delta") or {}).get("content"):
                        chunks += 1
                        if ttft is None:
                            ttft = time.perf_counter() - sent
                if ttft is None and not err:
                    err = (f"no tokens in stream (finish_reason={finish or 'none'}); "
                           f"last event: {last_data[:150]}")
    except Exception as e:  # timeouts, connection errors
        err = f"{type(e).__name__}: {e}"[:200]
    res.update(
        sent_t=sent - start,
        status=status,
        ok=int(status == 200 and ttft is not None and not err),
        ttft_s=ttft,
        e2e_s=time.perf_counter() - sent,
        output_chunks=chunks,
        finish_reason=finish,
        error=err.replace("\n", " ")[:300],
    )


async def run(args):
    reqs = build_schedule(args)
    duration = sum(s for _, s in parse_phases(args.phases))
    by_kind = {}
    for r in reqs:
        by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
    print(f"[loadgen] policy={args.policy} target={args.target} seed={args.seed}")
    print(f"[loadgen] {len(reqs)} requests over {duration:.0f}s, phases={args.phases}, mix={by_kind}")

    limits = httpx.Limits(max_connections=args.max_inflight,
                          max_keepalive_connections=args.max_inflight)
    async with httpx.AsyncClient(limits=limits) as client:
        start = time.perf_counter()
        tasks = []

        async def progress():
            while True:
                await asyncio.sleep(5)
                done = sum(1 for t in tasks if t.done())
                errs = sum(1 for r in reqs if r.result and not r.result.get("ok", 1))
                print(f"[loadgen] t={time.perf_counter() - start:5.1f}s sent={len(tasks)} "
                      f"done={done} in-flight={len(tasks) - done} errors={errs}", flush=True)

        prog = asyncio.create_task(progress())
        for r in reqs:  # open loop: fire each request at its scheduled time
            delay = r.sched_t - (time.perf_counter() - start)
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(send(client, args, r, start)))
        await asyncio.gather(*tasks)
        prog.cancel()

    fields = ["idx", "policy", "phase", "kind", "tenant", "prompt_words",
              "sched_t", "sent_t", "send_lag_s", "status", "ok", "ttft_s",
              "e2e_s", "output_chunks", "finish_reason", "error"]
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in reqs:
            w.writerow({"idx": r.idx, "policy": args.policy, "phase": r.phase,
                        "kind": r.kind, "tenant": r.tenant,
                        "prompt_words": r.prompt_words,
                        "sched_t": round(r.sched_t, 4), **r.result})

    ok = [r for r in reqs if r.result.get("ok")]
    ttfts = sorted(r.result["ttft_s"] for r in ok)
    def pct(p):
        return ttfts[min(len(ttfts) - 1, math.ceil(p / 100 * len(ttfts)) - 1)] if ttfts else float("nan")
    print(f"[loadgen] done: ok={len(ok)}/{len(reqs)}  TTFT p50={pct(50):.3f}s "
          f"p95={pct(95):.3f}s  max={ttfts[-1] if ttfts else float('nan'):.3f}s")
    print(f"[loadgen] wrote {args.out}")
    max_lag = max((r.result.get("send_lag_s", 0) for r in reqs), default=0)
    if max_lag > 0.5:
        print(f"[loadgen] WARNING: client fell up to {max_lag:.2f}s behind schedule; "
              "results may understate the burst", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="http://localhost:30080")
    ap.add_argument("--policy", default="unknown", help="label written into the CSV")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--phases", default="6:30,20:20,6:30",
                    help="RATE:SECONDS,... (default: 30s steady, 20s burst, 30s recovery)")
    ap.add_argument("--mix", default="short=0.6,shared-long=0.3,unique-long=0.1")
    ap.add_argument("--tenants", type=int, default=24, help="number of shared system prompts")
    ap.add_argument("--tenant-skew", type=float, default=1.0,
                    help="Zipf exponent of tenant popularity (0 = uniform)")
    ap.add_argument("--shared-prefix-words", type=int, default=600)
    ap.add_argument("--unique-words", type=int, default=800)
    ap.add_argument("--short-min", type=int, default=30)
    ap.add_argument("--short-max", type=int, default=80)
    # Note: in the simulator's echo mode the reply is the last message, so
    # its length is set by the user message, not by max_tokens.
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--model", default="meta-llama/Llama-3.1-8B-Instruct")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--max-inflight", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true",
                    help="print the schedule summary and exit without sending")
    args = ap.parse_args()

    if args.dry_run:
        reqs = build_schedule(args)
        for p_idx, (rate, secs) in enumerate(parse_phases(args.phases)):
            n = sum(1 for r in reqs if r.phase == p_idx)
            print(f"phase {p_idx}: {rate} req/s x {secs}s -> {n} requests")
        for k in ("short", "shared-long", "unique-long"):
            sel = [r for r in reqs if r.kind == k]
            if sel:
                print(f"{k:12s} n={len(sel):4d}  avg prompt words={sum(r.prompt_words for r in sel) / len(sel):.0f}")
        return
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
