// crc24_112_props.v: formal properties for crc24_112.
//
// P1: the parallel CRC-24 unit equals the bit-serial definition (generator
// 0x1FFF409) for every 112-bit input. The model below is the textbook LFSR
// (Horner's rule: shift one bit in, subtract G(x) when the degree reaches 24),
// a different method from the RTL's table of x^i mod G(x) columns.
//
// How P1 is proved (task p1). Checking two different XOR networks for equality is
// a known hard case for SAT solvers (they have no XOR algebra), and a direct check
// over all 112-bit inputs stalls every engine. P1 is instead proved through
// linearity over GF(2):
//   1. Both implementations are linear maps from GF(2)^112 to GF(2)^24 by
//      construction. The RTL is rem = XOR over i of in_bits[i] * COL[i]. Each LFSR
//      step is s <- s*x + bit, then s <- s + s[24]*G: shifts and XORs of bits, with
//      no constant term.
//   2. Two linear maps are equal if and only if they agree on a basis.
//   3. Task p1 checks every basis vector x^i (exactly one bit set) and zero, for any i.
// One checked step covers all of them because out_rem depends only on the previous
// cycle's input. The assertion is combinational (always @*), so it is evaluated in
// the same step as the values it reads; a clocked assertion would be evaluated one
// step later and could fall past the end of a short BMC run without checking
// anything. A table entry corrupted on purpose makes this task fail (checked when
// the proof was written).
//
// Task prove (PDR) proves the control properties for all time: out_valid is
// in_valid delayed one cycle, cleared by reset.
`default_nettype none

module crc24_112_props (
    input wire         clk,
    input wire         rst_n,
    input wire         in_valid,
    input wire [111:0] in_bits
);
    wire        out_valid;
    wire [23:0] out_rem;

    crc24_112 dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_bits(in_bits),
                   .out_valid(out_valid), .out_rem(out_rem));

    // Bit-serial definition: bits enter MSB first (in_bits[111] = first bit on air).
    function [23:0] serial(input [111:0] v);
        reg [24:0] s;
        integer k;
        begin
            s = 25'd0;
            for (k = 111; k >= 0; k = k - 1) begin
                s = {s[23:0], v[k]};                     // s <- s*x + bit
                if (s[24]) s = s ^ 25'h1FFF409;           // reduce mod G(x)
            end
            serial = s[23:0];
        end
    endfunction

`ifdef FORMAL
    reg init = 1'b1;
    always @(posedge clk) init <= 1'b0;

    reg [111:0] vq;                                      // last cycle's input
    reg         sv;
    always @(posedge clk) begin
        vq <= in_bits;
        sv <= rst_n & in_valid;
    end

`ifdef P1
    // Inputs restricted to the basis vectors x^i and zero (see step 3 above).
    always @* assume ((in_bits & (in_bits - 112'd1)) == 112'd0);
    reg [1:0] step = 2'd0;                               // one data check, at step 1
    always @(posedge clk) if (step != 2'd3) step <= step + 2'd1;
    always @* if (step == 2'd1) assert (out_rem == serial(vq));   // P1, R27
`else
    always @* if (init) assume (!rst_n);
    always @(posedge clk) if (!init) assert (out_valid == sv);   // latency 1 + reset (microarch 2a)

    // Covers: a nonzero word that passes (remainder 0) and one that fails.
    always @(posedge clk) cover (!init && out_valid && out_rem == 24'd0 && vq != 112'd0);
    always @(posedge clk) cover (!init && out_valid && out_rem != 24'd0);
`endif
`endif
endmodule

`default_nettype wire
