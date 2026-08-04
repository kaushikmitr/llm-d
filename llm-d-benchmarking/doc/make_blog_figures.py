#!/usr/bin/env python3
"""Regenerate the blog result figures (fig_5_*) from the aggregated
results.txt tables in ../workloads/*/analysis/.

The calibration and portability figures (fig_7_*) are produced separately
and are not touched here.

Usage: python make_blog_figures.py   (writes PNGs into images/)
"""

import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, NullFormatter

HERE = os.path.dirname(os.path.abspath(__file__))
WORKLOADS = os.path.join(HERE, "..", "workloads")
IMAGES = os.path.join(HERE, "images")

# Display name, color, marker for each arm. "legacy blend" is the
# multi-signal weighted scorer that shipped as the router default when the
# experiments were run (labeled "epp default" in results.txt).
ARMS = {
    "k8": dict(color="#666666", marker="o"),
    "run-req": dict(color="#ff7f0e", marker="v"),
    "prefix+token": dict(color="#1f77b4", marker="s"),
    "legacy blend": dict(color="#2ca02c", marker="^"),
    "latency-predictor": dict(color="#9467bd", marker="D"),
}

# results.txt section label -> arm display name, per workload
SECTION_TO_ARM = {
    "code-generation": {
        "k8": "k8",
        "epp prefix-cache filter + modified token-load 286k": "prefix+token",
        "epp default": "legacy blend",
        "latency-predictor 5s": "latency-predictor",
    },
    "reasoning": {
        "k8": "k8",
        "epp run-req": "run-req",
        "epp token-load": "prefix+token",
        "epp default": "legacy blend",
        "latency-predictor": "latency-predictor",
    },
    "b2b-saas": {
        "baseline": "k8",
        "epp prefix-cache filter + token-load": "prefix+token",
        "epp default": "legacy blend",
        "latency-predictor": "latency-predictor",
    },
}


def parse_value(tok):
    tok = tok.strip()
    m = re.fullmatch(r"(-?[\d.]+)(s|ms|%)?", tok)
    if not m:
        return tok
    v = float(m.group(1))
    return v  # unit is implied by the column


def load_results(workload):
    path = os.path.join(WORKLOADS, workload, "analysis", "results.txt")
    arms = {}
    with open(path) as f:
        header = [c.strip() for c in f.readline().split("|")]
        current = None
        for line in f:
            line = line.rstrip("\n")
            sec = re.fullmatch(r"--- (.+) ---", line.strip())
            if sec:
                name = SECTION_TO_ARM[workload].get(sec.group(1))
                current = arms.setdefault(name, []) if name else None
                continue
            if current is None or not line.strip():
                continue
            toks = [parse_value(t) for t in line.split("|")]
            current.append(dict(zip(header, toks)))
    return arms


