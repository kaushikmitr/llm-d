#!/usr/bin/env python3
"""Client x reuse x engine split (Section 3 definitions) for runs in an inputs json (retention_inputs / round2_inputs)."""
import sys, json, io, contextlib
_argv=sys.argv; sys.argv=[sys.argv[0]]  # kvmodel writes to sys.argv[1] on import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with contextlib.redirect_stdout(io.StringIO()):
    import retention as rt
B=int(_argv[2]) if len(_argv)>2 else 260096
rt.b=B; rt.P["b"]=B; rt.P["W"]=0.527*8*8.0e12; rt.W=rt.P["W"]
sys.argv=_argv; runs=json.load(open(sys.argv[1]))
print(f"b={B}  {'run':12s} {'S':>4s} {'Z':>6s} {'T':>6s} {'T_id':>5s} {'T_h':>5s} | {'client':>6s} {'reuse':>5s} {'engine':>6s} | {'eta':>5s} {'meas':>5s} | R/GPU")
for r in runs:
    I,O,h,n,S=r['I'],r['O'],r['h'],r['n_run'],r['conc']
    rho=0.98
    Z=r['Tp']+r['gap']; T=r['T_engine']
    Tid=O*rt.step(I,O,rho,max(n,1e-3)); Th=O*rt.step(I,O,h,max(n,1e-3))
    R=rt.R_open(I,O,rho)*48
    client=min(1.0,S*(I+O)/(Z+Tid)/R); reuse=(Z+Tid)/(Z+Th); engine=(Z+Th)/(Z+T)
    print(f"{r['name']:12s} {S:4d} {Z:6.1f} {T:6.1f} {Tid:5.1f} {Th:5.1f} | {client:6.2f} {reuse:5.2f} {engine:6.2f} | {client*reuse*engine:5.2f} {r['tps_gpu']*48/R:5.2f} | {R/48/1e3:.1f}k")
