// iq_mag_props.v: formal properties for iq_mag (R19, R22), proved for all inputs.
`default_nettype none

module iq_mag_props (
    input wire       clk,
    input wire       rst_n,
    input wire       in_valid,
    input wire [7:0] in_i,
    input wire [7:0] in_q
);
    localparam integer LAT = 2;
    wire       out_valid;
    wire [8:0] out_m;

    iq_mag dut (.clk(clk), .rst_n(rst_n), .in_valid(in_valid), .in_i(in_i), .in_q(in_q),
                .out_valid(out_valid), .out_m(out_m));

    // Independent model: signed arithmetic straight from the spec text, not the
    // RTL's bit trick. R19: x = 2u - 255; R22: max + (min >> 1).
    function [8:0] model(input [7:0] ui, input [7:0] uq);
        reg signed [9:0] xi, xq;
        reg        [8:0] ai, aq, mx, mn;
        begin
            xi = $signed({2'b00, ui}) * 2 - 10'sd255;
            xq = $signed({2'b00, uq}) * 2 - 10'sd255;
            ai = xi < 0 ? -xi : xi;
            aq = xq < 0 ? -xq : xq;
            if (ai > aq) begin mx = ai; mn = aq; end
            else         begin mx = aq; mn = ai; end
            model = mx + mn / 2;
        end
    endfunction

`ifdef FORMAL
    reg [7:0] t = 8'd0;
    always @(posedge clk) if (t != 8'hFF) t <= t + 8'd1;
    always @* if (t == 0) assume (!rst_n);

    reg [LAT-1:0] sv;
    reg [17:0]    sd;
    always @(posedge clk) begin
        sv <= rst_n ? {sv[0], in_valid} : 2'b00;
        sd <= {sd[8:0], model(in_i, in_q)};
    end

    always @(posedge clk) if (t > LAT) begin
        assert (out_valid == sv[1]);                    // latency 2 + reset (microarch 2a)
        if (out_valid) assert (out_m == sd[17:9]);      // R19, R22: function
        if (out_valid) assert (out_m >= 1 && out_m <= 382);  // R22 range, 9 bits
    end

    always @(posedge clk) cover (t > LAT && out_valid && out_m == 9'd382);
    always @(posedge clk) cover (t > LAT && out_valid && out_m == 9'd1);
`endif
endmodule

`default_nettype wire
