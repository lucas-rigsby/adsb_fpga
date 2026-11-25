# sdr_ulx3s — specification

Status: draft v0.4, 2026-11-24 (v0.4: microarchitecture questions Q1–Q5 settled, reference-model assumptions A1–A3 made requirements; v0.3: ADS-B at 2.4 MS/s with sub-sample phase search, 2.0 MS/s kept for bring-up). ADS-B receiver first; FM is a stretch goal (appendix). Golden file: every test, assertion, timing target and signoff check cites an ID below. Items marked **(assumed)** are defaults chosen before any measurement; revisit them with the `/spec` skill rather than silently changing the RTL.

## What it does

A real-time ADS-B receiver split between a MacBook and a ULX3S 85F. An RTL-SDR dongle on the Mac digitizes the 1090 MHz band. The Mac streams the raw 8-bit I/Q samples over 100 Mbit Ethernet (UDP) to the FPGA through a LAN8720 RMII module. The FPGA detects and decodes aircraft transponder messages (Mode S DF17/DF18, 112 bits) and returns each valid frame with its sample position over the same link.

The Mac only moves bytes: it never demodulates. The FPGA has no notion of wall-clock sample time; it processes samples in arrival order as fast as its pipeline allows, and pacing to real time is the host's job. Because the core is faster than the Ethernet line rate (R20), a recorded capture can be replayed at any speed up to line rate and give identical results.

```
 antenna ─► RTL-SDR ─USB─► Mac ─UDP/100M─► LAN8720 ─RMII─► ULX3S 85F
                            ▲                                  │
                            └──────── frames / CSR ◄───────────┘
```

## Target and environment

| Item | Value |
|------|-------|
| FPGA | Lattice ECP5 LFE5U-85F-6BG381C on ULX3S; nextpnr `--85k --package CABGA381 --speed 6` |
| Constraints | official ULX3S LPF from `emard/ulx3s` matching the board revision printed on the PCB |
| Programming | onboard FT231X JTAG, `openFPGALoader -b ulx3s` |
| PHY | LAN8720 RMII module (10/100), with its own 50 MHz oscillator, plugged into GP/GN 9–13 |
| Host | macOS, USB-C gigabit Ethernet adapter, static IP 192.168.18.254/24 **(assumed)** |
| FPGA address | IP 192.168.18.128, MAC 02:00:00:00:00:01 (locally administered); Verilog parameters, not CSRs **(assumed)** |
| Radio | RTL-SDR at 1090 MHz, 2.4 MS/s (2.0 MS/s during bring-up, R32), `rtl_sdr` writing 8-bit unsigned interleaved I/Q (`.cu8`), center code 127.5. A dongle with a built-in 1090 MHz SAW filter and LNA is recommended **(assumed)** |
| Toolchain | oss-cad-suite (Yosys, nextpnr-ecp5, Verilator, Icarus, SymbiYosys), cocotb 2.x, Python 3.12 + numpy, pyModeS |

### Wiring the LAN8720 module

