# References and how they relate to the spec

Notes only — `docs/spec.md` is the golden file. Anything here that should change behaviour goes through the `/spec` skill first.

## Primary reference: Nuand bladeRF-adsb — https://github.com/Nuand/bladeRF-adsb

MIT licence per README; check `LICENSE` before reusing code.

**What it is.** A full FPGA ADS-B demodulator in VHDL (`vhdl/`) for the bladeRF, decoding to 112-bit messages that a host utility forwards to dump1090 (port 30001). It is the primary reference for this project: the closest prior art for the whole DSP chain, and the design ours is measured against. The table below maps each of its stages to the spec.

| Stage | bladeRF-adsb | This spec | Note |
|-------|--------------|-----------|------|
| Sample rate | 16 MS/s, 8 samples/chip (`SPS = 8`) | 2.4 MS/s, 1.2 samples/chip, 5-phase search (spec v0.3); 2.0 MS/s, 1 sample/chip for bring-up | Far coarser; the phase search (R32, R33) stands in for oversampling. |
| Front end | 4-phase mixer + FIR (`adsb_fe.vhd`) | none (R19 offset removal only) | RTL-SDR already filters. |
| Magnitude | I² + Q², 24-bit | α-max + β-min, 9-bit (R22) | Ours is cheaper; fine for ratio tests. |
| Preamble | 5-sample window sums at offsets 0, 16, 56, 72, each > absolute `POWER_THRESHOLD` = 5000, plus an edge qualifier (`preamble_detector.vhd`) | dump1090-style shape test (R24) + ratio level test with `PRE_K` (R25) | Ours is gain-independent — no absolute threshold to tune. |
| Reference level | rpl = mean of the four pulse sums | H (R25), reported in the record | Same concept. |
| Candidate handling | Pool of `NUM_DECODERS = 8`; a new preamble with no free decoder is **dropped** ("ignored"); a stronger one may replace a pending one | Every position tested (R23), earliest CRC-valid one wins, 240-sample suppression (R28) | Ours is stricter and deterministic, which is what makes R21 bit-exactness achievable. |
| Bit decision | Soft scoring per bit vs rpl windows (0.25–1.75 × rpl, < 0.125 × rpl) → confidence `bsd` (`bsd_calculator.vhd`) | Hard compare of the two chip samples (R26) | Their confidence feeds bit flipping for error correction (`bit_flipper.vhd`). |
| CRC | `0x1FFF409`, byte-serial MSB-first, 56- or 112-bit chosen by DF (`adsb_crc.vhd`) | `0x1FFF409`, 112 bits only, parallel (R27, P1) | Same generator — good cross-check vector source. |
| Error correction | Low-confidence bit flipping | Out of scope (stretch) | If we add it, their bsd approach is a model. |
| Short messages (56-bit, DF11 etc.) | Supported | Out of scope | — |

## Implications for the microarchitecture

1. **R23 + R28 without a decoder pool.** Instead of bladeRF-adsb's 8 decoders (which drop preambles under load), every candidate is evaluated in order from a delay line, so nothing is ever dropped. The final structure is the tick delay line in [microarch.md](microarch.md) (1,435 tick energies, fixed taps 6 apart, one candidate per cycle).
2. **R29 latency** is then packet arrival + a few pipeline cycles + TX queueing; the budget is in microarch.md section 2c.
3. **2.0 MS/s phase risk → 2.4 MS/s.** At 2.0 MS/s there is only 1 sample per chip, so a frame whose chip boundaries fall mid-sample degrades badly (dump1090 moved to 2.4 MS/s with phase correction for this reason). Spec v0.3 adopts 2.4 MS/s with a 5-phase search: chip energies are overlap-weighted sums of 2 samples (R33), which reproduces dump1090-fa's `slice_phase0…4` weights exactly (checked by script), and the synthetic test now uses random continuous start times.

## Spec observations (historical: all settled in spec v0.3)

These came up while cross-checking against the first spec draft.

- **CSR packet length** isn't in R12. The request is 14 bytes (2+1+1+4+2+4); say whether exactly 14 is required.
- **CSR op vs flags.** "Every payload starts with … a 1-byte flags field", but CSR puts `op` there. Say explicitly that byte 3 is `op` for `CS`.
- **Ethernet padding.** Short UDP packets arrive padded to 60 bytes + FCS; the parser must take lengths from the IP/UDP headers, not the frame. Implied by R5/R12, could be a worked example.
- **R18 register numbers** check out against the LAN8720A datasheet as I know it (ID1 = 0x0007; reg 31 bits 4:2 = 110 for 100BASE-TX full duplex) — still confirm against the PDF as the spec asks.
- Arithmetic checks pass: R20's 5.79 MS/s (1,106 wire bytes per 512-pair packet), R22's 9-bit range (max 382), H fits 16 bits (max 1,528), R26/R28 sample offsets (16 and 240).
