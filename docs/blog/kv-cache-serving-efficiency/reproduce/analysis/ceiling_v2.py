#!/usr/bin/env python3
"""Ceiling with the all-tokens expert convention: W_b evaluated at the tokens in a step, n decode + n*U/O prefill (prefill spread evenly).
Outputs R, per-run own-I ceilings, the split table, the ideal throughput-vs-n curve, the two capacity walls, the 2.1 thresholds and the rule check."""
import sys,io,contextlib,json; sys.argv=[sys.argv[0]]; sys.path.insert(0,'/usr/local/google/home/kaushikmitra/kv-whitepaper')
with contextlib.redirect_stdout(io.StringIO()):
    import ceilings as c, retention as rt
b=253952; W=0.527*8*8.0e12; Ctok=3403712; TB=8192; FLOOR=0.0058; R8k=37217; p=-0.177; r_ridge=5829
rt.b=b; rt.P['b']=b; rt.P['W']=W; rt.W=W
def step(I,O,h,n):
    U=(1-h)*I; L=I+O/2; Ftok=R8k*(I/8192)**p; pre=n*U/O
    return max((c.wread(n+pre)+n*L*b+(pre/TB)*L*b)/W,(n+pre)/Ftok,FLOOR)
def ceiling(I,O,h):
    L=I+O/2; nmax=int(Ctok/L); best=None
    for n in range(1,nmax+1):
        t=step(I,O,h,n); tps=n/t*(I+O)/O   # per replica tokens/s
        if best is None or tps>best[1]: best=(n,tps,t)
    return best
I,O=116684,386; rho=1-(I+19*1500)/(20*I)
n,tps,t=ceiling(I,O,rho); Rg=tps/8; U=(1-rho)*I; pre=n*U/O; Ftok=R8k*(I/8192)**p
print(f"CEILING all-tokens: n {n}, step {t*1e3:.1f} ms, R {Rg/1e3:.1f}k/GPU, {tps/1e3:.0f}k/replica, fleet {tps*6/1e6:.3f}M; compute share {(n+pre)/Ftok/t:.2f}; W_b {c.wread(n+pre)/1e9:.0f} GB; turns/s fleet {tps*6/(I+O):.1f}; paused/replica {tps*6/(I+O)/6*12:.1f} -> {tps*6/(I+O)/6*12*(I+O)/1e6:.2f}M tok")
F=tps*6
H={'llm-d 120':(604,.98),'llm-d 180':(622,.92),'llm-d 240':(570,.90),'llm-d 300':(345,.75),'Dynamo 60':(253,.76),'Dynamo 90':(287,.74),'Dynamo 120':(271,.67),'rr 90':(191,.62),'rr 120':(180,.53)}
print('eta (fleet ceiling):',{k:round(v[0]*1e3/F,2) for k,v in H.items()})
print('--- split (T_ideal at 0.98, own-I ceilings):')
pts=[]
for r in json.load(open('retention/round2_inputs.json')):
    nm=r['name']; I_,O_,hh,nr,S=r['I'],r['O'],H[nm][1],r['n_run'],r['conc']; rho_=1-(I_+19*1500)/(20*I_)
    Z=r['Tp']+r['gap']; T=r['T_engine']; Tid=O_*step(I_,O_,0.98,nr); Th=O_*step(I_,O_,hh,nr)
    nn,tp,tt=ceiling(I_,O_,rho_); Rr=tp*6
    client=min(1,S*(I_+O_)/(Z+Tid)/Rr); reuse=min(1.0,(Z+Tid)/(Z+Th)); eng=(Z+Th)/(Z+T)
    ideal_n=6*nr/step(I,O,rho,nr)*(I+O)/O
    pts.append((nm,nr,H[nm][0],ideal_n))
    print(f"{nm:11s} S {S:3d} Z {Z:5.1f} T {T:5.1f} | client {client:.2f} reuse {reuse:.2f} engine {eng:.2f} | eta {client*reuse*eng:.2f} meas {r['tps_gpu']*48/Rr:.2f} | R {Rr/1e6:.2f}M")
print('--- ideal curve (fleet k tok/s) at h=rho vs n:')
curve=[(k,round(6*k/step(I,O,rho,k)*(I+O)/O/1e3)) for k in range(1,n+1)]; print(curve)
for k in range(1,n+1):
    tt=step(I,O,rho,k); lam=k/(O*tt); Ssess=k+lam*12.0
    if Ssess*(I+O/2)>Ctok: print('HBM-only wall n =',k-1,'sessions/replica %.1f'%Ssess); break
print('--- measured points vs ideal at their n:'); [print(f"  {nm:11s} n {nr:4.1f} meas {m}k ideal {ide/1e3:.0f}k ratio {m*1e3/ide:.2f}") for nm,nr,m,ide in pts]
print('--- 2.1 thresholds (weights for all tokens): threshold(n, U/O=y) = (L + W_b(n(1+y))/(n b))/r - 1, solved at the workload point')
def thr(nn,y):  # implicit in y; iterate
    for _ in range(50): y=(I+c.wread(nn*(1+y))/(nn*b))/r_ridge-1
    return y
print('  at n=29:',round(thr(29,19),1),' at n=12:',round(thr(12,19),1),' at n=5:',round(thr(5,19),1))
AI={'llm-d 120':36,'llm-d 180':172,'llm-d 240':239,'llm-d 300':737,'Dynamo 60':200,'Dynamo 90':283,'Dynamo 120':600,'rr 90':303,'rr 120':549}
agree=0
for r in json.load(open('retention/round2_inputs.json')):
    nm=r['name']; y=(1-H[nm][1])*r['I']/r['O']; nr=r['n_run']; th=(r['I']+c.wread(nr*(1+y))/(nr*b))/r_ridge-1
    a=(y>th)==(AI[nm]>290); agree+=a; print(f"  {nm:11s} U/O {y:6.1f} thr {th:5.1f} rule {'compute' if y>th else 'memory':7s} AI {AI[nm]} {'agree' if a else 'DISAGREE'}")
print('agree',agree,'of 9')