Plug the module into the J1 header at GP/GN 9–13, aligning the module's GND and VCC with the board's GND and 3.3 V. Pin mapping (from emard's working `ulx3s-misc/examples/eth/rmii` design):

| Module pin | ULX3S pin | Direction | Use |
|------------|-----------|-----------|-----|
| `TX_EN` | gn10 | FPGA → PHY | transmit enable |
| `TX0` | gp10 | FPGA → PHY | transmit dibit, bit 0 |
| `TX1` | gn9 | FPGA → PHY | transmit dibit, bit 1 |
| `CRS_DV` | gp12 | PHY → FPGA | carrier sense / data valid |
| `RX0` | gn11 | PHY → FPGA | receive dibit, bit 0 |
| `RX1` | gp11 | PHY → FPGA | receive dibit, bit 1 |
| `nINT/REFCLK` | gn12 | PHY → FPGA | 50 MHz RMII reference clock |
| `MDIO` | gn13 | bidirectional | management data |
| `MDC` | gp13 | FPGA → PHY | management clock |

## Clocks, resets, crossings

| Domain | Clock | Frequency | Source | Reset |
|--------|-------|-----------|--------|-------|
| `core` | `clk_core` | 50 MHz | ECP5 PLL from `clk_25mhz` | sync, active high, asserted while PLL unlocked or PWR button (btn[0]) pressed |
| `rmii` | `clk_rmii` | 50 MHz | LAN8720 oscillator via gn12 | sync, active high, same sources, deassertion synchronized into `rmii` |

The two 50 MHz clocks come from different oscillators and are asynchronous. The entire Ethernet stack (MAC, parser, packet builder, MDIO) is in `rmii`; the DSP and CSRs are in `core`. Allowed crossings, and nothing else:

| Signal group | From → to | Mechanism |
|--------------|-----------|-----------|
| I/Q samples and headers, CSR requests, PHY status (R18) | rmii → core | one async FIFO, gray-coded pointers; items reach the core in arrival order (R17, R21) |
| CSR responses, ADS-B records | core → rmii | async FIFO |
| Ethernet statistics counters | rmii → core | free-running 40-bit gray-coded counter + 2-flop synchronizer; `CTRL` clear is a baseline subtracted in `core` (R16), so nothing crosses core → rmii |
| Learned host address (MAC, IP, port) | rmii only | not crossed |
| Single-bit status | rmii → core | 2-flop synchronizer (none needed at present: link status reaches `core` inside `PHY_STATUS`, and led[3] is driven in `rmii`) |

## Requirements

### Ethernet receive

| ID | Requirement |
|----|-------------|
| R1 | **RMII receive.** On `clk_rmii`, the MAC samples RX[1:0] while CRS_DV is high, finds the $7 \times$ 0x55 + 0xD5 preamble/SFD, and assembles bytes LSB-first (first dibit = bits 1:0). Frames run 64–1518 bytes from destination MAC through FCS. |
| R2 | **FCS check.** Each received frame's CRC-32 (IEEE 802.3) is checked. A frame with a bad FCS, a runt (< 64 bytes) or a giant (> 1518 bytes) is discarded whole and increments `RX_BAD`. So is a frame that arrives when the store-and-forward buffer has no room for it; with traffic at or below line rate this cannot happen, because the parser and DSP drain faster than the link fills (R20, R34). |
| R3 | **Store and forward.** No byte of a frame reaches the parser, and no sample reaches the DSP, until that frame's FCS has passed. A corrupted packet never produces even a partial effect. |
| R4 | **Destination filter.** Frames are accepted only if the destination MAC is the FPGA's MAC or broadcast. Others are dropped silently (macOS sends mDNS/IPv6 traffic that must be ignored). |
| R5 | **IPv4/UDP filter.** An IPv4 frame is accepted only if: version 4, IHL 5, not fragmented (MF = 0, offset = 0), header checksum correct, destination IP = FPGA IP, protocol UDP. UDP checksums on receive are ignored. Everything else is dropped silently except as R6 says. |
| R6 | **ARP.** An ARP request (opcode 1) whose target IP is the FPGA IP gets one ARP reply (opcode 2) addressed to the requester's MAC, within 50 µs of the request's last FCS byte **(assumed)**. Example: the Mac pinging 192.168.18.128 makes `arp -a` show 02:00:00:00:00:01. |
| R7 | **Host learning.** The source MAC, IP and UDP port of the most recent packet to port 5000 or 5005 that passes R5 and R12 become the destination for all FPGA output. Until such a packet arrives, outputs are discarded and counted in `TX_NOHOST`. |

### Ethernet transmit

| ID | Requirement |
|----|-------------|
| R8 | **RMII transmit.** Each transmitted frame drives TX_EN high continuously for preamble, SFD, payload and FCS, LSB-first dibits, with a correct CRC-32. |
| R9 | **Frame rules.** Frames shorter than 64 bytes are zero-padded to 64. The gap between frames is at least 96 bit times (48 `clk_rmii` cycles). |
| R10 | **IPv4/UDP transmit.** Output packets are IPv4/UDP with IHL 5, TTL 64, DF set, a correct IPv4 header checksum and UDP checksum 0 (legal for IPv4). |
| R11 | **Priority.** When several outputs are ready, TX order is ARP reply, then CSR response, then ADS-B record. A frame in progress is never interrupted. |

### Packet formats

All multi-byte fields are big-endian. Every payload starts with a 2-byte ASCII magic, a 1-byte version (1), a 1-byte flags field and a 32-bit sequence number. The Python implementation of these formats lives in `host/protocol.py`, shared by the host tools and the testbenches. UDP port 5002 is reserved for the FM stretch goal.

**I/Q samples, host → FPGA, UDP port 5000.** 16-byte header then $N$ I/Q byte pairs, $1 \le N \le 700$:

| Offset | Bytes | Field |
|--------|-------|-------|
| 0 | 2 | magic `IQ` |
| 2 | 1 | version |
| 3 | 1 | flags (bit 0: first packet of a new stream, clears the DSP state (R17); bits 7:1 reserved, sent as 0 and ignored) |
| 4 | 4 | sequence number, +1 per packet |
| 8 | 8 | index of the first sample in this packet, counted from stream start (normally 0 in a packet with flag bit 0 set; the FPGA uses whatever value is sent, R14) |
| 16 | $2N$ | samples: $I_0, Q_0, I_1, Q_1, \ldots$ as `rtl_sdr` writes them |

The host sends 512 pairs per packet by default: 1,040-byte payload, 1,106 bytes on the wire including Ethernet/IP/UDP headers, FCS, preamble and inter-frame gap; about 41.5 Mbit/s at 2.4 MS/s (34.6 Mbit/s at 2.0 MS/s).

**ADS-B record, FPGA → host, port 5001.** One frame per packet, 32-byte payload: magic `AD`, version, flags (bit 0: DF18 rather than DF17; bits 3:1: phase $\varphi$ of the candidate, 0–4, always 0 at 2.0 MS/s; bit 4: `ADSB_RATE` in effect; bits 7:5 zero), sequence (+1 per record the DSP emits, including records later discarded under R7 or R34, so the host can detect losses), 8-byte sample index $n$ of the candidate (R23), 2-byte signal level $H$ (R27), 14-byte frame (bit 0 of the frame is the MSB of the first byte).

**CSR, host ↔ FPGA, port 5005.** Request, exactly 14 bytes: magic `CS`, version, op (0 = read, 1 = write; this byte takes the place of the flags field), sequence, 16-bit address, 32-bit data (ignored for reads). The FPGA answers every request with the same layout, op = 2, the same sequence number and the register's value after the operation.

Short packets arrive zero-padded to the 64-byte Ethernet minimum; all lengths are taken from the IPv4 total length and UDP length fields, never from the frame length. Example: a CSR request (14-byte payload, IPv4 total length 42) arrives as a 64-byte frame whose 4 padding bytes before the FCS are ignored.

| ID | Requirement |
|----|-------------|
| R12 | **Packet checks.** A packet to port 5000 or 5005 with the wrong magic, version or length (I/Q: $\text{UDP length} - 8 - 16$ not even, or $N$ outside 1–700; CSR: $\text{UDP length} \ne 8 + 14$, or op not 0 or 1) is dropped and counted in `RX_BADPKT`. |
| R13 | **Sequence gaps and stream continuity.** Each I/Q packet that passes R12 is compared with the previous one that passed R12, whatever `MODE` is (the first after reset has no predecessor and is never counted). If its flag bit 0 is clear: (a) when its sequence number is not the previous one $+ 1$, `RX_SEQGAP` increments; (b) when its sample index is lower than the previous packet's index $+ N$ (samples repeated or out of order), the packet starts a new stream exactly as if flag bit 0 were set (R17), and `RX_SEQGAP` increments; (a) and (b) together increment `RX_SEQGAP` once. Otherwise processing continues with the new samples; any skipped samples (index higher than expected) are not filled: the samples either side are processed as if adjacent, while positions still use the header indices (R14). Example: packet A has index 0 and $N = 512$. Packet B (flag clear, next sequence number) with index 1,000 continues the stream after a 488-sample gap and does not count; with index 256 it starts a new stream and `RX_SEQGAP` increments by one. |
| R14 | **Sample index.** Every ADS-B record's position $(n, \varphi)$ is derived from the I/Q header's sample index, so the host can timestamp the frame start as $S n + \varphi$ ticks of $\tfrac{1}{12}$ µs after stream start, i.e. $(n + \varphi/5) / 2.4$ µs at 2.4 MS/s or $n / 2.0$ µs at 2.0 MS/s ($S$ and $\varphi$ as in R32). Example: at 2.4 MS/s, $n = 2{,}400{,}000$, $\varphi = 3$ → 1.00000025 s after stream start. |

### Control and status registers

32-bit registers in the `core` domain, reached via port 5005. Reads of unmapped addresses return 0xDEADBEEF; writes to them and to read-only registers have no effect. Bits of a RW register beyond its stated width are ignored on write and read as 0. Addresses 0x0C, 0x10 and 0x3C are reserved for the FM stretch goal.

| Addr | Name | Access | Reset | Meaning |
|------|------|--------|-------|---------|
| 0x00 | `ID` | RO | 0x53445231 | ASCII "SDR1" |
| 0x04 | `VERSION` | RO | build parameter | git short hash |
| 0x08 | `MODE` | RW | 0 | 32 bits, read back as written: 1 = ADS-B; 0 and every other value = idle (samples discarded) |
| 0x14 | `PRE_K` | RW | 8 | ADS-B preamble threshold (R25), bits [7:0] **(assumed width)** |
| 0x18 | `CTRL` | WO | — | bit 0: clear all counters and the sticky-error LED; reads return 0 |
| 0x1C | `ADSB_RATE` | RW | 1 | bit 0: input sample rate, 0 = 2.0 MS/s, 1 = 2.4 MS/s (R32) |
| 0x20 | `RX_FRAMES` | RO | 0 | frames with good FCS |
| 0x24 | `RX_BAD` | RO | 0 | R2 |
| 0x28 | `RX_BADPKT` | RO | 0 | R12 |
| 0x2C | `RX_SEQGAP` | RO | 0 | R13 |
| 0x30 | `TX_NOHOST` | RO | 0 | R7 |
| 0x34 | `ADSB_CAND` | RO | 0 | positions that passed the preamble test |
| 0x38 | `ADSB_FRAMES` | RO | 0 | frames emitted |
| 0x40 | `PHY_STATUS` | RO | 0 | R18 |
| 0x44 | `TX_DROP` | RO | 0 | R34 |

| ID | Requirement |
|----|-------------|
| R15 | **CSR access.** Every well-formed CSR request gets exactly one response (R11 priority) carrying the register's value after the operation. The only exception is a response discarded under R34; the operation still takes effect. Example: write `MODE` = 1, response data = 1; read `ID`, response data = 0x53445231; write `CTRL` = 1, response data = 0. |
| R16 | **Counters.** Counters saturate at 0xFFFFFFFF instead of wrapping, and read 0 after `CTRL` bit 0 is written. The Ethernet counters (0x20–0x30) meet this for the first $2^{40}$ events after reset ($\ge 85$ days even at the highest possible event rate, 148,810 minimum-size frames per second). |
| R17 | **Configuration changes and ordering.** Writing `MODE`, `PRE_K` or `ADSB_RATE` (even with an unchanged value) clears all DSP state: the sample window, every candidate not yet evaluated, and the R28 suppression history. A new stream (flag bit 0, or R13 (b)) clears the same state. CSR requests and I/Q packets take effect in Ethernet arrival order: a CSR request is executed after every sample that arrived before it has been fully evaluated, and before any sample that arrived after it. So results never mix two configurations, and reads of `ADSB_CAND` and `ADSB_FRAMES` are repeatable (R21). |
| R18 | **PHY management.** After reset the MDIO master (MDC $\le 2.5$ MHz) finds the PHY by scanning addresses 0–31 for ID1 = 0x0007, then polls it every 100 ms **(assumed)**. `PHY_STATUS` bits: [4:0] PHY address, [5] PHY found, [6] link up (basic status register 1, bit 2), [9:7] speed/duplex indication (special control/status register 31, bits 4:2; 110 = 100 Mbit full duplex). Check these register numbers against the LAN8720A datasheet before implementing. |
| R34 | **Output queue overflow.** If an ADS-B record or a CSR response is produced while its queue to the transmitter is full, it is discarded and `TX_DROP` increments; the DSP and CSR logic never stall on a full output queue, so R20 holds regardless. This can only happen when TX is saturated, e.g. by a flood of ARP or CSR requests at line rate (R11 priority starves records). Queue depth $\ge 8$ entries each **(assumed)**. Example: with TX idle, the DSP's maximum record rate (1 per 1,440 cycles) never fills an 8-deep queue, so `TX_DROP` stays 0 in every non-flood test. |

### DSP input and throughput

| ID | Requirement |
|----|-------------|
| R19 | **Input conversion.** Each unsigned byte $u$ becomes the signed 9-bit odd value $x = 2u - 255$ (range $-255$ to $255$), removing the 127.5 offset exactly. Example: $u = 0 \to -255$, $u = 128 \to 1$, $u = 255 \to 255$. |
| R20 | **Line-rate processing.** At either `ADSB_RATE`, the DSP accepts one I/Q pair at least every 8 `clk_core` cycles ($\ge 6.25$ MS/s), faster than the most a 100 Mbit link can deliver ($\approx 5.79$ MS/s with the default 512-pair packets, $\approx 5.90$ MS/s with 700-pair packets). The sample FIFO therefore never overflows and no sample is ever dropped inside the FPGA. |
| R21 | **Bit-exact to the model.** For any sequence of input packets, the ADS-B record payloads the FPGA sends, excluding sequence numbers, are byte-identical and in the same order to those of `SystemModel` in `tb/adsb_ref.py` fed the same packets in the same order; records discarded under R7 or R34 are left out of the comparison. Values returned for reads of `ADSB_CAND` and `ADSB_FRAMES` also equal the model's at the same point in the sequence (R17). The Ethernet counters (0x20–0x30) are excluded: they cross clock domains asynchronously and may lag by a few cycles. |

### ADS-B decoding

Mode S uses 0.5 µs PPM chips: an 8 µs preamble (16 chips, pulses in chips 0, 2, 7, 9) then 112 bits of 2 chips each, 240 chips in all. Time is measured in **ticks** of $\tfrac{1}{12}$ µs: a chip is 6 ticks; a sample is $S = 6$ ticks at 2.0 MS/s and $S = 5$ ticks at 2.4 MS/s. Sample $n$ covers ticks $[Sn,\ Sn + S)$, counted from stream start; $m[n]$ is its magnitude (R22).

A **candidate** $(n, \varphi)$ is a possible frame start at tick $T = S n + \varphi$, with phase $\varphi \in \{0\}$ at 2.0 MS/s and $\varphi \in \{0, 1, 2, 3, 4\}$ at 2.4 MS/s. Its chip $j$ ($j = 0, \ldots, 239$) covers ticks $[T + 6j,\ T + 6j + 6)$. The **chip energy** $c_j$ is the sum of $m$ over the samples the chip overlaps, each weighted by the overlap in ticks (weights always sum to 6). This is the box-integration model behind dump1090-fa's 2.4 MS/s demodulator, generalized from bits to chips; at 2.0 MS/s it reduces to $c_j = 6\,m[n+j]$, which is the v0.2 algorithm scaled by 6.

| ID | Requirement |
|----|-------------|
| R22 | **Magnitude.** $m = \max(\lvert x_I \rvert, \lvert x_Q \rvert) + \lfloor \min(\lvert x_I \rvert, \lvert x_Q \rvert) / 2 \rfloor$, an unsigned 9-bit value (alpha-max-plus-beta-min with $\alpha = 1$, $\beta = \tfrac{1}{2}$). Example: $(x_I, x_Q) = (-255, 101) \to 255 + 50 = 305$. |
| R32 | **Sample rate.** `ADSB_RATE` selects $S$ and the phase set: 0 → 2.0 MS/s, $S = 6$, $\varphi \in \{0\}$; 1 → 2.4 MS/s, $S = 5$, $\varphi \in \{0, \ldots, 4\}$. The host must send samples at the selected rate; the FPGA cannot detect a mismatch. Default 1 **(assumed)**; the bring-up build order uses 0 first. |
| R33 | **Chip energy.** At 2.4 MS/s, with chip start tick $t = T + 6j$, $a = \lfloor t/5 \rfloor$, $s = t \bmod 5$: $c_j = (5 - s)\,m[a] + (1 + s)\,m[a+1]$. At 2.0 MS/s: $c_j = 6\,m[n+j]$. $c_j$ is unsigned, $c_j \le 6 \times 382 = 2{,}292$ (12 bits). Example (2.4 MS/s): candidate $(100, 3)$, chip 0 → $t = 503$, $a = 100$, $s = 3$ → $c_0 = 2\,m[100] + 4\,m[101]$; with $m[100] = 50$, $m[101] = 300$ → $c_0 = 1{,}300$. |
| R23 | **Every position is a candidate.** Every candidate $(n, \varphi)$ for every sample $n$ and every $\varphi$ allowed by R32 is tested as a possible frame start. No candidate is skipped because of processing load. A candidate is tested once every sample its 240 chips overlap has arrived; candidates whose samples are cut off by a new stream (flag bit 0, or R13) or by a configuration write (R17) are never tested. |
| R24 | **Preamble shape.** With $c_k$ the chip energies of the candidate, the shape test passes when all hold: $c_0 > c_1$, $c_1 < c_2$, $c_2 > c_3$, $c_3 < c_0$, $c_4 < c_0$, $c_5 < c_0$, $c_6 < c_0$, $c_7 > c_8$, $c_8 < c_9$, $c_9 > c_6$. Pulses fall at chips 0, 2, 7 and 9 (0, 1.0, 3.5 and 4.5 µs). |
| R25 | **Preamble level.** With $H = c_0 + c_2 + c_7 + c_9$ and $L = c_1 + c_3 + c_4 + c_5 + c_6 + c_8$, the level test passes when $6H \ge K L$, where $K$ is `PRE_K` (mean pulse $\ge K/4 \times$ mean gap; the default $K = 8$ means $2\times$), and $4 c_k < H$ for $k = 11, 12, 13, 14$ (quiet zone before the data). Candidates passing R24 and R25 increment `ADSB_CAND`, including those later rejected by R27 or R28. $H \le 9{,}168$ (fits the 16-bit record field). Example: $c_0, c_2, c_7, c_9 = 1{,}200$ and $c_1, c_3, \ldots, c_6, c_8 = 300$ → $H = 4{,}800$, $L = 1{,}800$, $6H = 28{,}800 \ge 8 \times 1{,}800 = 14{,}400$ → passes (if $c_{11}, \ldots, c_{14} < 1{,}200$). |
| R26 | **Bit decisions.** Data bit $i$ ($i = 0, \ldots, 111$) is 1 if $c_{16+2i} > c_{17+2i}$, else 0 (ties give 0). At 2.4 MS/s this equals the sign of dump1090-fa's `slice_phase0` to `slice_phase4` correlations. Example: bit whose first chip starts at offset $s = 0$ of sample $a$: $c_{16+2i} - c_{17+2i} = 5\,m[a] - 3\,m[a+1] - 2\,m[a+2]$; $m = (300, 20, 10)$ → $1{,}420 > 0$ → 1. |
| R27 | **Frame acceptance.** A candidate is emitted as an ADS-B record when its downlink format (bits 0–4) is 17 or 18 and the CRC-24 remainder of all 112 bits with generator 0x1FFF409 is zero. The record carries $n$, $\varphi$, the rate and the signal level $H$. |
| R28 | **Overlap suppression.** Candidates are evaluated in increasing order of $T$. After a frame is emitted at $T$, no frame is emitted at any $T'$ with $T < T' < T + 1{,}440$ (240 chips, 120 µs). Consequence: when several phases of one transmission pass the CRC, the earliest is reported. Example (2.4 MS/s): emit at $(1000, 2)$, $T = 5{,}002$ → $(1000, 3)$ and $(1288, 1)$ ($T' = 6{,}441$) are suppressed; $(1288, 2)$ ($T' = 6{,}442$) is not. |
| R29 | **Latency.** The ADS-B record's TX preamble starts within 120 µs **(assumed)** after the last FCS dibit of the I/Q packet containing the frame's final sample, $\lfloor (T + 1{,}439)/S \rfloor$. Measured in simulation. |

### Board status

| ID | Requirement |
|----|-------------|
| R30 | **LEDs.** led[0] blinks ~1 Hz from `clk_core`; led[1] blinks ~1 Hz from `clk_rmii` (proves the module's clock is present); led[2] PLL locked; led[3] PHY link up (R18); led[4] lit $\ge 50$ ms after each accepted I/Q packet; led[5] lit $\ge 50$ ms after each ADS-B frame; led[6] lit while `MODE` = 1; led[7] sticky: any of `RX_BAD`, `RX_BADPKT` or `RX_SEQGAP` changed since last cleared. |
| R31 | **Clock-domain crossings.** Every signal crossing between `rmii` and `core` uses one of the mechanisms in the crossings table. The `microarch` skill's `domains.py` report shows zero UNSAFE crossings. |

## Derived properties (for formal)

- P1 The parallel CRC-24 unit equals the bit-serial definition (generator 0x1FFF409) for every 112-bit input.
- P2 The parallel CRC-32 unit equals the bit-serial IEEE 802.3 definition for every input byte and state.
- P3 Each async FIFO never writes when full, never reads when empty, and returns data in write order.
- P4 The DSP's sample-input ready signal is never low for more than 7 consecutive `clk_core` cycles outside reset (R20).
- P5 TX_EN never drops in the middle of a frame, and stays low for at least 48 `clk_rmii` cycles between frames (R8, R9).
- P6 No two ADS-B records ever have frame starts T less than 1,440 ticks apart (R28).
- P7 Read-only CSRs never change value as a result of a CSR write (R15).
- P8 Counters never decrease except when cleared, and stop at 0xFFFFFFFF (R16, within its $2^{40}$-event bound).

## Budget

| ID | Target |
|----|--------|
| T1 | `clk_core` and `clk_rmii` Fmax $\ge 60$ MHz each in nextpnr on LFE5U-85F speed 6 (20% margin over 50 MHz). |
| T2 | $\le 20{,}000$ LUT4s (about 24% of the device), leaving room for the FM stretch goal. |
| T3 | $\le 8$ MULT18X18D blocks of 156 (ADS-B needs few or none). |
| T4 | $\le 30$ % of EBR block RAM. |

## Verification plan

- **Reference model.** `tb/adsb_ref.py` (golden) implements every stage bit-exactly with numpy integers and exposes each stage's output as a tap (magnitudes, candidates with pass/fail reason, bits, CRC remainder, records), so a failing block test points to one block. It also contains a synthetic signal generator: random DF17/DF18 frames at a chosen SNR and position, over Gaussian noise, quantized to u8 like the RTL-SDR.
- **Block tests.** cocotb tests per block compare against the matching tap and cite R/P IDs: RMII RX/TX, CRC-32, MAC filter, IPv4/UDP/ARP parser, packet builder, CSR, async FIFO, input conversion, magnitude, preamble detector, bit slicer + CRC-24, overlap suppression.
- **System test.** The full top level in Verilator with a cocotb RMII PHY model that sends Ethernet frames built by `host/protocol.py`. It covers ARP, CSR access, bad-FCS and bad-packet injection, sequence gaps, an I/Q index that goes backwards (R13), a CSR write between two packets of one stream (R17), reads of `ADSB_CAND` mid-stream, an ARP flood that forces `TX_DROP` (R34), back-to-back frames at the minimum gap, and ADS-B frames split across packet boundaries.
- **ADS-B acceptance (synthetic).** The generator places each frame at a uniformly random *continuous* start time (not just whole samples or ticks), renders the PPM envelope at a high internal rate, and box-integrates it to the output rate before adding noise and quantizing to u8. At 2.4 MS/s: 10,000 random DF17/DF18 frames at 20 dB peak SNR → $\ge 99$ % **(assumed)** emitted, zero frames emitted that were not sent. At 2.0 MS/s: 10,000 sample-aligned frames → $\ge 99$ %; the same random-start set is also run and its yield reported (no target; it shows what the phase search buys). $10^8$ samples of noise alone produce zero frames at each rate (reference model; hardware replay confirms).
- **ADS-B acceptance (recorded).** One-second clips from a real antenna are committed and used in CI: `captures/adsb_1s_2m4.cu8` (2.4 MS/s, 4.8 MB) and `captures/adsb_1s_2m0.cu8` (2.0 MS/s, 4.0 MB). Longer captures stay out of git, listed with SHA-256 hashes in `captures/manifest.json`. On each capture the FPGA output equals the reference model (R21), and the model emits $\ge 80$ % **(assumed)** of the DF17 frames that dump1090-fa (or readsb) decodes from the same file at the same rate. A 2.4 MS/s and a 2.0 MS/s capture of the same sky are compared to confirm the phase search earns its keep.
- **Formal.** SymbiYosys proofs for P1–P8 with bounded depth where induction is impractical.
- **Coverage and mutation.** Same targets as the bf16 project: 100% line coverage after waivers, every mutant killed or explained. Functional coverage includes each R24/R25 failure reason, CRC pass/fail, DF17 vs DF18, overlapping candidates, every packet-drop reason, and every TX priority conflict.

## Host tools (Python, macOS)

| Tool | Job |
|------|-----|
| `host/send_iq.py` | read a `.cu8` file or stdin (`rtl_sdr -f 1090e6 -s 2.4e6 -g 40 -`), packetize per R12–R14, `--rate 2.4e6` (default) or `2e6`, setting `ADSB_RATE` to match via the CSR port, send at `--speed` $\times$ real time (0 = as fast as the link allows) |
| `host/adsb_rx.py` | receive records and print AVR lines (`*8D…;` with sample-index timestamps), optionally decoding callsigns and positions with pyModeS |
| `host/csr.py` | read and write registers by name |

## Build order

Each step ends with a hardware check before the next begins.

| Step | Goal | Hardware check | Requirements |
|------|------|----------------|--------------|
| Step 0 · Clocks | PLL, resets, LED heartbeats | led[0], led[1], led[2] lit/blinking with the module plugged in | R30 |
| Step 1 · Receive | RMII RX, FCS, filters, counters | `RX_FRAMES` activity on led[4] while the Mac pings | R1–R5 |
| Step 2 · ARP | ARP reply, RMII TX | `arp -a` on the Mac shows the FPGA MAC | R6, R8, R9 |
| Step 3 · CSR | UDP parse/build, CSR block, crossings | `host/csr.py read ID` prints 0x53445231 | R7, R10–R12, R15–R16, R31, R34 |
| Step 4 · Loopback | temporary echo of I/Q packets (removed later) | 30 s at full line rate, no loss, data equal | R13, R14, R20 |
| Step 5 · ADS-B replay, 2.0 MS/s | full ADS-B chain with `ADSB_RATE` = 0 on the 2.0 MS/s capture | output equals the model, frames decode with pyModeS | R17, R19, R21, R22, R23, R24, R25, R26, R27, R28, R29, R32, R33 |
| Step 5b · ADS-B replay, 2.4 MS/s | phase search enabled, `ADSB_RATE` = 1, on the 2.4 MS/s capture | output equals the model; yield vs dump1090-fa recorded | R23, R26, R28, R32, R33 |
| Step 6 · ADS-B live | `rtl_sdr` at 2.4 MS/s piped through `send_iq.py` | aircraft appear in real time | R21, R32 |
| Step 7 · PHY status | MDIO master | `PHY_STATUS` shows link up, 100 Mbit full duplex | R18 |

If anything on the module misbehaves during Step 1, emard's example notes that the module can be brought up with MDC and MDIO disconnected; that's why Step 7 comes last.

## Out of scope

- TCP, DHCP, IPv6, ICMP echo, IP fragmentation, VLAN tags, jumbo frames.
- Mode S formats other than DF17/DF18, CRC error correction, ADS-B position decoding on the FPGA (the host does it).
- Gigabit Ethernet, and any second Ethernet port.
- Controlling the RTL-SDR from the FPGA; tuning and gain stay on the host.
- Multiple simultaneous hosts.

Stretch goals (give each an R number before starting it): FM mode (appendix); ICMP echo for `ping`; single-bit CRC correction for DF17 (bladeRF-adsb's soft bit confidence is a model); choosing the best-scoring phase instead of the earliest (finer timestamps, larger H); a higher-rate radio (e.g. Airspy) over gigabit Ethernet via an RGMII PHY module.

## Open questions

- Confirm the gn12 reference-clock input routes acceptably in nextpnr (it is not necessarily a dedicated clock pin), and check the timing report for the `rmii` domain.
- Confirm which dump1090-fa or readsb build and options decode a 2.0 MS/s `.cu8` file for the bring-up comparison (2.4 MS/s is their native rate).
- Confirm the RTL-SDR runs at 2.4 MS/s on macOS without USB sample drops over a 10-minute capture (the FPGA cannot see drops; R13 only sees network gaps).
- Confirm the PWR button's polarity in the board-revision LPF/schematic.

## Appendix: FM mode (stretch goal, not yet requirements)

Promote these items to numbered requirements, add a reference model `tb/fm_ref.py`, and update the packet, CSR, LED and budget sections before starting. Nothing here is checked by `spec_trace.py` until then.

**Interface additions.** `MODE` = 2 selects FM. New registers: 0x0C `NCO_PINC` (RW, signed 24-bit), 0x10 `DEEMPH` (RW, 0: 75 µs, 1: 50 µs), 0x3C `AUDIO_PKTS` (RO). Audio goes to host port 5002: 16-byte header (magic `AU`, version, flags, sequence, 8-byte index of the first audio sample) then 480 big-endian s16 samples (10 ms at 48 kHz). Audio packets come after ADS-B records in TX priority. Writing `NCO_PINC` or `DEEMPH` clears DSP state. led[6] also lights after each audio packet.

**Signal chain.** Sample rate 1.92 MS/s; the host tunes 250 kHz away from the station to keep the RTL-SDR's DC spike out of the channel.

| Stage | Rate | Output format |
|-------|------|---------------|
| input conversion (as R19) | 1.92 MS/s | s9 I/Q |
| NCO mixer | 1.92 MS/s | s16 I/Q |
| CIC decimator | 240 kS/s | s18 I/Q |
| discriminator | 240 kS/s | s16 phase, $32768 = \pi$ rad |
| de-emphasis | 240 kS/s | s16 |
| decimating FIR | 48 kS/s | s16 audio |

- **NCO mixer.** 24-bit phase accumulator advancing by `NCO_PINC` per sample; top 10 bits address a 1024-entry s12 sine/cosine table; complex product rounded to s16. Shift $= \text{NCO\_PINC} / 2^{24} \times 1.92$ MHz ($+250$ kHz → 2184533). Spurious-free dynamic range $\ge 60$ dB.
- **CIC decimator.** 4th order, decimation 8, differential delay 1, full 12-bit internal growth, output truncated to s18. About 57 dB attenuation of the adjacent channel 200 kHz away.
- **Discriminator.** Angle of $z[n]\,\overline{z[n-1]}$ within $\pm 2$ LSB of exact $\operatorname{atan2}$ for $\lvert z \rvert \ge 64$. 75 kHz deviation gives $\pm 20480$.
- **De-emphasis.** $y[n] = y[n-1] + \alpha\,(x[n] - y[n-1])$, $\alpha = 3542/65536$ (75 µs, $-3$ dB at 2122 Hz) or $5240/65536$ (50 µs, 3183 Hz).
- **Decimating FIR.** Linear-phase lowpass, decimation 5: ripple $\le 1$ dB over 0–15 kHz, $\ge 40$ dB attenuation from 19 kHz (removes the stereo pilot). Coefficients from `scripts/gen_fir.py`, shared by RTL and model.
- **Level and clipping.** A 400 Hz tone at 75 kHz deviation gives a peak of $20480 \pm 5$ %; results beyond s16 saturate.
- **Quality.** Synthetic 1 kHz tone, 75 kHz deviation, CNR 30 dB in 200 kHz, quantized to u8: SINAD $\ge 40$ dB.
- **Line rate and bit-exactness.** Same rules as R20 and R21.
- **Host tool.** `host/fm_rx.py` writes a WAV file or pipes to `sox -t raw -r 48000 -e signed -b 16 -B -c 1 - -d`.

## Sources

- [emard/ulx3s-misc RMII LAN8720 example](https://github.com/emard/ulx3s-misc/tree/master/examples/eth/rmii/proj) (pin mapping and wiring)
