"""Generate (or check) the column table in rtl/crc24_112.v.

CRC-24 (generator G(x) = 0x1FFF409, degree 24) is linear over GF(2): the remainder
of a 112-bit word v is the XOR of x^i mod G(x) over the bits i that are set. This
script computes those 112 columns by repeated multiplication by x (shift left,
reduce when bit 24 appears), which is a different method from both the reference
model's long division (tb/adsb_ref.py) and the bit-serial LFSR in the formal
properties (formal/crc24_112_props.v).

  python3 scripts/gen_crc24_table.py            # rewrite the table between the markers
  python3 scripts/gen_crc24_table.py --check    # exit 1 if the table in the RTL is stale
"""
import pathlib
import sys

GEN = 0x1FFF409          # R27
NBITS = 112
RTL = pathlib.Path(__file__).resolve().parent.parent / "rtl" / "crc24_112.v"
BEGIN, END = "// BEGIN GENERATED TABLE", "// END GENERATED TABLE"


def columns():
    cols, c = [], 1                      # x^0
    for _ in range(NBITS):
        cols.append(c)
        c <<= 1                          # multiply by x
        if c >> 24 & 1:
            c ^= GEN                     # reduce modulo G(x)
    return cols


def table_text():
    cols = columns()
    lines = [BEGIN + " (scripts/gen_crc24_table.py): COL[i] = x^i mod G(x)",
             "    localparam [24*112-1:0] COL = {"]
    for i in range(NBITS - 1, -1, -1):
        sep = "," if i else ""
        lines.append(f"        24'h{cols[i]:06X}{sep}   // x^{i}")
    lines += ["    };", "    " + END]
    return "\n".join("    " + l if l.startswith("//") else l for l in lines)


def main():
    src = RTL.read_text()
    a, b = src.index("    " + BEGIN), src.index(END) + len(END)
    new = src[:a] + table_text() + src[b:]
    if "--check" in sys.argv:
        if new != src:
            sys.exit(f"{RTL}: generated table is stale; run scripts/gen_crc24_table.py")
        print("table up to date")
    else:
        RTL.write_text(new)
        print(f"wrote {NBITS} columns to {RTL}")


if __name__ == "__main__":
    main()