def plot(ax, arms, order, xkey, ykey, xlabel, ylabel, title, logy=False,
         yticks=None, suffix=None):
    for name in order:
        rows = arms.get(name)
        if not rows:
            continue
        style = ARMS[name]
        label = f"{name}{suffix}" if suffix and name in ("prefix+token", "run-req") else name
        ax.plot([r[xkey] for r in rows], [r[ykey] for r in rows],
                marker=style["marker"], color=style["color"],
                linewidth=2, markersize=6, label=label)
    if logy:
        ax.set_yscale("log")
        if yticks:
            ax.set_yticks(yticks)
            ax.yaxis.set_major_formatter(
                FuncFormatter(lambda v, _: f"{v:g}"))
            ax.yaxis.set_minor_formatter(NullFormatter())
    elif ykey in ("in_t/s", "out_t/s"):
        ax.yaxis.set_major_formatter(
            FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.grid(alpha=0.3)
    ax.legend()


def single(filename, *args, **kwargs):
    fig, ax = plt.subplots(figsize=(9.1, 6))
    plot(ax, *args, **kwargs)
    fig.tight_layout()
    fig.savefig(os.path.join(IMAGES, filename), dpi=100)
    plt.close(fig)
    print(f"wrote {filename}")


def main():
    plt.rcParams.update({"font.size": 12})
    cg = load_results("code-generation")
    rs = load_results("reasoning")
    bb = load_results("b2b-saas")

    cg_order = ["k8", "prefix+token", "legacy blend", "latency-predictor"]
    rs_order = ["k8", "run-req", "prefix+token", "legacy blend", "latency-predictor"]

    ttft_ticks = [10, 15, 20, 30, 50, 75, 100, 150, 200]
    b2b_ticks = [0.3, 0.5, 1, 2, 3, 5, 10, 20, 30, 50, 100, 200]

    single("fig_5_1_1_codegen_ttft.png", cg, cg_order, "conc", "TTFT p90",
           "Concurrency", "TTFT-p90 (s)", "Code-gen: TTFT-p90 vs concurrency",
           logy=True, yticks=ttft_ticks)
    single("fig_5_1_2_codegen_int.png", cg, cg_order, "conc", "in_t/s",
           "Concurrency", "Input tokens / sec (cluster)",
           "Code-gen: Input tokens/sec vs concurrency")
    single("fig_5_1_3_codegen_prefix.png", cg, cg_order, "conc", "prefix%",
           "Concurrency", "Prefix cache hit rate (%)",
           "Code-gen: Prefix cache hit rate vs concurrency")

    single("fig_5_2_1_reasoning_tpot.png", rs, rs_order, "conc", "TPOT p90",
           "Concurrency", "TPOT-p90 (ms)", "Reasoning: TPOT-p90 vs concurrency")
    single("fig_5_2_2_reasoning_outt.png", rs, rs_order, "conc", "out_t/s",
           "Concurrency", "Output tokens / sec (cluster)",
           "Reasoning: Output tokens/sec vs concurrency")
    fig, ax = plt.subplots(figsize=(9.1, 6))
    plot(ax, rs, rs_order, "conc", "prefix%", "Concurrency",
         "Prefix cache hit rate (%)",
         "Reasoning: Prefix cache hit rate vs concurrency")
    ax.legend(loc="lower left")
    ax.axhline(80, color="red", linestyle="--", linewidth=1, alpha=0.6)
    ax.text(0.99, 0.97, "affinity threshold (0.8)", color="red",
            transform=ax.transAxes, ha="right", va="top")
    fig.tight_layout()
    fig.savefig(os.path.join(IMAGES, "fig_5_2_3_reasoning_prefix.png"), dpi=100)
    plt.close(fig)
    print("wrote fig_5_2_3_reasoning_prefix.png")

    single("fig_5_3_1_b2b_ttft.png", bb, cg_order, "ach_qps", "TTFT p90",
           "Achieved QPS", "TTFT-p90 (s)", "B2B-SaaS: TTFT-p90 vs achieved QPS",
           logy=True, yticks=b2b_ticks)
    single("fig_5_3_2_b2b_int.png", bb, cg_order, "ach_qps", "in_t/s",
           "Achieved QPS", "Input tokens / sec (cluster)",
           "B2B-SaaS: Input tokens/sec vs achieved QPS")
    single("fig_5_3_3_b2b_prefix.png", bb, ["prefix+token", "legacy blend"],
           "ach_qps", "prefix%", "Achieved QPS", "Prefix cache hit rate (%)",
           "B2B-SaaS: prefix cache hit rate")

    # Cross-workload summary: matched configuration vs k8 vs legacy blend.
    fig, axes = plt.subplots(1, 3, figsize=(17.4, 6.5))
    plot(axes[0], cg, ["k8", "prefix+token", "legacy blend"], "conc",
         "TTFT p90", "Concurrency", "TTFT-p90 (s)", "Code-gen (prefill-bound)",
         logy=True, suffix=" (matched)")
    plot(axes[1], rs, ["k8", "run-req", "legacy blend"], "conc", "TPOT p90",
         "Concurrency", "TPOT-p90 (ms)", "Reasoning (decode-bound)",
         suffix=" (matched)")
    axes[1].set_ylim(top=185)
    plot(axes[2], bb, ["k8", "prefix+token", "legacy blend"], "ach_qps",
         "TTFT p90", "Achieved QPS", "TTFT-p90 (s)",
         "B2B-SaaS (prefill-bound, pathological)", logy=True,
         suffix=" (matched)")
    fig.suptitle("Matched configuration vs alternatives across all three workloads",
                 fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(os.path.join(IMAGES, "fig_5_4_cross_workload.png"), dpi=100)
    plt.close(fig)
    print("wrote fig_5_4_cross_workload.png")


if __name__ == "__main__":
    main()
