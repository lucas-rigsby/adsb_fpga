// cand_eval_props.v: formal properties for cand_eval (R24, R25, R26, R27).
//
// Task data (BMC): the data outputs. cand_eval is feed-forward: the outputs at
// step s + 5 depend only on the input at step s and on K = pre_k, which is held
// constant here (its precondition: it only changes while the pipeline is idle,
// R17). So one check, at an exact step, of an unconstrained input and K proves
// the outputs for every input. The assertions are combinational (always @*) and
// gated by a step counter, so each is evaluated in the step it reads. A clocked
// assertion is evaluated one step later and could fall past the end of the run
// without checking anything. A deliberately broken RTL makes this task fail
// (checked when the properties were written).
//
// The multiplier. Proving the RTL's K * L equal to a separately built product is
// a known hard case for SAT (the check stalls), so the proof is split around the
// multiply: the operands entering it are proved to be K and the model's L, and
// out_cand is proved to be shape AND quiet AND (6H >= the registered product).
// The '*' itself is Verilog's primitive, mapped by synthesis to the vendor
// MULT18X18D; it is not re-proved here, and the cocotb tests exercise it over
// thousands of K and L values. A formal-only port (f_mul) exposes the operand and
// product registers.
//
// The model below is written differently from the RTL: shape compares as signed
// differences, H and L as loops over chip lists, comparisons in 32-bit integers.
//
// Task prove (PDR): the control behavior for all time: out_valid is in_valid
// delayed 5 cycles, cleared by reset, and idle is high exactly when nothing is in
// flight.
`default_nettype none

module cand_eval_props (
    input wire          clk,
    input wire          rst_n,
    input wire [7:0]    pre_k,
    input wire          in_valid,
    input wire [2879:0] in_taps,
    input wire [2:0]    in_phi,
    input wire [8:0]    in_p
);
    wire         out_valid, out_cand, out_df_ok, idle;
    wire [111:0] out_bits;
    wire [13:0]  out_h;
    wire [2:0]   out_phi;
    wire [8:0]   out_p;
    wire [43:0]  f_mul;
    wire [21:0]  f_kl = f_mul[43:22];
    wire [7:0]   f_k  = f_mul[21:14];
    wire [13:0]  f_l  = f_mul[13:0];

    cand_eval dut (.clk(clk), .rst_n(rst_n), .pre_k(pre_k), .in_valid(in_valid),
        .in_taps(in_taps), .in_phi(in_phi), .in_p(in_p), .out_valid(out_valid),
        .out_cand(out_cand), .out_df_ok(out_df_ok), .out_bits(out_bits), .out_h(out_h),
        .out_phi(out_phi), .out_p(out_p), .idle(idle), .f_mul(f_mul));

    // ---- independent model --------------------------------------------------
    function [11:0] ch(input [2879:0] t, input integer j);
        ch = t[12*j +: 12];
    endfunction

    function gt(input [11:0] a, input [11:0] b);        // a > b  <=>  b - a < 0
        reg signed [12:0] d;
        begin
            d = $signed({1'b0, b}) - $signed({1'b0, a});
            gt = d < 0;
        end
    endfunction

    function m_shape(input [2879:0] t);                  // R24
        m_shape = gt(ch(t,0), ch(t,1)) && gt(ch(t,2), ch(t,1)) && gt(ch(t,2), ch(t,3)) &&
                  gt(ch(t,0), ch(t,3)) && gt(ch(t,0), ch(t,4)) && gt(ch(t,0), ch(t,5)) &&
                  gt(ch(t,0), ch(t,6)) && gt(ch(t,7), ch(t,8)) && gt(ch(t,9), ch(t,8)) &&
                  gt(ch(t,9), ch(t,6));
    endfunction

    function [31:0] m_h(input [2879:0] t);               // R25: pulse chips 0, 2, 7, 9
        integer n;
        reg [31:0] s;
        reg [31:0] idx;
        begin
            s = 0; idx = {8'd9, 8'd7, 8'd2, 8'd0};
            for (n = 0; n < 4; n = n + 1) s = s + ch(t, idx[8*n +: 8]);
            m_h = s;
        end
    endfunction

    function [31:0] m_l(input [2879:0] t);               // R25: gap chips 1, 3, 4, 5, 6, 8
        integer n;
        reg [31:0] s;
        reg [47:0] idx;
        begin
            s = 0; idx = {8'd8, 8'd6, 8'd5, 8'd4, 8'd3, 8'd1};
            for (n = 0; n < 6; n = n + 1) s = s + ch(t, idx[8*n +: 8]);
            m_l = s;
        end
    endfunction

    function m_quiet(input [2879:0] t);                  // R25: 4 c_k < H, k = 11..14
        reg [31:0] h;
        integer q;
        reg ok;
        begin
            h = m_h(t); ok = 1'b1;
            for (q = 11; q <= 14; q = q + 1) ok = ok && (4 * ch(t, q) < h);
            m_quiet = ok;
        end
    endfunction

    function [111:0] m_bits(input [2879:0] t);           // R26
        integer b;
        begin
            for (b = 0; b < 112; b = b + 1) m_bits[111 - b] = gt(ch(t, 16 + 2*b), ch(t, 17 + 2*b));
        end
    endfunction

`ifdef FORMAL
    reg init = 1'b1;
    always @(posedge clk) init <= 1'b0;

