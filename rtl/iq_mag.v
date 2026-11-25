// iq_mag.v: stages A1/A2 of the ADS-B DSP (docs/microarch.md 2a).
// One rtl_sdr I/Q byte pair in, one 9-bit magnitude out, LATENCY = 2 cycles.
//   R19: x = 2u - 255 (odd, -255..255)
//   R22: m = max(|xI|,|xQ|) + (min(|xI|,|xQ|) >> 1), 1..382
`default_nettype none

module iq_mag (
    input  wire       clk,
    input  wire       rst_n,        // synchronous, active low; clears valid only
    input  wire       in_valid,
    input  wire [7:0] in_i,         // unsigned rtl_sdr byte
    input  wire [7:0] in_q,
    output wire       out_valid,    // in_valid delayed by 2 cycles
    output wire [8:0] out_m
);
    // R19 without a carry chain: |2u - 255| is odd and equals
    //   u >= 128: 2(u - 128) + 1 = {u[6:0], 1}
    //   u <  128: 2(127 - u) + 1 = {~u[6:0], 1}
    // so |x| = {h, 1'b1} with h = u[6:0] ^ {7{~u[7]}}, 8 bits, max 255.
    // Only h is stored: bit 0 of |x| is always 1.
    function [6:0] absh(input [7:0] u);
        absh = u[6:0] ^ {7{~u[7]}};
    endfunction

    // ---- A1: |x| >> 1 for I and Q
    reg [6:0] hi_q, hq_q;
    reg       v1_q;
    always @(posedge clk) begin
        hi_q <= absh(in_i);
        hq_q <= absh(in_q);
    end

    // ---- A2: alpha-max-plus-beta-min, alpha = 1, beta = 1/2 (R22)
    // Both |x| share bit 0 = 1, so comparing h compares |x|, and min >> 1 is h of the min.
    wire       i_big = hi_q >= hq_q;
    wire [6:0] hmx = i_big ? hi_q : hq_q;
    wire [6:0] hmn = i_big ? hq_q : hi_q;
    reg  [8:0] m_q;
    reg        v2_q;
    always @(posedge clk) m_q <= {1'b0, hmx, 1'b1} + {2'b0, hmn};

    always @(posedge clk) begin                 // valid: reset
        if (!rst_n) {v1_q, v2_q} <= 2'b00;
        else        {v1_q, v2_q} <= {in_valid, v1_q};
    end

    assign out_valid = v2_q;
    assign out_m     = m_q;
endmodule

`default_nettype wire
