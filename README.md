# sdr_ulx3s: an ADS-B receiver on a ULX3S FPGA

This project decodes the radio messages that aircraft broadcast about
themselves, using an FPGA. An RTL-SDR dongle on a Mac records the 1090 MHz
band. The things I will do to avoid using a machine running Windows. The Mac sends the raw samples over Ethernet to a ULX3S board with a
Lattice ECP5 85F FPGA, which finds and checks each aircraft message and sends
it back. The Mac only moves bytes; all of the signal processing runs on the
FPGA.

This README explains the radio background and the decoding method, the
hardware design, how to run the tests, and the design flow used to build it.
It assumes some familiarity with digital logic but not with radio or signal
processing.

- [Overview](#overview)
- [Why an FPGA?](#why-an-fpga)
- [Background](#background)
- [How the decoder works](#how-the-decoder-works)
- [Hardware design](#hardware-design)
- [Getting started](#getting-started)
- [Repository layout](#repository-layout)
- [Design flow](#design-flow)
- [What this project covers](#what-this-project-covers)

---

## Overview

### What it does

```
 antenna ─► RTL-SDR ─USB─► Mac ─UDP/100 Mbit─► LAN8720 ─RMII─► ULX3S 85F
                            ▲                                      │
                            └──────────── frames, registers ◄──────┘
```

1. The RTL-SDR samples the 1090 MHz band at 2.4 million samples per second
   (MS/s) and writes 8-bit I/Q samples (explained [below](#from-radio-waves-to-numbers-what-the-rtl-sdr-does)).
2. A Python tool on the Mac packs the samples into UDP packets and sends them
   over 100 Mbit Ethernet to a LAN8720 PHY module plugged into the ULX3S.
3. The FPGA receives the Ethernet frames, checks them, and passes the samples
   to the decoder.
4. The decoder tests every possible message start position, and returns each
   message that passes its checksum as a 32-byte record over UDP.
5. A second Python tool prints the messages in the standard text format, so
   ordinary ADS-B software can decode callsigns and positions from them.

The FPGA has no clock tied to the radio. It processes samples in the order
they arrive, as fast as they arrive, and it is faster than the Ethernet link.
A recording can therefore be replayed at any speed, and the FPGA produces the
same output every time. That property is what makes the design testable, since every
output can be compared, byte for byte, with a Python reference model.

The full requirements are in [docs/spec.md](docs/spec.md) (revision 0.4).
Behavioural requirements are numbered R1 to R34, properties for formal proof
P1 to P8, and resource and timing budgets T1 to T4. Every test cites the IDs
it checks.

### Status

| Item | State |
|---|---|
| Specification | v0.4: all questions raised by the microarchitecture settled |
| Reference model (`tb/adsb_ref.py`) | complete: bit-exact decoder, Ethernet/CSR system model, synthetic signal generator |
| Microarchitecture ([docs/microarch.md](docs/microarch.md)) | complete for the whole design |
| DSP stages A–C: `iq_mag`, `chip_energy`, `tick_line` | complete: lint, cocotb tests against the model, formal proofs and covers pass (the `tick_line` proof takes about 18 minutes); mutation testing not yet run |
| DSP stages D–G: candidate tests, CRC, record output | not started |
| Ethernet MAC, parser, packet builder, registers | not started |
| Host tools (`send_iq.py`, `adsb_rx.py`, `csr.py`) | packet formats only (`host/protocol.py`) |
| Hardware bring-up | not started |

---

## Why an FPGA?

For ADS-B alone, software is fast enough: dump1090 decodes it on a Raspberry
Pi. The FPGA is not here for raw speed at 2.4 MS/s. It is here because the
architecture is the point:

- **It scales.** Receivers in radar, electronic warfare and signals
  intelligence process hundreds of megasamples per second or more, where
  only FPGAs and ASICs keep up. This design is the same kind of pipeline at a
  smaller rate. Handling a faster ADC means processing more samples per
  clock, not rewriting the algorithm.
- **Its latency is deterministic.** Hardware takes the same number of clock
  cycles every time, with no operating system, scheduler or cache misses.
  That is a requirement in defense systems and in low-latency trading:
  packet in, fixed processing, packet out, with latency counted in cycles.
  Here the budget is R29: a record leaves within 120 µs of the packet that
  completed it.
- **It forces design decisions.** Fixed-point formats, multiplier sharing,
  pipelining and clock domains are invisible in Python and unavoidable in
  hardware.

---

## Background

### From Radio Waves to Numbers

An aircraft transmits at 1090 MHz. No affordable chip can sample at gigahertz
rates, so the dongle does what every radio does: it mixes the signal down.
Multiplying by a local oscillator shifts the spectrum. This is the
frequency-shift (modulation) property of the Fourier transform:

```math
x(t)\, e^{-j\omega_0 t} \;\longleftrightarrow\; X(\omega + \omega_0),
```

so everything moves down by $\omega_0$. The 1090 MHz signal ends up centred
near 0 Hz, where a slow, cheap ADC can sample it.

The mixing is done twice, with a cosine and with a sine 90° apart. (In the
RTL-SDR the tuner chip first mixes to a low intermediate frequency, and the
RTL2832U chip digitizes that and does the final quadrature mixing digitally;
the result is the same.) That gives two real sample streams, **I**
(in-phase) and **Q** (quadrature). Together they form one complex number per
sample,

```math
x[n] = I[n] + j\,Q[n],
```

called **complex baseband**. Complex samples matter because they separate
positive frequencies from negative ones. With real samples, a signal 100 kHz
above the center frequency is indistinguishable from one 100 kHz below. With
complex samples they are different. So sampling at 2.4 MS/s gives a full
2.4 MHz of usable bandwidth, from $-1.2$ to $+1.2$ MHz, instead of the
1.2 MHz that the Nyquist limit allows for real samples at the same rate.

The dongle delivers I and Q as 8-bit unsigned values centered on 127.5, at
2.4 million samples per second (2.0 MS/s during bring-up). That stream of
byte pairs is all the hardware ever sees of the outside world.

### What aircraft transmit: ADS-B

ADS-B (Automatic Dependent Surveillance–Broadcast) means each aircraft works
out its own position from GPS and broadcasts it, roughly twice a second, along
with its identity, altitude and velocity. Since 2020 it has been required in
most US controlled airspace. The aircraft simply announces itself, and anyone with a receiver can listen. 

The radio format is old and simple, inherited from Mode S secondary radar.
The transmitter just switches on and off: a burst of 1090 MHz means "pulse",
silence means "no pulse". A message is an 8 µs preamble, a fixed pattern of
four pulses, followed by 112 data bits at one bit per microsecond. Each bit
uses **pulse-position modulation (PPM)**: a pulse in the first half of the
bit period means 1, a pulse in the second half means 0. The bit is carried by
the position of the pulse rather than its height.

It helps to count time in half-bit slots of 0.5 µs, called **chips**. The
preamble is 16 chips with pulses in chips 0, 2, 7 and 9 (at 0, 1.0, 3.5 and
4.5 µs), and each data bit is two chips: pulse-then-gap for 1, gap-then-pulse
for 0. A whole message is $16 + 2 \times 112 = 240$ chips, or 120 µs.

```
 chip:    0 1 2 3 4 5 6 7 8 9 10..15 | 16 17 | 18 19 | ...
          █ _ █ _ _ _ _ █ _ █ _ ... _ | █  _  | _  █  | ...
          └──────── preamble ────────┘  bit=1   bit=0
```

Pulses of neighbouring bits can touch. A 0 followed by a 1 is gap, pulse,
pulse, gap, so the two pulses merge into one pulse twice as wide:

```
 bits:     0       1
 chips:  _  █  |  █  _        one 1 µs pulse across the bit boundary
```

A bit slicer that looks for pulse edges gets this wrong. Comparing the two
chips inside each bit, as this design does, handles it naturally, and the
bit-slicer test will include it as a directed case.

This project decodes the two ADS-B formats:

- **DF17**, broadcast by aircraft transponders;
- **DF18**, broadcast by other ADS-B transmitters, such as airport ground
  vehicles.

Both are 112 bits long with the same layout:

| Field | Bits | Meaning |
|---|---|---|
| DF (downlink format) | 5 | message type: 17 or 18 for ADS-B |
| CA (capability) | 3 | transponder capability (CF, control field, in DF18) |
| ICAO address | 24 | the aircraft's unique, permanent ID |
| ME (message) | 56 | the payload: position, velocity, callsign, altitude, ... |
| PI (parity) | 24 | CRC-24 checksum |

### The checksum: polynomial division over GF(2)

To verify that a 112-bit radio transmission arrived without interference or bit errors, the system uses a mathematical checksum called a Cyclic Redundancy Check (CRC-24) based on Galois Field arithmetic. Before sending, the transmitter treats the 88-bit data payload as a polynomial over $GF(2)$, where coefficients can only be 0 or 1, and divides it by a standard, fixed degree-24 generator polynomial (0x1FFF409). Because all math in $GF(2)$ is performed modulo 2, standard subtraction is replaced by simple bitwise XOR operations with no carries or borrows. The leftover 24-bit remainder acts as a unique error-detection signature (parity field) which gets appended to the data to form the complete 112-bit codeword. When the receiver captures the broadcast, it runs the exact same $GF(2)$ polynomial division across all 112 bits. Because the remainder was built into the transmission, an uncorrupted message divides cleanly to leave a remainder of zero, which is the integrity check that routine R27 performs.

In hardware, the textbook implementation is a 24-bit shift register with
feedback taps (an LFSR) that takes one bit per clock. The faster version
unrolls that loop so all 112 bits are processed at once by a block of XOR
gates. This design uses the unrolled form, and the spec requires a formal
proof that it equals the bit-serial definition for every input (P1).

The checksum is what keeps noise out. Noise alone looks like a preamble
fairly often, but random bits pass a 24-bit check only about once in 16.8
million tries. Requiring DF = 17 or 18 as well cuts this by another factor of
about 16.

Some other Mode S formats (surveillance replies such as DF4, DF5, DF20 and
DF21) XOR the aircraft's address into the parity field. For those, the CRC
remainder is the aircraft's ID, so a CRC checker doubles as an address
detector. ADS-B formats DF17 and DF18 use plain parity, so this design does
not need that, but it is how the decoder could be extended.

### Where positions are decoded: on the Mac

Positions are packed with **Compact Position Reporting (CPR)**. Each message
carries only the low-order bits of latitude and longitude. An "even" and an
"odd" message, which use slightly different grids, together pin down the
exact location. It is tidy number theory, but the arithmetic is awkward and
only needs to run a couple of times per second per aircraft. That is a job
for software (`pyModeS` on the Mac), so the FPGA stops at validated 112-bit
messages. Splitting the work this way, fast fixed-function hardware for the
sample stream and software for rare, complex arithmetic, is a deliberate
hardware/software partitioning decision.

### How fast to sample

One chip lasts 0.5 µs, so 2 MS/s gives exactly one sample per chip. That
works only when chip boundaries happen to line up with sample boundaries. A
message can start at any instant, so usually each sample straddles two chips
and blurs them together. This hurts most for bits whose pulses touch. In the
reference model's tests at 2 MS/s, 100 % of messages aligned to sample
boundaries were decoded, but only 76.5 % of messages with random start
times.

Better decoders estimate where within a sample the pulse edges fell. At
**2.4 MS/s** there are 1.2 samples per chip, and the pattern of how chips fall
across samples repeats every 5 samples (6 chips), so there are only 5
distinct alignments, called **phases**. Testing all 5 phases recovers the
messages that 2 MS/s blurs. This is the approach of dump1090-fa, the widely
used software decoder. In the model, 2.4 MS/s with the 5-phase search decoded
all 10,000 random-start test messages.

The faster rate also fits the hardware: 2.4 MS/s of 8-bit I/Q is 38.4 Mbit/s,
well within a 100 Mbit Ethernet link, and 2.4 MS/s is a standard RTL-SDR
rate. (Whether the dongle sustains it on macOS without dropping samples over
long captures is still an open question in the spec.) 2.0 MS/s is kept as a
simpler first step during bring-up.

### Prior art: bladeRF-adsb

The primary reference for this design is Nuand's
[bladeRF-adsb](https://github.com/Nuand/bladeRF-adsb), a complete ADS-B
demodulator in VHDL for the bladeRF radio's FPGA. It is the closest existing
design to this one and the one it is measured against.
[docs/references.md](docs/references.md) compares it stage by stage. The main
differences:

| | bladeRF-adsb | This design |
|---|---|---|
| Sample rate | 16 MS/s, 8 samples per chip | 2.4 MS/s with a 5-phase search |
| Magnitude | $I^2 + Q^2$, 24 bits | approximation, 9 bits (below) |
| Preamble detection | window sums above a fixed power threshold | shape test plus a ratio test, so no threshold depends on gain |
| Candidate handling | a pool of 8 decoders; a preamble arriving when all 8 are busy is dropped | every possible start position is tested, in order; nothing is dropped |
| Bit decisions | soft confidence per bit, used for bit-flip error correction | hard comparison of two chips; error correction is a stretch goal |
| CRC | the same generator, byte-serial | the same generator, all 112 bits in parallel |

The candidate-handling difference matters most. Testing every position in a
fixed order makes the FPGA's output fully determined by its input, so it can
be compared exactly with a model. A decoder pool's output depends on how busy
it was.

---

## How the decoder works

The decoder is a chain of simple steps. Each is a requirement in the spec and
a function in the reference model.

### 1. Input conversion and magnitude (R19, R22)

Each byte $u$ becomes a signed odd value $x = 2u - 255$. This removes the
127.5 offset exactly, without fractions: $u = 0 \to -255$, $u = 128 \to 1$.

**Magnitude: throw away the phase.** Because ADS-B is on/off pulses, the
only question at each sample is whether there is energy there, which is the
magnitude $\lvert x \rvert = \sqrt{x_I^2 + x_Q^2}$. Neither the phase nor a
small frequency error in the dongle matters. This is called **non-coherent
detection**, and it is why ADS-B is a friendly first target for a hardware
receiver.

A real square root is expensive in hardware, so the decoder uses the
alpha-max-plus-beta-min approximation, which needs only a comparison, a shift
and an add:

```math
m = \max(|x_I|, |x_Q|) + \tfrac{1}{2}\min(|x_I|, |x_Q|).
```

It is never below the true magnitude and at most 11.8 % above it
($\sqrt{1.25} \approx 1.118$, where one component is twice the other). That
is accurate enough, because every later test compares chips of the same
message with each other rather than with a fixed level. It is a classic
accuracy-for-area trade. The result $m$ fits
in 9 bits (at most 382).

### 2. Ticks and chip energies (R32, R33)

To handle both sample rates with one method, time is counted in **ticks** of
$\tfrac{1}{12}$ µs. A chip is 6 ticks. A sample is 5 ticks at 2.4 MS/s and 6 ticks at
2.0 MS/s. (12 is the smallest number of ticks per µs that divides both.)

A **candidate** is a possible message start, at tick $T$. Its chip $j$ covers
ticks $T + 6j$ to $T + 6j + 6$. The decoder estimates how much signal fell in
that chip, the **chip energy** $c_j$: the sum of the magnitudes of the
samples the chip overlaps, each weighted by how many ticks of the chip it
covers. The weights always add up to 6.

At 2.4 MS/s a chip always overlaps exactly two samples. With $s$ the chip's
starting tick within its first sample:

| $s$ | weight on first sample | weight on second sample |
|---|---|---|
| 0 | 5 | 1 |
| 1 | 4 | 2 |
| 2 | 3 | 3 |
| 3 | 2 | 4 |
| 4 | 1 | 5 |

At 2.0 MS/s, a candidate that starts on a sample boundary has each chip
exactly on one sample, so $c_j = 6\,m$.

Example (R33): a chip starting at tick 503 at 2.4 MS/s lies in sample 100
with $s = 3$, so $c = 2\,m[100] + 4\,m[101]$. With $m[100] = 50$ and
$m[101] = 300$, $c = 1{,}300$.

These weights are the same ones dump1090-fa uses for its 5 phases, written as
one rule for every chip instead of five special cases.

### 3. Preamble tests (R24, R25)

Finding the preamble means sliding the known four-pulse pattern along the
signal and checking where it fits: high where pulses should be, low in the
gaps. That is correlation, the same operation as convolution with the pattern
flipped. It is the **matched filter** from detection theory: correlating
against the exact shape you expect maximises the chance of spotting it in
noise. Here each candidate start $T$ is one position of the slide, and the
tick delay line ([below](#the-tick-delay-line)) does the sliding, one
position per clock cycle.

With the chip energies of a candidate:

- **Shape.** The four pulse chips must be higher than their neighbours:
  $c_0 > c_1$, $c_1 < c_2$, $c_2 > c_3$, $c_3 < c_0$, $c_4, c_5, c_6 < c_0$,
  $c_7 > c_8$, $c_8 < c_9$, $c_9 > c_6$.
- **Level.** With $H = c_0 + c_2 + c_7 + c_9$ (pulses) and
  $L = c_1 + c_3 + c_4 + c_5 + c_6 + c_8$ (gaps), the test is
  $6H \ge K \cdot L$. $K$ is the register `PRE_K`, default 8, which means
  the average pulse must be at least twice the average gap. Because this is a
  ratio, it does not depend on the radio's gain setting.
- **Quiet zone.** Chips 11 to 14 must each be under a quarter of $H$:
  $4 c_k < H$.

$H$ is also reported with each message as its signal level.

**No separate noise floor.** A pulse only means something relative to the
background. Many decoders track the noise floor with a running average of the
magnitude (with a power-of-two window, that is a shift and an add) and scale
every threshold from it. This design instead compares each candidate's pulse
chips with its own gap chips. The level test is itself a correlation, with
weight $+6$ on the pulse chips and $-K$ on the gap chips:

```math
6H - K L \ge 0 .
```

So the threshold follows the local background automatically, with no state
carried between candidates and nothing to retune when the radio's gain
changes. If recorded captures show too many noise preambles reaching the CRC,
a noise-floor estimate can be added as an extra test.

### 4. Bits, format and checksum (R26, R27)

Bit $i$ is 1 when its first chip is stronger than its second,
$c_{16+2i} > c_{17+2i}$. Comparing the two halves is the optimal decision
rule for PPM in noise: both halves see the same noise, and only one of them
contains the pulse, so no threshold is needed. The hard part is timing, which
the chip energies and the phase search take care of.

The candidate becomes a message when its first 5 bits give DF 17 or 18 and
the CRC-24 remainder of all 112 bits is zero. The CRC is computed for all 112
bits at once by the unrolled XOR network described
[above](#the-checksum-polynomial-division-over-gf2).

### 5. One report per transmission (R28)

The same transmission often passes at two neighbouring phases. Candidates are
evaluated in order of increasing $T$. Once a message is reported at $T$, no
other message is reported until $T + 1{,}440$ ticks (120 µs, one full
message). The earliest passing phase is the one reported.

### How well it works

The reference model's synthetic generator places messages at random
continuous start times, renders the pulses at a high internal rate, averages
them down to the sample rate, adds Gaussian noise and rounds to bytes like
the RTL-SDR. Results at 20 dB signal-to-noise ratio, 10,000 messages per run:

| Rate | Start times | Decoded | False messages |
|---|---|---|---|
| 2.4 MS/s | random | 10,000 (100 %) | 0 |
| 2.0 MS/s | on sample boundaries | 10,000 (100 %) | 0 |
| 2.0 MS/s | random | 7,652 (76.5 %) | 0 |

Two million samples of noise alone produced no messages at either rate.

---

## Hardware design

The details are in [docs/microarch.md](docs/microarch.md). This section gives
the main ideas.

### Two clock domains

The FPGA runs two 50 MHz clocks. The Ethernet side runs on the clock from the
LAN8720 module's own oscillator, because the RMII interface requires it. The
decoder runs on a clock from the FPGA's PLL. The two come from different
crystals, so they drift relative to each other, and every signal passing
between them needs a safe crossing (an asynchronous FIFO or a synchronizer).
The spec lists every allowed crossing, and nothing else may cross.

### Ethernet without a processor

There is no CPU and no network stack. Fixed hardware handles exactly what the
project needs:

- **receive:** collect each frame and check its CRC-32 before anything acts
  on it, so a corrupted packet never has even a partial effect; filter by
  destination address; parse IPv4 and UDP; answer ARP requests so the Mac
  can find the board;
- **transmit:** build IPv4/UDP packets for messages and register responses,
  with priority ARP > register responses > messages.

| UDP port | Direction | Payload |
|---|---|---|
| 5000 | Mac → FPGA | I/Q samples: 16-byte header (sequence number, sample index) + up to 700 sample pairs |
| 5001 | FPGA → Mac | one 32-byte record per message: sample index, phase, rate, level, 14-byte message |
| 5005 | both | register read/write requests and responses, 14 bytes |

The formats are in [host/protocol.py](host/protocol.py), which both the host
tools and the testbenches use.

### The tick delay line

The decoder's central structure is a shift register of tick energies.

Each incoming sample produces 5 tick energies (6 at 2.0 MS/s), one per clock
cycle. Each value is the energy of a chip that would start at that tick. They
are shifted into a line of 1,435 entries. After each shift, the oldest entry
is the start $T$ of a complete candidate, and its 240 chip energies sit at
entries 0, 6, 12, ..., 1,434. Those entries are fixed, so the preamble tests,
the 112 bit decisions and the CRC are wired straight to them, with no
multiplexers or memory reads.

So every cycle with a shift tests one candidate, and candidates come out in
order of $T$. That gives three requirements by construction:

- every position is tested (R23);
- candidates are evaluated in order (R28);
- one sample is consumed every 5 or 6 cycles, faster than Ethernet can
  deliver them (R20).

The cost is flip-flops: $1{,}435 \times 12 \approx 17{,}200$ bits, about 21 % of the
device's 83,640. Synthesis of the finished block confirms this (17,244
flip-flops, 41 LUTs, no RAM).

### Pipeline

| Stage | Block | Does |
|---|---|---|
| A | `iq_mag` | input conversion and magnitude (2 cycles) |
| B | `chip_energy` | turns each sample into 5 or 6 tick energies |
| C | `tick_line` | the delay line; presents one candidate per shift |
| D–F | `cand_eval`, `crc24_112` | preamble tests, bit decisions, DF check, CRC |
| G | `adsb_emit` | overlap suppression, sample index lookup, record output |

Estimated total: about 8,100 LUTs (budget 20,000), 20,300 flip-flops, 7 block
RAMs and one multiplier, on a device with 83,640 LUTs.

---

## Getting started

### Tools

The project uses the
[OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build), which
provides Yosys, nextpnr-ecp5, Icarus Verilog, Verilator, SymbiYosys,
openFPGALoader, cocotb, Python, numpy and pytest.

```sh
source <path>/oss-cad-suite/environment
make model     # reference-model doctests and self-test
make lint      # Verilator -Wall on every RTL block
make test      # all cocotb block tests, under a minute
make formal    # SymbiYosys proofs and cover checks
make trace     # which requirements have a test or proof
make all       # model, lint, test and formal
```

To run one block's tests:

```sh
make -C tb tick_line
```

Two setup details:

- The cocotb build in the OSS CAD Suite contains a libpython path from the
  machine it was built on. The test Makefile sets
  `LIBPYTHON_LOC=$(cocotb-config --libpython)` to correct this.
- Source the Suite's `environment` script rather than only adding its `bin/`
  directory to `PATH`, so the tools use the Suite's own Python.

### Hardware

| Item | Notes |
|---|---|
| ULX3S with LFE5U-85F | programmed over its onboard USB JTAG with `openFPGALoader -b ulx3s` |
| LAN8720 RMII module | plugs into header J1 at GP/GN 9–13; pin map in the spec |
| RTL-SDR | ideally one with a built-in 1090 MHz filter and amplifier |
| Mac with a gigabit USB-C Ethernet adapter | static IP 192.168.18.254; the FPGA is 192.168.18.128 |

The hardware steps are not reached yet; see [Design flow](#design-flow).

---

## Repository layout

```
docs/        spec.md               requirements, budgets, build order (golden)
             microarch.md          block diagram, pipeline, FSMs, clock crossings
             references.md         bladeRF-adsb comparison
rtl/         iq_mag.v              stage A: input conversion and magnitude
             chip_energy.v         stage B: tick energies
             tick_line.v           stage C: tick delay line
tb/          adsb_ref.py           reference model, system model, signal generator (golden)
             test_iq_mag.py        all 65,536 I/Q byte pairs
             test_chip_energy.py   both rates, throughput, clear
             test_tick_line.py     every candidate's 240 taps, phase and index
             Makefile              cocotb targets
formal/      *_props.v, *.sby      SymbiYosys properties and jobs per block
host/        protocol.py           UDP packet formats shared by host tools and tests
scripts/     tickline_proto.py     Python prototype of the delay-line design
captures/    manifest.json         recorded radio captures (large files stay out of git)
constraints/                       ULX3S pin constraints (to come)
Makefile                           model, lint, test, formal, trace
```

---

## Design flow

The project follows the same sequence for every part of the design. Each
step produces a document or a check that the later steps must agree with.

### 1. Specification

[docs/spec.md](docs/spec.md) is written before any RTL. Every requirement
describes something observable at the FPGA's pins or network port, with
numbers and at least one worked example. Values chosen before any
measurement are marked **(assumed)** so they can be revisited. The spec is a
"golden" file: it is only changed through a reviewed draft, and code is
changed to match the spec, never the other way round.

Two revisions came from working through the design:

- **v0.3** moved from 2.0 to 2.4 MS/s with the 5-phase search. The other
  option was a faster radio, but bladeRF-style oversampling needs a more
  expensive radio and more Ethernet bandwidth. The phase search recovers
  most of the benefit with the same RTL-SDR. This revision also introduced
  ticks, so one rule describes both rates.
- **v0.4** settled five questions the microarchitecture raised: what happens
  when a sample index goes backwards, what happens when an output queue is
  full (the item is dropped and counted in `TX_DROP`, so the decoder never
  stalls), how a full receive buffer is counted, how the PHY status crosses
  clock domains, and how long the 40-bit counters stay correct.

### 2. Reference model

[tb/adsb_ref.py](tb/adsb_ref.py) implements the spec in Python, as a second
and independent version of the design. A test is only meaningful if the
model and the RTL compute the same thing in different ways, so the model is
written for clarity, not to mirror the hardware:

- Chip energies come from a general interval-overlap calculation, not from
  the weight table.
- Candidates are tested with numpy over the whole stream at once, then
  sorted by start time.
- The system model replays Ethernet packets and register requests in order,
  including the counters, so the full FPGA can be compared with it.

All arithmetic is in integers, so there is no rounding to argue about. The
model also contains the synthetic signal generator, a list of directed test
cases for every example in the spec, and boundary cases aimed at every
threshold.

### 3. Microarchitecture

[docs/microarch.md](docs/microarch.md) decides how the spec becomes
hardware:

- the block diagram;
- the pipeline stages and what each stores;
- the width of every bus, with the reason for it;
- every state machine, with its reset state and what happens in unused
  states;
- every clock-domain crossing and its mechanism;
- the handshake rules between blocks;
- resource estimates and risks.

The tick delay line was checked before any RTL was written. A Python
prototype of exactly that structure ([scripts/tickline_proto.py](scripts/tickline_proto.py))
produced the same messages and candidate counts as the reference model on
every test stream. That included messages ending exactly at the end of a
stream, at all 12 tick offsets.

### 4. Blocks

Each block is built with its checks at the same time:

1. **Scaffold.** A script creates the module, a cocotb test, formal
   properties and a SymbiYosys job. The first version passes everything
   trivially, so the first real change is the first thing that can fail.
2. **Test against the model.** The cocotb test drives the block with
   directed cases from the spec and random streams, and compares every
   output with the reference model.
3. **Formal properties.** The properties include a separately written Verilog
   model of the block's function, and SymbiYosys proves the RTL matches it
   for every input. Cover statements confirm the interesting cases are
   reachable, so a proof cannot pass vacuously. Large blocks are proved in a
   small configuration: the 1,435-entry delay line is proved as a 19-entry
   line, which uses the same RTL with a different parameter.
4. **Lint.** Verilator with all warnings enabled.
5. **Mutation testing.** Deliberate bugs are inserted one at a time to
   confirm the tests and proofs catch them.

Formal verification has already found a real bug. In `chip_energy`, a sample
offered while the block was being cleared still loaded its internal
registers. The next stream's first tick energy would then have used a stale
value. The fix ignores samples during reset and clear.

It also caught mistakes in my own properties, which matters as much. Twice,
in `tick_line`, a property checked a candidate produced just before a rate
change against the new rate. The fix was to record the rate in force when
each candidate was produced.

Two tool problems came up and are worked around in the repository:

- Yosys does not connect hierarchical references such as `dut.state` in this
  flow. It silently creates undriven wires instead, so the properties were
  checking nothing. Blocks that need internal state for a proof expose it on
  a port that exists only in formal builds (`f_state`).
- The development build of cocotb in the Suite crashes when a test ends with
  a signal write still pending. Tests wait for one clock edge after their
  last write.

### 5. System simulation

Once all blocks exist, the full design runs in Verilator with a simulated
Ethernet PHY. The test sends real Ethernet frames built with
`host/protocol.py` and compares every output with the system model. It
includes the awkward cases on purpose: bad checksums, sequence gaps, a
register write between two packets of one stream, and messages split across
packet boundaries.

### 6. Hardware bring-up

The board is brought up one layer at a time, and each step ends with a check
on real hardware before the next begins:

| Step | Goal | Check |
|---|---|---|
| 0 | clocks and resets | LEDs blink from both clocks |
| 1 | Ethernet receive | an LED flickers while the Mac pings the board |
| 2 | ARP and transmit | `arp -a` on the Mac shows the FPGA's address |
| 3 | registers | `csr.py read ID` returns the design ID |
| 4 | loopback | 30 s at full line rate with no loss |
| 5 | ADS-B replay at 2.0 MS/s | output equals the model on a recorded capture |
| 5b | ADS-B replay at 2.4 MS/s | output equals the model; yield compared with dump1090-fa |
| 6 | live reception | aircraft appear in real time |
| 7 | PHY status | link status read over MDIO |

Replaying recorded captures in steps 5 and 5b means the hardware is checked
against the model with the same input before it meets live radio.

---

## What this project covers

- Complex baseband, I/Q sampling, and why complex samples double the usable
  bandwidth.
- Matched filtering, non-coherent detection, and the PPM decision rule.
- CRC as polynomial arithmetic over GF(2), and unrolling an LFSR into
  parallel logic, with a formal proof that the two match (P1).
- Fixed-point design: bit growth, quantisation, and choosing every word
  length from an exact bound (the widths table in
  [docs/microarch.md](docs/microarch.md)).
- Streaming design: budgeting cycles per sample (5 or 6 here, R20) and
  sharing resources where the budget allows (one multiplier for the level
  test).
- Clock-domain crossing: the asynchronous FIFOs between the 50 MHz RMII
  Ethernet clock from the PHY module and the 50 MHz DSP clock from the
  FPGA's PLL, which come from different oscillators.
- Verification against a golden model and a real-world reference
  (dump1090-fa on the same recorded capture), with a measured result.
- With the FM stretch goal: CIC filters and decimation, analysed through
  their z-transforms.
