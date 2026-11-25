"""cocotb tests for iq_mag (stages A1/A2): every one of the 65,536 I/Q byte pairs
against the reference model's R19 + R22 (tb/adsb_ref.py), exact 2-cycle latency,
and reset clearing in-flight results."""
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from adsb_ref import magnitude, to_signed

LATENCY = 2                 # microarch 2a: stages A1, A2

# R19 / R22 worked examples and boundaries: (u_i, u_q, m, why)
DIRECTED = [
    (0, 178, 305, "R22 example: (x_I, x_Q) = (-255, 101) -> 305"),
    (0, 0, 382, "maximum magnitude: |x| = 255 on both"),
    (255, 255, 382, "maximum magnitude, positive side"),
    (127, 128, 1, "minimum magnitude: x = -1 and +1"),
    (128, 127, 1, "minimum magnitude, swapped"),
    (128, 128, 1, "R19: u = 128 -> x = 1"),
    (255, 128, 255, "R19: u = 255 -> x = 255, min 1 >> 1 = 0"),
    (0, 128, 255, "R19: u = 0 -> x = -255"),
    (200, 60, 145 + (135 >> 1), "I > Q branch"),
    (60, 200, 145 + (135 >> 1), "Q > I branch, same value"),
]


def ref(ui: int, uq: int) -> int:
    return magnitude(to_signed(ui), to_signed(uq))


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.in_valid.value = 0
    dut.in_i.value = 0
    dut.in_q.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


@cocotb.test()
async def latency_exact(dut):
    """One input appears exactly 2 cycles later with the R22 example value (R19, R22)."""
    await start(dut)
    dut.in_i.value, dut.in_q.value, dut.in_valid.value = 0, 178, 1
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    for cycle in range(1, LATENCY + 4):
        if dut.out_valid.value == 1:
            assert cycle == LATENCY, f"output after {cycle} cycles, expected {LATENCY}"
            assert dut.out_m.value.to_unsigned() == 305, "R22 worked example"
            return
        await FallingEdge(dut.clk)
    raise AssertionError("out_valid never went high")


@cocotb.test()
async def exhaustive_stream(dut):
    """Directed cases, then all 65,536 (I, Q) pairs in random order with random gaps;
    every output compared in order with the model (R19, R22)."""
    for ui, uq, m, why in DIRECTED:
        assert ref(ui, uq) == m, f"DIRECTED entry disagrees with model: {why}"
    await start(dut)
    rng = random.Random(1)
    pairs = [(i, q) for i in range(256) for q in range(256)]
    rng.shuffle(pairs)
    stimulus = [(i, q) for i, q, _, _ in DIRECTED] + pairs
    expected = deque()
    checked = 0
    for item in stimulus + [None] * (LATENCY + 1):
        if item is not None:
            ui, uq = item
            while rng.random() < 0.1:              # idle cycles with junk data
                dut.in_valid.value = 0
                dut.in_i.value, dut.in_q.value = rng.getrandbits(8), rng.getrandbits(8)
                await FallingEdge(dut.clk)
                if dut.out_valid.value == 1:
                    assert dut.out_m.value.to_unsigned() == expected.popleft()
                    checked += 1
            dut.in_i.value, dut.in_q.value, dut.in_valid.value = ui, uq, 1
            expected.append(ref(ui, uq))
        else:
            dut.in_valid.value = 0
        await FallingEdge(dut.clk)
        if dut.out_valid.value == 1:
            assert expected, "unexpected output"
            got, exp = dut.out_m.value.to_unsigned(), expected.popleft()
            assert got == exp, f"m = {got}, expected {exp} (R22)"
            checked += 1
    assert checked == len(stimulus) and not expected, f"sent {len(stimulus)}, checked {checked}"


@cocotb.test()
async def reset_flushes_pipeline(dut):
    """Reset mid-stream drops in-flight results (microarch 4: reset clears valid)."""
    await start(dut)
    dut.in_valid.value = 1
    await FallingEdge(dut.clk)
    dut.rst_n.value, dut.in_valid.value = 0, 0
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(LATENCY + 2):
        assert dut.out_valid.value == 0, "result survived reset"
        await FallingEdge(dut.clk)
