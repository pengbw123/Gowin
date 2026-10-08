// Runtime additive-synth parameter receiver.
//
// The board's BL616 UART RX line is observed in parallel with the USB-host
// soft CPU. The CPU may continue printing text on TX while this module
// accepts binary configuration packets on RX.
//
// Packet (12 bytes, 115200 8-N-1):
//   A5 5A CMD DATA0 DATA1 ... DATA7 CHECKSUM
// CHECKSUM = XOR(CMD, DATA0..DATA7), DATA is little-endian.
//
// Commands:
//   10/11/13: ADSR attack/decay/release {samples:u32, phase_inc:u32}
//   12:       ADSR sustain Q0.16 in DATA0..1
//   40:       harmonic amplitude {anchor:u8, harmonic:u8, amp_q15:u16}
//   41:       harmonic decay {anchor:u8, harmonic:u8, alpha_q24:u24}
//   42:       end-of-update marker (reserved for future double buffering)
module synth_uart_control #(
    parameter [31:0] AMP_ATTACK_SAMPLES      = 32'd1,
    parameter [31:0] AMP_ATTACK_PHASE_INC    = 32'hFFFFFFFF,
    parameter [31:0] AMP_DECAY_SAMPLES       = 32'd1,
    parameter [31:0] AMP_DECAY_PHASE_INC     = 32'hFFFFFFFF,
    parameter [15:0] AMP_SUSTAIN_Q16         = 16'd32768,
    parameter [31:0] AMP_RELEASE_SAMPLES     = 32'd1,
    parameter [31:0] AMP_RELEASE_PHASE_INC   = 32'hFFFFFFFF
) (
    input  wire        clk,
    input  wire        reset_n,
    input  wire        uart_rx_pin,

    output reg  [31:0] amp_attack_samples,
    output reg  [31:0] amp_attack_phase_inc,
    output reg  [31:0] amp_decay_samples,
    output reg  [31:0] amp_decay_phase_inc,
    output reg  [15:0] amp_sustain_q16,
    output reg  [31:0] amp_release_samples,
    output reg  [31:0] amp_release_phase_inc,

    output reg         harmonic_write_valid,
    output reg         harmonic_write_decay,
    output reg  [1:0]  harmonic_write_anchor,
    output reg  [3:0]  harmonic_write_index,
    output reg  [23:0] harmonic_write_value,
    output reg         harmonic_commit,

    output reg         packet_received,
    output reg         packet_error
);

    localparam [3:0] WAIT_A5  = 4'd0;
    localparam [3:0] WAIT_5A  = 4'd1;
    localparam [3:0] READ_CMD = 4'd2;
    localparam [3:0] READ_D0  = 4'd3;
    localparam [3:0] READ_D1  = 4'd4;
    localparam [3:0] READ_D2  = 4'd5;
    localparam [3:0] READ_D3  = 4'd6;
    localparam [3:0] READ_D4  = 4'd7;
    localparam [3:0] READ_D5  = 4'd8;
    localparam [3:0] READ_D6  = 4'd9;
    localparam [3:0] READ_D7  = 4'd10;
    localparam [3:0] READ_SUM = 4'd11;

    wire [7:0] rx_byte;
    wire       rx_valid;
    reg  [3:0] parser_state;
    reg  [7:0] command;
    reg  [63:0] payload;
    reg  [7:0] checksum;

    uart_rx #(.CLKS_PER_BIT(434)) u_uart_rx (
        .clk     (clk),
        .reset_n (reset_n),
        .rx      (uart_rx_pin),
        .data    (rx_byte),
        .valid   (rx_valid)
    );

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            parser_state <= WAIT_A5;
            command      <= 8'd0;
            payload      <= 64'd0;
            checksum     <= 8'd0;

            amp_attack_samples       <= AMP_ATTACK_SAMPLES;
            amp_attack_phase_inc     <= AMP_ATTACK_PHASE_INC;
            amp_decay_samples        <= AMP_DECAY_SAMPLES;
            amp_decay_phase_inc      <= AMP_DECAY_PHASE_INC;
            amp_sustain_q16          <= AMP_SUSTAIN_Q16;
            amp_release_samples      <= AMP_RELEASE_SAMPLES;
            amp_release_phase_inc    <= AMP_RELEASE_PHASE_INC;

            harmonic_write_valid     <= 1'b0;
            harmonic_write_decay     <= 1'b0;
            harmonic_write_anchor    <= 2'd0;
            harmonic_write_index     <= 4'd0;
            harmonic_write_value     <= 24'd0;
            harmonic_commit          <= 1'b0;
            packet_received          <= 1'b0;
            packet_error             <= 1'b0;
        end else begin
            harmonic_write_valid <= 1'b0;
            harmonic_commit      <= 1'b0;
            packet_received      <= 1'b0;
            packet_error         <= 1'b0;

            if (rx_valid) begin
                case (parser_state)
                    WAIT_A5: begin
                        if (rx_byte == 8'hA5)
                            parser_state <= WAIT_5A;
                    end
                    WAIT_5A: begin
                        if (rx_byte == 8'h5A)
                            parser_state <= READ_CMD;
                        else if (rx_byte != 8'hA5)
                            parser_state <= WAIT_A5;
                    end
                    READ_CMD: begin
                        command      <= rx_byte;
                        checksum     <= rx_byte;
                        parser_state <= READ_D0;
                    end
                    READ_D0: begin payload[7:0]   <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D1; end
                    READ_D1: begin payload[15:8]  <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D2; end
                    READ_D2: begin payload[23:16] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D3; end
                    READ_D3: begin payload[31:24] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D4; end
                    READ_D4: begin payload[39:32] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D5; end
                    READ_D5: begin payload[47:40] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D6; end
                    READ_D6: begin payload[55:48] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_D7; end
                    READ_D7: begin payload[63:56] <= rx_byte; checksum <= checksum ^ rx_byte; parser_state <= READ_SUM; end
                    READ_SUM: begin
                        parser_state <= WAIT_A5;
                        if (rx_byte != checksum) begin
                            packet_error <= 1'b1;
                        end else begin
                            packet_received <= 1'b1;
                            case (command)
                                8'h10: begin
                                    amp_attack_samples   <= (payload[31:0] == 0) ? 32'd1 : payload[31:0];
                                    amp_attack_phase_inc <= payload[63:32];
                                end
                                8'h11: begin
                                    amp_decay_samples   <= (payload[31:0] == 0) ? 32'd1 : payload[31:0];
                                    amp_decay_phase_inc <= payload[63:32];
                                end
                                8'h12: amp_sustain_q16 <= payload[15:0];
                                8'h13: begin
                                    amp_release_samples   <= (payload[31:0] == 0) ? 32'd1 : payload[31:0];
                                    amp_release_phase_inc <= payload[63:32];
                                end
                                8'h40, 8'h41: begin
                                    if ((payload[7:0] < 4) && (payload[15:8] < 16)) begin
                                        harmonic_write_valid  <= 1'b1;
                                        harmonic_write_decay  <= (command == 8'h41);
                                        harmonic_write_anchor <= payload[1:0];
                                        harmonic_write_index  <= payload[11:8];
                                        harmonic_write_value  <= (command == 8'h41)
                                                               ? payload[39:16]
                                                               : {8'd0, payload[31:16]};
                                    end else begin
                                        packet_error <= 1'b1;
                                    end
                                end
                                8'h42: harmonic_commit <= 1'b1;
                                default: packet_error <= 1'b1;
                            endcase
                        end
                    end
                    default: parser_state <= WAIT_A5;
                endcase
            end
        end
    end

endmodule
