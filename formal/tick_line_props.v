// tick_line_props.v: formal properties for tick_line on a small line (microarch risk 7):
// CHIPS = 4 -> LINE = 19, W = 4, PW = 3. Same RTL, only the parameters differ.
`default_nettype none

module tick_line_props (
    input wire       clk,
    input wire       rst_n,
    input wire       clear,
    input wire       rate,
    input wire       in_valid,
    input wire [3:0] in_e
);
    localparam integer W = 4, CHIPS = 4, PW = 3;
    localparam integer LINE = 6 * (CHIPS - 1) + 1;   // 19
    localparam integer THR  = LINE + 5;              // shifts before T = 0 is complete

    wire               out_valid;
    wire [2:0]         out_phi;
    wire [PW-1:0]      out_p;
    wire [W*CHIPS-1:0] out_taps;

    tick_line #(.W(W), .CHIPS(CHIPS), .PW(PW)) dut (
        .clk(clk), .rst_n(rst_n), .clear(clear), .rate(rate), .in_valid(in_valid), .in_e(in_e),
        .out_valid(out_valid), .out_phi(out_phi), .out_p(out_p), .out_taps(out_taps));

`ifdef FORMAL
    reg [7:0] t = 8'd0;
    always @(posedge clk) if (t != 8'hFF) t <= t + 8'd1;
    always @* if (t == 0) assume (!rst_n);

    // rate changes only together with clear or reset (R17)
    reg prev_rate;
    always @(posedge clk) prev_rate <= rate;
    always @* if (t > 0 && rst_n && !clear) assume (rate == prev_rate);

    // Shadow: shifts accepted in this segment. The head after shift c is T = c - THR.
    wire       shift = in_valid && rst_n && !clear;
    reg  [6:0] c = 7'd0;
    always @* if (c >= 7'd120) assume (!in_valid);        // segments up to 120 ticks (p wraps at 8)
    always @(posedge clk)
        if (!rst_n || clear) c <= 7'd0;
        else if (shift)      c <= c + 7'd1;

    // Expected head after this cycle's shift, latched with the rate in force at the shift.
    wire [2:0] S    = rate ? 3'd5 : 3'd6;
    wire [6:0] Tn   = c + 7'd1 - THR;
    reg  [6:0] Tp;
    reg  [2:0] Tphi;
    reg        srate;
    always @(posedge clk) if (shift) begin
        srate <= rate;
        Tp   <= Tn / S;
        Tphi <= Tn % S;
    end

    // Expected strobe: one cycle after a shift that completed a candidate of the phase set.
    reg sv = 1'b0;
    always @(posedge clk)
        sv <= rst_n && !clear && shift && (c + 7'd1 >= THR) && (rate || ((Tn % 6) == 0));

    always @(posedge clk) if (t > 1) begin
        assert (out_valid == sv);                         // R23, R32: every candidate once
        if (out_valid) assert (out_phi == Tphi);          // T mod S
        if (out_valid) assert (out_p == Tp[PW-1:0]);      // R14: T div S, mod 2^PW
        if (out_valid && !srate) assert (out_phi == 3'd0); // R32: 2.0 MS/s phase 0 only
    end

    // Data: an arbitrary tick (ordinal f_n in its segment) must sit at tap j exactly when
    // it is chip j of the head candidate, i.e. at entry 6j from the oldest (R33).
    (* anyconst *) reg [6:0] f_n;
    reg       f_have = 1'b0;
    reg [3:0] f_val;
    always @(posedge clk)
        if (!rst_n || clear) f_have <= 1'b0;
        else if (shift && c == f_n) begin f_have <= 1'b1; f_val <= in_e; end

    wire [6:0] age = c - 7'd1 - f_n;                     // shifts after it entered
    wire [6:0] idx = LINE - 1 - age;                      // entry from the oldest
    integer j;
    always @(posedge clk) if (t > 1 && f_have && age <= LINE - 1)
        for (j = 0; j < CHIPS; j = j + 1)
            if (idx == 6 * j) assert (out_taps[W*j +: W] == f_val);

    // invariant help: f_have implies it was captured in this segment
    always @(posedge clk) if (t > 1 && f_have) assert (c > f_n);

    always @(posedge clk) cover (t > 1 && out_valid && out_p == 3'd7 && out_phi == 3'd4);
    always @(posedge clk) cover (t > 1 && out_valid && !rate && out_p == 3'd2);
    always @(posedge clk) cover (t > 1 && f_have && age == LINE - 1 && out_valid);
`endif
endmodule

`default_nettype wire
