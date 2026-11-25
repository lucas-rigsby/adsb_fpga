// dsp_core.v: the ADS-B DSP chain (docs/microarch.md 2a), from rtl_sdr I/Q bytes
// to ADS-B records, in the core clock domain.
//
//   iq_mag -> chip_energy -> tick_line -> cand_eval -> crc24_112 -> adsb_emit
//                                                       index ring -^
//
// Sample input (from stream_demux): valid/ready. After accepting a sample the core
// holds in_ready low for S - 1 cycles (S = 5 at 2.4 MS/s, 6 at 2.0 MS/s), so each
// sample reaches chip_energy, through iq_mag's fixed 2-cycle pipeline, exactly when
// chip_energy can take it; in_ready is never low for more than S - 1 <= 5 cycles
// outside reset and clear (P4, R20). in_index is the stream sample index of the
// sample, derived from the I/Q packet header (R14).
//
// Index ring: 512 x 64 b, written when a sample is accepted, at its position in the
// segment mod 512; read at cand_eval's out_p, so the index arrives at adsb_emit one
// cycle later, aligned with the CRC remainder. A candidate's first sample is at most
// span + a few samples (about 300) behind the newest, so it is never overwritten
// before it is read.
//
// clear (R17) starts a new segment: it resets the position counter and the state
// of every stage. Preconditions: rate and pre_k change only together with clear,
// and clear is raised only while idle (WAIT_DRAIN).
`default_nettype none

module dsp_core (
    input  wire          clk,
    input  wire          rst_n,      // synchronous, active low
    input  wire          clear,      // R17 dsp_clear
    input  wire          rate,       // ADSB_RATE (R32)
    input  wire [7:0]    pre_k,      // PRE_K (R25)
    // samples
    input  wire          in_valid,
    output wire          in_ready,
    input  wire [7:0]    in_i,
    input  wire [7:0]    in_q,
    input  wire [63:0]   in_index,
    // records (to AF3) and counters
    input  wire          af3_full,
    output wire          out_rec,
    output wire          out_push,
    output wire          out_drop,
    output wire [31:0]   out_seq,
    output wire [63:0]   out_n,
    output wire [2:0]    out_phi,
    output wire          out_rate,
    output wire          out_df18,
    output wire [15:0]   out_h,
    output wire [111:0]  out_frame,
    output wire          cand_inc,   // ADSB_CAND
    output wire          idle        // nothing in flight anywhere (WAIT_DRAIN)
);
    // ---- sample pacing (P4) -------------------------------------------------
    reg  [2:0] wait_q;                          // cycles until the next sample may enter
    wire       accept = in_valid & (wait_q == 3'd0) & rst_n & ~clear;
    always @(posedge clk)
        if (!rst_n || clear)  wait_q <= 3'd0;
        else if (accept)      wait_q <= rate ? 3'd4 : 3'd5;     // S - 1
        else if (wait_q != 0) wait_q <= wait_q - 3'd1;
    assign in_ready = (wait_q == 3'd0) & rst_n & ~clear;

    // ---- index ring ---------------------------------------------------------
    reg [8:0]  pos_q;                           // segment sample position, mod 512
    always @(posedge clk)
        if (!rst_n || clear) pos_q <= 9'd0;
        else if (accept)     pos_q <= pos_q + 9'd1;

    reg [63:0] ring [0:511];
    reg [63:0] ring_rd;
    wire [8:0] ce_p;
    always @(posedge clk) begin
        if (accept) ring[pos_q] <= in_index;
        ring_rd <= ring[ce_p];
    end

    // ---- A: magnitude ---------------------------------------------------------
    wire       m_valid;
    wire [8:0] m;
    reg  [1:0] a_v;                             // iq_mag pipeline occupancy (for idle)
    always @(posedge clk) a_v <= (!rst_n || clear) ? 2'b00 : {a_v[0], accept};

    iq_mag u_mag (.clk(clk), .rst_n(rst_n & ~clear), .in_valid(accept), .in_i(in_i),
                  .in_q(in_q), .out_valid(m_valid), .out_m(m));

    // ---- B: chip energy ---------------------------------------------------------
    wire        e_valid, e_ready_unused, ce_idle;
    wire [11:0] e;
    chip_energy u_ce (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate),
                      .in_valid(m_valid), .in_m(m), .in_ready(e_ready_unused),
                      .out_valid(e_valid), .out_e(e), .idle(ce_idle));

    // ---- C: tick delay line -------------------------------------------------------
    wire          t_valid;
    wire [2:0]    t_phi;
    wire [8:0]    t_p;
    wire [2879:0] taps;
    tick_line u_tl (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate),
                    .in_valid(e_valid), .in_e(e), .out_valid(t_valid), .out_phi(t_phi),
                    .out_p(t_p), .out_taps(taps));

    // ---- D1-F: candidate evaluation ---------------------------------------------
    wire         c_valid, c_cand, c_df_ok, c_idle;
    wire [111:0] c_bits;
    wire [13:0]  c_h;
    wire [2:0]   c_phi;
    cand_eval u_ev (.clk(clk), .rst_n(rst_n & ~clear), .pre_k(pre_k), .in_valid(t_valid),
                    .in_taps(taps), .in_phi(t_phi), .in_p(t_p), .out_valid(c_valid),
                    .out_cand(c_cand), .out_df_ok(c_df_ok), .out_bits(c_bits), .out_h(c_h),
                    .out_phi(c_phi), .out_p(ce_p), .idle(c_idle));

    // ---- F2: CRC (runs alongside G1) ----------------------------------------------
    wire        r_valid_unused;
    wire [23:0] rem;
    crc24_112 u_crc (.clk(clk), .rst_n(rst_n), .in_valid(c_valid), .in_bits(c_bits),
                     .out_valid(r_valid_unused), .out_rem(rem));

    // ---- G: records -----------------------------------------------------------------
    wire g_idle;
    adsb_emit u_em (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate),
                    .in_valid(c_valid), .in_cand(c_cand), .in_df_ok(c_df_ok),
                    .in_bits(c_bits), .in_h(c_h), .in_phi(c_phi), .in_rem(rem),
                    .in_n(ring_rd), .cand_inc(cand_inc), .out_rec(out_rec),
                    .out_push(out_push), .out_drop(out_drop), .af3_full(af3_full),
                    .out_seq(out_seq), .out_n(out_n), .out_phi(out_phi),
                    .out_rate(out_rate), .out_df18(out_df18), .out_h(out_h),
                    .out_frame(out_frame), .idle(g_idle));

    assign idle = ~|a_v & ce_idle & ~t_valid & c_idle & g_idle;
endmodule

`default_nettype wire
