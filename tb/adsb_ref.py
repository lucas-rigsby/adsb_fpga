"""Golden reference model for the ADS-B DSP chain (docs/spec.md v0.4: R13, R14, R17, R19, R21-R28, R32, R33).

Stages and their taps (each is a public function so a failing block test points to one block):

    magnitudes()      R19 + R22   u8 I/Q bytes      -> m[n]
    chip_weights()    R33         (S, phi, chip j)  -> {sample offset: ticks of overlap}
    chip_energies()   R33         m, candidate      -> c[0..239]
    preamble()        R24, R25    c                 -> (first failing test or None, H, L)
    slice_bits()      R26         c                 -> 112-bit int, bit 0 = MSB
    crc24_remainder() R27         112-bit int       -> remainder
    suppress()        R28         sorted T list     -> which are emitted
    decode()          all of the above over a whole stream -> Result
    SystemModel       R13, R14, R16, R17, R21, R32: packets and CSR operations in, records out

Independence from the RTL
-------------------------
* Chip energies come from a generic integer interval-intersection of each chip
  [T+6j, T+6j+6) with each sample [S*k, S*k+S), in ticks of 1/12 us. The RTL is
  expected to use the closed form (5-s, 1+s) of R33; the doctests check the two agree.
* The CRC is polynomial long division on a Python big int, not a parallel XOR
  network or a byte-wise table.
* The preamble test is evaluated with numpy over whole arrays, then candidates
  that pass are replayed one at a time in increasing T (R28). The RTL evaluates
  one candidate at a time as samples arrive.

Exactness
---------
Everything is integer. m <= 382 (9 bits), c <= 6*382 = 2,292, H <= 9,168,
L <= 13,752, PRE_K*L <= 255*13,752 < 2^22 and 6H < 2^16: all far inside
int64, so numpy never wraps. No floats are used in the model (the synthetic
signal generator uses floats, but it only makes stimulus).

Stream rules (spec v0.4; these were assumptions A1-A3 in v0.3)
--------------------------------------------------------------
A1 (R13) Across a sequence gap the samples are processed as if adjacent; a
   candidate's n is the header-derived index of its first sample, so T uses
   real indices even across a gap. An index lower than the previous accepted
   I/Q packet's index + N (flag bit 0 clear) starts a new stream.
A2 (R25) ADSB_CAND counts every candidate that passes R24 and R25, including
   those later rejected by DF, CRC or R28 suppression.
A3 (R23) A candidate is evaluated only once every sample its 240 chips touch
   has arrived. Candidates cut short by a new stream or a configuration write
   (R17) are never evaluated.

Doctests (python3 -m doctest -v tb/adsb_ref.py):

R19 input conversion
>>> [to_signed(u) for u in (0, 128, 255)]
[-255, 1, 255]

R22 magnitude
>>> magnitude(-255, 101)
305

R33 chip weights: generic overlap equals the closed form (5-s, 1+s) for every offset
>>> all(chip_weights(5, t % 5, 0) == {0: 5 - t % 5, 1: 1 + t % 5} for t in range(5))
True
>>> chip_weights(6, 0, 7)                    # 2.0 MS/s: chip j is 6 x sample j
{7: 6}

R33 worked example: candidate (100, 3), chip 0, m[100] = 50, m[101] = 300
>>> m = np.zeros(400, dtype=np.int64); m[100], m[101] = 50, 300
>>> chip_energy(m, 100, 3, 5, 0)
1300

R26 equals dump1090-fa's slice_phase0..4 weights (first chip minus second chip)
>>> [bit_weights(s) for s in range(5)]
[[5, -3, -2], [4, -1, -3], [3, 1, -4], [2, 3, -5], [1, 5, -5, -1]]

R26 worked example: offset 0, m = (300, 20, 10) -> 1420 > 0 -> bit 1
>>> sum(w * v for w, v in zip(bit_weights(0), (300, 20, 10)))
1420

R25 worked example: pulses 1,200, gaps 300, quiet 0 -> passes with H = 4,800, L = 1,800
>>> c = [0] * 240
>>> for j in PULSE_CHIPS: c[j] = 1200
>>> for j in GAP_CHIPS: c[j] = 300
>>> preamble(c, 8)
(None, 4800, 1800)
>>> c[12] = 1200; preamble(c, 8)[0]       # quiet zone: 4*1200 = H, not < H
'quiet12'

R27 CRC: known-good DF17 messages have remainder 0; one flipped bit does not
>>> crc24_remainder(int('8D4840D6202CC371C32CE0576098', 16))
0
>>> crc24_remainder(int('8D406B902015A678D4D220AA4BDA', 16))
0
>>> crc24_remainder(int('8D4840D6202CC371C32CE0576098', 16) ^ (1 << 50)) != 0
True
>>> v = (0x8D4840D6202CC371C32CE0 << 24); crc24_parity(v >> 24) == 0x576098
True

R28 worked example (2.4 MS/s): emit at (1000, 2), T = 5,002
>>> suppress([5002, 5003, 6441, 6442])
[True, False, False, True]

R14 worked example: n = 2,400,000, phi = 3 at 2.4 MS/s -> 1.00000025 s
>>> timestamp_us(2_400_000, 3, rate=1)
Fraction(4000001, 4)
"""
from __future__ import annotations

