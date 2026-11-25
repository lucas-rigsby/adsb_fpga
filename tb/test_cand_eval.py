"""cocotb tests for cand_eval (stages D1-F): the preamble shape test (R24), level and
quiet-zone tests (R25), bit decisions (R26) and DF check (R27) against the reference
model (tb/adsb_ref.preamble, slice_bits, downlink_format), with exact 5-cycle latency,
back-to-back candidates, PRE_K changes while idle (R17) and reset."""
import random
from collections import Counter, deque

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

from adsb_ref import (FRAME_CHIPS, GAP_CHIPS, PULSE_CHIPS, QUIET_CHIPS, RATES, REASONS, SHAPE,
                      chip_energies, crc24_parity, downlink_format, frame_chips, magnitudes,
                      preamble, rand_frame, rand_stream, slice_bits, span)

LATENCY = 5                         # microarch 2a: D1, D2, E1, E2, F
CMAX = 6 * 382                      # largest chip energy (R33)


def pack(c) -> int:
    return sum(int(v) << (12 * j) for j, v in enumerate(c))


def expect(c, k):
    """Model outputs for one candidate: (cand, df_ok, bits, H, reason)."""
    reason, _, _ = preamble(c, k)
    bits = slice_bits(c)
    return (reason is None, downlink_format(bits) in (17, 18), bits,
            sum(c[j] for j in PULSE_CHIPS), reason or "pass")


def frame_c(bits, pulse=1200, gap=300, quiet=60, low=60):
    """Chip energies of a clean frame: preamble pulses, gaps, quiet zone, data chips."""
    c = [low] * FRAME_CHIPS
    for j in GAP_CHIPS:
        c[j] = gap
    for j in (10, 11, 12, 13, 14, 15):
        c[j] = quiet
    for j in frame_chips(bits):
        c[j] = pulse
    return c


