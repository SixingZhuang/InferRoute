#!/usr/bin/env python3
"""Summarize and plot one experiment run.

Usage: python3 analysis/analyze.py results/<run-dir>

Reads every <policy>.csv written by load/loadgen.py in the run directory, plus
the per-replica metric dumps in <run-dir>/metrics/<policy>/<pod>.txt saved by
scripts/run_experiment.sh, and writes into the same directory:

  summary.md              TTFT percentiles per policy and per request type,
                          plus per-replica load and prefix-cache hit rate
  ttft_percentiles.png    P50 / P95 / P99 TTFT per policy
  ttft_by_kind.png        P95 TTFT per request type per policy
  ttft_timeline.png       rolling P95 TTFT over time, burst window shaded
"""
import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

# Categorical slots 1-3 of the reference palette (validated as a set),
# assigned to policies in a fixed order so a policy keeps its color in
# every chart.
POLICY_ORDER = ["round-robin", "queue-aware", "queue-cache-aware"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
INK, INK_2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
KIND_ORDER = ["short", "shared-long", "unique-long"]

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "axes.edgecolor": GRID, "axes.labelcolor": INK_2, "text.color": INK,
    "xtick.color": INK_2, "ytick.color": INK_2, "font.size": 11,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True, "legend.frameon": False,
})


def colors_for(policies):
    known = [p for p in POLICY_ORDER if p in policies]
    extra = [p for p in policies if p not in POLICY_ORDER]
    ordered = known + sorted(extra)
    return ordered, {p: SERIES[i % len(SERIES)] for i, p in enumerate(ordered)}


def pct(s, q):
    return s.quantile(q / 100) if len(s) else float("nan")


def load_runs(run_dir: Path) -> pd.DataFrame:
    frames = [pd.read_csv(f) for f in sorted(run_dir.glob("*.csv"))]
    if not frames:
        sys.exit(f"no CSV files in {run_dir}")
    return pd.concat(frames, ignore_index=True)


METRIC_RE = re.compile(r"^(vllm:[a-z_]+?)(?:_total)?(?:\{[^}]*\})?\s+([0-9.eE+-]+)$")


def read_pod_metrics(path: Path) -> dict:
    out = {}
    for line in path.read_text().splitlines():
        m = METRIC_RE.match(line.strip())
        if m:
            out[m.group(1)] = out.get(m.group(1), 0.0) + float(m.group(2))
    return out


