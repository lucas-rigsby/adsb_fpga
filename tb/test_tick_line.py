"""cocotb tests for tick_line (stage C): fed the model's tick energies e(t)
(tb/adsb_ref.chip_energy), it must present every candidate (n, phi) of the phase set
exactly once, in increasing T, with taps equal to the model's chip energies
c_j = e(T + 6j) (R23, R32, R33), restart cleanly on clear (R17), and give the
index-ring address p mod 512 (R14)."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from adsb_ref import FRAME_CHIPS, MAG_MAX, RATES, chip_energies, chip_energy

W, LINE, PW = 12, 1435, 9
FIRST_TICK = -5             # chip_energy's first batch starts at tick -5
MASK = (1 << W) - 1


async def reset(dut, rate):
    dut.in_valid.value = 0
    dut.in_e.value = 0
    dut.clear.value = 0
    dut.rate.value = rate
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def start(dut, rate):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut, rate)


def ticks_for(m, S, rng):
    """Tick energies for samples m[0..K-1], as chip_energy would emit them:
    S*K ticks from t = -5; pre-stream ticks are junk."""
    out = []
    for idx in range(S * len(m)):
        t = FIRST_TICK + idx
        out.append(rng.getrandbits(W) if t < 0 else chip_energy(m, 0, t, S, 0))
    return out


def expected_candidates(n_ticks, rate):
    S, phases = RATES[rate][0], RATES[rate][1]
    last_T = FIRST_TICK + n_ticks - 1 - (LINE - 1)
    return [T for T in range(0, last_T + 1) if T % S in phases]


async def run_segment(dut, m, rate, rng, gap_p=0.3, spot=50):
    """Shift the segment's ticks with random gaps; check every candidate. Returns count."""
    S = RATES[rate][0]
    e = ticks_for(m, S, rng)
    want = expected_candidates(len(e), rate)
    got = 0
    k = 0
    while k < len(e) or dut.out_valid.value == 1:
        if k < len(e) and rng.random() >= gap_p:
            dut.in_e.value, dut.in_valid.value = e[k], 1
            k += 1
        else:
            dut.in_valid.value = 0
            dut.in_e.value = rng.getrandbits(W)
        await FallingEdge(dut.clk)
        if dut.out_valid.value == 1:
            assert got < len(want), "R23: more candidates than the model has"
            T = want[got]
            p, phi = divmod(T, S)
            assert dut.out_phi.value.to_unsigned() == phi, f"T={T}: phi"
            assert dut.out_p.value.to_unsigned() == p % (1 << PW), f"T={T}: p mod 512 (R14)"
            taps = dut.out_taps.value.to_unsigned()
            c = [(taps >> (W * j)) & MASK for j in range(FRAME_CHIPS)]
            ref = [e[T + 6 * j - FIRST_TICK] for j in range(FRAME_CHIPS)]
            assert c == ref, f"R33: taps of candidate T={T} differ at chip {next(j for j in range(FRAME_CHIPS) if c[j] != ref[j])}"
            if got % spot == 0:   # tie to the model's per-candidate chip energies
                assert c == chip_energies(m, p, phi, S), f"R33: candidate ({p}, {phi})"
            got += 1
    dut.in_valid.value = 0
    await FallingEdge(dut.clk)          # cocotb 2.1-dev crashes on a write pending at exit
    assert got == len(want), f"R23: {got} candidates, model has {len(want)}"
    return got


def rand_m(rng, n):
    return [rng.choice((1, MAG_MAX, rng.randint(1, MAG_MAX))) for _ in range(n)]


@cocotb.test()
async def first_candidate_timing(dut):
    """No candidate until the frame is complete: at 2.4 MS/s the first appears on the
    1440th shift (T = 0), none before (R23; microarch fill counter from -5)."""
    await start(dut, rate=1)
    for i in range(1440):
        assert dut.out_valid.value == 0, f"candidate after only {i} shifts"
        dut.in_e.value, dut.in_valid.value = i & MASK, 1
        await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    assert dut.out_valid.value == 1 and dut.out_phi.value == 0 and dut.out_p.value == 0
    await FallingEdge(dut.clk)
    assert dut.out_valid.value == 0, "one candidate per shift"


@cocotb.test()
async def both_rates_every_candidate(dut):
    """Random streams at 2.4 and 2.0 MS/s with random shift gaps: every candidate of the
    phase set, in order, taps equal to the model (R23, R32, R33)."""
    rng = random.Random(5)
    await start(dut, rate=1)
    for rate in (1, 0):
        await reset(dut, rate)
        n = await run_segment(dut, rand_m(rng, 400), rate, rng)
        dut._log.info(f"rate {rate}: {n} candidates checked")


@cocotb.test()
async def clear_restarts_segment(dut):
    """clear mid-stream (and a rate change with it) starts a new segment: the fill
    counter, phase and p restart, so no candidate mixes two segments (R17, R32)."""
    rng = random.Random(6)
    await start(dut, rate=1)
    S = 5
    for t in range(1700):                         # part of a segment, some candidates out
        dut.in_e.value, dut.in_valid.value = rng.getrandbits(W), 1
        await FallingEdge(dut.clk)
    dut.in_valid.value = 1                        # shift offered together with clear: ignored
    dut.clear.value = 1
    dut.rate.value = 0
    await FallingEdge(dut.clk)
    dut.clear.value = 0
    dut.in_valid.value = 0
    await FallingEdge(dut.clk)
    assert dut.out_valid.value == 0
    await run_segment(dut, rand_m(rng, 300), 0, rng)
    assert S == RATES[1][0]


@cocotb.test()
async def index_ring_address_wraps(dut):
    """Long segment at 2.4 MS/s with no gaps: p passes 512 and out_p wraps (R14)."""
    rng = random.Random(7)
    await start(dut, rate=1)
    n = await run_segment(dut, rand_m(rng, 289 + 560), 1, rng, gap_p=0.0, spot=500)
    assert n > 512 * 5, n
