"""Ceilings for the agentic replay with the TRUE output length (harness max_tokens mean), per calibrated deployment."""
import io, contextlib, sys, math; sys.path.insert(0,'.')
with contextlib.redirect_stdout(io.StringIO()):
    from kvmodel import Replica, GiB
N_act=35e9; N_tot=480e9; E,k=160,8; e=(N_tot-N_act)/(E-k); dense=N_act-k*e
def wread(n): return dense+E*(1-(1-k/E)**n)*e
HW={ # R8k tok/s, falloff p, alpha_W*W_peak (B/s), C tokens, b bytes/token, gpus
 'H200 v0.27.1': dict(R=26701, p=-0.257, W=0.70*8*4.8e12, C=2209344, b=260096, g=8),
 'B200 v0.28.0': dict(R=37217, p=-0.177, W=0.54*8*8.0e12, C=3403712, b=260096, g=8),
 'TPU7x x4 (fp8 KV)': dict(R=16444, p=-0.326, W=0.67*4*7.37e12, C=1150000, b=126976, g=4),
}
def ceiling(hw, I, O):
    h=HW[hw]; rho=1-(I+19*1500)/(20*I); F=h['R']*2*N_act*(I/8192)**h['p']; n=20
    for _ in range(6):
        rep=Replica(hw,N_act,h['b'],wread(n),F,h['W'],h['C'],8192); lam,why,t=rep.max_rate(I,O,rho); n=max(1,lam*O*t)
    return lam*(I+O)/h['g'], why, n, t, rho
if __name__=='__main__':
    for hw,I,O,label in [('H200 v0.27.1',120642,238,'H200 single-replica ladder'),('B200 v0.28.0',113443,343,'B200 single-replica ladder (v0.27.1 engine, ceiling from v0.28.0 calib)'),('B200 v0.28.0',116684,386,'B200 fleet arms so far'),('B200 v0.28.0',121425,1101,'B200 fleet, OLD inflated O (for reference)')]:
        R,why,n,t,rho=ceiling(hw,I,O); print(f"{label:70s} I={I:7d} O={O:5d} rho={rho:.3f}: ceiling {R/1000:5.1f}k tok/s/GPU, {why}, resident {n:.1f}, t_iter {t*1000:.1f} ms")
