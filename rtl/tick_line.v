// tick_line.v: stage C of the ADS-B DSP (docs/microarch.md 2a), the tick delay line.
// Holds the last LINE = 6*(CHIPS-1)+1 tick energies e(t). After every shift the
// oldest tick T is a candidate start, and its CHIPS chip energies sit at fixed
// taps 6 apart: c_j = e(T + 6j) (R33) = out_taps[12*j +: 12]. One candidate per
// shift, in increasing T, every T tested (R23).
//
// Validity (microarch 2a): the first batch of a segment carries 5 ticks before
// t = 0 (chip_energy's insertion rule), so a candidate is valid once
// LINE + 5 = 1440 ticks have been shifted, i.e. exactly when T >= 0 and the
// whole frame is present. At 2.0 MS/s only phase 0 is a candidate (R32).
//
// out_valid is high for the one cycle after a shift that produced a valid
// candidate; out_taps, out_phi and out_p then describe it. T = S*p + phi with p
// counted in samples from the segment start (out_p is p mod 2^PW, the index-ring
// address of the candidate's first sample, R14).
`default_nettype none

module tick_line #(
    parameter integer W     = 12,       // R33: e <= 2292
    parameter integer CHIPS = 240,      // Mode S frame: 16 preamble + 224 data chips
    parameter integer PW    = 9         // index-ring address bits
) (
    input  wire               clk,
    input  wire               rst_n,    // synchronous, active low
    input  wire               clear,    // R17 dsp_clear: start a new segment
    input  wire               rate,     // 1 = 2.4 MS/s (S = 5), 0 = 2.0 MS/s (S = 6); change only with clear
    input  wire               in_valid, // shift strobe from chip_energy
    input  wire [W-1:0]       in_e,
    output wire               out_valid,
    output wire [2:0]         out_phi,  // T mod S
    output wire [PW-1:0]      out_p,    // T div S, mod 2^PW
    output wire [W*CHIPS-1:0] out_taps  // c_0 .. c_{CHIPS-1}, c_0 in the low bits
);
    localparam integer LINE = 6 * (CHIPS - 1) + 1;   // 1,435
    localparam integer THR  = LINE + 5;              // ticks shifted before T = 0 is complete
    localparam integer FW   = $clog2(THR + 1);
    localparam [31:0]   THR32  = THR;
    localparam [FW-1:0] THR_FW = THR32[FW-1:0];
    localparam [FW-1:0] THR_M1 = THR_FW - 1'b1;

    // ---- the line: entry 0 = oldest tick T, entry LINE-1 = newest (data: no reset)
    reg [W*LINE-1:0] line_q;
    wire shift = in_valid & rst_n & ~clear;
    always @(posedge clk)
        if (shift) line_q <= {in_e, line_q[W*LINE-1:W]};

    genvar j;
    generate
        for (j = 0; j < CHIPS; j = j + 1) begin : g_tap
            assign out_taps[W*j +: W] = line_q[W*6*j +: W];   // c_j = e(T + 6j)
        end
    endgenerate

    // ---- fill counter and head position (T = S*p + phi)
    reg [FW-1:0] fill_q;
    reg [2:0]    phi_q;
    reg [PW-1:0] p_q;
    reg          v_q;

    wire [2:0] s_last = rate ? 3'd4 : 3'd5;            // S - 1 (R32)
    wire       full_n = fill_q >= THR_M1;             // full after this shift
    wire       first  = fill_q == THR_M1;             // this shift makes T = 0 the head
    wire       wrap   = phi_q == s_last;
    wire [2:0]    phi_n = (first | wrap) ? 3'd0 : phi_q + 3'd1;
    wire [PW-1:0] p_n   = first ? {PW{1'b0}} : (wrap ? p_q + 1'b1 : p_q);

    always @(posedge clk) begin
        if (!rst_n || clear) begin
            fill_q <= {FW{1'b0}};
            phi_q  <= 3'd0;
            p_q    <= {PW{1'b0}};
            v_q    <= 1'b0;
        end else begin
            v_q <= shift & full_n & (rate | (phi_n == 3'd0));   // R32: phase set
            if (shift) begin
                fill_q <= full_n ? THR_FW : fill_q + 1'b1;
                if (full_n) begin
                    phi_q <= phi_n;
                    p_q   <= p_n;
                end
            end
        end
    end

    assign out_valid = v_q;
    assign out_phi   = phi_q;
    assign out_p     = p_q;
endmodule

`default_nettype wire
