// dsp_core_props.v: P4 for the whole DSP chain.
//   P4  The DSP's sample-input ready signal is never low for more than 7
//       consecutive clk_core cycles outside reset (R20). Here: never more than
//       S - 1 = 5, and not at all while no sample has been accepted.
// Outside reset and clear (both hold in_ready low by design: clear lasts one cycle
// and only happens while idle).
// The whole design is read, but only the pacing counter is in the cone of
// influence of the property, so PDR proves it quickly.
`default_nettype none

module dsp_core_props (
    input wire        clk,
    input wire        rst_n,
    input wire        clear,
    input wire        rate,
    input wire [7:0]  pre_k,
    input wire        in_valid,
    input wire [7:0]  in_i,
    input wire [7:0]  in_q,
    input wire [63:0] in_index,
    input wire        af3_full
);
    wire in_ready;
    dsp_core dut (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate), .pre_k(pre_k),
        .in_valid(in_valid), .in_ready(in_ready), .in_i(in_i), .in_q(in_q),
        .in_index(in_index), .af3_full(af3_full), .out_rec(), .out_push(), .out_drop(),
        .out_seq(), .out_n(), .out_phi(), .out_rate(), .out_df18(), .out_h(),
        .out_frame(), .cand_inc(), .idle());

`ifdef FORMAL
    reg init = 1'b1;
    always @(posedge clk) init <= 1'b0;
    always @* if (init) assume (!rst_n);

    // consecutive cycles with in_ready low, outside reset and clear
    reg [3:0] low = 4'd0;
    always @(posedge clk)
        if (!rst_n || clear || in_ready) low <= 4'd0;
        else if (low != 4'd15) low <= low + 4'd1;

    always @* if (!init) assert (low <= 4'd5);                   // P4 (limit 7), R20

    // after an idle cycle with nothing accepted, ready stays high until a sample is taken
    always @(posedge clk) if (!init && rst_n && !clear && $past(rst_n) && !$past(clear) &&
                              $past(in_ready) && !$past(in_valid)) assert (in_ready);

    always @(posedge clk) cover (!init && low == 4'd5);           // the 2.0 MS/s worst case
`endif
endmodule

`default_nettype wire
