// chip_energy_props.v: formal properties for chip_energy (R20, R32, R33).
`default_nettype none

module chip_energy_props (
    input wire       clk,
    input wire       rst_n,
    input wire       clear,
    input wire       rate,
    input wire       in_valid,
    input wire [8:0] in_m
);
    wire        in_ready, out_valid, idle;
    wire [11:0] out_e;

    chip_energy dut (.clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate),
        .in_valid(in_valid), .in_m(in_m), .in_ready(in_ready),
        .out_valid(out_valid), .out_e(out_e), .idle(idle), .f_state(f_state));
    wire [21:0] f_state;
    wire        f_more = f_state[21];
    wire [2:0]  f_i    = f_state[20:18];
    wire [8:0]  f_mp   = f_state[17:9];
    wire [8:0]  f_mc   = f_state[8:0];

    // Independent model of R33 for one tick: chip start tick t = S*k - 5 + i,
    // written as an interval overlap with sample k-1 = [S*k - S, S*k).
    function [11:0] model(input [2:0] S, input [2:0] i, input [8:0] mprev, input [8:0] mcur);
        integer t0, ov;
        begin
            t0 = i - 5;                 // chip start, relative to the start of sample k
            ov = (t0 < 0) ? -t0 : 0;    // ticks of [t0, t0+6) before 0, i.e. in sample k-1
            if (ov > 6) ov = 6;
            model = ov * mprev + (6 - ov) * mcur;
        end
    endfunction

`ifdef FORMAL
    reg [7:0] t = 8'd0;
    always @(posedge clk) if (t != 8'hFF) t <= t + 8'd1;
    always @* if (t == 0) assume (!rst_n);

    // Shadow: which tick of which batch must be on the outputs.
    reg       act = 1'b0;
    reg [2:0] k;
    reg [8:0] sp, sm;
    reg       srate;
    // sm/sp become known after one/two accepts; before that the DUT's own
    // registers hold unconstrained power-up values (data registers are unreset).
    reg       sm_ok = 1'b0, sp_ok = 1'b0;
    wire [2:0] S = srate ? 3'd5 : 3'd6;            // R32

    // Preconditions
    always @* if (in_valid) assume (in_ready || !rst_n || clear);
    always @(posedge clk) if (t > 0 && act) assume (rate == srate);

    always @(posedge clk) begin
        if (!rst_n || clear) act <= 1'b0;
        else if (in_valid) begin
            act <= 1'b1; k <= 3'd0; sp <= sm; sm <= in_m; srate <= rate;
            sp_ok <= sm_ok; sm_ok <= 1'b1;
        end else if (act) begin
            if (k == S - 1) act <= 1'b0;
            else k <= k + 3'd1;
        end
    end

    always @(posedge clk) if (t > 1) begin
        assert (out_valid == act);                          // S ticks per sample (R33, R20)
        assert (in_ready == (rst_n && !clear && !(act && k != S - 1)));  // R20: one sample per S cycles
        assert (idle == !act);
        if (act) assert (k <= S - 1);
        if (act && sp_ok) assert (out_e == model(S, k, sp, sm));          // R33
        if (act && k == 3'd5) assert (out_e == model(S, k, 9'd0, sm));   // R33: tick in sample k only
        if (act && sp_ok && sp <= 9'd382 && sm <= 9'd382)
            assert (out_e <= 12'd2292);                                   // R33: 12 bits suffice
    end

    // White-box invariants tying the shadow to the DUT's state (needed for induction).
    always @(posedge clk) if (t > 1) begin
        if (sm_ok) assert (f_mc == sm);
        if (sp_ok) assert (f_mp == sp);
        if (act)   assert (f_i == k);
        assert (f_more == (act && k != S - 1));
        assert (sp_ok <= sm_ok);
    end

    always @(posedge clk) cover (t > 1 && act && srate == 0 && k == 3'd5);
    always @(posedge clk) cover (t > 1 && act && srate == 1 && k == 3'd4 && in_valid);
`endif
endmodule

`default_nettype wire
