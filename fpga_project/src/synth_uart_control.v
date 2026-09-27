// Runtime synthesizer parameter receiver.
//
// Fixed 12-byte packet, sent at 115200 baud:
//   A5 5A CMD DATA0 DATA1 ... DATA7 CHECKSUM
// DATA is little-endian. CHECKSUM is XOR(CMD, DATA0..DATA7).
// Timing commands carry {phase_increment[31:0], sample_count[31:0]}.
module synth_uart_control #(
    parameter [31:0] AMP_ATTACK_SAMPLES       = 32'd1,
    parameter [31:0] AMP_ATTACK_PHASE_INC     = 32'hFFFFFFFF,
    parameter [31:0] AMP_DECAY_SAMPLES        = 32'd1,
    parameter [31:0] AMP_DECAY_PHASE_INC      = 32'hFFFFFFFF,
    parameter [15:0] AMP_SUSTAIN_Q16           = 16'd32768,
    parameter [31:0] AMP_RELEASE_SAMPLES      = 32'd1,
    parameter [31:0] AMP_RELEASE_PHASE_INC    = 32'hFFFFFFFF,
    parameter [31:0] FILTER_ATTACK_SAMPLES    = 32'd1,
    parameter [31:0] FILTER_ATTACK_PHASE_INC  = 32'hFFFFFFFF,
    parameter [31:0] FILTER_DECAY_SAMPLES     = 32'd1,
    parameter [31:0] FILTER_DECAY_PHASE_INC   = 32'hFFFFFFFF,
    parameter [15:0] FILTER_SUSTAIN_Q16        = 16'd32768,
    parameter [31:0] FILTER_RELEASE_SAMPLES   = 32'd1,
    parameter [31:0] FILTER_RELEASE_PHASE_INC = 32'hFFFFFFFF,
    parameter         FILTER_FOLLOW_AMP        = 1'b1,
    parameter [2:0]   UNISON_COUNT             = 3'd1,
    parameter [31:0]  UNISON_DETUNE_STEP       = 32'd80000
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
    output reg  [31:0] filter_attack_samples,
    output reg  [31:0] filter_attack_phase_inc,
    output reg  [31:0] filter_decay_samples,
    output reg  [31:0] filter_decay_phase_inc,
    output reg  [15:0] filter_sustain_q16,
    output reg  [31:0] filter_release_samples,
    output reg  [31:0] filter_release_phase_inc,
    output reg         filter_follow_amp,
    output reg  [3:0]  unison_count,
    output reg  [31:0] unison_detune_step,
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
            packet_received <= 1'b0;
            packet_error    <= 1'b0;

            amp_attack_samples       <= AMP_ATTACK_SAMPLES;
            amp_attack_phase_inc     <= AMP_ATTACK_PHASE_INC;
            amp_decay_samples        <= AMP_DECAY_SAMPLES;
            amp_decay_phase_inc      <= AMP_DECAY_PHASE_INC;
            amp_sustain_q16          <= AMP_SUSTAIN_Q16;
            amp_release_samples      <= AMP_RELEASE_SAMPLES;
            amp_release_phase_inc    <= AMP_RELEASE_PHASE_INC;
            filter_attack_samples    <= FILTER_ATTACK_SAMPLES;
            filter_attack_phase_inc  <= FILTER_ATTACK_PHASE_INC;
            filter_decay_samples     <= FILTER_DECAY_SAMPLES;
            filter_decay_phase_inc   <= FILTER_DECAY_PHASE_INC;
            filter_sustain_q16       <= FILTER_SUSTAIN_Q16;
            filter_release_samples   <= FILTER_RELEASE_SAMPLES;
            filter_release_phase_inc <= FILTER_RELEASE_PHASE_INC;
            filter_follow_amp        <= FILTER_FOLLOW_AMP;
            unison_count             <= {1'b0, UNISON_COUNT};
            unison_detune_step       <= UNISON_DETUNE_STEP;
        end else begin
            packet_received <= 1'b0;
            packet_error    <= 1'b0;

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
                        if (rx_byte == checksum) begin
                            packet_received <= 1'b1;
                            case (command)
                                8'h10: begin amp_attack_samples       <= payload[31:0]; amp_attack_phase_inc       <= payload[63:32]; end
                                8'h11: begin amp_decay_samples        <= payload[31:0]; amp_decay_phase_inc        <= payload[63:32]; end
                                8'h12:       amp_sustain_q16           <= payload[15:0];
                                8'h13: begin amp_release_samples      <= payload[31:0]; amp_release_phase_inc      <= payload[63:32]; end
                                8'h20: begin filter_attack_samples    <= payload[31:0]; filter_attack_phase_inc    <= payload[63:32]; end
                                8'h21: begin filter_decay_samples     <= payload[31:0]; filter_decay_phase_inc     <= payload[63:32]; end
                                8'h22:       filter_sustain_q16        <= payload[15:0];
                                8'h23: begin filter_release_samples   <= payload[31:0]; filter_release_phase_inc   <= payload[63:32]; end
                                8'h24:       filter_follow_amp        <= payload[0];
                                8'h30: begin
                                    if (payload[7:0] < 1)
                                        unison_count <= 4'd1;
                                    else if (payload[7:0] > 8)
                                        unison_count <= 4'd8;
                                    else
                                        unison_count <= payload[3:0];
                                end
                                8'h31:       unison_detune_step       <= payload[31:0];
                                default:    packet_error              <= 1'b1;
                            endcase
                        end else begin
                            packet_error <= 1'b1;
                        end
                    end
                    default: parser_state <= WAIT_A5;
                endcase
            end
        end
    end

endmodule
