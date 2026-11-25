"""cocotb tests for adsb_emit (stage G): frame acceptance (R27), the header-derived
frame start T = S n + phi (R14), overlap suppression (R28), record sequence numbers
and output-queue overflow (R34), against the reference model: decode() for real
streams, record_payload() for the record bytes, and suppress() for the R28 rule."""
import random
from collections import deque

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

import protocol
from adsb_ref import (FRAME_TICKS, RATES, Candidate, crc24_remainder, decode, downlink_format,
                      magnitudes, rand_frame, rand_stream, record_payload, suppress)

LATENCY = 3                     # G1, G2, G3


class Cand:
    """One candidate as cand_eval + crc24_112 + the index ring present it."""

    def __init__(self, n, phi, bits, h=1000, cand=True, df_ok=None, rem=None):
        self.n, self.phi, self.bits, self.h, self.cand = n, phi, bits, h, cand
        self.df_ok = downlink_format(bits) in (17, 18) if df_ok is None else df_ok
        self.rem = crc24_remainder(bits) if rem is None else rem

    def ok(self):
        return self.cand and self.df_ok and self.rem == 0


def expected_records(cands, rate, seq0=0):
    """R27 + R28 with the model's suppress(); returns (record payload bytes, seq)."""
    S = RATES[rate][0]
    ok = [c for c in cands if c.ok()]
    keep = suppress([S * c.n + c.phi for c in ok])
    out = []
    for c, k in zip(ok, keep):
        if k:
            m = Candidate(c.n, c.phi, S * c.n + c.phi, c.h, 0, c.bits, c.rem, "emitted")
            out.append(record_payload(m, rate, seq=seq0 + len(out)))
    return out


async def start(dut, rate=1):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for s in ("in_valid", "in_cand", "in_df_ok", "in_bits", "in_h", "in_phi", "in_rem", "in_n",
              "clear", "af3_full"):
        getattr(dut, s).value = 0
    dut.rate.value = rate
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


class Driver:
    """Drives candidates (fields at t, remainder and index at t + 1) and collects records."""

    def __init__(self, dut, rng, gap_p=0.3, full_p=0.0):
        self.dut, self.rng, self.gap_p, self.full_p = dut, rng, gap_p, full_p
        self.late = None
        self.records, self.pushed, self.dropped, self.cand_inc = [], 0, 0, 0
        self.in_flight = 0

    async def tick(self, c=None):
        d = self.dut
        if c is not None:
            d.in_valid.value, d.in_cand.value, d.in_df_ok.value = 1, int(c.cand), int(c.df_ok)
            d.in_bits.value, d.in_h.value, d.in_phi.value = c.bits, c.h, c.phi
        else:
            d.in_valid.value = 0
            d.in_cand.value, d.in_df_ok.value = self.rng.getrandbits(1), self.rng.getrandbits(1)
            d.in_bits.value, d.in_phi.value = self.rng.getrandbits(112), self.rng.randrange(5)
        if self.late is not None:                       # remainder and index, one cycle late
            d.in_rem.value, d.in_n.value = self.late.rem, self.late.n
        else:
            d.in_rem.value, d.in_n.value = self.rng.getrandbits(24), self.rng.getrandbits(64)
        d.af3_full.value = int(self.rng.random() < self.full_p)
        self.late = c
        await FallingEdge(d.clk)
        if d.cand_inc.value == 1:
            self.cand_inc += 1
        if d.out_rec.value == 1:
            full = d.af3_full.value == 1
            assert d.out_push.value == int(not full) and d.out_drop.value == int(full), "R34 push/drop"
            self.pushed += int(not full)
            self.dropped += int(full)
            self.records.append(protocol.pack_adsb(
                seq=d.out_seq.value.to_unsigned(), index=d.out_n.value.to_unsigned(),
                phase=d.out_phi.value.to_unsigned(), rate24=bool(d.out_rate.value),
                df18=bool(d.out_df18.value), level=d.out_h.value.to_unsigned(),
                frame=d.out_frame.value.to_unsigned().to_bytes(14, "big")))
        else:
            assert d.out_push.value == 0 and d.out_drop.value == 0

    async def run(self, cands):
        for c in cands:
            while self.rng.random() < self.gap_p:
                await self.tick()
            await self.tick(c)
        for _ in range(LATENCY + 2):
            await self.tick()
        assert self.dut.idle.value == 1


