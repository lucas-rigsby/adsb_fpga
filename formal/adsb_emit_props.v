// adsb_emit_props.v: formal properties for adsb_emit, proved for all time (PDR).
//   P6  No two ADS-B records of one segment have frame starts T less than 1,440
//       ticks apart (R28). T is recomputed here from the record fields, S n + phi,
//       with a constant multiply (the RTL uses shifts). No assumption about the
//       order of the candidates is needed.
//   seq Each record carries the number of records emitted since reset (record
//       format; R34 drops still consume their number).
//   R34 Each record is either pushed or dropped, never both, by af3_full.
//   ctl A record appears only 3 cycles after a candidate entered, cand_inc follows
//       in_cand one cycle later, and idle is high exactly when nothing is in flight.
// Precondition (R17): rate changes only together with clear.
//
// Engine: k-induction (smtbmc, depth 8). The suppression state (have, lim) and the
// sequence counter are exposed on a formal-only port (f_state) and tied to the
// shadow copies here by invariants; the pipeline registers between them are
// covered by the induction depth. PDR alone stalls on the 64-bit arithmetic.
`default_nettype none

module adsb_emit_props (
    input wire          clk,
    input wire          rst_n,
    input wire          clear,
    input wire          rate,
    input wire          in_valid,
    input wire          in_cand,
    input wire          in_df_ok,
    input wire [111:0]  in_bits,
    input wire [13:0]   in_h,
    input wire [2:0]    in_phi,
    input wire [23:0]   in_rem,
    input wire [63:0]   in_n,
    input wire          af3_full
);
    wire         cand_inc, out_rec, out_push, out_drop, out_rate, out_df18, idle;
    wire [31:0]  out_seq;
    wire [63:0]  out_n;
    wire [2:0]   out_phi;
    wire [15:0]  out_h;
    wire [111:0] out_frame;
    wire [100:0] f_state;
    wire         f_have = f_state[100];
    wire [31:0]  f_seq  = f_state[99:68];
    wire [67:0]  f_lim  = f_state[67:0];

    adsb_emit dut (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate),
        .in_valid(in_valid), .in_cand(in_cand), .in_df_ok(in_df_ok), .in_bits(in_bits),
        .in_h(in_h), .in_phi(in_phi), .in_rem(in_rem), .in_n(in_n), .cand_inc(cand_inc),
        .out_rec(out_rec), .out_push(out_push), .out_drop(out_drop), .af3_full(af3_full),
        .out_seq(out_seq), .out_n(out_n), .out_phi(out_phi), .out_rate(out_rate),
        .out_df18(out_df18), .out_h(out_h), .out_frame(out_frame), .idle(idle), .f_state(f_state));

`ifdef FORMAL
    reg init = 1'b1;
    always @(posedge clk) init <= 1'b0;
    always @* if (init) assume (!rst_n);

    // rate changes only with clear or reset (R17)
    reg prev_rate;
    always @(posedge clk) prev_rate <= rate;
    always @* if (!init && rst_n && !clear) assume (rate == prev_rate);

    // T of the record on the outputs, from its own fields (R14)
    wire [67:0] t_rec = (out_rate ? 68'd5 : 68'd6) * {4'd0, out_n} + {65'd0, out_phi};

    // ---- P6 -------------------------------------------------------------
    reg        have_s = 1'b0;
    reg [67:0] tprev_s;
    always @(posedge clk) begin
        if (!rst_n || clear) have_s <= 1'b0;
        else if (out_rec) begin have_s <= 1'b1; tprev_s <= t_rec; end
    end
    always @* if (!init && out_rec && have_s) assert (t_rec >= tprev_s + 68'd1440);   // P6, R28

    // ---- record rate flag follows the segment's rate --------------------
    // (not in a clear or reset cycle: a record of the old segment may still be on the outputs)
    always @* if (!init && out_rec && rst_n && !clear) assert (out_rate == rate);

    // ---- sequence numbers -------------------------------------------------
    reg [31:0] cnt_s = 32'd0;
    always @(posedge clk)
        if (!rst_n) cnt_s <= 32'd0;
        else if (out_rec) cnt_s <= cnt_s + 32'd1;
    always @* if (!init && out_rec) assert (out_seq == cnt_s);                       // record format, R34

    // ---- invariants tying the RTL state to the shadows (for induction) -----
    always @* if (!init) begin
        if (out_rec) begin
            assert (f_have && f_lim == t_rec + 68'd1440 && f_seq == cnt_s + 32'd1);
        end else begin
            assert (f_have == have_s && f_seq == cnt_s);
            if (f_have) assert (f_lim == tprev_s + 68'd1440);
        end
    end

    // ---- R34 push / drop ----------------------------------------------
    always @* if (!init) begin
        assert (out_push == (out_rec && !af3_full));
        assert (out_drop == (out_rec && af3_full));
    end

    // ---- control: latency, cand_inc, idle -------------------------------
    reg [2:0] sv;                                 // candidate entered 1, 2, 3 cycles ago
    reg       sc1;                                // ... and it was a candidate
    always @(posedge clk) begin
        if (!rst_n) begin sv <= 3'b000; sc1 <= 1'b0; end
        else begin
            sv  <= {sv[1] & ~clear, sv[0] & ~clear, in_valid & ~clear};
            sc1 <= in_valid & ~clear & in_cand;
        end
    end
    always @* if (!init) begin
        if (out_rec) assert (sv[2]);              // only 3 cycles after a candidate
        assert (cand_inc == sc1);                 // R25: ADSB_CAND pulse
        assert (idle == !(sv[0] || sv[1] || out_rec));
    end

    // Covers: two records in one segment, a record after a clear, a dropped record.
    reg [1:0] nrec = 2'd0;
    always @(posedge clk) if (out_rec && nrec != 2'd3) nrec <= nrec + 2'd1;
    always @(posedge clk) cover (!init && nrec == 2'd2 && have_s);
    reg cleared = 1'b0;
    always @(posedge clk) if (rst_n && clear) cleared <= 1'b1;
    always @(posedge clk) cover (!init && out_rec && cleared);
    always @(posedge clk) cover (!init && out_drop);
`endif
endmodule

`default_nettype wire
