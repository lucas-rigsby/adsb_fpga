// crc24_112.v: stage E1 CRC of the ADS-B DSP (docs/microarch.md 2a).
// Remainder of the 112 decided bits modulo the Mode S generator G(x) = 0x1FFF409
// (R27), all bits in parallel, LATENCY = 1 cycle. A frame passes when the
// remainder is zero; stage F makes that comparison.
//
// Bit order: in_bits[111] is data bit 0, the first bit on air (the MSB of the
// frame as a 112-bit number, as in tb/adsb_ref.py), so in_bits[i] is the
// coefficient of x^i.
//
// Method: CRC is linear over GF(2), so  v mod G = XOR of (x^i mod G) over set bits i.
// The 112 columns x^i mod G are constants (generated table below), so the whole
// remainder is a fixed XOR network, about 3 LUT levels per output bit. P1 proves
// it equal to the bit-serial definition for every input.
`default_nettype none

module crc24_112 (
    input  wire         clk,
    input  wire         rst_n,       // synchronous, active low; clears valid only
    input  wire         in_valid,
    input  wire [111:0] in_bits,     // in_bits[111] = first bit on air
    output wire         out_valid,   // in_valid delayed by 1 cycle
    output wire [23:0]  out_rem      // in_bits(x) mod G(x)
);
    // BEGIN GENERATED TABLE (scripts/gen_crc24_table.py): COL[i] = x^i mod G(x)
    localparam [24*112-1:0] COL = {
        24'h3935EA,   // x^111
        24'h1C9AF5,   // x^110
        24'hF1B77E,   // x^109
        24'h78DBBF,   // x^108
        24'hC397DB,   // x^107
        24'h9E31E9,   // x^106
        24'hB0E2F0,   // x^105
        24'h587178,   // x^104
        24'h2C38BC,   // x^103
        24'h161C5E,   // x^102
        24'h0B0E2F,   // x^101
        24'hFA7D13,   // x^100
        24'h82C48D,   // x^99
        24'hBE9842,   // x^98
        24'h5F4C21,   // x^97
        24'hD05C14,   // x^96
        24'h682E0A,   // x^95
        24'h341705,   // x^94
        24'hE5F186,   // x^93
        24'h72F8C3,   // x^92
        24'hC68665,   // x^91
        24'h9CB936,   // x^90
        24'h4E5C9B,   // x^89
        24'hD8D449,   // x^88
        24'h939020,   // x^87
        24'h49C810,   // x^86
        24'h24E408,   // x^85
        24'h127204,   // x^84
        24'h093902,   // x^83
        24'h049C81,   // x^82
        24'hFDB444,   // x^81
        24'h7EDA22,   // x^80
        24'h3F6D11,   // x^79
        24'hE04C8C,   // x^78
        24'h702646,   // x^77
        24'h381323,   // x^76
        24'hE3F395,   // x^75
        24'h8E03CE,   // x^74
        24'h4701E7,   // x^73
        24'hDC7AF7,   // x^72
        24'h91C77F,   // x^71
        24'hB719BB,   // x^70
        24'hA476D9,   // x^69
        24'hADC168,   // x^68
        24'h56E0B4,   // x^67
        24'h2B705A,   // x^66
        24'h15B82D,   // x^65
        24'hF52612,   // x^64
        24'h7A9309,   // x^63
        24'hC2B380,   // x^62
        24'h6159C0,   // x^61
        24'h30ACE0,   // x^60
        24'h185670,   // x^59
        24'h0C2B38,   // x^58
        24'h06159C,   // x^57
        24'h030ACE,   // x^56
        24'h018567,   // x^55
        24'hFF38B7,   // x^54
        24'h80665F,   // x^53
        24'hBFC92B,   // x^52
        24'hA01E91,   // x^51
        24'hAFF54C,   // x^50
        24'h57FAA6,   // x^49
        24'h2BFD53,   // x^48
        24'hEA04AD,   // x^47
        24'h8AF852,   // x^46
        24'h457C29,   // x^45
        24'hDD4410,   // x^44
        24'h6EA208,   // x^43
        24'h375104,   // x^42
        24'h1BA882,   // x^41
        24'h0DD441,   // x^40
        24'hF91024,   // x^39
        24'h7C8812,   // x^38
        24'h3E4409,   // x^37
        24'hE0D800,   // x^36
        24'h706C00,   // x^35
        24'h383600,   // x^34
        24'h1C1B00,   // x^33
        24'h0E0D80,   // x^32
        24'h0706C0,   // x^31
        24'h038360,   // x^30
        24'h01C1B0,   // x^29
        24'h00E0D8,   // x^28
        24'h00706C,   // x^27
        24'h003836,   // x^26
        24'h001C1B,   // x^25
        24'hFFF409,   // x^24
        24'h800000,   // x^23
        24'h400000,   // x^22
        24'h200000,   // x^21
        24'h100000,   // x^20
        24'h080000,   // x^19
        24'h040000,   // x^18
        24'h020000,   // x^17
        24'h010000,   // x^16
        24'h008000,   // x^15
        24'h004000,   // x^14
        24'h002000,   // x^13
        24'h001000,   // x^12
        24'h000800,   // x^11
        24'h000400,   // x^10
        24'h000200,   // x^9
        24'h000100,   // x^8
        24'h000080,   // x^7
        24'h000040,   // x^6
        24'h000020,   // x^5
        24'h000010,   // x^4
        24'h000008,   // x^3
        24'h000004,   // x^2
        24'h000002,   // x^1
        24'h000001   // x^0
    };
    // END GENERATED TABLE

    reg [23:0] rem;
    integer i;
    always @* begin
        rem = 24'd0;
        for (i = 0; i < 112; i = i + 1)
            if (in_bits[i]) rem = rem ^ COL[24*i +: 24];
    end

    reg [23:0] rem_q;
    reg        v_q;
    always @(posedge clk) rem_q <= rem;                  // data: no reset
    always @(posedge clk) v_q <= rst_n & in_valid;       // valid: reset

    assign out_valid = v_q;
    assign out_rem   = rem_q;
endmodule

`default_nettype wire
