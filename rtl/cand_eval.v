// cand_eval.v: stages D1-F of the ADS-B DSP (docs/microarch.md 2a).
// Evaluates one candidate per cycle from the 240 chip energies on the tick_line
// taps: the preamble shape test (R24), the level and quiet-zone tests (R25), the
// 112 bit decisions (R26) and the downlink-format check (R27). LATENCY = 5 cycles,
// fully pipelined (one candidate may enter every cycle).
//
//   D1  10 shape compares; 112 bit compares; pairwise sums toward H and L
//   D2  H = c0 + c2 + c7 + c9, L = c1 + c3 + c4 + c5 + c6 + c8; DF in {17, 18}
//   E1  multiplier operands K, L registered; 6H; quiet zone 4 c_k < H, k = 11..14
//   E2  K * L registered (MULT18X18D output register)
//   F   level 6H >= K L; out_cand = shape & level & quiet
//
// The CRC-24 of out_bits is computed after this block by crc24_112 (proved
// separately, P1); adsb_emit combines out_cand, out_df_ok and the remainder.
//
// Widths (microarch 3): c_j <= 2292 (12 b), H <= 9168 and L <= 13752 (14 b),
// 6H <= 55008 (16 b), K L <= 255 * 13752 = 3506760 (22 b). Nothing can overflow.
// Preconditions: every tap is a chip energy, c_j <= 2292 (R33, guaranteed by
// chip_energy); pre_k changes only while the pipeline is idle (R17, WAIT_DRAIN).
`default_nettype none

module cand_eval (
    input  wire          clk,
    input  wire          rst_n,      // synchronous, active low; clears valid only
    input  wire [7:0]    pre_k,      // PRE_K = K in the level test (R25)
    input  wire          in_valid,   // tick_line out_valid
    input  wire [2879:0] in_taps,    // c_0 .. c_239, 12 b each, c_0 in the low bits
    input  wire [2:0]    in_phi,
    input  wire [8:0]    in_p,
    output wire          out_valid,  // in_valid delayed by 5 cycles
    output wire          out_cand,   // passed R24 and R25 (counts in ADSB_CAND)
    output wire          out_df_ok,  // DF = bits 0-4 is 17 or 18 (R27)
    output wire [111:0]  out_bits,   // R26; out_bits[111] = data bit 0 (first on air)
    output wire [13:0]   out_h,      // H, the signal level carried in the record (R25, R27)
    output wire [2:0]    out_phi,
    output wire [8:0]    out_p,
    output wire          idle        // no candidate in the pipeline (WAIT_DRAIN)
`ifdef FORMAL
  , output wire [43:0]   f_mul       // {e2_kl, e1_k, e1_l}: multiplier operands and product