def level_boundary_cases(f):
    """Frames with K L = 6H + 1 (fails by one) and K L = 6H (passes), found by search
    over feasible pulse levels and K; gaps stay below the pulses so R24 holds."""
    out = []
    found = {"6H + 1": 0, "6H": 0}
    for pulse in range(1100, 1400):
        H = 4 * pulse
        for k in range(2, 256):
            for target, why in ((6 * H + 1, "6H + 1"), (6 * H, "6H")):
                if target % k:
                    continue
                L = target // k
                if L > 6 * (pulse - 1) or L < 6:
                    continue
                gaps = [L // 6 + (1 if i < L % 6 else 0) for i in range(6)]
                c = frame_c(f, pulse=pulse)
                for j, g in zip(GAP_CHIPS, gaps):
                    c[j] = g
                reason = preamble(c, k)[0]
                want = "level" if target == 6 * H + 1 else None
                if reason == want and found[why] < 2:
                    found[why] += 1
                    out.append((c, k, f"R25 K L = {why} (K = {k}, H = {H})"))
            if min(found.values()) >= 2:
                return out
    raise AssertionError("no level boundary case found")


def directed_cases(rng):
    """(c, K, why) for every R24-R27 boundary in the spec."""
    f = rand_frame(rng, 17)
    out = [(frame_c(f), 8, "R25 example: H = 4,800, L = 1,800 passes")]
    # Each R24 relation tied so that it is the ONLY failing test: every other shape
    # relation, the level test and the quiet zone pass. Then a > / >= slip in that one
    # compare changes out_cand (pulses 1200, gaps 300; lowered pulses keep the others).
    ties = {
        "c0>c1": {0: 1000, 1: 1000},
        "c1<c2": {2: 1000, 1: 1000},
        "c2>c3": {2: 1000, 3: 1000},
        "c3<c0": {0: 1000, 3: 1000},
        "c4<c0": {4: 1200},
        "c5<c0": {5: 1200},
        "c6<c0": {0: 1000, 6: 1000},
        "c7>c8": {7: 1000, 8: 1000},
        "c8<c9": {9: 1000, 8: 1000},
        "c9>c6": {9: 1000, 6: 1000},
    }
    shape = {n: (x, y) for n, x, y in SHAPE}
    for name, edits in ties.items():
        c = frame_c(f)
        for j, v in edits.items():
            c[j] = v
        failing = [n for n, (x, y) in shape.items() if not c[x] > c[y]]
        assert failing == [name], f"tie for {name} fails {failing}"
        fixed = list(c)
        fixed[shape[name][0]] += 1                     # break the tie the right way
        assert preamble(fixed, 8)[0] is None, f"{name}: everything else must pass"
        out.append((c, 8, f"R24 {name} tied, the only failing test"))
        out.append((fixed, 8, f"R24 {name} one above the tie passes"))
    # R25 quiet zone at H = 4 c_k + 1: passes, and fails for 4 c_k + 1 < H off by one
    c = frame_c(f); c[9] = 1201; c[12] = 1200
    assert sum(c[j] for j in PULSE_CHIPS) == 4 * 1200 + 1 and preamble(c, 8)[0] is None
    out.append((c, 8, "R25 4 c12 = H - 1 passes"))
    # R25 level at K L = 6H + 1 (fails) and K L = 6H (passes)
    for c, k, why in level_boundary_cases(f):
        out.append((c, k, why))
    out.append((frame_c(f, gap=600), 8, "R25 6H == K L (28,800) passes"))
    c = frame_c(f, gap=600); c[4] = 601
    out.append((c, 8, "R25 6H == K L - 8 fails"))
    c = frame_c(f); c[12] = 1200
    out.append((c, 8, "R25 4 c12 == H fails"))
    c = frame_c(f); c[12] = 1199
    out.append((c, 8, "R25 4 c12 == H - 4 passes"))
    for k in QUIET_CHIPS:
        c = frame_c(f); c[k] = 1200
        out.append((c, 8, f"R25 quiet chip {k} at H/4 fails"))
    out.append((frame_c(f, gap=1199), 0, "K = 0: level always passes"))
    out.append((frame_c(f, gap=1), 255, "K = 255, tiny gaps: passes"))
    out.append((frame_c(f, gap=19), 255, "K = 255: 255 x 114 > 28,800 fails"))
    out.append((frame_c(f, pulse=CMAX, gap=CMAX - 1, quiet=CMAX - 1, low=0), 4,
                "max energies: 6H = 55,008 >= 4 x 13,746 passes; quiet 4 x 2,291 < 9,168"))
    out.append((frame_c(f, pulse=CMAX, gap=CMAX - 1, quiet=0, low=0), 5,
                "max energies: 5 x 13,746 > 55,008 fails"))
    out.append(([0] * FRAME_CHIPS, 8, "all zero: shape fails"))
    out.append(([CMAX] * FRAME_CHIPS, 8, "all max: shape fails, bits all 0 (ties)"))
    ones = [i for i in range(112) if f >> (111 - i) & 1]
    c = frame_c(f); i = ones[0]; c[16 + 2 * i] = c[17 + 2 * i] = 1200
    out.append((c, 8, "R26 tie on a 1 bit decides 0"))
    for df in (16, 17, 18, 19, 11):
        first88 = (df << 83) | (rand_frame(rng) >> 24) & ((1 << 83) - 1)
        out.append((frame_c((first88 << 24) | crc24_parity(first88)), 8, f"R27 DF {df}"))
    return out


def stream_cases(rng, per_rate=1500):
    """Real candidate windows: every (n, phi) near synthetic frames, both rates."""
    out = []
    for rate in (1, 0):
        S, phases, _ = RATES[rate]
        iq, truth = rand_stream(np.random.default_rng(int(rng.random() * 1e9)), 6, rate, 15.0)
        m = magnitudes(iq)
        starts = sorted({max(0, int(t * float(RATES[rate][2])) - 8) for t, *_ in truth})
        for n0 in starts:
            for n in range(n0, n0 + 16):
                for phi in phases:
                    if n + span(S, phi) < m.size:
                        out.append((chip_energies(m, n, phi, S), 8, f"stream rate {rate}"))
        rng.shuffle(out)
    return out[: 2 * per_rate]


def random_cases(rng, n=2000):
    """Random frames with levels straddling the R25 thresholds."""
    out = []
    for _ in range(n):
        f = rand_frame(np.random.default_rng(rng.getrandbits(32)))
        pulse = rng.randint(200, CMAX)
        k = rng.choice((0, 1, 8, 8, 8, 16, 255, rng.randint(0, 255)))
        ratio = rng.uniform(0.5, 1.5)
        gap = min(pulse - 1, max(0, int(round(pulse * 4 * ratio / max(k, 1)))))
        quiet = min(CMAX, max(0, int(round(pulse * rng.uniform(0.9, 1.1)))))
        c = frame_c(f, pulse, gap, quiet, rng.randint(0, pulse))
        for _ in range(rng.randint(0, 6)):                       # noise on a few chips
            j = rng.randrange(FRAME_CHIPS)
            c[j] = rng.randint(0, CMAX)
        out.append((c, k, "random"))
    return out


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    dut.in_valid.value = 0
    dut.in_taps.value = 0
    dut.in_phi.value = 0
    dut.in_p.value = 0
    dut.pre_k.value = 8
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


async def run(dut, cases, rng, gap_p=0.3):
    """Stream cases (changing PRE_K only while idle, R17); check every output in order.
    Returns a Counter of model outcomes (functional coverage)."""
    expected = deque()
    seen = Counter()
    k_now = None
    tag = 0

    async def tick():
        await FallingEdge(dut.clk)
        in_flight = len(expected)              # includes a candidate now on the output
        assert dut.idle.value == int(in_flight == 0), "idle must be high exactly when nothing is in flight"
        if dut.out_valid.value == 1:
            assert expected, "unexpected output"
            (cand, df_ok, bits, h, reason), phi, p, why = expected.popleft()
            assert dut.out_cand.value == int(cand), f"R24/R25 ({reason}): out_cand ({why})"
            assert dut.out_df_ok.value == int(df_ok), f"R27 df_ok ({why})"
            assert dut.out_bits.value.to_unsigned() == bits, f"R26 bits ({why})"
            assert dut.out_h.value.to_unsigned() == h, f"R25 H ({why})"
            assert dut.out_phi.value.to_unsigned() == phi and dut.out_p.value.to_unsigned() == p

    for c, k, why in cases:
        if k != k_now:                                           # R17: change PRE_K only when idle
            dut.in_valid.value = 0
            while expected:
                await tick()
            dut.pre_k.value = k_now = k
        while rng.random() < gap_p:
            dut.in_valid.value = 0
            dut.in_taps.value = rng.getrandbits(2880)
            await tick()
        phi, p = tag % 5, tag % 512
        tag += 1
        dut.in_taps.value, dut.in_phi.value, dut.in_p.value = pack(c), phi, p
        dut.in_valid.value = 1
        e = expect(c, k)
        seen[e[4]] += 1
        seen["df_ok" if e[1] else "df_bad"] += 1
        expected.append((e, phi, p, why))
        await tick()
    dut.in_valid.value = 0
    while expected:
        await tick()
    await FallingEdge(dut.clk)              # cocotb 2.1-dev crashes on a write pending at exit
    return seen


@cocotb.test()
async def latency_exact(dut):
    """The R25 worked example passes, exactly 5 cycles later (R24, R25)."""
    await start(dut)
    c, k, _ = directed_cases(np.random.default_rng(1))[0]
    dut.in_taps.value, dut.in_valid.value = pack(c), 1
    await FallingEdge(dut.clk)
    dut.in_valid.value = 0
    dut.in_taps.value = 0
    for cycle in range(1, LATENCY + 3):
        if dut.out_valid.value == 1:
            assert cycle == LATENCY, f"output after {cycle} cycles, expected {LATENCY}"
            assert dut.out_cand.value == 1 and dut.out_h.value.to_unsigned() == 4800
            return
        await FallingEdge(dut.clk)
    raise AssertionError("out_valid never went high")


@cocotb.test()
async def directed(dut):
    """Every R24-R27 boundary from the spec, back to back (R24, R25, R26, R27)."""
    await start(dut)
    seen = await run(dut, directed_cases(np.random.default_rng(2)), random.Random(2), gap_p=0.0)
    dut._log.info(f"directed outcomes: {dict(seen)}")


@cocotb.test()
async def stream_and_random(dut):
    """Real candidate windows from synthetic streams at both rates, plus random frames
    straddling the thresholds; every model outcome must occur (R24, R25, R26, R27)."""
    rng = random.Random(3)
    await start(dut)
    cases = directed_cases(np.random.default_rng(3)) + stream_cases(rng) + random_cases(rng)
    seen = await run(dut, cases, rng)
    dut._log.info(f"outcomes: {dict(seen)}")
    missing = [r for r in REASONS + ("pass", "df_ok", "df_bad") if seen[r] == 0]
    assert not missing, f"functional coverage: outcomes never exercised: {missing}"


@cocotb.test()
async def reset_flushes_pipeline(dut):
    """Reset mid-pipeline drops every in-flight candidate (microarch 4)."""
    await start(dut)
    dut.in_valid.value = 1
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value, dut.in_valid.value = 0, 0
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1
    for _ in range(LATENCY + 2):
        assert dut.out_valid.value == 0, "candidate survived reset"
        assert dut.idle.value == 1
        await FallingEdge(dut.clk)
