// Active-high push-button synchronizer and 1 ms debounce filter at 50 MHz.
module button_debounce #(
    parameter integer STABLE_CYCLES = 50000
) (
    input  wire clk,
    input  wire reset_n,
    input  wire button_in,
    output reg  button_out
);

    reg [1:0] sync_ff;
    reg [31:0] stable_count;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sync_ff      <= 2'b00;
            stable_count <= 32'd0;
            button_out   <= 1'b0;
        end else begin
            sync_ff <= {sync_ff[0], button_in};
            if (sync_ff[1] == button_out) begin
                stable_count <= 32'd0;
            end else if (stable_count >= STABLE_CYCLES - 1) begin
                button_out   <= sync_ff[1];
                stable_count <= 32'd0;
            end else begin
                stable_count <= stable_count + 1'b1;
            end
        end
    end

endmodule