`endif
);
    // Chip energies as named wires (a function reading in_taps directly would hide
    // the dependency from the simulator's sensitivity list).
    wire [11:0] c [0:239];
    genvar j;
    generate
        for (j = 0; j < 240; j = j + 1) begin : g_chip
            assign c[j] = in_taps[12*j +: 12];
        end
    endgenerate

    // ---- D1: shape (R24), bits (R26), partial sums ------------------------------
    wire shape_ok = (c[0] > c[1]) & (c[2] > c[1]) & (c[2] > c[3]) & (c[0] > c[3]) &
                    (c[0] > c[4]) & (c[0] > c[5]) & (c[0] > c[6]) & (c[7] > c[8]) &
                    (c[9] > c[8]) & (c[9] > c[6]);

    wire [111:0] bits;
    genvar i;
    generate
        for (i = 0; i < 112; i = i + 1) begin : g_bit
            // data bit i is 1 when its first chip is stronger; ties give 0 (R26)
            assign bits[111 - i] = c[16 + 2*i] > c[17 + 2*i];
        end
    endgenerate

    reg         d1_shape;
    reg [111:0] d1_bits;
    reg [12:0]  d1_h02, d1_h79;                 // c0 + c2, c7 + c9
    reg [12:0]  d1_l13, d1_l45, d1_l68;         // c1 + c3, c4 + c5, c6 + c8
    reg [47:0]  d1_q;                           // c11 .. c14
    reg [2:0]   d1_phi;
    reg [8:0]   d1_p;
    always @(posedge clk) begin
        d1_shape <= shape_ok;
        d1_bits  <= bits;
        d1_h02   <= c[0] + c[2];
        d1_h79   <= c[7] + c[9];
        d1_l13   <= c[1] + c[3];
        d1_l45   <= c[4] + c[5];
        d1_l68   <= c[6] + c[8];
        d1_q     <= in_taps[12*11 +: 48];
        d1_phi   <= in_phi;
        d1_p     <= in_p;
    end

    // ---- D2: H, L (R25), DF (R27) ---------------------------------------------
    reg         d2_shape, d2_df_ok;
    reg [111:0] d2_bits;
    reg [13:0]  d2_h, d2_l;
    reg [47:0]  d2_q;
    reg [2:0]   d2_phi;
    reg [8:0]   d2_p;
    wire [4:0]  df = d1_bits[111:107];
    always @(posedge clk) begin
        d2_shape <= d1_shape;
        d2_df_ok <= (df == 5'd17) | (df == 5'd18);
        d2_bits  <= d1_bits;
        d2_h     <= d1_h02 + d1_h79;
        d2_l     <= d1_l13 + d1_l45 + d1_l68;
        d2_q     <= d1_q;
        d2_phi   <= d1_phi;
        d2_p     <= d1_p;
    end

    // ---- E1: multiplier operands, 6H, quiet zone (R25) -----------------------
    function quiet(input [11:0] ck, input [13:0] h);
        quiet = {ck, 2'b00} < h;                // 4 c_k < H
    endfunction

    reg         e1_shape, e1_df_ok, e1_quiet;
    reg [111:0] e1_bits;
    reg [13:0]  e1_h, e1_l;
    reg [7:0]   e1_k;
    reg [15:0]  e1_6h;
    reg [2:0]   e1_phi;
    reg [8:0]   e1_p;
    always @(posedge clk) begin
        e1_shape <= d2_shape;
        e1_df_ok <= d2_df_ok;
        e1_quiet <= quiet(d2_q[0 +: 12], d2_h) & quiet(d2_q[12 +: 12], d2_h) &
                    quiet(d2_q[24 +: 12], d2_h) & quiet(d2_q[36 +: 12], d2_h);
        e1_bits  <= d2_bits;
        e1_h     <= d2_h;
        e1_l     <= d2_l;                       // MULT18X18D input register
        e1_k     <= pre_k;                      // MULT18X18D input register
        e1_6h    <= {d2_h, 2'b00} + {1'b0, d2_h, 1'b0};
        e1_phi   <= d2_phi;
        e1_p     <= d2_p;
    end

    // ---- E2: K * L ------------------------------------------------------------
    reg         e2_shape, e2_df_ok, e2_quiet;
    reg [111:0] e2_bits;
    reg [13:0]  e2_h;
    reg [15:0]  e2_6h;
    reg [21:0]  e2_kl;
    reg [2:0]   e2_phi;
    reg [8:0]   e2_p;
    always @(posedge clk) begin
        e2_shape <= e1_shape;
        e2_df_ok <= e1_df_ok;
        e2_quiet <= e1_quiet;
        e2_bits  <= e1_bits;
        e2_h     <= e1_h;
        e2_6h    <= e1_6h;
        e2_kl    <= e1_k * e1_l;                // MULT18X18D output register
        e2_phi   <= e1_phi;
        e2_p     <= e1_p;
    end

    // ---- F: level test, combine (R25) -----------------------------------------
    reg         f_cand, f_df_ok;
    reg [111:0] f_bits;
    reg [13:0]  f_h;
    reg [2:0]   f_phi;
    reg [8:0]   f_p;
    always @(posedge clk) begin
        f_cand  <= e2_shape & ({6'b0, e2_6h} >= e2_kl) & e2_quiet;
        f_df_ok <= e2_df_ok;
        f_bits  <= e2_bits;
        f_h     <= e2_h;
        f_phi   <= e2_phi;
        f_p     <= e2_p;
    end

    // ---- valid pipeline (reset) ---------------------------------------------
    reg [4:0] v_q;
    always @(posedge clk)
        if (!rst_n) v_q <= 5'd0;
        else        v_q <= {v_q[3:0], in_valid};

    assign out_valid = v_q[4];
    assign out_cand  = f_cand;
    assign out_df_ok = f_df_ok;
    assign out_bits  = f_bits;
    assign out_h     = f_h;
    assign out_phi   = f_phi;
    assign out_p     = f_p;
    assign idle      = ~|v_q;
`ifdef FORMAL
    assign f_mul     = {e2_kl, e1_k, e1_l};
`endif
endmodule

`default_nettype wire
