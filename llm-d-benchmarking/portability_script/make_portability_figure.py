#!/usr/bin/env python3
"""
Render doc/images/fig_7_4_1_portability.png from portability.py's data.

Every bar is a measured serving path: τ_sat = peakPrefillThroughput × T_max.
Importing portability.py keeps the figure and the §7.4 table in lockstep.
"""

import os
import sys

import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portability

T_MAX = portability.T_MAX

rows = sorted(portability.MEASURED, key=lambda r: r[3])  # ascending: largest ends up top
labels = [f'{path} · {engine}' for path, engine, tp, r, src in rows]
tau_k = [r * T_MAX / 1000 for path, engine, tp, r, src in rows]
anchored = [src == 'anchored' for path, engine, tp, r, src in rows]

fig, ax = plt.subplots(figsize=(10.4, 5.4), dpi=100)

bars = ax.barh(labels, tau_k,
               color=['#2a78d6' if a else '#86b6ef' for a in anchored],
               height=0.62, zorder=3)

for bar, tk, a, (path, engine, tp, r, src) in zip(bars, tau_k, anchored, rows):
    if a:
        text = f'{int(r * T_MAX):,}  (our deployment)'
    else:
        text = f'≈{tk:,.0f}k'
    ax.text(bar.get_width() + 6, bar.get_y() + bar.get_height() / 2, text,
            va='center', ha='left', fontsize=10.5, color='#0b0b0b',
            fontweight='bold' if a else 'normal')

ax.set_xlabel(f'τ_sat = peakPrefillThroughput × T_max = {T_MAX:.0f}s  (thousands of tokens)',
              fontsize=12)
ax.set_title('τ_sat across measured serving paths', fontsize=13)
ax.set_xlim(0, max(tau_k) * 1.22)
ax.grid(True, axis='x', alpha=0.3, linewidth=0.6, zorder=0)
ax.spines['top'].set_visible(False)
ax.spines['right'].set_visible(False)
ax.tick_params(axis='y', labelsize=10.5)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   '..', 'doc', 'images', 'fig_7_4_1_portability.png')
fig.tight_layout()
fig.savefig(out, facecolor='white')
print(f'wrote {os.path.normpath(out)}')