def ok_frame(rng, df=17):
    return rand_frame(np.random.default_rng(rng.getrandbits(32)), df)


@cocotb.test()
async def latency_exact(dut):
    """A valid frame becomes a record exactly 3 cycles after in_valid (R27)."""
    await start(dut, rate=1)
    rng = random.Random(0)
    f = ok_frame(rng)
    drv = Driver(dut, rng, gap_p=0.0)
    await drv.tick(Cand(1000, 2, f, h=4800))
    for cycle in range(1, LATENCY + 3):
        if dut.out_rec.value == 1:
            assert cycle == LATENCY, f"record after {cycle} cycles, expected {LATENCY}"
            assert dut.out_n.value.to_unsigned() == 1000 and dut.out_phi.value == 2
            assert dut.out_h.value.to_unsigned() == 4800 and dut.out_seq.value == 0
            return
        await drv.tick()
    raise AssertionError("no record")


@cocotb.test()
async def r28_worked_example(dut):
    """Spec R28 example at 2.4 MS/s: emit at (1000, 2), T = 5,002; (1000, 3) and (1288, 1)
    (T' = 6,441) are suppressed; (1288, 2) (T' = 6,442) is emitted (R28, R14)."""
    await start(dut, rate=1)
    rng = random.Random(1)
    cands = [Cand(1000, 2, ok_frame(rng)), Cand(1000, 3, ok_frame(rng)),
             Cand(1288, 1, ok_frame(rng)), Cand(1288, 2, ok_frame(rng))]
    drv = Driver(dut, rng, gap_p=0.0)
    await drv.run(cands)
    want = expected_records(cands, 1)
    assert drv.records == want, "R28 example"
    assert [protocol.parse_adsb(r).index for r in drv.records] == [1000, 1288]


@cocotb.test()
async def rejection_kinds(dut):
    """Only cand AND df_ok AND rem == 0 is emitted; cand_inc counts every cand (R25, R27)."""
    await start(dut, rate=0)
    rng = random.Random(2)
    f = ok_frame(rng)
    first88 = (11 << 83) | (f >> 24) & ((1 << 83) - 1)
    df11 = (first88 << 24) | crc24_remainder(first88 << 24)
    cands = [Cand(0, 0, f, cand=False),                          # failed R24/R25: nothing
             Cand(400, 0, f ^ 1),                                # CRC fails
             Cand(800, 0, df11),                                 # DF 11, CRC fine
             Cand(1200, 0, ok_frame(rng, 18)),                   # DF18: emitted
             Cand(1600, 0, f, cand=False, df_ok=True, rem=0)]    # ok fields but not a candidate
    drv = Driver(dut, rng)
    await drv.run(cands)
    assert drv.records == expected_records(cands, 0)
    assert len(drv.records) == 1 and protocol.parse_adsb(drv.records[0]).df18
    assert drv.cand_inc == 3, f"R25: ADSB_CAND pulses {drv.cand_inc}, expected 3"


@cocotb.test()
async def model_streams(dut):
    """Every preamble-passing candidate of the model's decode() on synthetic streams at
    both rates, in T order with non-candidates between: records byte-identical to the
    model's, ADSB_CAND and ADSB_FRAMES equal (R14, R21, R25, R27, R28)."""
    rng = random.Random(3)
    await start(dut, rate=1)
    seq = 0
    total_records = 0
    for rate, snr, frames in ((1, 20.0, 40), (0, 20.0, 40), (1, 12.0, 60)):
        dut.rate.value = rate
        dut.clear.value = 1                                      # new segment (R17)
        await FallingEdge(dut.clk)
        dut.clear.value = 0
        iq, _ = rand_stream(np.random.default_rng(rng.getrandbits(32)), frames, rate, snr)
        m = magnitudes(iq)
        index = np.arange(m.size, dtype=np.int64) + rng.getrandbits(40)   # header indices
        r = decode(m, rate, 8, index)
        cands = []
        for c in r.candidates:
            if rng.random() < 0.5:                               # a few non-candidates between
                cands.append(Cand(int(c.n), rng.randrange(5), rng.getrandbits(112), cand=False))
            cands.append(Cand(int(c.n), c.phi, c.bits, h=c.H))
        drv = Driver(dut, rng)
        await drv.run(cands)
        want = [record_payload(c, rate, seq=seq + i) for i, c in enumerate(r.records)]
        assert drv.records == want, f"rate {rate}: records differ from the model (R21)"
        assert drv.cand_inc == r.adsb_cand, f"ADSB_CAND {drv.cand_inc} != {r.adsb_cand}"
        assert len(r.records) > 0
        if snr < 20:                                             # weak signal: every outcome occurs
            kinds = {c.outcome for c in r.candidates}
            assert kinds >= {"df", "crc", "suppressed", "emitted"}, kinds
        seq += len(r.records)
        total_records += len(r.records)
        dut._log.info(f"rate {rate}, SNR {snr}: {len(r.candidates)} candidates, {len(r.records)} records")
    assert total_records == seq


