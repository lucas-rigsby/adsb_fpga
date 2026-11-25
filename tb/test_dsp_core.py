"""End-to-end cocotb tests for dsp_core (the whole ADS-B DSP chain, stages A-G):
raw rtl_sdr I/Q bytes in, ADS-B records out, compared byte for byte with the
reference model's decode() and record_payload() (R21 for the DSP), plus the
ADSB_CAND count (R25), sample pacing (R20, P4) and segment restarts (R17)."""
import random

import cocotb
import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge

import protocol
from adsb_ref import RATES, boundary_cases, decode, magnitudes, rand_stream, record_payload


async def start(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for s in ("in_valid", "in_i", "in_q", "in_index", "clear", "af3_full"):
        getattr(dut, s).value = 0
    dut.rate.value = 1
    dut.pre_k.value = 8
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    await FallingEdge(dut.clk)
    dut.rst_n.value = 1


class Harness:
    def __init__(self, dut, rng, gap_p=0.2, full_p=0.0):
        self.dut, self.rng, self.gap_p, self.full_p = dut, rng, gap_p, full_p
        self.records, self.cand = [], 0
        self.max_not_ready = 0
        self._not_ready = 0

    async def tick(self):
        d = self.dut
        d.af3_full.value = int(self.rng.random() < self.full_p)
        await FallingEdge(d.clk)
        if d.cand_inc.value == 1:
            self.cand += 1
        if d.out_rec.value == 1:
            self.records.append(protocol.pack_adsb(
                seq=d.out_seq.value.to_unsigned(), index=d.out_n.value.to_unsigned(),
                phase=d.out_phi.value.to_unsigned(), rate24=bool(d.out_rate.value),
                df18=bool(d.out_df18.value), level=d.out_h.value.to_unsigned(),
                frame=d.out_frame.value.to_unsigned().to_bytes(14, "big")))
        self._not_ready = 0 if d.in_ready.value == 1 else self._not_ready + 1
        self.max_not_ready = max(self.max_not_ready, self._not_ready)

    async def segment(self, iq: bytes, index, rate, pre_k):
        """New segment (R17): clear while idle, set rate and PRE_K, stream the samples."""
        d = self.dut
        while d.idle.value != 1:
            await self.tick()
        d.rate.value, d.pre_k.value, d.clear.value = rate, pre_k, 1
        await self.tick()
        d.clear.value = 0
        u = np.frombuffer(iq, dtype=np.uint8)
        k = 0
        while k < len(index):
            if d.in_ready.value == 1 and self.rng.random() >= self.gap_p:
                d.in_valid.value = 1
                d.in_i.value, d.in_q.value = int(u[2 * k]), int(u[2 * k + 1])
                d.in_index.value = int(index[k])
                k += 1
            else:
                d.in_valid.value = 0
            await self.tick()
        d.in_valid.value = 0
        while d.idle.value != 1:                                 # drain (WAIT_DRAIN)
            await self.tick()
        await self.tick()


def expected(iq, index, rate, pre_k, seq0):
    r = decode(magnitudes(iq), rate, pre_k, index)
    return [record_payload(c, rate, seq=seq0 + i) for i, c in enumerate(r.records)], r


@cocotb.test()
async def model_streams_both_rates(dut):
    """Synthetic streams at 2.4 and 2.0 MS/s, header indices with an offset and a jump
    mid-stream (R13 gap: samples processed as adjacent, positions from the headers);
    records byte-identical to the model, ADSB_CAND equal (R14, R21, R23-R28, R32, R33)."""
    rng = random.Random(1)
    await start(dut)
    h = Harness(dut, rng)
    seq = 0
    for rate, snr, frames in ((1, 20.0, 12), (0, 20.0, 12), (1, 12.0, 16)):
        iq, _ = rand_stream(np.random.default_rng(rng.getrandbits(32)), frames, rate, snr)
        n = len(iq) // 2
        index = np.arange(n, dtype=np.int64) + rng.getrandbits(36)
        cut = n // 2
        index[cut:] += 777                                       # sequence gap in the headers
        want, r = expected(iq, index, rate, 8, seq)
        got0, cand0 = len(h.records), h.cand
        await h.segment(iq, index, rate, 8)
        got = h.records[got0:]
        dut._log.info(f"rate {rate}, {snr} dB: {n} samples, {r.adsb_cand} candidates, "
                      f"{len(want)} records (model), {len(got)} (RTL)")
        assert got == want, f"rate {rate}: records differ from the model (R21)"
        assert h.cand - cand0 == r.adsb_cand, f"ADSB_CAND {h.cand - cand0} != {r.adsb_cand}"
        assert len(want) > 0
        seq += len(want)
    assert h.max_not_ready <= 7, f"P4: in_ready low for {h.max_not_ready} cycles"


@cocotb.test()
async def model_boundary_cases(dut):
    """Every boundary case of the reference model (R24-R28 thresholds, ties, DF, R28 at
    p + 239 / p + 240), each as its own segment with clear between (R17, R21)."""
    rng = random.Random(2)
    await start(dut)
    h = Harness(dut, rng, gap_p=0.0)
    seq = 0
    for name, iq, rate, pre_k, n_emit, why in boundary_cases(np.random.default_rng(1)):
        n = len(iq) // 2
        index = np.arange(n, dtype=np.int64)
        want, r = expected(iq, index, rate, pre_k, seq)
        assert len(want) == n_emit, f"model disagrees with its own case {name}"
        got0 = len(h.records)
        await h.segment(iq, index, rate, pre_k)
        assert h.records[got0:] == want, f"{name} ({why})"
        seq += len(want)


@cocotb.test()
async def full_rate_and_queue_full(dut):
    """Samples offered on every cycle: one accepted per S cycles exactly (R20, P4);
    with AF3 randomly full every record is still produced, numbered in order (R34)."""
    rng = random.Random(3)
    await start(dut)
    h = Harness(dut, rng, gap_p=0.0, full_p=0.3)
    iq, _ = rand_stream(np.random.default_rng(7), 10, 1, 20.0)
    index = np.arange(len(iq) // 2, dtype=np.int64)
    want, _ = expected(iq, index, 1, 8, 0)
    await h.segment(iq, index, 1, 8)
    assert h.records == want
    assert h.max_not_ready == RATES[1][0] - 1, f"in_ready low for {h.max_not_ready} cycles, expected 4"
