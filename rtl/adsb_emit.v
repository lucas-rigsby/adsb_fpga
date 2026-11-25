// adsb_emit.v: stage G of the ADS-B DSP (docs/microarch.md 2a).
// Turns evaluated candidates into ADS-B records: frame acceptance (R27), the
// header-derived frame start T = S n + phi (R14), overlap suppression (R28), the
// record sequence number and output-queue overflow (R34). LATENCY = 3 cycles from
// in_valid to out_rec; one candidate may enter every cycle.
//
// Timing of the inputs. The candidate fields come from cand_eval at cycle t. Two
// inputs arrive one cycle later, at t + 1:
//   in_rem  the CRC-24 remainder of in_bits from crc24_112 (1-cycle latency);
//   in_n    the stream sample index of the candidate's first sample, read from the
//           index ring at address in_p (a synchronous RAM, 1-cycle read).
//
//   G1  register the candidate; cand_inc pulse (ADSB_CAND counts every candidate
//       that passed R24/R25, R25)
//   G2  ok = cand AND df_ok AND rem == 0 (R27); T = S n + phi (R14)
//   G3  emit = ok AND (no record yet in this segment OR T >= T_last + 1440) (R28);
//       on emit: lim <- T + 1440, seq <- seq + 1
//
// Output queue (R34): out_push = out_rec AND NOT af3_full, out_drop = out_rec AND
// af3_full (counted in TX_DROP). The sequence number advances either way, so the
// host sees the gap. clear (R17: new stream or configuration write) resets the
// suppression history only; the sequence number runs from reset (record format).
//
// Widths: n is 64 b, T = S n + phi <= 6 (2^64 - 1) + 4 < 2^67 (67 b), and the
// limit T + 1440 < 2^68 (68 b), so nothing wraps for any index.
// Precondition: rate changes only together with clear (R17).
`default_nettype none

module adsb_emit (
    input  wire          clk,
    input  wire          rst_n,      // synchronous, active low
    input  wire          clear,      // R17 dsp_clear: forget the last emitted T
    input  wire          rate,       // ADSB_RATE: 1 = 2.4 MS/s (S = 5), 0 = 2.0 MS/s (S = 6)
    // candidate from cand_eval, cycle t
    input  wire          in_valid,
    input  wire          in_cand,    // passed R24, R25
    input  wire          in_df_ok,   // DF 17 or 18
    input  wire [111:0]  in_bits,
    input  wire [13:0]   in_h,
    input  wire [2:0]    in_phi,
    // late inputs, cycle t + 1
    input  wire [23:0]   in_rem,     // crc24_112 remainder of in_bits
    input  wire [63:0]   in_n,       // index ring at in_p
    // to the counters (csr_regs)
    output wire          cand_inc,   // one pulse per candidate that passed R24/R25
    // record (valid-only), and the push into AF3
    output wire          out_rec,    // a record was emitted this cycle (counts in ADSB_FRAMES)
    output wire          out_push,   // out_rec and AF3 has room
    output wire          out_drop,   // out_rec and AF3 full (TX_DROP, R34)
    input  wire          af3_full,
    output wire [31:0]   out_seq,
    output wire [63:0]   out_n,
    output wire [2:0]    out_phi,
    output wire          out_rate,
    output wire          out_df18,
    output wire [15:0]   out_h,
    output wire [111:0]  out_frame,
    output wire          idle        // nothing in flight (WAIT_DRAIN)
`ifdef FORMAL
  , output wire [100:0]  f_state     // {have_q, seq_q, lim_q} for the induction invariants
`endif
);
    // ---- G1: candidate registered; late inputs arrive in this cycle -------
    reg         g1_v, g1_cand, g1_df_ok;
    reg [111:0] g1_bits;
    reg [13:0]  g1_h;
    reg [2:0]   g1_phi;
    always @(posedge clk) begin
        g1_cand  <= in_cand;
        g1_df_ok <= in_df_ok;
        g1_bits  <= in_bits;
        g1_h     <= in_h;
        g1_phi   <= in_phi;
    end

    // ---- G2: acceptance (R27) and frame start (R14) ----------------------
    // S n as shifts: 5n = 4n + n, 6n = 4n + 2n
    wire [66:0] n4  = {1'b0, in_n, 2'b00};
    wire [66:0] sn  = n4 + (rate ? {3'b000, in_n} : {2'b00, in_n, 1'b0});
    wire [66:0] t_n = sn + {64'd0, g1_phi};

    reg         g2_v, g2_ok;
    reg [66:0]  g2_t;
    reg [63:0]  g2_n;
    reg [111:0] g2_bits;
    reg [13:0]  g2_h;
    reg [2:0]   g2_phi;
    always @(posedge clk) begin
        g2_ok   <= g1_cand & g1_df_ok & (in_rem == 24'd0);
        g2_t    <= t_n;
        g2_n    <= in_n;
        g2_bits <= g1_bits;
        g2_h    <= g1_h;
        g2_phi  <= g1_phi;
    end

    // ---- G3: overlap suppression (R28), sequence number -------------------
    reg         have_q;                     // a record was emitted in this segment
    reg [67:0]  lim_q;                      // T of that record + 1440
    reg [31:0]  seq_q;                      // sequence number of the next record
    wire        emit = g2_v & g2_ok & (~have_q | ({1'b0, g2_t} >= lim_q));

    reg         rec_q;
    reg [31:0]  o_seq;
    reg [63:0]  o_n;
    reg [111:0] o_frame;
    reg [13:0]  o_h;
    reg [2:0]   o_phi;
    reg         o_rate;
    always @(posedge clk) begin
        if (emit) begin
            o_seq   <= seq_q;
            o_n     <= g2_n;
            o_frame <= g2_bits;
            o_h     <= g2_h;
            o_phi   <= g2_phi;
            o_rate  <= rate;
        end
    end

    always @(posedge clk) begin
        if (!rst_n) begin
            {g1_v, g2_v, rec_q} <= 3'b000;
            have_q <= 1'b0;
            seq_q  <= 32'd0;
        end else begin
            g1_v  <= in_valid & ~clear;
            g2_v  <= g1_v & ~clear;
            rec_q <= emit & ~clear;
            if (clear) have_q <= 1'b0;
            else if (emit) begin
                have_q <= 1'b1;
                seq_q  <= seq_q + 32'd1;
            end
        end
        if (emit) lim_q <= {1'b0, g2_t} + 68'd1440;     // data: no reset
    end

    assign cand_inc  = g1_v & g1_cand;
    assign out_rec   = rec_q;
    assign out_push  = rec_q & ~af3_full;
    assign out_drop  = rec_q & af3_full;
    assign out_seq   = o_seq;
    assign out_n     = o_n;
    assign out_phi   = o_phi;
    assign out_rate  = o_rate;
    assign out_df18  = o_frame[111:107] == 5'd18;
    assign out_h     = {2'b00, o_h};
    assign out_frame = o_frame;
    assign idle      = ~(g1_v | g2_v | rec_q);
`ifdef FORMAL
    assign f_state   = {have_q, seq_q, lim_q};
`endif
endmodule

`default_nettype wire