`ifdef DATA
    // K constant over the run (R17 precondition); one input captured at step 1.
    reg [7:0] k0;
    always @(posedge clk) k0 <= pre_k;
    always @* if (!init) assume (pre_k == k0);

    reg [2:0] step = 3'd0;
    always @(posedge clk) if (step != 3'd7) step <= step + 3'd1;

    // Input domain: every tap is a chip energy, c_j <= 6 x 382 = 2292 (R33; proved
    // for chip_energy's output). The RTL's widths rely on it (L <= 13752 in 14 b).
    integer a;
    always @* for (a = 0; a < 240; a = a + 1) assume (ch(in_taps, a) <= 12'd2292);

    reg [2879:0] cap;
    reg [2:0]    cap_phi;
    reg [8:0]    cap_p;
    always @(posedge clk) if (step == 3'd1) begin
        cap <= in_taps; cap_phi <= in_phi; cap_p <= in_p;
    end

    // input sampled at the edge after step 1: E1 registers at step 4, E2 at step 5,
    // outputs at step 6
    reg [21:0] kl5;
    always @(posedge clk) if (step == 3'd5) kl5 <= f_kl;

    always @* if (step == 3'd4) begin
        assert (f_k == pre_k);                                           // R25 K operand
        assert ({18'd0, f_l} == m_l(cap));                               // R25 L operand
    end

    wire [111:0] eb = m_bits(cap);
    always @* if (step == 3'd6) begin
        assert (out_cand == (m_shape(cap) && m_quiet(cap) &&
                             6 * m_h(cap) >= {10'd0, kl5}));             // R24, R25
        assert (out_bits == eb);                                         // R26
        assert (out_df_ok == (eb[111:107] == 5'd17 || eb[111:107] == 5'd18));   // R27
        assert ({18'd0, out_h} == m_h(cap));                             // R25 H
        assert (out_phi == cap_phi && out_p == cap_p);
    end
`else
    always @* if (init) assume (!rst_n);

    reg [4:0] sv;
    always @(posedge clk) sv <= rst_n ? {sv[3:0], in_valid} : 5'd0;

    always @(posedge clk) if (!init) begin
        assert (out_valid == sv[4]);                // latency 5 + reset (microarch 2a)
        assert (idle == (sv == 5'd0));              // WAIT_DRAIN (R17)
    end

    // Covers: a candidate that passes with a good DF, and one that fails.
    always @(posedge clk) cover (!init && out_valid && out_cand && out_df_ok);
    always @(posedge clk) cover (!init && out_valid && !out_cand);
`endif
`endif
endmodule

`default_nettype wire
