#!/usr/bin/env python3
"""Ceiling B: calibrated prefill rate, DATASHEET HBM bandwidth, prefill spread over steps, step = max(memory, compute).
Adds a measured engine layer (C): step = t0 + bytes/W_fit for decode steps, plus serialized 8k chunk steps at the measured
chunk-step time, so the execution factor splits into engine (oracle step -> steady-engine step) and orchestration
(steady-engine step -> measured fleet step). Constants from calibration/b200_calib_summary.txt and calib-2026-11-16.
Usage: analysis/ceiling_v3.py [inputs.json]   (default results/round2_inputs.json)"""
import sys,io,contextlib,json,os
HERE=os.path.dirname(os.path.abspath(__file__))
INPUTS=sys.argv[1] if len(sys.argv)>1 else os.environ.get('INPUTS', os.path.join(HERE,'..','results','round2_inputs.json'))
sys.argv=[sys.argv[0]]; sys.path.insert(0,HERE)
with contextlib.redirect_stdout(io.StringIO()):
    import ceilings as c
b=253952; Wpk=8*8.0e12; Ctok=3403712; TB=8192; R8k=37217; p=-0.177
T0=6.82e-3; WFIT=64.4e12; TCHUNK=0.364      # engine layer: fixed step cost, fitted stream rate, measured 8k-chunk step at 116k ctx
I,O=116684,386; rho=1-(I+19*1500)/(20*I); NREP=6
def Ftok(I_): return R8k*(I_/8192)**p
def step(I_,O_,h,n):                      # ceiling B step: max(memory at datasheet rate, compute at calibrated prefill rate)
    U=(1-h)*I_; L=I_+O_/2; pre=n*U/O_
    mem=(c.wread(n+pre)+n*L*b+(pre/TB)*I_*b)/Wpk; comp=(n+pre)/Ftok(I_)
    return max(mem,comp)
def engine_step(I_,O_,h,n):               # engine layer C: steady vLLM step at batch n, hit rate h
    U=(1-h)*I_; L=I_+O_/2; pre=n*U/O_; f=pre/(TB-n)
    dec=T0+(c.wread(n)+n*L*b)/WFIT
    return (1-f)*dec+f*TCHUNK
def ceiling(I_,O_,h):
    L=I_+O_/2; nmax=int(Ctok/L); best=None
    for n in range(1,nmax+1):
        t=step(I_,O_,h,n); tps=n/t*(I_+O_)/O_
        if best is None or tps>best[1]+1e-6: best=(n,tps,t)
    return best
n,tps,t=ceiling(I,O,rho); Rg=tps/8; F=tps*NREP; U=(1-rho)*I
closed=Ftok(I)*(I+O)/(O+U)
print(f"CEILING B: n* {n}, step {t*1e3:.1f} ms, R {Rg/1e3:.1f}k/GPU, {tps/1e3:.0f}k/replica, fleet {F/1e6:.3f}M; closed form F(I)(I+O)/(O+U) = {closed/8/1e3:.1f}k/GPU; capacity cap n = {int(Ctok/(I+O/2))}")
for k in (12,16,18,20,22,24,29):
    U_=U; pre=k*U_/O; mem=(c.wread(k+pre)+k*(I+O/2)*b+(pre/TB)*I*b)/Wpk; comp=(k+pre)/Ftok(I)
    print(f"  n={k:2d} memory {mem*1e3:5.1f} ms compute {comp*1e3:5.1f} ms -> {'compute' if comp>mem else 'memory'} binds; fleet {k/max(mem,comp)*(I+O)/O*NREP/1e6:.2f}M")
H={'llm-d 120':(604,.98),'llm-d 180':(622,.92),'llm-d 240':(570,.90),'llm-d 300':(345,.75),'rr 90':(191,.62),'rr 120':(180,.53)}
print('eta (fleet ceiling B):',{k:round(v[0]*1e3/F,2) for k,v in H.items()})
print('--- split: client * reuse * engine * orchestration = eta (T_ideal at h=0.98)')
worst=0.0
for r in json.load(open(INPUTS)):
    nm=r['name']; I_,O_,hh,nr,S=r['I'],r['O'],H[nm][1],r['n_run'],r['conc']
    Z=r['Tp']+r['gap']; T=r['T_engine']; Tid=O_*step(I_,O_,0.98,nr); Th=O_*step(I_,O_,hh,nr); Te=O_*engine_step(I_,O_,hh,nr)
    client=S*(I_+O_)/(Z+Tid)/F; reuse=(Z+Tid)/(Z+Th); eng=(Z+Th)/(Z+Te); orch=(Z+Te)/(Z+T); execu=(Z+Th)/(Z+T)
    print(f"{nm:11s} S {S:3d} n {nr:4.1f} Z {Z:5.1f} T_id {Tid:5.1f} T_h {Th:5.1f} T_eng {Te:5.1f} T {T:5.1f} | client {client:.2f} reuse {reuse:.2f} execution {execu:.2f} (= engine {eng:.2f} x orch {orch:.2f}) | eta {client*reuse*execu:.2f} meas {r['tps_gpu']*48/F:.2f}")
    worst=max(worst,abs(client*reuse*execu/(r['tps_gpu']*48/F)-1))
print(f"identity check: worst |client*reuse*execution / measured eta - 1| = {worst:.5f}")
print('--- ideal curve (fleet k tok/s) at h=rho vs n:', [(k,round(NREP*k/step(I,O,rho,k)*(I+O)/O/1e3)) for k in range(1,30)])
print('--- engine-layer curve (fleet k tok/s) at h=rho vs n:', [(k,round(NREP*k/engine_step(I,O,rho,k)*(I+O)/O/1e3)) for k in (4,8,12,18,24,29)])
for k in range(1,40):
    tt=step(I,O,rho,k); lam=k/(O*tt); Ssess=k+lam*12.0
    if Ssess*(I+O/2)>Ctok: print('HBM-only wall n =',k-1,'sessions/replica %.1f'%Ssess); break