def replica_table(run_dir: Path, policies) -> list:
    rows = []
    for p in policies:
        pods = sorted((run_dir / "metrics" / p).glob("*.txt"))
        if not pods:
            continue
        stats = [read_pod_metrics(f) for f in pods]
        served = [s.get("vllm:request_success", 0) for s in stats]
        hits = sum(s.get("vllm:prefix_cache_hits", 0) for s in stats)
        queries = sum(s.get("vllm:prefix_cache_queries", 0) for s in stats)
        total = sum(served) or 1
        rows.append({
            "policy": p,
            "share": " / ".join(f"{100 * x / total:.0f}%" for x in sorted(served, reverse=True)),
            "max_share": max(served) / total,
            "hit_rate": hits / queries if queries else float("nan"),
        })
    return rows


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    run_dir = Path(sys.argv[1])
    df = load_runs(run_dir)
    policies, color = colors_for(list(df["policy"].unique()))
    ok = df[df["ok"] == 1]

    # ---------- summary.md ----------
    lines = [f"# Results: {run_dir.name}", ""]
    lines += ["## TTFT by policy (seconds)", "",
              "| Policy | OK / sent | P50 | P95 | P99 | Max |", "|---|---|---|---|---|---|"]
    for p in policies:
        t = ok[ok.policy == p]["ttft_s"]
        sent = (df.policy == p).sum()
        lines.append(f"| {p} | {len(t)} / {sent} | {pct(t, 50):.2f} | {pct(t, 95):.2f} "
                     f"| {pct(t, 99):.2f} | {t.max():.2f} |")
    lines += ["", "## P95 TTFT by request type (seconds)", "",
              "| Policy | " + " | ".join(KIND_ORDER) + " |",
              "|---" * (len(KIND_ORDER) + 1) + "|"]
    for p in policies:
        cells = [f"{pct(ok[(ok.policy == p) & (ok.kind == k)]['ttft_s'], 95):.2f}" for k in KIND_ORDER]
        lines.append(f"| {p} | " + " | ".join(cells) + " |")
    reps = replica_table(run_dir, policies)
    if reps:
        lines += ["", "## Replica load and prefix-cache hit rate", "",
                  "| Policy | Requests per replica (largest first) | Prefix-cache hit rate |",
                  "|---|---|---|"]
        for r in reps:
            lines.append(f"| {r['policy']} | {r['share']} | {100 * r['hit_rate']:.0f}% |")
    errs = df[df["ok"] != 1]
    if len(errs):
        lines += ["", f"**{len(errs)} failed requests** "
                  f"(e.g. `{str(errs['error'].dropna().head(1).tolist())[:120]}`)."]
    lag = df["send_lag_s"].max()
    if lag > 0.5:
        lines += ["", f"Warning: the load generator fell up to {lag:.2f}s behind schedule."]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))

    # ---------- chart 1: P50 / P95 / P99 per policy ----------
    qs = [50, 95, 99]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    width = 0.8 / len(policies)
    for i, p in enumerate(policies):
        vals = [pct(ok[ok.policy == p]["ttft_s"], q) for q in qs]
        xs = [j + (i - (len(policies) - 1) / 2) * width for j in range(len(qs))]
        bars = ax.bar(xs, vals, width * 0.92, color=color[p], label=p)
        for x, v in zip(xs, vals):
            ax.text(x, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9, color=INK_2)
    ax.set_xticks(range(len(qs)), [f"P{q}" for q in qs])
    ax.set_ylabel("Time to first token (s)")
    ax.set_title("TTFT percentiles by routing policy", loc="left", fontsize=13, color=INK)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(run_dir / "ttft_percentiles.png", dpi=200)
    plt.close(fig)

    # ---------- chart 2: P95 TTFT by request type ----------
    kinds = [k for k in KIND_ORDER if k in set(ok["kind"])]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, p in enumerate(policies):
        vals = [pct(ok[(ok.policy == p) & (ok.kind == k)]["ttft_s"], 95) for k in kinds]
        xs = [j + (i - (len(policies) - 1) / 2) * width for j in range(len(kinds))]
        ax.bar(xs, vals, width * 0.92, color=color[p], label=p)
        for x, v in zip(xs, vals):
            ax.text(x, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9, color=INK_2)
    ax.set_xticks(range(len(kinds)), kinds)
    ax.set_ylabel("P95 time to first token (s)")
    ax.set_title("P95 TTFT by request type", loc="left", fontsize=13, color=INK)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left")
    fig.tight_layout()
    fig.savefig(run_dir / "ttft_by_kind.png", dpi=200)
    plt.close(fig)

    # ---------- chart 3: rolling P95 TTFT over time ----------
    fig, ax = plt.subplots(figsize=(9, 4.5))
    # Shade the burst: the phase with the highest request rate.
    rates = df[df.policy == policies[0]].groupby("phase")["sched_t"].agg(["min", "max", "count"])
    rates["rate"] = rates["count"] / (rates["max"] - rates["min"]).clip(lower=1e-9)
    burst = rates["rate"].idxmax()
    ax.axvspan(rates.loc[burst, "min"], rates.loc[burst, "max"], color=GRID, alpha=0.6, lw=0)
    ax.text(rates.loc[burst, "min"], 1.0, " burst", transform=ax.get_xaxis_transform(),
            va="top", ha="left", fontsize=9, color=INK_2)
    for p in policies:
        s = ok[ok.policy == p].sort_values("sched_t")
        if s.empty:
            continue
        roll = s.set_index(pd.to_timedelta(s["sched_t"], unit="s"))["ttft_s"].rolling("3s").quantile(0.95)
        ax.plot(s["sched_t"], roll.values, color=color[p], lw=2, label=p)
        ax.text(s["sched_t"].iloc[-1], roll.values[-1], f" {p}", color=INK_2,
                fontsize=9, va="center")
    ax.set_xlabel("Time since start of run (s)")
    ax.set_ylabel("Rolling P95 TTFT, 3 s window (s)")
    ax.set_title("TTFT during the burst", loc="left", fontsize=13, color=INK)
    ax.legend(loc="upper left")
    ax.margins(x=0.12)
    fig.tight_layout()
    fig.savefig(run_dir / "ttft_timeline.png", dpi=200)
    plt.close(fig)
    print(f"\nwrote summary.md and 3 charts to {run_dir}/")


if __name__ == "__main__":
    main()
