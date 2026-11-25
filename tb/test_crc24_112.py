"""cocotb tests for crc24_112 (stage E1): the 112-bit CRC-24 remainder against the
reference model's long division (tb/adsb_ref.crc24_remainder, R27), exact 1-cycle
latency, and reset. P1 (equality with the bit-serial definition for every input)
is proved in formal/crc24_112_props.v; these tests check the same function against
a third, independent implementation."""
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from adsb_ref import CRC_GEN, crc24_parity, crc24_remainder

LATENCY = 1                     # microarch 2a: stage E1
MASK112 = (1 << 112) - 1

DF17_A = int("8D4840D6202CC371C32CE0576098", 16)   # real DF17 frames (pyModeS examples)
DF17_B = int("8D406B902015A678D4D220AA4BDA", 16)

# (112-bit word, expected remainder or None = use the model, why)
DIRECTED = [
    (DF17_A, 0, "R27: valid DF17 frame has remainder 0"),
    (DF17_B, 0, "R27: second valid DF17 frame"),
    (DF17_A ^ (1 << 111), None, "R27: error in the first bit on air (DF field)"),
    (DF17_A ^ 1, None, "R27: error in the last parity bit"),
    (DF17_A ^ (1 << 50), None, "R27: error in the ME field"),
    (DF17_A ^ (0b11 << 60), None, "burst error, 2 bits"),
    (0, 0, "all zero: remainder 0"),
    (MASK112, None, "all ones"),
    (CRC_GEN << 87, 0, "a multiple of G(x) at the top of the word"),
    (CRC_GEN, 0, "G(x) itself"),
    (1 << 24, CRC_GEN ^ (1 << 24), "x^24 reduces to G(x) - x^24"),
    ((1 << 24) - 1, (1 << 24) - 1, "degree < 24: remainder is the word itself"),
]


def ref(v: int) -> int:
    return crc24_remainder(v)


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.in_valid.value = 0
    dut.in_bits.value = 0
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


@cocotb.test()
async def latency_exact(dut):
    """A valid DF17 frame gives remainder 0 exactly 1 cycle later (R27)."""
    await start(dut)
    dut.in_bits.value, dut.in_valid.value = DF17_A, 1
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    dut.in_bits.value = DF17_A ^ 1                 # change the input: output must not follow
    assert dut.out_valid.value == 1, "output not 1 cycle after input"
    assert dut.out_rem.value.to_unsigned() == 0, "R27: valid frame"
    await FallingEdge(dut.clk)
    assert dut.out_valid.value == 0, "one output per input"


async def stream(dut, words, rng, gap_p=0.2):
    """Feed words with random idle cycles; check every remainder in order (R27)."""
    expected = deque()
    checked = 0
    for w in list(words) + [None] * (LATENCY + 1):
        if w is not None:
            while rng.random() < gap_p:
                dut.in_valid.value = 0
                dut.in_bits.value = rng.getrandbits(112)
                await FallingEdge(dut.clk)
                if dut.out_valid.value == 1:
                    got, exp = dut.out_rem.value.to_unsigned(), expected.popleft()
                    assert got == exp, f"rem 0x{got:06x}, expected 0x{exp:06x}"
                    checked += 1
            dut.in_bits.value, dut.in_valid.value = w, 1
            expected.append(ref(w))
        else:
            dut.in_valid.value = 0
        await FallingEdge(dut.clk)
        if dut.out_valid.value == 1:
            assert expected, "unexpected output"
            got, exp = dut.out_rem.value.to_unsigned(), expected.popleft()
            assert got == exp, f"R27: rem 0x{got:06x}, expected 0x{exp:06x}"
            checked += 1
    assert checked == len(words) and not expected, f"sent {len(words)}, checked {checked}"


@cocotb.test()
async def directed(dut):
    """Spec frames, single-bit and burst errors, G(x) multiples, boundaries (R27)."""
    for w, exp, why in DIRECTED:
        if exp is not None:
            assert ref(w) == exp, f"DIRECTED entry disagrees with model: {why}"
    await start(dut)
    await stream(dut, [w for w, _, _ in DIRECTED], random.Random(0))


@cocotb.test()
async def every_column(dut):
    """Each single bit x^i, i = 0..111: checks every column of the XOR network on its
    own, so a wrong table entry or wrong bit order fails here (R27)."""
    await start(dut)
    await stream(dut, [1 << i for i in range(112)], random.Random(1), gap_p=0.0)


@cocotb.test()
async def random_and_valid_frames(dut):
    """5,000 random words, plus 2,000 random frames with correct parity (remainder 0)
    and the same frames with one random bit flipped (remainder never 0) (R27)."""
    rng = random.Random(2)
    words = [rng.getrandbits(112) for _ in range(5000)]
    for _ in range(2000):
        first88 = rng.getrandbits(88)
        good = (first88 << 24) | crc24_parity(first88)
        assert ref(good) == 0
        bad = good ^ (1 << rng.randrange(112))
        assert ref(bad) != 0, "a single-bit error always changes the remainder"
        words += [good, bad]
    await start(dut)
    await stream(dut, words, rng)


@cocotb.test()
async def reset_flushes_pipeline(dut):
    """Reset drops the in-flight result (microarch 4: reset clears valid)."""
    await start(dut)
    dut.in_bits.value, dut.in_valid.value = DF17_A, 1
    dut.rst_n.value = 0
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    assert dut.out_valid.value == 0, "result captured during reset"
    dut.rst_n.value = 1
    await FallingEdge(dut.clk)
    assert dut.out_valid.value == 0