@cocotb.test()
async def r28_boundaries_and_clear(dut):
    """T_last + 1439 suppressed, T_last + 1440 emitted; clear forgets T_last (a smaller T
    in the new segment is emitted); the sequence number keeps counting (R17, R28)."""
    await start(dut, rate=0)                                     # S = 6
    rng = random.Random(4)
    drv = Driver(dut, rng)
    base = 10_000                                                # T = 60,000
    a = [Cand(base, 0, ok_frame(rng)), Cand(base + 239, 0, ok_frame(rng)),   # T + 1434
         Cand(base + 240, 0, ok_frame(rng))]                                 # T + 1440
    await drv.run(a)
    assert [protocol.parse_adsb(r).index for r in drv.records] == [base, base + 240]
    dut.clear.value = 1
    await FallingEdge(dut.clk)
    dut.clear.value = 0
    await drv.run([Cand(5, 0, ok_frame(rng))])                   # earlier T, new segment
    recs = [protocol.parse_adsb(r) for r in drv.records]
    assert [x.index for x in recs] == [base, base + 240, 5], "R17: clear resets suppression"
    assert [x.seq for x in recs] == [0, 1, 2], "sequence numbers run across segments"


@cocotb.test()
async def huge_indices(dut):
    """Indices near 2^64: T = S n + phi needs 67 bits and the limit 68; no wrap (R14, R28)."""
    rng = random.Random(5)
    await start(dut, rate=0)
    n0 = (1 << 64) - 1000
    cands = [Cand(n0, 0, ok_frame(rng)), Cand(n0 + 239, 0, ok_frame(rng)),
             Cand(n0 + 240, 0, ok_frame(rng)), Cand(n0 + 999, 0, ok_frame(rng))]
    drv = Driver(dut, rng)
    await drv.run(cands)
    assert drv.records == expected_records(cands, 0)
    assert [protocol.parse_adsb(r).index for r in drv.records] == [n0, n0 + 240, n0 + 999]


@cocotb.test()
async def queue_full_drops(dut):
    """AF3 full: the record is dropped and counted (out_drop), never pushed, and its
    sequence number is still used, so the host sees the gap (R34)."""
    rng = random.Random(6)
    await start(dut, rate=1)
    cands = [Cand(1000 + 300 * i, rng.randrange(5), ok_frame(rng)) for i in range(200)]
    drv = Driver(dut, rng, gap_p=0.2, full_p=0.3)
    await drv.run(cands)
    want = expected_records(cands, 1)
    assert drv.records == want, "every emitted record is still produced, with its sequence number"
    assert drv.pushed + drv.dropped == len(want) and drv.dropped > 0 and drv.pushed > 0
    assert [protocol.parse_adsb(r).seq for r in drv.records] == list(range(len(want)))


@cocotb.test()
async def reset_flushes(dut):
    """Reset drops in-flight candidates, clears the suppression history and the sequence."""
    await start(dut, rate=1)
    rng = random.Random(7)
    drv = Driver(dut, rng, gap_p=0.0)
    await drv.run([Cand(100, 0, ok_frame(rng))])
    await drv.tick(Cand(120, 0, ok_frame(rng)))                  # in flight
    dut.rst_n.value = 0
    await drv.tick()
    dut.rst_n.value = 1
    for _ in range(LATENCY + 1):
        await drv.tick()
    assert len(drv.records) == 1, "candidate survived reset"
    await drv.run([Cand(110, 0, ok_frame(rng))])                 # would be suppressed without reset
    assert len(drv.records) == 2 and protocol.parse_adsb(drv.records[1]).seq == 0
