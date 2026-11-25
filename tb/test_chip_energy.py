"""cocotb tests for chip_energy (stage B): tick energies e(t) against the reference
model's interval-overlap chip energy (tb/adsb_ref.chip_energy, R33) at both rates,
plus throughput (one sample per S cycles, R20, R32) and dsp_clear (R17)."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from adsb_ref import MAG_MAX, RATES, chip_energy

FIRST_TICK = -5          # microarch 2a: sample 0's batch starts at tick -5


async def reset(dut, rate):
    """Hold reset for 3 cycles with rate set (the clock must already run)."""
    dut.in_valid.value = 0
    dut.in_m.value = 0
    dut.clear.value = 0
    dut.rate.value = rate
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def start(dut, rate):
    """Start the clock (once per test) and reset."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    await reset(dut, rate)


async def run_segment(dut, m, rng, gap_p=0.3):
    """Feed magnitudes m[0..] with random idle gaps, honouring in_ready.
    Returns {tick t: e} for every tick shifted out, plus cycle stamps of each accept."""
    ticks, accepts = [], []
    cycle = 0
    k = 0
    while k < len(m) or dut.idle.value == 0:
        if k < len(m) and dut.in_ready.value == 1 and rng.random() >= gap_p:
            dut.in_m.value, dut.in_valid.value = int(m[k]), 1
            accepts.append(cycle)
            k += 1
        else:
            dut.in_valid.value = 0
            dut.in_m.value = rng.getrandbits(9)      # junk on idle cycles
        await FallingEdge(dut.clk)
        cycle += 1
        if dut.out_valid.value == 1:
            ticks.append(dut.out_e.value)
            # R17 WAIT_DRAIN relies on idle: never high while a tick is still on the output
            assert dut.idle.value == 0, "idle high while a tick is being shifted out"
        elif dut.in_ready.value == 1 and dut.rst_n.value == 1:
            assert dut.idle.value == 1, "idle low with nothing in flight"
    dut.in_valid.value = 0
    await FallingEdge(dut.clk)      # let the write land: cocotb 2.1-dev crashes on a write pending at exit
    return ticks, accepts


def check_ticks(ticks, m, S):
    assert len(ticks) == S * len(m), f"R33: {len(ticks)} ticks for {len(m)} samples at S = {S}"
    for idx, v in enumerate(ticks):
        t = FIRST_TICK + idx
        if t < 0:
            continue                                  # pre-stream ticks: never valid (fill counter)
        got = v.to_unsigned()
        exp = chip_energy(m, 0, t, S, 0)              # chip starting at tick t
        assert got == exp, f"R33: e({t}) = {got}, expected {exp} (S = {S})"


@cocotb.test()
async def r33_worked_example(dut):
    """R33 example: 2.4 MS/s, candidate (100, 3), chip 0 is tick 503 = 2*m[100] + 4*m[101] = 1300."""
    await start(dut, rate=1)
    m = [1] * 102
    m[100], m[101] = 50, 300
    ticks, _ = await run_segment(dut, m, random.Random(0), gap_p=0.0)
    assert ticks[503 - FIRST_TICK].to_unsigned() == 1300, "R33 worked example"
    check_ticks(ticks, m, 5)


@cocotb.test()
async def random_streams_both_rates(dut):
    """Random magnitudes incl. extremes 1 and 382, random gaps, both rates;
    every tick compared with the model (R32, R33)."""
    rng = random.Random(2)
    await start(dut, rate=1)
    for rate in (1, 0, 1, 0):
        S = RATES[rate][0]
        dut.rate.value = rate
        dut.clear.value = 1                           # new segment (R17)
        await FallingEdge(dut.clk)
        dut.clear.value = 0
        m = [rng.choice((1, MAG_MAX, rng.randint(1, MAG_MAX))) for _ in range(600)]
        ticks, _ = await run_segment(dut, m, rng)
        check_ticks(ticks, m, S)


@cocotb.test()
async def full_rate_throughput(dut):
    """With in_valid always offered, a sample is accepted exactly every S cycles and a
    tick is shifted every cycle (R20: 10 / 8.3 MS/s capacity, R32)."""
    rng = random.Random(3)
    await start(dut, rate=1)
    for rate in (1, 0):
        await reset(dut, rate)
        S = RATES[rate][0]
        m = [rng.randint(1, MAG_MAX) for _ in range(50)]
        ticks, accepts = await run_segment(dut, m, rng, gap_p=0.0)
        gaps = {b - a for a, b in zip(accepts, accepts[1:])}
        assert gaps == {S}, f"R20: accept spacing {gaps}, expected {{{S}}}"
        check_ticks(ticks, m, S)


@cocotb.test()
async def clear_abandons_batch(dut):
    """dsp_clear mid-batch stops the remaining ticks; the next segment starts clean (R17)."""
    await start(dut, rate=1)
    dut.in_m.value, dut.in_valid.value = 100, 1
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    await FallingEdge(dut.clk)                        # one or two ticks out
    dut.clear.value = 1
    await FallingEdge(dut.clk)
    assert dut.out_valid.value == 0 and dut.idle.value == 1, "R17: batch abandoned"
    assert dut.in_ready.value == 0, "no sample accepted while clear is high"
    dut.clear.value = 0
    await FallingEdge(dut.clk)
    assert dut.in_ready.value == 1
    m = [rng_m for rng_m in range(1, 40)]
    ticks, _ = await run_segment(dut, m, random.Random(4), gap_p=0.0)
    check_ticks(ticks, m, 5)
