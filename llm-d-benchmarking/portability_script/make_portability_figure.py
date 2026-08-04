#!/usr/bin/env python3
"""
Render doc/images/fig_7_4_1_portability.png from portability.py's data.

τ_sat = R_peak × T_max is linear in T_max, so each (model, accelerator) row
of the §7.4 table is a straight line through the origin with slope R_peak.
Importing portability.py keeps the figure and the table in lockstep.
"""

import contextlib
import io
import os
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):
    import portability

B = portability.B
T_MAX = portability.T_MAX

# Qwen3-32B rows only (the Llama3-8B row is table-only, as in the original figure)
rows = []
for label, params_b, hw, tp, T_B_given, kind in portability.rows:
    if not label.startswith('Qwen3-32B'):
        continue
    T_B = T_B_given if T_B_given is not None else portability.estimate_T_B(params_b, hw, tp, B)
    rows.append((label.replace('Qwen3-32B / ', ''), B / T_B, kind))

rows.sort(key=lambda r: -r[1])  # stacking order: steepest slope on top

# Canonical categorical order (pre-validated adjacency), assigned top-to-bottom.
# Line style encodes provenance: anchored = thick solid, measured (calibration
# recipe) = solid, estimated = dashed.
colors = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#4a3aa7']
est_dashes = iter([(4, 2), (6, 2, 1, 2), (2, 2), (5, 2), (7, 3)])

fig, ax = plt.subplots(figsize=(10.4, 6.6), dpi=100)

t = [5, 30]
anchored_color = '#0b0b0b'
for (label, r_peak, kind), color in zip(rows, colors):
    if kind == 'anchored':
        anchored_color = color
    ax.plot(t, [r_peak * x / 1000 for x in t],
            color=color,
            linewidth={'anchored': 3.0, 'measured': 2.0}.get(kind, 1.75),
            dashes=next(est_dashes) if kind == 'estimated' else (),
            solid_capstyle='round',
            label=label)

# Our deployment: the anchored H100 line at T_max = 14s
tau_k = portability.tau_sat(0.40, T_MAX, B)[1] / 1000
ax.plot([T_MAX], [tau_k], marker='*', markersize=17, color=anchored_color,
        markeredgecolor='#0b0b0b', markeredgewidth=0.8, zorder=5)
ax.annotate(f'Our deployment:\nT_max = {T_MAX:.0f}s\nτ = 286,720',
            xy=(T_MAX, tau_k), xytext=(17.5, 170),
            fontsize=10.5, color='#0b0b0b',
            bbox=dict(facecolor='white', edgecolor='none', alpha=0.85, pad=2),
            arrowprops=dict(arrowstyle='-', color='#52514e', linewidth=1))
ax.plot([T_MAX, T_MAX], [0, tau_k], color='#52514e', linewidth=0.8,
        linestyle=(0, (2, 3)), zorder=1)

ax.set_xlabel('T_max (TTFT degradation tolerance, seconds)', fontsize=12)
ax.set_ylabel('τ_sat (thousands of tokens)', fontsize=12)
ax.set_title('τ_sat = R_peak · T_max across accelerators (Qwen3-32B, B=8192)',
             fontsize=13)
ax.set_xlim(4, 31)
ax.set_ylim(0, None)
ax.grid(True, alpha=0.3, linewidth=0.6)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.legend(loc='upper left', fontsize=10.5, framealpha=0.95)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'doc', 'images', 'fig_7_4_1_portability.png')
fig.tight_layout()
fig.savefig(out, facecolor='white')
print(f'wrote {os.path.normpath(out)}')
