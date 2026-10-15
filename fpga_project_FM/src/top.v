// Tang Primer 25K USB-MIDI six-operator FM synthesizer.
// USB-A on the Dock is the host port (D+=L6, D-=K6). USB-C remains the
// BL616 JTAG/UART connection and carries 115200-baud diagnostics/configuration.
module top (
    input  wire clk,
    input  wire key_s2,
    input  wire uart_rx_pin,
    output wire uart_tx_pin,
    inout  wire usb_host_dp,
    inout  wire usb_host_dn,
    output wire i2s_bclk,
    output wire i2s_lrclk,
    output wire i2s_din,
    output wire lcd_resetn,
    output wire lcd_clk,
    output wire lcd_cs,
    output wire lcd_dc,
    output wire lcd_data
);
    wire clk_usb_48m;
    wire clk_usb_bram_96m;
    wire usb_pll_lock;
    wire usb_debug_reset_n;
    wire usb_debug_cpu_fetch;
    wire usb_debug_uart_write;
    wire [31:0] usb_debug_cpu_addr;
    reg usb_pll_lock_meta = 1'b0;
    reg usb_pll_lock_sync = 1'b0;
    reg usb_pll_lock_prev = 1'b0;
    reg [31:0] usb_pll_unlock_count = 32'd0;

    // Retain the proven USB soft-core watchdog from the additive build.
    localparam [27:0] USB_ALIVE_TIMEOUT_CYCLES = 28'd150_000_000;
    localparam [22:0] USB_RESET_HOLD_CYCLES = 23'd5_000_000;
    reg [1:0] usb_uart_activity_sync = 2'b00;
    reg [27:0] usb_alive_timeout_count = 28'd0;
    reg [22:0] usb_reset_hold_count = 23'd0;
    reg usb_watchdog_reset = 1'b0;
    wire audio_reset_n = ~(key_s2 | usb_watchdog_reset);
    wire usb_reset = key_s2 | ~usb_pll_lock | usb_watchdog_reset;

    usb_pll u_usb_pll (
        .clkin(clk), .clkout0(clk_usb_48m),
        .clkout1(clk_usb_bram_96m), .lock(usb_pll_lock)
    );

    always @(posedge clk) begin
        usb_pll_lock_meta <= usb_pll_lock;
        usb_pll_lock_sync <= usb_pll_lock_meta;
        usb_pll_lock_prev <= usb_pll_lock_sync;
        if (key_s2) usb_pll_unlock_count <= 32'd0;
        else if (usb_pll_lock_prev && !usb_pll_lock_sync)
            usb_pll_unlock_count <= usb_pll_unlock_count + 1'b1;
    end

    always @(posedge clk) begin
        usb_uart_activity_sync <= {usb_uart_activity_sync[0], usb_debug_uart_write};
        if (key_s2 || !usb_pll_lock_sync) begin
            usb_alive_timeout_count <= 28'd0;
            usb_reset_hold_count <= 23'd0;
            usb_watchdog_reset <= 1'b0;
        end else if (usb_watchdog_reset) begin
            if (usb_reset_hold_count == USB_RESET_HOLD_CYCLES - 1'b1) begin
                usb_reset_hold_count <= 23'd0;
                usb_alive_timeout_count <= 28'd0;
                usb_watchdog_reset <= 1'b0;
            end else usb_reset_hold_count <= usb_reset_hold_count + 1'b1;
        end else if (usb_uart_activity_sync[1]) begin
            usb_alive_timeout_count <= 28'd0;
        end else if (usb_alive_timeout_count == USB_ALIVE_TIMEOUT_CYCLES - 1'b1) begin
            usb_alive_timeout_count <= 28'd0;
            usb_reset_hold_count <= 23'd0;
            usb_watchdog_reset <= 1'b1;
        end else usb_alive_timeout_count <= usb_alive_timeout_count + 1'b1;
    end

    wire [31:0] usb_midi_event_data;
    wire usb_midi_event_toggle;
    reg midi_event_ack_toggle;
    wire usb_midi_connected;
    usb_midi_host u_usb_midi_host (
        .clk_48m(clk_usb_48m), .clk_cpu_bram_96m(clk_usb_bram_96m),
        .reset(usb_reset), .usb_dp(usb_host_dp), .usb_dn(usb_host_dn),
        .cpu_uart_tx(uart_tx_pin), .cpu_uart_rx(uart_rx_pin),
        .midi_event_data(usb_midi_event_data),
        .midi_event_toggle(usb_midi_event_toggle),
        .midi_event_ack_toggle(midi_event_ack_toggle),
        .midi_connected(usb_midi_connected),
        .debug_pll_unlock_count(usb_pll_unlock_count),
        .debug_reset_n(usb_debug_reset_n),
        .debug_cpu_fetch(usb_debug_cpu_fetch),
        .debug_uart_write(usb_debug_uart_write),
        .debug_cpu_addr(usb_debug_cpu_addr)
    );

    reg [1:0] midi_request_sync;
    reg [31:0] midi_event_latched;
    reg midi_event_valid;
    wire raw_midi_event_ready;
    always @(posedge clk or negedge audio_reset_n) begin
        if (!audio_reset_n) begin
            midi_request_sync <= 2'b00;
            midi_event_ack_toggle <= 1'b0;
            midi_event_latched <= 32'd0;
            midi_event_valid <= 1'b0;
        end else begin
            midi_request_sync <= {midi_request_sync[0], usb_midi_event_toggle};
            midi_event_valid <= 1'b0;
            if ((midi_request_sync[1] != midi_event_ack_toggle) && raw_midi_event_ready) begin
                midi_event_latched <= usb_midi_event_data;
                midi_event_valid <= 1'b1;
                midi_event_ack_toggle <= midi_request_sync[1];
            end
        end
    end

    wire [13:0] pitch_bend;
    wire [6:0] vibrato_depth, vibrato_rate, portamento_time;
    wire [6:0] chorus_mix, delay_mix, delay_time, reverb_mix;
    wire preset_select_valid;
    wire [4:0] preset_select;
    midi_performance_control u_midi_performance_control (
        .clk(clk), .reset_n(audio_reset_n),
        .midi_event_data(midi_event_latched),
        .midi_event_valid(midi_event_valid),
        .pitch_bend(pitch_bend), .vibrato_depth(vibrato_depth),
        .vibrato_rate(vibrato_rate), .portamento_time(portamento_time),
        .chorus_mix(chorus_mix), .delay_mix(delay_mix),
        .delay_time(delay_time), .reverb_mix(reverb_mix),
        .preset_select_valid(preset_select_valid),
        .preset_select(preset_select)
    );

    wire strum_mode;
    wire [15:0] strum_interval_ms, strum_gate_ms;
    wire [6:0] strum_velocity_speed_depth, strum_velocity_brightness_depth;
    wire [31:0] uart_midi_event_data;
    wire uart_midi_event_valid, uart_midi_event_ready;
    wire [6:0] uart_midi_event_brightness;
    wire uart_packet_received, uart_packet_error;
    wire fm_global_write_valid;
    wire [4:0] fm_algorithm;
    wire [2:0] fm_feedback;
    wire fm_osc_sync;
    wire [6:0] fm_transpose;
    wire [2:0] fm_lfo_waveform;
    wire [6:0] fm_lfo_speed, fm_lfo_delay;
    wire fm_lfo_sync;
    wire [6:0] fm_lfo_pitch_depth, fm_lfo_amp_depth;
    wire [2:0] fm_pitch_mod_sensitivity;
    wire fm_operator_write_valid;
    wire [2:0] fm_operator_index;
    wire fm_operator_enable, fm_operator_fixed;
    wire [31:0] fm_operator_frequency;
    wire [15:0] fm_operator_output_gain;
    wire fm_envelope_write_valid;
    wire [2:0] fm_envelope_operator;
    wire [1:0] fm_envelope_stage;
    wire [15:0] fm_envelope_coefficient, fm_envelope_level;
    wire [2:0] fm_rate_scaling, fm_velocity_sensitivity;
    wire [1:0] fm_amp_mod_sensitivity;
    wire fm_scaling_write_valid;
    wire [2:0] fm_scaling_operator;
    wire [6:0] fm_scaling_breakpoint, fm_scaling_left_depth, fm_scaling_right_depth;
    wire [1:0] fm_scaling_left_curve, fm_scaling_right_curve;
    wire fm_pitch_envelope_write_valid;
    wire [1:0] fm_pitch_envelope_stage;
    wire [15:0] fm_pitch_envelope_coefficient;
    wire [20:0] fm_pitch_envelope_ratio;
    wire fm_algorithm_flags_write_valid;
    wire [47:0] fm_algorithm_flags;
    wire fm_note_gain_write_valid;
    wire [9:0] fm_note_gain_address;
    wire [15:0] fm_note_gain_value;
    wire fm_patch_commit;
    wire [4:0] current_preset;

    fm_uart_control u_fm_uart_control (
        .clk(clk), .reset_n(audio_reset_n), .uart_rx_pin(uart_rx_pin),
        .preset_select_valid(preset_select_valid),
        .preset_select(preset_select),
        .midi_event_data(uart_midi_event_data),
        .midi_event_valid(uart_midi_event_valid),
        .midi_event_brightness(uart_midi_event_brightness),
        .midi_event_ready(uart_midi_event_ready),
        .strum_mode(strum_mode), .strum_interval_ms(strum_interval_ms),
        .strum_gate_ms(strum_gate_ms),
        .strum_velocity_speed_depth(strum_velocity_speed_depth),
        .strum_velocity_brightness_depth(strum_velocity_brightness_depth),
        .fm_global_write_valid(fm_global_write_valid),
        .fm_algorithm(fm_algorithm), .fm_feedback(fm_feedback),
        .fm_osc_sync(fm_osc_sync), .fm_transpose(fm_transpose),
        .fm_lfo_waveform(fm_lfo_waveform), .fm_lfo_speed(fm_lfo_speed),
        .fm_lfo_delay(fm_lfo_delay),
        .fm_lfo_sync(fm_lfo_sync),
        .fm_lfo_pitch_depth(fm_lfo_pitch_depth),
        .fm_lfo_amp_depth(fm_lfo_amp_depth),
        .fm_pitch_mod_sensitivity(fm_pitch_mod_sensitivity),
        .fm_operator_write_valid(fm_operator_write_valid),
        .fm_operator_index(fm_operator_index),
        .fm_operator_enable(fm_operator_enable),
        .fm_operator_fixed(fm_operator_fixed),
        .fm_operator_frequency(fm_operator_frequency),
        .fm_operator_output_gain(fm_operator_output_gain),
        .fm_envelope_write_valid(fm_envelope_write_valid),
        .fm_envelope_operator(fm_envelope_operator),
        .fm_envelope_stage(fm_envelope_stage),
        .fm_envelope_coefficient(fm_envelope_coefficient),
        .fm_envelope_level(fm_envelope_level),
        .fm_rate_scaling(fm_rate_scaling),
        .fm_velocity_sensitivity(fm_velocity_sensitivity),
        .fm_amp_mod_sensitivity(fm_amp_mod_sensitivity),
        .fm_scaling_write_valid(fm_scaling_write_valid),
        .fm_scaling_operator(fm_scaling_operator),
        .fm_scaling_breakpoint(fm_scaling_breakpoint),
        .fm_scaling_left_depth(fm_scaling_left_depth),
        .fm_scaling_right_depth(fm_scaling_right_depth),
        .fm_scaling_left_curve(fm_scaling_left_curve),
        .fm_scaling_right_curve(fm_scaling_right_curve),
        .fm_pitch_envelope_write_valid(fm_pitch_envelope_write_valid),
        .fm_pitch_envelope_stage(fm_pitch_envelope_stage),
        .fm_pitch_envelope_coefficient(fm_pitch_envelope_coefficient),
        .fm_pitch_envelope_ratio(fm_pitch_envelope_ratio),
        .fm_algorithm_flags_write_valid(fm_algorithm_flags_write_valid),
        .fm_algorithm_flags(fm_algorithm_flags),
        .fm_note_gain_write_valid(fm_note_gain_write_valid),
        .fm_note_gain_address(fm_note_gain_address),
        .fm_note_gain_value(fm_note_gain_value),
        .fm_patch_commit(fm_patch_commit),
        .packet_received(uart_packet_received), .packet_error(uart_packet_error),
        .current_preset(current_preset)
    );

    wire sample_tick;
    // FM build keeps the event path deterministic: physical USB-MIDI gets
    // priority for the one cycle in which it is presented, while queued UART
    // notes remain pending until accepted.  The separate additive branch
    // retains the experimental chord/strum scheduler.
    wire [31:0] synth_midi_event_data = midi_event_valid
        ? midi_event_latched : uart_midi_event_data;
    wire synth_midi_event_valid = midi_event_valid | uart_midi_event_valid;
    wire [6:0] synth_midi_event_brightness = midi_event_valid
        ? midi_event_latched[6:0] : uart_midi_event_brightness;
    wire synth_midi_event_ready;
    assign raw_midi_event_ready = synth_midi_event_ready & ~uart_midi_event_valid;
    assign uart_midi_event_ready = synth_midi_event_ready & ~midi_event_valid;

    wire signed [15:0] synth_sample;
    wire [4:0] active_voice_count;
    fm_poly_synth #(.VOICE_COUNT(16)) u_fm_poly_synth (
        .clk(clk), .reset_n(audio_reset_n), .sample_tick(sample_tick),
        .midi_event_data(synth_midi_event_data),
        .midi_event_valid(synth_midi_event_valid),
        .midi_event_brightness(synth_midi_event_brightness),
        .midi_event_ready(synth_midi_event_ready),
        .fm_algorithm(fm_algorithm), .fm_feedback(fm_feedback),
        .fm_osc_sync(fm_osc_sync), .fm_transpose(fm_transpose),
        .fm_lfo_waveform(fm_lfo_waveform), .fm_lfo_speed(fm_lfo_speed),
        .fm_lfo_delay(fm_lfo_delay), .fm_lfo_sync(fm_lfo_sync),
        .fm_lfo_pitch_depth(fm_lfo_pitch_depth),
        .fm_lfo_amp_depth(fm_lfo_amp_depth),
        .fm_pitch_mod_sensitivity(fm_pitch_mod_sensitivity),
        .fm_operator_write_valid(fm_operator_write_valid),
        .fm_operator_index(fm_operator_index),
        .fm_operator_enable(fm_operator_enable),
        .fm_operator_fixed(fm_operator_fixed),
        .fm_operator_frequency(fm_operator_frequency),
        .fm_operator_output_gain(fm_operator_output_gain),
        .fm_envelope_write_valid(fm_envelope_write_valid),
        .fm_envelope_operator(fm_envelope_operator),
        .fm_envelope_stage(fm_envelope_stage),
        .fm_envelope_coefficient(fm_envelope_coefficient),
        .fm_envelope_level(fm_envelope_level),
        .fm_rate_scaling(fm_rate_scaling),
        .fm_velocity_sensitivity(fm_velocity_sensitivity),
        .fm_amp_mod_sensitivity(fm_amp_mod_sensitivity),
        .fm_scaling_write_valid(fm_scaling_write_valid),
        .fm_scaling_operator(fm_scaling_operator),
        .fm_scaling_breakpoint(fm_scaling_breakpoint),
        .fm_scaling_left_depth(fm_scaling_left_depth),
        .fm_scaling_right_depth(fm_scaling_right_depth),
        .fm_scaling_left_curve(fm_scaling_left_curve),
        .fm_scaling_right_curve(fm_scaling_right_curve),
        .fm_pitch_envelope_write_valid(fm_pitch_envelope_write_valid),
        .fm_pitch_envelope_stage(fm_pitch_envelope_stage),
        .fm_pitch_envelope_coefficient(fm_pitch_envelope_coefficient),
        .fm_pitch_envelope_ratio(fm_pitch_envelope_ratio),
        .fm_algorithm_flags_write_valid(fm_algorithm_flags_write_valid),
        .fm_algorithm_flags(fm_algorithm_flags),
        .fm_note_gain_write_valid(fm_note_gain_write_valid),
        .fm_note_gain_address(fm_note_gain_address),
        .fm_note_gain_value(fm_note_gain_value),
        .fm_patch_commit(fm_patch_commit), .pitch_bend(pitch_bend),
        .vibrato_depth(vibrato_depth), .vibrato_rate(vibrato_rate),
        .portamento_time(portamento_time), .sample_out(synth_sample),
        .active_voice_count(active_voice_count)
    );

    wire signed [15:0] effected_sample;
    audio_effects u_audio_effects (
        .clk(clk), .reset_n(audio_reset_n), .sample_tick(sample_tick),
        .dry_sample(synth_sample), .chorus_mix(chorus_mix),
        .reverb_mix(reverb_mix), .sample_out(effected_sample)
    );
    i2s_tx #(.BCLK_HALF_DIV(8)) u_i2s_tx (
        .clk(clk), .reset_n(audio_reset_n),
        .sample_left(effected_sample), .sample_right(effected_sample),
        .sample_tick(sample_tick), .i2s_bclk(i2s_bclk),
        .i2s_lrclk(i2s_lrclk), .i2s_din(i2s_din)
    );

    fm_status_lcd u_fm_status_lcd (
        .clk(clk), .reset_n(audio_reset_n), .sample_tick(sample_tick),
        .audio_sample(effected_sample),
        .active_voice_count(active_voice_count),
        .preset_index(current_preset), .algorithm(fm_algorithm),
        .lcd_resetn(lcd_resetn), .lcd_clk(lcd_clk), .lcd_cs(lcd_cs),
        .lcd_dc(lcd_dc), .lcd_data(lcd_data)
    );

    wire unused_fm_global_write_valid = fm_global_write_valid;
    wire [6:0] unused_delay = delay_mix ^ delay_time;
    wire [4:0] unused_voice_count = active_voice_count;
    wire unused_strum_config = strum_mode ^ strum_interval_ms[0] ^
        strum_gate_ms[0] ^ strum_velocity_speed_depth[0] ^
        strum_velocity_brightness_depth[0];
    wire unused_status = unused_strum_config ^ uart_packet_received ^ uart_packet_error ^
                         usb_debug_reset_n ^ usb_debug_cpu_fetch;
    wire [31:0] unused_usb_debug_cpu_addr = usb_debug_cpu_addr;
endmodule
