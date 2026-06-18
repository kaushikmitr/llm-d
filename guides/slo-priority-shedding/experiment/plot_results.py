#!/usr/bin/env python3
"""Plot SLO priority-shedding results from results.json.
Produces results/shed_by_class.png and results/highprio_ttft_vs_slo.png.
Run inside the venv: source ../myproject/bin/activate (matplotlib required)."""
import json, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(HERE, "results", "results.json")))
phases = data["phases"]          # ordered list of phase dicts
slo_ttft_ms = data["slo_ttft_ms"]

labels = [p["label"] for p in phases]
high_shed = [p["high"]["shed_pct"] for p in phases]
low_shed = [p["low"]["shed_pct"] for p in phases]
x = range(len(labels)); w = 0.38

# --- Chart 1: shed % by class × phase ---
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.bar([i - w/2 for i in x], high_shed, w, label="high-priority (protected)", color="#2a7", edgecolor="black")
ax.bar([i + w/2 for i in x], low_shed, w, label="low-priority (sheddable)", color="#c44", edgecolor="black")
for i, v in enumerate(high_shed): ax.text(i - w/2, v + 1, f"{v:.0f}%", ha="center", fontsize=9)
for i, v in enumerate(low_shed): ax.text(i + w/2, v + 1, f"{v:.0f}%", ha="center", fontsize=9)
ax.set_xticks(list(x)); ax.set_xticklabels(labels)
ax.set_ylabel("requests shed (429) %"); ax.set_ylim(0, 105)
ax.set_title("SLO-aware priority shedding: shed rate by class × load")
ax.legend(); fig.tight_layout()
fig.savefig(os.path.join(HERE, "results", "shed_by_class.png"), dpi=130)

# --- Chart 2: high- vs low-priority served TTFT vs SLO ---
# Plot MEAN (the predictor's objective, so the SLO-relevant central metric) + p90, not median.
# Phases with a mean. Baseline latency anchor comes from a TPOT=40 baseline run (see
# results.json latency_note); saturated phases from their own runs.
lat = [p for p in phases if p["high"].get("ttft_mean_s") is not None]
llabels = [p["label"] for p in lat]
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.plot(llabels, [p["high"]["ttft_mean_s"] for p in lat], "o-", color="#2a7", label="high-priority TTFT mean")
ax.plot(llabels, [p["high"]["ttft_p90_s"] for p in lat], "o--", color="#2a7", alpha=0.6, label="high-priority TTFT p90")
ax.plot(llabels, [p["low"]["ttft_mean_s"] for p in lat], "s-", color="#c44", label="low-priority TTFT mean (served)")
ax.plot(llabels, [p["low"]["ttft_p90_s"] for p in lat], "s--", color="#c44", alpha=0.6, label="low-priority TTFT p90 (served)")
ax.axhline(slo_ttft_ms/1000.0, color="black", ls=":", label=f"TTFT SLO ({slo_ttft_ms/1000:.0f}s)")
ax.set_ylabel("TTFT (s)"); ax.set_title("Served TTFT (mean + p90) vs SLO by load")
ax.legend(); fig.tight_layout()
fig.savefig(os.path.join(HERE, "results", "highprio_ttft_vs_slo.png"), dpi=130)
print("wrote results/shed_by_class.png and results/highprio_ttft_vs_slo.png")
