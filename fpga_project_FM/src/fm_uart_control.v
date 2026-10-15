// UART control plane for the 6-operator FM synthesizer.
//
// The framing stays compatible with the other tools in this repository:
//   A5 5A CMD DATA0..DATA7 XOR
// All DX7-specific nonlinear conversions are deliberately performed by the
// PC editor. The FPGA receives frequency words plus compact fixed-point
// fields. Operator EG commands use DX7 qrate/log-level units; Pitch EG keeps
// its one-pole coefficient.
module fm_uart_control (
    input  wire        clk,
    input  wire        reset_n,
    input  wire        uart_rx_pin,
    input  wire        preset_select_valid,
    input  wire [4:0]  preset_select,

    output reg  [31:0] midi_event_data,
    output reg         midi_event_valid,
    output reg  [6:0]  midi_event_brightness,
    input  wire        midi_event_ready,

    output reg         strum_mode,
    output reg  [15:0] strum_interval_ms,
    output reg  [15:0] strum_gate_ms,
    output reg  [6:0]  strum_velocity_speed_depth,
    output reg  [6:0]  strum_velocity_brightness_depth,

    output reg         fm_global_write_valid,
    output reg  [4:0]  fm_algorithm,
    output reg  [2:0]  fm_feedback,
    output reg         fm_osc_sync,
    output reg  [6:0]  fm_transpose,
    output reg  [2:0]  fm_lfo_waveform,
    output reg  [6:0]  fm_lfo_speed,
    output reg  [6:0]  fm_lfo_delay,
    output reg         fm_lfo_sync,
    output reg  [6:0]  fm_lfo_pitch_depth,
    output reg  [6:0]  fm_lfo_amp_depth,
    output reg  [2:0]  fm_pitch_mod_sensitivity,

    output reg         fm_operator_write_valid,
    output reg  [2:0]  fm_operator_index,
    output reg         fm_operator_enable,
    output reg         fm_operator_fixed,
    output reg  [31:0] fm_operator_frequency,
    output reg  [15:0] fm_operator_output_gain,

    output reg         fm_envelope_write_valid,
    output reg  [2:0]  fm_envelope_operator,
    output reg  [1:0]  fm_envelope_stage,
    output reg  [15:0] fm_envelope_coefficient,
    output reg  [15:0] fm_envelope_level,
    output reg  [2:0]  fm_rate_scaling,
    output reg  [2:0]  fm_velocity_sensitivity,
    output reg  [1:0]  fm_amp_mod_sensitivity,

    output reg         fm_scaling_write_valid,
    output reg  [2:0]  fm_scaling_operator,
    output reg  [6:0]  fm_scaling_breakpoint,
    output reg  [6:0]  fm_scaling_left_depth,
    output reg  [6:0]  fm_scaling_right_depth,
    output reg  [1:0]  fm_scaling_left_curve,
    output reg  [1:0]  fm_scaling_right_curve,

    output reg         fm_pitch_envelope_write_valid,
    output reg  [1:0]  fm_pitch_envelope_stage,
    output reg  [15:0] fm_pitch_envelope_coefficient,
    output reg  [20:0] fm_pitch_envelope_ratio,
    output reg         fm_algorithm_flags_write_valid,
    output reg  [47:0] fm_algorithm_flags,
    output reg         fm_note_gain_write_valid,
    output reg  [9:0]  fm_note_gain_address,
    output reg  [15:0] fm_note_gain_value,
    output reg         fm_patch_commit,

    output reg         packet_received,
    output reg         packet_error,
    output reg  [4:0]  current_preset
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

    reg [63:0] preset_global_memory [0:31];
    reg [63:0] preset_operator_memory [0:191];
    reg [39:0] preset_scaling_memory [0:191];
    reg [23:0] preset_envelope_memory [0:767];
    reg [39:0] preset_pitch_memory [0:127];
    reg [47:0] preset_algorithm_memory [0:31];
    initial begin
        $readmemh("src/generated/fm_rom1a_global.mem", preset_global_memory);
        $readmemh("src/generated/fm_rom1a_operator.mem", preset_operator_memory);
        $readmemh("src/generated/fm_rom1a_scaling.mem", preset_scaling_memory);
        $readmemh("src/generated/fm_rom1a_envelope.mem", preset_envelope_memory);
        $readmemh("src/generated/fm_rom1a_pitch.mem", preset_pitch_memory);
        $readmemh("src/generated/fm_rom1a_algorithm.mem", preset_algorithm_memory);
    end
    reg [5:0] preset_load_state;
    reg [2:0] preset_op_index;
    reg [4:0] preset_eg_index;
    reg [2:0] preset_pitch_index;
    reg       preset_read_wait;
    wire [7:0] preset_op_address = current_preset * 6 + preset_op_index;
    wire [9:0] preset_eg_address = current_preset * 24 + preset_eg_index;
    wire [6:0] preset_pitch_address = current_preset * 4 + preset_pitch_index;
    wire [63:0] preset_global_word = preset_global_memory[current_preset];
    wire [63:0] preset_operator_word = preset_operator_memory[preset_op_address];
    wire [39:0] preset_scaling_word = preset_scaling_memory[preset_op_address];
    wire [23:0] preset_envelope_word = preset_envelope_memory[preset_eg_address];
    wire [39:0] preset_pitch_word = preset_pitch_memory[preset_pitch_address];

    uart_rx #(.CLKS_PER_BIT(434)) u_uart_rx (
        .clk(clk), .reset_n(reset_n), .rx(uart_rx_pin),
        .data(rx_byte), .valid(rx_valid)
    );

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            parser_state <= WAIT_A5;
            command <= 8'd0;
            payload <= 64'd0;
            checksum <= 8'd0;
            midi_event_data <= 32'd0;
            midi_event_valid <= 1'b0;
            midi_event_brightness <= 7'd0;
            strum_mode <= 1'b0;
            strum_interval_ms <= 16'd18;
            strum_gate_ms <= 16'd120;
            strum_velocity_speed_depth <= 7'd80;
            strum_velocity_brightness_depth <= 7'd96;

            // A gentle electric-piano style power-on patch.  The detailed
            // operator defaults live in fm_poly_synth so the control plane
            // remains independent of one particular preset.
            fm_algorithm <= 5'd4;
            fm_feedback <= 3'd2;
            fm_osc_sync <= 1'b1;
            fm_transpose <= 7'd24;
            fm_lfo_waveform <= 3'd4;
            fm_lfo_speed <= 7'd35;
            fm_lfo_delay <= 7'd20;
            fm_lfo_sync <= 1'b1;
            fm_lfo_pitch_depth <= 7'd0;
            fm_lfo_amp_depth <= 7'd0;
            fm_pitch_mod_sensitivity <= 3'd3;
            fm_global_write_valid <= 1'b0;

            fm_operator_write_valid <= 1'b0;
            fm_operator_index <= 3'd0;
            fm_operator_enable <= 1'b1;
            fm_operator_fixed <= 1'b0;
            fm_operator_frequency <= 32'h00010000;
            fm_operator_output_gain <= 16'h4000;
            fm_envelope_write_valid <= 1'b0;
            fm_envelope_operator <= 3'd0;
            fm_envelope_stage <= 2'd0;
            fm_envelope_coefficient <= 16'd1;
            fm_envelope_level <= 16'd0;
            fm_rate_scaling <= 3'd0;
            fm_velocity_sensitivity <= 3'd0;
            fm_amp_mod_sensitivity <= 2'd0;
            fm_scaling_write_valid <= 1'b0;
            fm_scaling_operator <= 3'd0;
            fm_scaling_breakpoint <= 7'd60;
            fm_scaling_left_depth <= 7'd0;
            fm_scaling_right_depth <= 7'd0;
            fm_scaling_left_curve <= 2'd0;
            fm_scaling_right_curve <= 2'd0;
            fm_pitch_envelope_write_valid <= 1'b0;
            fm_pitch_envelope_stage <= 2'd0;
            fm_pitch_envelope_coefficient <= 16'd1;
            fm_pitch_envelope_ratio <= 21'h10000;
            fm_algorithm_flags_write_valid <= 1'b0;
            fm_algorithm_flags <= 48'h1401140114c1;
            fm_note_gain_write_valid <= 1'b0;
            fm_note_gain_address <= 10'd0;
            fm_note_gain_value <= 16'd0;
            fm_patch_commit <= 1'b0;
            packet_received <= 1'b0;
            packet_error <= 1'b0;
            current_preset <= 5'd0;
            preset_load_state <= 6'd1;
            preset_op_index <= 3'd0;
            preset_eg_index <= 5'd0;
            preset_pitch_index <= 3'd0;
            preset_read_wait <= 1'b0;
        end else begin
            fm_global_write_valid <= 1'b0;
            fm_operator_write_valid <= 1'b0;
            fm_envelope_write_valid <= 1'b0;
            fm_scaling_write_valid <= 1'b0;
            fm_pitch_envelope_write_valid <= 1'b0;
            fm_algorithm_flags_write_valid <= 1'b0;
            fm_note_gain_write_valid <= 1'b0;
            fm_patch_commit <= 1'b0;
            packet_received <= 1'b0;
            packet_error <= 1'b0;
            if (midi_event_valid && midi_event_ready)
                midi_event_valid <= 1'b0;

            if (rx_valid) begin
                case (parser_state)
                    WAIT_A5: if (rx_byte == 8'ha5) parser_state <= WAIT_5A;
                    WAIT_5A: begin
                        if (rx_byte == 8'h5a) parser_state <= READ_CMD;
                        else if (rx_byte != 8'ha5) parser_state <= WAIT_A5;
                    end
                    READ_CMD: begin command <= rx_byte; checksum <= rx_byte; parser_state <= READ_D0; end
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
                                8'h30: begin
                                    if (!midi_event_valid || midi_event_ready) begin
                                        midi_event_data <= {8'd0, payload[7:0],
                                            1'b0, payload[14:8], 1'b0, payload[22:16]};
                                        midi_event_brightness <= (payload[31:24] > 127)
                                            ? 7'd127 : payload[30:24];
                                        midi_event_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                8'h50: begin
                                    strum_mode <= payload[0];
                                    strum_interval_ms <= (payload[23:8] == 0)
                                        ? 16'd1 : payload[23:8];
                                    strum_gate_ms <= (payload[39:24] == 0)
                                        ? 16'd1 : payload[39:24];
                                    strum_velocity_speed_depth <=
                                        (payload[47:40] > 127) ? 7'd127 : payload[46:40];
                                    strum_velocity_brightness_depth <=
                                        (payload[55:48] > 127) ? 7'd127 : payload[54:48];
                                end
                                8'h60: begin
                                    fm_algorithm <= payload[4:0];
                                    fm_feedback <= payload[10:8];
                                    fm_osc_sync <= payload[11];
                                    fm_lfo_sync <= payload[12];
                                    fm_transpose <= (payload[23:16] > 48)
                                        ? 7'd48 : payload[22:16];
                                    fm_lfo_waveform <= payload[26:24];
                                    fm_lfo_speed <= (payload[39:32] > 127)
                                        ? 7'd127 : payload[38:32];
                                    fm_lfo_delay <= (payload[47:40] > 127)
                                        ? 7'd127 : payload[46:40];
                                    fm_lfo_pitch_depth <= (payload[55:48] > 127)
                                        ? 7'd127 : payload[54:48];
                                    fm_lfo_amp_depth <= (payload[63:56] > 127)
                                        ? 7'd127 : payload[62:56];
                                    fm_global_write_valid <= 1'b1;
                                end
                                8'h61: begin
                                    fm_pitch_mod_sensitivity <= payload[2:0];
                                    fm_global_write_valid <= 1'b1;
                                end
                                8'h62: begin
                                    if (payload[7:0] < 6) begin
                                        fm_operator_index <= payload[2:0];
                                        fm_operator_enable <= payload[8];
                                        fm_operator_fixed <= payload[9];
                                        fm_operator_frequency <= payload[47:16];
                                        fm_operator_output_gain <= payload[63:48];
                                        fm_operator_write_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                8'h63: begin
                                    if ((payload[7:0] < 6) && (payload[15:8] < 4)) begin
                                        fm_envelope_operator <= payload[2:0];
                                        fm_envelope_stage <= payload[9:8];
                                        fm_envelope_coefficient <= payload[31:16];
                                        fm_envelope_level <= payload[47:32];
                                        fm_rate_scaling <= payload[50:48];
                                        fm_velocity_sensitivity <= payload[58:56];
                                        fm_amp_mod_sensitivity <= payload[61:60];
                                        fm_envelope_write_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                8'h64: begin
                                    if (payload[7:0] < 6) begin
                                        fm_scaling_operator <= payload[2:0];
                                        fm_scaling_breakpoint <= (payload[15:8] > 127)
                                            ? 7'd127 : payload[14:8];
                                        fm_scaling_left_depth <= (payload[23:16] > 99)
                                            ? 7'd99 : payload[22:16];
                                        fm_scaling_right_depth <= (payload[31:24] > 99)
                                            ? 7'd99 : payload[30:24];
                                        fm_scaling_left_curve <= payload[33:32];
                                        fm_scaling_right_curve <= payload[41:40];
                                        fm_scaling_write_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                8'h65: begin
                                    if (payload[7:0] < 4) begin
                                        fm_pitch_envelope_stage <= payload[1:0];
                                        fm_pitch_envelope_coefficient <= payload[23:8];
                                    fm_pitch_envelope_ratio <= payload[44:24];
                                        fm_pitch_envelope_write_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                8'h66: fm_patch_commit <= 1'b1;
                                8'h67: begin
                                    fm_algorithm_flags <= payload[47:0];
                                    fm_algorithm_flags_write_valid <= 1'b1;
                                end
                                8'h68: begin
                                    if ((payload[7:0] < 6) && (payload[15:8] < 128)) begin
                                        fm_note_gain_address <= {payload[2:0], payload[14:8]};
                                        fm_note_gain_value <= payload[31:16];
                                        fm_note_gain_write_valid <= 1'b1;
                                    end else packet_error <= 1'b1;
                                end
                                default: packet_error <= 1'b1;
                            endcase
                        end
                    end
                    default: parser_state <= WAIT_A5;
                endcase
            end

            // The physical TRANSPOSE knob selects one of the 32 ROM1A
            // patches. Loading takes under 100 clocks and first silences the
            // old patch, so no voice can hear a half-old/half-new parameter
            // set. UART patch editing remains available while idle.
            if (preset_select_valid && preset_load_state == 0) begin
                current_preset <= preset_select;
                preset_op_index <= 0;
                preset_eg_index <= 0;
                preset_pitch_index <= 0;
                preset_read_wait <= 1'b0;
                fm_patch_commit <= 1'b1;
                preset_load_state <= 1;
            end else if (preset_load_state != 0) begin
                case (preset_load_state)
                    1: begin
                        if (!preset_read_wait) begin
                            preset_read_wait <= 1'b1;
                        end else begin
                        preset_read_wait <= 1'b0;
                        fm_algorithm <= preset_global_word[4:0];
                        fm_feedback <= preset_global_word[7:5];
                        fm_osc_sync <= preset_global_word[8];
                        fm_lfo_sync <= preset_global_word[9];
                        fm_transpose <= preset_global_word[16:10];
                        fm_lfo_waveform <= preset_global_word[19:17];
                        fm_lfo_speed <= preset_global_word[26:20];
                        fm_lfo_delay <= preset_global_word[33:27];
                        fm_lfo_pitch_depth <= preset_global_word[40:34];
                        fm_lfo_amp_depth <= preset_global_word[47:41];
                        fm_pitch_mod_sensitivity <= preset_global_word[50:48];
                        fm_global_write_valid <= 1'b1;
                        preset_load_state <= 2;
                        end
                    end
                    2: begin
                        if (!preset_read_wait) begin
                            preset_read_wait <= 1'b1;
                        end else begin
                        preset_read_wait <= 1'b0;
                        fm_operator_index <= preset_op_index;
                        fm_operator_enable <= preset_operator_word[0];
                        fm_operator_fixed <= preset_operator_word[1];
                        fm_operator_frequency <= preset_operator_word[33:2];
                        fm_operator_output_gain <= {9'd0, preset_operator_word[40:34]};
                        fm_operator_write_valid <= 1'b1;
                        fm_scaling_operator <= preset_op_index;
                        fm_scaling_breakpoint <= preset_scaling_word[6:0];
                        fm_scaling_left_depth <= preset_scaling_word[13:7];
                        fm_scaling_right_depth <= preset_scaling_word[20:14];
                        fm_scaling_left_curve <= preset_scaling_word[22:21];
                        fm_scaling_right_curve <= preset_scaling_word[24:23];
                        fm_rate_scaling <= preset_scaling_word[27:25];
                        fm_velocity_sensitivity <= preset_scaling_word[30:28];
                        fm_amp_mod_sensitivity <= preset_scaling_word[32:31];
                        fm_scaling_write_valid <= 1'b1;
                        if (preset_op_index == 5) begin
                            preset_op_index <= 0;
                            preset_load_state <= 3;
                        end else preset_op_index <= preset_op_index + 1'b1;
                        end
                    end
                    3: begin
                        if (!preset_read_wait) begin
                            preset_read_wait <= 1'b1;
                        end else begin
                        preset_read_wait <= 1'b0;
                        fm_envelope_operator <= preset_eg_index[4:2];
                        fm_envelope_stage <= preset_eg_index[1:0];
                        fm_envelope_coefficient <= {10'd0, preset_envelope_word[5:0]};
                        fm_envelope_level <= preset_envelope_word[21:6];
                        fm_envelope_write_valid <= 1'b1;
                        if (preset_eg_index == 23) begin
                            preset_eg_index <= 0;
                            preset_load_state <= 4;
                        end else preset_eg_index <= preset_eg_index + 1'b1;
                        end
                    end
                    4: begin
                        if (!preset_read_wait) begin
                            preset_read_wait <= 1'b1;
                        end else begin
                        preset_read_wait <= 1'b0;
                        fm_pitch_envelope_stage <= preset_pitch_index[1:0];
                        fm_pitch_envelope_coefficient <= preset_pitch_word[15:0];
                        fm_pitch_envelope_ratio <= preset_pitch_word[36:16];
                        fm_pitch_envelope_write_valid <= 1'b1;
                        if (preset_pitch_index == 3) begin
                            preset_pitch_index <= 0;
                            preset_load_state <= 5;
                        end else preset_pitch_index <= preset_pitch_index + 1'b1;
                        end
                    end
                    5: begin
                        if (!preset_read_wait) begin
                            preset_read_wait <= 1'b1;
                        end else begin
                        preset_read_wait <= 1'b0;
                        fm_algorithm_flags <= preset_algorithm_memory[current_preset];
                        fm_algorithm_flags_write_valid <= 1'b1;
                        preset_load_state <= 6;
                        end
                    end
                    default: begin
                        fm_patch_commit <= 1'b1;
                        preset_load_state <= 0;
                    end
                endcase
            end
        end
    end
endmodule