import pathlib
import sys
from collections import Counter
from fractions import Fraction
from typing import NamedTuple

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "host"))
import protocol  # noqa: E402

# ------------------------------------------------------------------ constants
TICKS_PER_US = 12
CHIP_TICKS = 6
FRAME_CHIPS = 240
FRAME_TICKS = FRAME_CHIPS * CHIP_TICKS      # 1,440 = 120 us (R28)
DATA_BITS = 112
DATA_CHIP0 = 16
CRC_GEN = 0x1FFF409                         # R27
PRE_K_DEFAULT = 8
PRE_K_MAX = 255                              # bits [7:0] (assumed width)
MAG_MAX = 382

# R32: ADSB_RATE -> (S ticks per sample, allowed phases, MS/s)
RATES = {0: (6, (0,), Fraction(2)), 1: (5, (0, 1, 2, 3, 4), Fraction(12, 5))}

PULSE_CHIPS = (0, 2, 7, 9)                   # R24/R25
GAP_CHIPS = (1, 3, 4, 5, 6, 8)
QUIET_CHIPS = (11, 12, 13, 14)
# R24 as (a, b) pairs meaning c_a > c_b, in the order the spec lists them
SHAPE = (
    ("c0>c1", 0, 1), ("c1<c2", 2, 1), ("c2>c3", 2, 3), ("c3<c0", 0, 3), ("c4<c0", 0, 4),
    ("c5<c0", 0, 5), ("c6<c0", 0, 6), ("c7>c8", 7, 8), ("c8<c9", 9, 8), ("c9>c6", 9, 6),
)
REASONS = tuple(r for r, _, _ in SHAPE) + ("level",) + tuple(f"quiet{k}" for k in QUIET_CHIPS)


# ---------------------------------------------------------- R19, R22 magnitude
def to_signed(u: int) -> int:
    """R19: unsigned byte -> odd signed value 2u - 255."""
    assert 0 <= u <= 255, "R19: input is a byte"
    return 2 * u - 255


def magnitude(xi: int, xq: int) -> int:
    """R22: max(|I|,|Q|) + (min(|I|,|Q|) >> 1)."""
    a, b = abs(xi), abs(xq)
    return max(a, b) + (min(a, b) >> 1)


def magnitudes(iq) -> np.ndarray:
    """R19 + R22 over interleaved u8 I/Q (bytes or uint8 array) -> int64 m[n]."""
    u = np.frombuffer(bytes(iq), dtype=np.uint8).astype(np.int64) if not isinstance(iq, np.ndarray) else iq.astype(np.int64)
    assert u.size % 2 == 0, "I/Q comes in pairs"
    x = np.abs(2 * u - 255).reshape(-1, 2)
    return x.max(axis=1) + (x.min(axis=1) >> 1)


