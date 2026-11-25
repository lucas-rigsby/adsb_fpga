"""Behavioral prototype of the tick-delay-line DSP (docs/microarch.md), checked against tb/adsb_ref.py. Run from anywhere: python3 scripts/tickline_proto.py"""
import sys; import pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / 'tb'))
import numpy as np, adsb_ref as A
from collections import deque
LINE=1435
def tickline_decode(m, index, rate, pre_k=8):
    S=A.RATES[rate][0]
    line=deque([0]*LINE, maxlen=LINE)   # line[-1] newest tick
    ring={}                              # sample position -> header index (index EBR)
    valid=-5                             # valid ticks inserted (first batch has 5 invalid)
    prev=0; recs=[]; last_T=None; cands=0
    for k in range(len(m)):
        ring[k]=int(index[k])
        for i in range(S):               # ticks t = S*k-5 .. S*k-5+S-1
            t=S*k-5+i
            a,s=divmod(t,S)
            w0=S-s; w1=6-w0
            ma = prev if a==k-1 else int(m[k])     # a is k-1 or k
            mb = int(m[k]) if a==k-1 else 0         # sample a+1 (only weight>0 when a==k-1)
            if a==k: assert w1==0
            e=w0*ma+w1*mb
            line.append(e); valid=min(valid+1,LINE)
            T=t-1434                      # oldest tick = candidate start
            if valid<LINE or T<0: continue
            p,phi=divmod(T,S)
            if phi not in A.RATES[rate][1]: continue
            c=[line[6*j] for j in range(240)]      # fixed taps
            reason,H,L=A.preamble(c,pre_k)
            if reason: continue
            cands+=1
            bits=A.slice_bits(c)
            if A.downlink_format(bits) not in (17,18) or A.crc24_remainder(bits): continue
            Th=S*ring[p]+phi                        # header-derived T (A1)
            if last_T is not None and Th<last_T+1440: continue
            last_T=Th; recs.append((ring[p],phi,H,bits))
        prev=int(m[k])
    return recs,cands
rng=np.random.default_rng(3)
for rate in (1,0):
    for trial in range(3):
        iq,truth=A.rand_stream(rng,40,rate,20 if trial<2 else 10)
        m=A.magnitudes(iq); idx=np.arange(m.size)
        if trial==1:   # sequence gap: drop 777 samples mid-stream, indices jump
            cut=m.size//2; m=np.concatenate([m[:cut],m[cut+777:]]); idx=np.concatenate([idx[:cut],idx[cut+777:]])
        r=A.decode(m,rate,8,idx)
        got,cands=tickline_decode(m,idx,rate)
        want=[(c.n,c.phi,c.H,c.bits) for c in r.records]
        print(f"rate={rate} trial={trial}: model {len(want)} recs/{r.adsb_cand} cand, tickline {len(got)} recs/{cands} cand, equal={got==want and cands==r.adsb_cand}")
        assert got==want and cands==r.adsb_cand
# boundary cases incl. R28 at p+239/240 and segment-end frames
for name,iq,rate,k,n,_ in A.boundary_cases(np.random.default_rng(1)):
    m=A.magnitudes(iq); r=A.decode(m,rate,k); got,c=tickline_decode(m,np.arange(m.size),rate,k)
    assert got==[(x.n,x.phi,x.H,x.bits) for x in r.records] and c==r.adsb_cand, name
# frame ending exactly at the segment end, both rates
for rate in (0,1):
    S=A.RATES[rate][0]; f=A.rand_frame(rng)
    for t0 in np.arange(5.0,6.0,1/12):
        iq=A.render(int(np.ceil((t0+120)*float(A.RATES[rate][2]))), rate, [(t0,f,80,0.3)], rng, 0.5)
        m=A.magnitudes(iq); r=A.decode(m,rate); got,c=tickline_decode(m,np.arange(m.size),rate)
        assert got==[(x.n,x.phi,x.H,x.bits) for x in r.records] and c==r.adsb_cand,(rate,t0)
print("boundary + segment-end cases equal")
