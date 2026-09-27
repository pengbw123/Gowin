// 8-N-1 UART receiver.  CLKS_PER_BIT=434 gives about 115207 baud at 50 MHz.
module uart_rx #(
    parameter integer CLKS_PER_BIT = 434
) (
    input  wire       clk,
    input  wire       reset_n,
    input  wire       rx,
    output reg  [7:0] data,
    output reg        valid
);

    localparam [2:0] RX_IDLE  = 3'd0;
    localparam [2:0] RX_START = 3'd1;
    localparam [2:0] RX_DATA  = 3'd2;
    localparam [2:0] RX_STOP  = 3'd3;

    reg [2:0] state;
    reg [15:0] clock_count;
    reg [2:0] bit_index;
    reg [7:0] shift_reg;
    reg [1:0] rx_sync;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            rx_sync <= 2'b11;
        end else begin
            rx_sync <= {rx_sync[0], rx};
        end
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state       <= RX_IDLE;
            clock_count <= 16'd0;
            bit_index   <= 3'd0;
            shift_reg   <= 8'd0;
            data        <= 8'd0;
            valid       <= 1'b0;
        end else begin
            valid <= 1'b0;
            case (state)
                RX_IDLE: begin
                    clock_count <= 16'd0;
                    bit_index   <= 3'd0;
                    if (!rx_sync[1])
                        state <= RX_START;
                end

                RX_START: begin
                    if (clock_count == (CLKS_PER_BIT / 2) - 1) begin
                        clock_count <= 16'd0;
                        if (!rx_sync[1])
                            state <= RX_DATA;
                        else
                            state <= RX_IDLE;
                    end else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                RX_DATA: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count          <= 16'd0;
                        shift_reg[bit_index] <= rx_sync[1];
                        if (bit_index == 3'd7) begin
                            bit_index <= 3'd0;
                            state     <= RX_STOP;
                        end else begin
                            bit_index <= bit_index + 1'b1;
                        end
                    end else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                RX_STOP: begin
                    if (clock_count == CLKS_PER_BIT - 1) begin
                        clock_count <= 16'd0;
                        state       <= RX_IDLE;
                        if (rx_sync[1]) begin
                            data  <= shift_reg;
                            valid <= 1'b1;
                        end
                    end else begin
                        clock_count <= clock_count + 1'b1;
                    end
                end

                default: state <= RX_IDLE;
            endcase
        end
    end

endmodule