# -------------------------------------------------------------- R33 chip energy
def chip_weights(S: int, phi: int, j: int) -> dict[int, int]:
    """R33: overlap in ticks of chip j of a candidate starting at tick phi of sample 0
    with each sample k = [S*k, S*k+S). Generic interval intersection (not the closed form)."""
    lo = phi + CHIP_TICKS * j
    hi = lo + CHIP_TICKS
    w = {}
    for k in range(lo // S, (hi - 1) // S + 1):
        ov = min(hi, S * k + S) - max(lo, S * k)
        if ov > 0:
            w[k] = ov
    assert sum(w.values()) == CHIP_TICKS
    return w


def span(S: int, phi: int) -> int:
    """Offset of the last sample any of the 240 chips touches (R29's final sample)."""
    return (phi + FRAME_TICKS - 1) // S


def chip_energy(m, n: int, phi: int, S: int, j: int) -> int:
    return int(sum(w * int(m[n + k]) for k, w in chip_weights(S, phi, j).items()))


def chip_energies(m, n: int, phi: int, S: int) -> list[int]:
    return [chip_energy(m, n, phi, S, j) for j in range(FRAME_CHIPS)]


def bit_weights(s: int) -> list[int]:
    """Weights of c_first - c_second for a bit whose first chip starts at offset s of a
    2.4 MS/s sample (used to cross-check against dump1090-fa's slice_phase functions)."""
    a, b = chip_weights(5, s, 0), chip_weights(5, s, 1)
    d = [a.get(k, 0) - b.get(k, 0) for k in range(4)]
    while d[-1] == 0:
        d.pop()
    return d


# ------------------------------------------------------------ R24, R25 preamble
def preamble(c, pre_k: int):
    """R24 then R25. Returns (name of the first failing test or None, H, L)."""
    assert 0 <= pre_k <= PRE_K_MAX, "PRE_K is 8 bits"
    for name, a, b in SHAPE:                       # R24
        if not c[a] > c[b]:
            return name, None, None
    H = sum(c[j] for j in PULSE_CHIPS)             # R25
    L = sum(c[j] for j in GAP_CHIPS)
    if not 6 * H >= pre_k * L:
        return "level", H, L
    for k in QUIET_CHIPS:
        if not 4 * c[k] < H:
            return f"quiet{k}", H, L
    return None, H, L


# -------------------------------------------------------------- R26, R27 bits
def slice_bits(c) -> int:
    """R26: bit i = 1 iff c[16+2i] > c[17+2i] (ties give 0). Bit 0 is the MSB of the result."""
    v = 0
    for i in range(DATA_BITS):
        v = (v << 1) | (1 if c[DATA_CHIP0 + 2 * i] > c[DATA_CHIP0 + 2 * i + 1] else 0)
    return v


def crc24_remainder(v: int, nbits: int = DATA_BITS) -> int:
    """R27: remainder of the nbits-bit polynomial v (MSB first) modulo 0x1FFF409."""
    assert 0 <= v < (1 << nbits)
    for i in range(nbits - 1, 23, -1):
        if v >> i & 1:
            v ^= CRC_GEN << (i - 24)
    return v


def crc24_parity(first88: int) -> int:
    """Parity field that makes the 112-bit frame's remainder zero (for building stimulus)."""
    return crc24_remainder(first88 << 24)


def downlink_format(v: int) -> int:
    return v >> (DATA_BITS - 5)


# ------------------------------------------------------------------ R28
def suppress(ts: list[int]) -> list[bool]:
    """R28 over CRC-valid candidates sorted by T: True where a record is emitted."""
    out, last = [], None
    for t in ts:
        assert last is None or t >= last, "R28: candidates are evaluated in increasing T"
        ok = last is None or t >= last_emit + FRAME_TICKS
        if ok:
            last_emit = t
        out.append(ok)
        last = t
    return out


def timestamp_us(n: int, phi: int, rate: int) -> Fraction:
    """R14: frame start in microseconds after stream start."""
    S = RATES[rate][0]
    return Fraction(S * n + phi, TICKS_PER_US)


# ------------------------------------------------------------------ decode
class Candidate(NamedTuple):
    n: int           # header-derived index of the candidate's first sample
    phi: int
    T: int           # S*n + phi, ticks
    H: int
    L: int
    bits: int        # 112 bits, MSB = bit 0
    remainder: int
    outcome: str     # 'emitted' | 'suppressed' | 'df' | 'crc'


class Result(NamedTuple):
    m: np.ndarray
    reasons: Counter          # every candidate's first failing test, plus 'pass'
    candidates: list          # Candidates that passed R24+R25, in T order
    records: list             # the emitted subset

    @property
    def adsb_cand(self) -> int:
        return self.reasons["pass"]


def decode(m, rate: int = 1, pre_k: int = PRE_K_DEFAULT, index=None) -> Result:
    """Run R23-R28 on one uninterrupted segment of magnitudes m (from magnitudes()).

    index[k] is the stream sample index of m[k] (default 0, 1, 2, ...); see A1.
    """
    assert rate in RATES, "R32: ADSB_RATE bit 0"
    S, phases, _ = RATES[rate]
    m = np.asarray(m, dtype=np.int64)
    N = m.size
    index = np.arange(N, dtype=np.int64) if index is None else np.asarray(index, dtype=np.int64)
    assert index.size == N
    reasons: Counter = Counter()
    passing = []
    for phi in phases:                                          # R23: every (n, phi)
        P = N - span(S, phi)                                    # A3: full window present
        if P <= 0:
            continue
        c = {}
        for j in range(15):
            acc = np.zeros(P, dtype=np.int64)
            for k, w in chip_weights(S, phi, j).items():
                acc += w * m[k:k + P]
            c[j] = acc
        fail = np.full(P, -1, dtype=np.int64)                   # index into REASONS
        conds = [c[a] > c[b] for _, a, b in SHAPE]
        H = c[0] + c[2] + c[7] + c[9]
        L = c[1] + c[3] + c[4] + c[5] + c[6] + c[8]
        conds.append(6 * H >= pre_k * L)
        conds += [4 * c[k] < H for k in QUIET_CHIPS]
        for r, ok in enumerate(conds):
            newly = (fail < 0) & ~ok
            fail[newly] = r
            reasons[REASONS[r]] += int(newly.sum())
        good = np.nonzero(fail < 0)[0]
        reasons["pass"] += int(good.size)
        passing += [(int(S * index[p] + phi), int(p), phi, int(H[p]), int(L[p])) for p in good]
    passing.sort()
    cands, last_emit = [], None
    for T, p, phi, H, L in passing:                             # R28: increasing T
        c = chip_energies(m, p, phi, S)
        assert preamble(c, pre_k) == (None, H, L)               # vectorized == scalar
        bits = slice_bits(c)
        rem = crc24_remainder(bits)
        if downlink_format(bits) not in (17, 18):
            outcome = "df"
        elif rem != 0:
            outcome = "crc"
        elif last_emit is not None and T < last_emit + FRAME_TICKS:
            outcome = "suppressed"
        else:
            outcome, last_emit = "emitted", T
        cands.append(Candidate(int(index[p]), phi, T, H, L, bits, rem, outcome))
    return Result(m, reasons, cands, [c for c in cands if c.outcome == "emitted"])


def record_payload(c: Candidate, rate: int, seq: int = 0) -> bytes:
    """R27 record as the FPGA sends it (sequence numbers are excluded from R21 comparison)."""
    return protocol.pack_adsb(seq=seq, index=c.n, phase=c.phi, rate24=bool(rate), df18=downlink_format(c.bits) == 18,
                              level=c.H, frame=c.bits.to_bytes(14, "big"))


# ------------------------------------------------------------------ system level
class SystemModel:
    """Packet- and CSR-level model of the DSP for R21: feed I/Q payloads and CSR
    operations in Ethernet arrival order (R17), read record payloads out.

    Modeled registers: MODE, PRE_K, ADSB_RATE, CTRL (clear), ADSB_CAND, ADSB_FRAMES.
    Ethernet counters are not modeled (excluded from R21).

    >>> s = SystemModel(); s.csr_write(protocol.CSR["MODE"], 1)     # R15 worked example
    1
    >>> s.csr_write(protocol.CSR["PRE_K"], 0x108)                   # bits above [7:0] ignored
    8
    >>> s.csr_write(protocol.CSR["CTRL"], 1), s.csr_read(protocol.CSR["ADSB_CAND"])
    (0, 0)
    """

    def __init__(self):
        self.mode, self.pre_k, self.rate = 0, PRE_K_DEFAULT, 1
        self.adsb_cand = self.adsb_frames = 0                   # closed segments, since reset
        self._base_cand = self._base_frames = 0                 # CTRL clear baselines (R16)
        self.records: list[bytes] = []
        self._u: list[bytes] = []
        self._idx: list[np.ndarray] = []
        self._expect: int | None = None                         # previous accepted packet's index + N

    def csr_write(self, addr: int, data: int):
        """Returns the response data of the write (value after the operation, R15)."""
        if addr == protocol.CSR["CTRL"]:
            if data & 1:                                        # R16: counters read 0 afterwards
                self._base_cand, self._base_frames = self._totals()
            return 0
        reg = {protocol.CSR["MODE"]: "mode", protocol.CSR["PRE_K"]: "pre_k", protocol.CSR["ADSB_RATE"]: "rate"}.get(addr)
        if reg is None:
            return None                                         # not modeled
        self._flush()                                           # R17: clear DSP state
        if reg == "mode":
            self.mode = data & 0xFFFFFFFF
        elif reg == "pre_k":
            self.pre_k = data & 0xFF
        else:
            self.rate = data & 1
        return self.csr_read(addr)

    def csr_read(self, addr: int):
        """Register value at this point in the arrival order (R17); None if not modeled."""
        sat = lambda v: min(v, 0xFFFFFFFF)                      # R16
        cand, frames = self._totals()
        return {protocol.CSR["MODE"]: self.mode, protocol.CSR["PRE_K"]: self.pre_k,
                protocol.CSR["ADSB_RATE"]: self.rate, protocol.CSR["CTRL"]: 0,
                protocol.CSR["ID"]: 0x53445231,
                protocol.CSR["ADSB_CAND"]: sat(cand - self._base_cand),
                protocol.CSR["ADSB_FRAMES"]: sat(frames - self._base_frames)}.get(addr)

    def _totals(self):
        """Counts so far, including candidates already complete in the open segment (R23)."""
        cand, frames = self.adsb_cand, self.adsb_frames
        if self._u:
            r = decode(magnitudes(b"".join(self._u)), self.rate, self.pre_k, np.concatenate(self._idx))
            cand, frames = cand + r.adsb_cand, frames + len(r.records)
        return cand, frames

    def iq_packet(self, payload: bytes):
        try:
            p = protocol.parse_iq(payload)
        except protocol.BadPacket:
            return                                              # R12: dropped before the DSP
        n_pairs = len(p.samples) // 2
        backwards = not p.flags & 1 and self._expect is not None and p.index < self._expect
        self._expect = p.index + n_pairs                        # R13: tracked whatever MODE is
        if p.flags & 1 or backwards:
            self._flush()                                       # new stream (or R13 backwards index) resets DSP state
        if self.mode != 1:
            return                                              # MODE 0 (and others): samples discarded
        self._u.append(p.samples)
        self._idx.append(p.index + np.arange(n_pairs, dtype=np.int64))

    def finish(self) -> list[bytes]:
        self._flush()
        return self.records

    def _flush(self):
        if self._u:
            r = decode(magnitudes(b"".join(self._u)), self.rate, self.pre_k, np.concatenate(self._idx))
            self.adsb_cand += r.adsb_cand
            for c in r.records:
                self.records.append(record_payload(c, self.rate, seq=len(self.records)))
            self.adsb_frames += len(r.records)
        self._u, self._idx = [], []


# ================================================================== stimulus
def rand_frame(rng: np.random.Generator, df: int | None = None) -> int:
    """Random CRC-valid 112-bit DF17 or DF18 frame."""
    df = int(rng.choice([17, 18])) if df is None else df
    payload = (int(rng.integers(0, 1 << 62)) << 21) | int(rng.integers(0, 1 << 21))   # 83 random bits
    first88 = (df << 83) | payload
    return (first88 << 24) | crc24_parity(first88)


def frame_chips(bits: int) -> list[int]:
    """Chip indices (0..239) that carry a pulse."""
    on = list(PULSE_CHIPS)
    for i in range(DATA_BITS):
        b = bits >> (DATA_BITS - 1 - i) & 1
        on.append(DATA_CHIP0 + 2 * i + (0 if b else 1))
    return on


def render(n_samples: int, rate: int, frames, rng: np.random.Generator, noise_sigma: float = 4.0) -> bytes:
    """u8 I/Q stream with frames = [(t0_us, bits, amplitude, carrier_phase_rad)].

    Each pulse is box-integrated exactly over each sample interval (the model R33
    assumes), summed as complex baseband, plus complex Gaussian noise of sigma per
    component, then quantized like rtl_sdr (127.5 offset, clip to 0..255).
    """
    rate_mhz = float(RATES[rate][2])
    z = np.zeros(n_samples, dtype=np.complex128)
    for t0, bits, amp, theta in frames:
        a = amp * np.exp(1j * theta)
        for j in frame_chips(bits):
            lo, hi = (t0 + 0.5 * j) * rate_mhz, (t0 + 0.5 * j + 0.5) * rate_mhz   # in samples
            for k in range(int(np.floor(lo)), int(np.ceil(hi))):
                if 0 <= k < n_samples:
                    z[k] += a * (min(hi, k + 1) - max(lo, k))
    z += rng.normal(0, noise_sigma, n_samples) + 1j * rng.normal(0, noise_sigma, n_samples)
    iq = np.empty(2 * n_samples)
    iq[0::2], iq[1::2] = z.real, z.imag
    return np.clip(np.rint(127.5 + iq), 0, 255).astype(np.uint8).tobytes()


def rand_stream(rng: np.random.Generator, n_frames: int, rate: int = 1, snr_db: float = 20.0,
                aligned: bool = False, noise_sigma: float = 4.0, gap_us=(10.0, 200.0)):
    """Synthetic acceptance stimulus (verification plan). Frames do not overlap.

    Peak SNR = A^2 / (2 sigma^2). aligned=True puts every start on a whole sample.
    Returns (iq bytes, truth = [(t0_us, bits)]).
    """
    amp = noise_sigma * np.sqrt(2) * 10 ** (snr_db / 20)
    rate_mhz = float(RATES[rate][2])
    t, truth, frames = 20.0, [], []
    for _ in range(n_frames):
        t0 = t + rng.uniform(*gap_us)
        if aligned:
            t0 = np.ceil(t0 * rate_mhz) / rate_mhz
        bits = rand_frame(rng)
        frames.append((t0, bits, amp, rng.uniform(0, 2 * np.pi)))
        truth.append((t0, bits))
        t = t0 + 120.0
    n = int(np.ceil((t + 50.0) * rate_mhz))
    return render(n, rate, frames, rng, noise_sigma), truth


def score(records: list, truth, rate: int, tol_us: float = 1.0):
    """(found, false): sent frames emitted near their true start, and records matching nothing."""
    want = {bits: t0 for t0, bits in truth}
    found = set()
    false = 0
    for c in records:
        t0 = want.get(c.bits)
        if t0 is not None and abs(float(timestamp_us(c.n, c.phi, rate)) - t0) <= tol_us:
            found.add(c.bits)
        else:
            false += 1
    return len(found), false


# --------------------------------------------------- boundary (threshold) stimulus
_IQ_FOR_MAG: dict[int, tuple[int, int]] = {}


def iq_for_mag(m: int) -> tuple[int, int]:
    """A u8 (I, Q) pair whose R22 magnitude is m. Reachable: 1, 3..382 (0 and 2 are not)."""
    if not _IQ_FOR_MAG:
        for ui in range(256):
            for uq in (128, 129, 127, 126):          # prefer a quiet Q
                _IQ_FOR_MAG.setdefault(magnitude(to_signed(ui), to_signed(uq)), (ui, uq))
        for ui in range(256):
            for uq in range(256):
                _IQ_FOR_MAG.setdefault(magnitude(to_signed(ui), to_signed(uq)), (ui, uq))
    assert m in _IQ_FOR_MAG, f"R22: magnitude {m} is not reachable"
    return _IQ_FOR_MAG[m]


def mags_to_iq(m) -> bytes:
    return bytes(b for v in m for b in iq_for_mag(int(v)))


def _frame_mags(bits: int, pulse: int, low: int = 1, gaps: int | None = None, quiet: int = 1) -> list[int]:
    """2.0 MS/s magnitudes for one frame: 1 sample per chip, so c_j = 6*m (R33)."""
    m = [low] * FRAME_CHIPS
    for j in GAP_CHIPS:
        m[j] = low if gaps is None else gaps
    for j in range(10, 16):
        m[j] = quiet
    for j in frame_chips(bits):
        m[j] = pulse
    return m


def boundary_cases(rng: np.random.Generator):
    """Streams aimed at every threshold in R24-R28, at 2.0 MS/s where m maps 1:1 to chips.

    Yields (name, iq bytes, rate, pre_k, expected emitted count, why).
    """
    pad = [1] * 40
    f = rand_frame(rng, 17)

    def case(name, mags, n_emit, why, pre_k=8):
        return name, mags_to_iq(pad + mags + pad), 0, pre_k, n_emit, why

    yield case("nominal", _frame_mags(f, 40), 1, "R24-R27 clean frame")
    yield case("level_equal", _frame_mags(f, 40, gaps=20), 1, "R25 6H == K*L passes")
    m = _frame_mags(f, 40, gaps=20); m[4] = 21
    yield case("level_below", m, 0, "R25 6H == K*L - 6 fails")
    m = _frame_mags(f, 40); m[12] = 40
    yield case("quiet_equal", m, 0, "R25 4*c12 == H fails")
    m = _frame_mags(f, 40); m[12] = 39
    yield case("quiet_below", m, 1, "R25 4*c12 == H - 24 passes")
    m = _frame_mags(f, 40); m[1] = 40
    yield case("shape_equal", m, 0, "R24 c0 == c1 fails")
    # R26 ties: find a 0 bit and a 1 bit and make their two chips equal
    zeros = [i for i in range(DATA_BITS) if not f >> (DATA_BITS - 1 - i) & 1]
    ones = [i for i in range(DATA_BITS) if f >> (DATA_BITS - 1 - i) & 1]
    m = _frame_mags(f, 40); i = zeros[0]; m[DATA_CHIP0 + 2 * i] = m[DATA_CHIP0 + 2 * i + 1] = 40
    yield case("tie_on_0", m, 1, "R26 tie decides 0, frame still valid")
    m = _frame_mags(f, 40); i = ones[0]; m[DATA_CHIP0 + 2 * i] = m[DATA_CHIP0 + 2 * i + 1] = 40
    yield case("tie_on_1", m, 0, "R26 tie decides 0, CRC fails")
    # R27: valid CRC but DF 11 -> not emitted; DF18 -> emitted
    first88 = (11 << 83) | (rand_frame(rng) >> 24) & ((1 << 83) - 1)
    yield case("df11", _frame_mags((first88 << 24) | crc24_parity(first88), 40), 0, "R27 DF not 17/18")
    yield case("df18", _frame_mags(rand_frame(rng, 18), 40), 1, "R27 DF18 accepted")
    # R28: the second frame shares one chip with the first, so frame 1's last bit must be 0
    while f & 1:
        f = rand_frame(rng, 17)
    g = rand_frame(rng, 17)
    a, b = _frame_mags(f, 40), _frame_mags(g, 40)
    yield case("r28_at_239", a[:239] + b, 1, "R28 second frame at p+239 suppressed")
    yield case("r28_at_240", a + b, 2, "R28 second frame at p+240 emitted")


# Every worked example and boundary in the spec: (callable, args, expected, why)
DIRECTED = [
    (to_signed, (0,), -255, "R19 u = 0"),
    (to_signed, (128,), 1, "R19 u = 128"),
    (to_signed, (255,), 255, "R19 u = 255"),
    (magnitude, (-255, 101), 305, "R22 worked example"),
    (magnitude, (-255, -255), MAG_MAX, "R22 largest magnitude"),
    (magnitude, (1, 1), 1, "R22 smallest magnitude"),
    (lambda: chip_weights(5, 3, 0), (), {0: 2, 1: 4}, "R33 worked example weights"),
    (lambda: span(5, 4), (), 288, "R29 final sample offset at 2.4 MS/s"),
    (lambda: span(6, 0), (), 239, "R29 final sample offset at 2.0 MS/s"),
    (suppress, ([5002, 5003, 6441, 6442],), [True, False, False, True], "R28 worked example"),
    (timestamp_us, (2_400_000, 3, 1), Fraction(4000001, 4), "R14 worked example"),
    (crc24_remainder, (int("8D4840D6202CC371C32CE0576098", 16),), 0, "R27 known DF17"),
]


def _selftest():
    for fn, args, want, why in DIRECTED:
        got = fn(*args)
        assert got == want, f"{why}: got {got!r}, want {want!r}"
    rng = np.random.default_rng(1)
    for name, iq, rate, pre_k, n_emit, why in boundary_cases(rng):
        r = decode(magnitudes(iq), rate, pre_k)
        assert len(r.records) == n_emit, f"{name} ({why}): {len(r.records)} emitted, want {n_emit}"
    return True


if __name__ == "__main__":
    print("selftest", _selftest())
