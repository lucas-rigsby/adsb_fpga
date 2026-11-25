"""UDP payload formats shared by the host tools and the testbenches (docs/spec.md, "Packet formats").

All multi-byte fields are big-endian. Every payload starts with a 2-byte ASCII magic,
a 1-byte version, a 1-byte flags field (op for CSR) and a 32-bit sequence number.

>>> p = pack_iq(seq=7, index=1000, samples=bytes([0, 255, 128, 127]), first=True)
>>> len(p), p[:4]
(20, b'IQ\\x01\\x01')
>>> parse_iq(p)
IqPacket(flags=1, seq=7, index=1000, samples=b'\\x00\\xff\\x80\\x7f')
>>> r = pack_adsb(seq=1, index=2400000, phase=3, rate24=True, df18=False, level=4800,
...               frame=bytes.fromhex('8D4840D6202CC371C32CE0576098'))
>>> len(r), parse_adsb(r).phase, parse_adsb(r).level
(32, 3, 4800)
>>> parse_csr(pack_csr(op=0, seq=5, addr=0x00))
CsrPacket(op=0, seq=5, addr=0, data=0)
"""
from __future__ import annotations

import struct
from typing import NamedTuple

VERSION = 1

PORT_IQ = 5000
PORT_ADSB = 5001
PORT_FM = 5002  # reserved for the FM stretch goal
PORT_CSR = 5005

IQ_HDR = struct.Struct(">2sBBIQ")        # magic, version, flags, seq, index: 16 bytes
ADSB_FMT = struct.Struct(">2sBBIQH14s")  # 32 bytes
CSR_FMT = struct.Struct(">2sBBIHI")      # 14 bytes

IQ_MAX_PAIRS = 700
IQ_DEFAULT_PAIRS = 512

CSR_READ, CSR_WRITE, CSR_RESP = 0, 1, 2

# CSR addresses (spec, "Control and status registers")
CSR = {
    "ID": 0x00, "VERSION": 0x04, "MODE": 0x08, "PRE_K": 0x14, "CTRL": 0x18,
    "ADSB_RATE": 0x1C, "RX_FRAMES": 0x20, "RX_BAD": 0x24, "RX_BADPKT": 0x28,
    "RX_SEQGAP": 0x2C, "TX_NOHOST": 0x30, "ADSB_CAND": 0x34, "ADSB_FRAMES": 0x38,
    "PHY_STATUS": 0x40,
}


class BadPacket(ValueError):
    """R12: wrong magic, version or length; the FPGA drops it and counts RX_BADPKT."""


class IqPacket(NamedTuple):
    flags: int
    seq: int
    index: int
    samples: bytes  # I0, Q0, I1, Q1, ... (u8)


class AdsbRecord(NamedTuple):
    seq: int
    index: int
    phase: int
    rate24: bool
    df18: bool
    level: int
    frame: bytes


class CsrPacket(NamedTuple):
    op: int
    seq: int
    addr: int
    data: int


def pack_iq(seq: int, index: int, samples: bytes, first: bool = False) -> bytes:
    assert len(samples) % 2 == 0 and 1 <= len(samples) // 2 <= IQ_MAX_PAIRS, "R12: N outside 1-700"
    return IQ_HDR.pack(b"IQ", VERSION, 1 if first else 0, seq & 0xFFFFFFFF, index) + bytes(samples)


def parse_iq(p: bytes) -> IqPacket:
    if len(p) < IQ_HDR.size:
        raise BadPacket("short")
    magic, ver, flags, seq, index = IQ_HDR.unpack_from(p)
    body = p[IQ_HDR.size:]
    if magic != b"IQ" or ver != VERSION:
        raise BadPacket("magic/version")
    if len(body) % 2 or not 1 <= len(body) // 2 <= IQ_MAX_PAIRS:
        raise BadPacket("length")
    return IqPacket(flags, seq, index, bytes(body))


def pack_adsb(seq: int, index: int, phase: int, rate24: bool, df18: bool, level: int, frame: bytes) -> bytes:
    assert 0 <= phase <= 4 and 0 <= level <= 0xFFFF and len(frame) == 14
    flags = (1 if df18 else 0) | (phase << 1) | ((1 if rate24 else 0) << 4)
    return ADSB_FMT.pack(b"AD", VERSION, flags, seq & 0xFFFFFFFF, index, level, frame)


def parse_adsb(p: bytes) -> AdsbRecord:
    if len(p) != ADSB_FMT.size:
        raise BadPacket("length")
    magic, ver, flags, seq, index, level, frame = ADSB_FMT.unpack(p)
    if magic != b"AD" or ver != VERSION:
        raise BadPacket("magic/version")
    return AdsbRecord(seq, index, (flags >> 1) & 7, bool(flags >> 4 & 1), bool(flags & 1), level, frame)


def pack_csr(op: int, seq: int, addr: int, data: int = 0) -> bytes:
    return CSR_FMT.pack(b"CS", VERSION, op, seq & 0xFFFFFFFF, addr, data & 0xFFFFFFFF)


def parse_csr(p: bytes, request: bool = True) -> CsrPacket:
    if len(p) != CSR_FMT.size:
        raise BadPacket("length")
    magic, ver, op, seq, addr, data = CSR_FMT.unpack(p)
    if magic != b"CS" or ver != VERSION:
        raise BadPacket("magic/version")
    if request and op not in (CSR_READ, CSR_WRITE):
        raise BadPacket("op")
    return CsrPacket(op, seq, addr, data)


def packetize(iq: bytes, pairs: int = IQ_DEFAULT_PAIRS, seq0: int = 0, index0: int = 0):
    """Split a .cu8 byte stream into I/Q payloads; the first carries the new-stream flag."""
    out = []
    for k, off in enumerate(range(0, len(iq), 2 * pairs)):
        chunk = iq[off:off + 2 * pairs]
        out.append(pack_iq(seq0 + k, index0 + off // 2, chunk, first=(k == 0)))
    return out
