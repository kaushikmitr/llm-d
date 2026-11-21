#!/usr/bin/env python3
"""Roofs-only roofline of one replica on 8 x B200 (TP=8) and 8 x TPU7x (TP=16): the Section 2 figure of the KV Cache-Miss Tax post.
Deployment roof = the calibrated 128k-prompt prefill rate (Appendix A) times the 4.3e11 FLOPs per token of Appendix B.2 (2N plus attention at
this workload's context), per accelerator; bandwidth = what the decode ladder reached. Datasheet roofs dashed. Usage: roofline_tpu_vs_gpu.py out.png"""
import sys, numpy as np, matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
FLOP_TOK = 4.3e11
PLAT = {'B200': dict(acc=8, F_peak=4.5e15, W_peak=8e12, R128k=22794, W_dep=8e12, color='#2a78d6', label='8 x B200, TP=8'),
        'TPU7x': dict(acc=8, F_peak=4.6e15, W_peak=7.37e12, R128k=12937, W_dep=49.8e12/8, color='#eb6834', label='8 x TPU7x, TP=16')}
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11, 'axes.edgecolor': '#c3c2b7', 'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e', 'text.color': '#0b0b0b'})
fig, ax = plt.subplots(figsize=(11.5, 6.2), dpi=150); fig.patch.set_facecolor('#fcfcfb'); ax.set_facecolor('#fcfcfb')
x = np.logspace(np.log10(15), np.log10(4000), 400)
for name, p in PLAT.items():
    F_dep = p['R128k'] * FLOP_TOK / p['acc']; ridge = F_dep / p['W_dep']
    print(f"{name}: deployment {F_dep/1e15:.3f} PF/s per accelerator, {p['W_dep']/1e12:.1f} TB/s, ridge {ridge:.0f}; datasheet ridge {p['F_peak']/p['W_peak']:.0f}")
    ax.plot(x, np.minimum(p['F_peak'], p['W_peak'] * x) / 1e15, ls='--', lw=1.4, color=p['color'], alpha=0.55)
    ax.plot(x, np.minimum(F_dep, p['W_dep'] * x) / 1e15, ls='-', lw=2, color=p['color'])
    ax.text(3600, F_dep / 1e15 * 1.08, f"{name} deployment {F_dep/1e15:.2f} PF/s, {p['W_dep']/1e12:.1f} TB/s, ridge {ridge:.0f}", ha='right', va='bottom', fontsize=9.5, color='#52514e')
ax.text(3600, 4.6 * 1.08, 'datasheets: B200 4.5 PF/s dense FP8, 8 TB/s; TPU7x 4.6 PF/s, 7.4 TB/s', ha='right', va='bottom', fontsize=9.5, color='#52514e')
ax.set_xscale('log'); ax.set_yscale('log'); ax.set_xlim(15, 4000); ax.set_ylim(0.15, 10)
ax.set_xticks([20, 50, 100, 200, 500, 1000, 2000]); ax.set_xticklabels(['20', '50', '100', '200', '500', '1000', '2000'])
ax.set_yticks([0.3, 1, 3, 10]); ax.set_yticklabels(['0.3', '1', '3', '10'])
ax.grid(True, which='major', axis='y', color='#e6e5e0', lw=0.8); ax.grid(False, which='minor')
for s in ('top', 'right'): ax.spines[s].set_visible(False)
ax.set_xlabel('arithmetic intensity, FLOP per byte of HBM traffic (log)'); ax.set_ylabel('achieved compute, PFLOP/s per accelerator (log)')
handles = [Line2D([], [], color=PLAT['B200']['color'], lw=2, label='8 x B200, TP=8'), Line2D([], [], color=PLAT['TPU7x']['color'], lw=2, label='8 x TPU7x, TP=16'),
           Line2D([], [], color='#52514e', lw=1.4, ls='--', label='datasheet roof'), Line2D([], [], color='#52514e', lw=2, label='deployment roof (Appendix A method)')]
ax.legend(handles=handles, loc='lower right', frameon=False, fontsize=9.5)
fig.suptitle('Same datasheet, different ceilings: the roofline of one replica on B200 and on TPU7x', x=0.02, ha='left', fontsize=14, fontweight='bold')
ax.set_title('Qwen3-Coder-480B-FP8, 8 accelerators per replica · solid roof: calibrated prefill rate at the workload context, bandwidth the decode ladder reached · log axes', loc='left', fontsize=9.5, color='#52514e', pad=12)
fig.tight_layout(rect=(0, 0, 1, 0.95)); out = sys.argv[1]; fig.savefig(out, facecolor=fig.get_facecolor()); print('wrote', out)
