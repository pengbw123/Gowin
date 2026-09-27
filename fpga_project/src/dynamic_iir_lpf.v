// First-order dynamic low-pass IIR.
//
// Difference equation (alpha is unsigned Q0.16):
//   y[n] = y[n-1] + alpha[n] * (x[n] - y[n-1])
//
// alpha may change on every audio sample, so an ADSR/filter envelope can
// continuously move the cutoff frequency without changing the filter shape.
module dynamic_iir_lpf (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire               enable,
    input  wire signed [15:0] sample_in,
    input  wire        [15:0] alpha_q16,
    output reg  signed [31:0] sample_out
);

    reg signed [31:0] state;
    wire signed [32:0] delta =
        $signed({{17{sample_in[15]}}, sample_in}) - $signed({state[31], state});
    wire signed [49:0] product = delta * $signed({1'b0, alpha_q16});
    wire signed [33:0] scaled_product = $signed(product[49:16]);
    wire signed [33:0] next_wide =
        $signed({state[31], state[31], state}) + scaled_product;
    wire signed [31:0] next_state = next_wide[31:0];

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state      <= 32'sd0;
            sample_out <= 32'sd0;
        end else if (sample_tick) begin
            if (enable) begin
                state      <= next_state;
                sample_out <= next_state;
            end else begin
                state      <= {{16{sample_in[15]}}, sample_in};
                sample_out <= {{16{sample_in[15]}}, sample_in};
            end
        end
    end

endmodule
