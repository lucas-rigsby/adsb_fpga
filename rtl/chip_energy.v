// chip_energy.v: stage B of the ADS-B DSP (docs/microarch.md 2a).
// Turns each magnitude sample into S tick energies e(t), one per cycle, for the
// tick delay line. S = 5 at 2.4 MS/s (rate = 1), 6 at 2.0 MS/s (rate = 0) (R32).
//
// Insertion rule (microarch 2a): when sample k arrives, ticks t = S*k - 5 + i,
// i = 0..S-1, are emitted. Tick t is the start of a 6-tick chip, so its energy
// is the overlap-weighted sum of the samples it covers (R33):
//   i <= 4: the chip covers 5 - i ticks of sample k-1 and 1 + i ticks of sample k
//   i == 5 (2.0 MS/s only): the chip lies wholly in sample k, i.e. 0 and 6
// so for both rates  e = (5 - i)*m[k-1] + (1 + i)*m[k],  0 <= e <= 6*382 = 2292.
//
// Timing: a sample accepted on cycle c yields tick i on out_e after edge c+i
// (first tick 1 cycle after in_valid). in_ready is low while ticks remain, so
// samples are accepted at most one per S cycles (R20).
// Preconditions (checked by formal assumptions and the test): in_valid only
// while in_ready; rate changes only while idle.
`default_nettype none

module chip_energy (
    input  wire        clk,
    input  wire        rst_n,       // synchronous, active low
    input  wire        clear,       // R17 dsp_clear: abandon the batch in progress
    input  wire        rate,        // ADSB_RATE: 1 = 2.4 MS/s (S = 5), 0 = 2.0 MS/s (S = 6)
    input  wire        in_valid,
    input  wire [8:0]  in_m,        // R22 magnitude
    output wire        in_ready,
    output wire        out_valid,   // shift strobe for tick_line
    output wire [11:0] out_e,       // R33 tick energy
    output wire        idle         // nothing in flight (for WAIT_DRAIN)
`ifdef FORMAL
  , output wire [21:0] f_state      // {more, i, m[k-1], m[k]} for the induction invariants
`endif
);
    // w * m for w in 0..7 by shift-add: no multiplier, and a zero weight gives
    // a clean 0 even if m is still unknown after reset.
    function [11:0] wmul(input [2:0] w, input [8:0] m);
        wmul = (w[0] ? {3'b0, m}       : 12'd0)
             + (w[1] ? {2'b0, m, 1'b0} : 12'd0)
             + (w[2] ? {1'b0, m, 2'b0} : 12'd0);
    endfunction

    wire [2:0] last = rate ? 3'd4 : 3'd5;   // S - 1

    reg  [8:0] mp_q, mc_q;     // m[k-1], m[k] of the current batch (data: no reset)
    reg  [2:0] i_q;            // index of the tick on out_e
    reg        more_q;         // ticks of the current batch still to come
    reg        v_q;
    reg [11:0] e_q;

    // A sample offered during reset or clear is ignored (it must not become m[k-1]).
    wire       accept = in_valid & ~more_q & rst_n & ~clear;
    wire [8:0] mp_n   = accept ? mc_q : mp_q;
    wire [8:0] mc_n   = accept ? in_m : mc_q;
    wire [2:0] i_n    = accept ? 3'd0 : i_q + 3'd1;

    always @(posedge clk) begin
        mp_q <= mp_n;
        mc_q <= mc_n;
        i_q  <= i_n;
        e_q  <= wmul(3'd5 - i_n, mp_n) + wmul(3'd1 + i_n, mc_n);   // R33
    end

    always @(posedge clk) begin
        if (!rst_n || clear) begin
            more_q <= 1'b0;
            v_q    <= 1'b0;
        end else begin
            v_q    <= accept | more_q;
            more_q <= (accept | more_q) & (i_n != last);
        end
    end

    assign in_ready  = ~more_q & rst_n & ~clear;
    assign out_valid = v_q;
    assign out_e     = e_q;
    assign idle      = ~more_q & ~v_q;
`ifdef FORMAL
    assign f_state   = {more_q, i_q, mp_q, mc_q};
`endif
endmodule

`default_nettype wire
