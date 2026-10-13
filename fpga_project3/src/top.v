`include "src/generated/tone_params.vh"

// Tang Primer 25K USB-MIDI additive synthesizer.
// USB-A on the Dock is the host port (D+=L6, D-=K6). USB-C remains the
// BL616 JTAG/UART connection and carries the 115200-baud diagnostics.
module top (
    input  wire clk,
    input  wire key_s2,
    input  wire uart_rx_pin,
    output wire uart_tx_pin,
    inout  wire usb_host_dp,
    inout  wire usb_host_dn,
    output wire i2s_bclk,
    output wire i2s_lrclk,
    output wire i2s_din
);

    wire clk_usb_48m;
    wire clk_usb_bram_96m;
    wire usb_pll_lock;
    wire usb_debug_reset_n;
    wire usb_debug_cpu_fetch;
    wire usb_debug_uart_write;
    wire [31:0] usb_debug_cpu_addr;

    reg        usb_pll_lock_meta = 1'b0;
    reg        usb_pll_lock_sync = 1'b0;
    reg        usb_pll_lock_prev = 1'b0;
    reg [31:0] usb_pll_unlock_count = 32'd0;

    // The firmware emits a UART heartbeat every second.  If no UART write is
    // observed for three seconds, reset both the USB soft core and the audio
    // mailbox for 100 ms. This recovers a genuinely stuck CPU/USB transaction
    // without relying on unplugging the keyboard or reconfiguring the FPGA.
    localparam [27:0] USB_ALIVE_TIMEOUT_CYCLES = 28'd150_000_000;
    localparam [22:0] USB_RESET_HOLD_CYCLES = 23'd5_000_000;
    reg [1:0]  usb_uart_activity_sync = 2'b00;
    reg [27:0] usb_alive_timeout_count = 28'd0;
    reg [22:0] usb_reset_hold_count = 23'd0;
    reg        usb_watchdog_reset = 1'b0;

    wire audio_reset_n = ~(key_s2 | usb_watchdog_reset);
    wire usb_reset = key_s2 | ~usb_pll_lock | usb_watchdog_reset;

    usb_pll u_usb_pll (
        .clkin(clk),
        .clkout0(clk_usb_48m),
        .clkout1(clk_usb_bram_96m),
        .lock(usb_pll_lock)
    );

    // Persistent diagnostic counter in the board's 50 MHz clock domain.
    // Unlike the USB host it is not reset when the USB PLL loses lock, so a
    // firmware restart can report whether a PLL/reset event caused it.
    always @(posedge clk) begin
        usb_pll_lock_meta <= usb_pll_lock;
        usb_pll_lock_sync <= usb_pll_lock_meta;
        usb_pll_lock_prev <= usb_pll_lock_sync;
        if (key_s2)
            usb_pll_unlock_count <= 32'd0;
        else if (usb_pll_lock_prev && !usb_pll_lock_sync)
            usb_pll_unlock_count <= usb_pll_unlock_count + 1'b1;
    end

    always @(posedge clk) begin
        usb_uart_activity_sync <= {usb_uart_activity_sync[0],
                                   usb_debug_uart_write};
        if (key_s2 || !usb_pll_lock_sync) begin
            usb_alive_timeout_count <= 28'd0;
            usb_reset_hold_count <= 23'd0;
            usb_watchdog_reset <= 1'b0;
        end else if (usb_watchdog_reset) begin
            if (usb_reset_hold_count == USB_RESET_HOLD_CYCLES - 1'b1) begin
                usb_reset_hold_count <= 23'd0;
                usb_alive_timeout_count <= 28'd0;
                usb_watchdog_reset <= 1'b0;
            end else begin
                usb_reset_hold_count <= usb_reset_hold_count + 1'b1;
            end
        end else if (usb_uart_activity_sync[1]) begin
            usb_alive_timeout_count <= 28'd0;
        end else if (usb_alive_timeout_count ==
                     USB_ALIVE_TIMEOUT_CYCLES - 1'b1) begin
            usb_alive_timeout_count <= 28'd0;
            usb_reset_hold_count <= 23'd0;
            usb_watchdog_reset <= 1'b1;
        end else begin
            usb_alive_timeout_count <= usb_alive_timeout_count + 1'b1;
        end
    end

    wire [31:0] usb_midi_event_data;
    wire        usb_midi_event_toggle;
    reg         midi_event_ack_toggle;
    wire        usb_midi_connected;

    usb_midi_host u_usb_midi_host (
        .clk_48m(clk_usb_48m),
        .clk_cpu_bram_96m(clk_usb_bram_96m),
        .reset(usb_reset),
        .usb_dp(usb_host_dp),
        .usb_dn(usb_host_dn),
        .cpu_uart_tx(uart_tx_pin),
        .cpu_uart_rx(uart_rx_pin),
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

    // Safe multi-clock mailbox receive. Event data stays unchanged from the
    // USB request toggle until this domain returns the acknowledge toggle.
    reg [1:0] midi_request_sync;
    reg [31:0] midi_event_latched;
    reg midi_event_valid;
    wire raw_midi_event_ready;

    always @(posedge clk or negedge audio_reset_n) begin
        if (!audio_reset_n) begin
            midi_request_sync    <= 2'b00;
            midi_event_ack_toggle <= 1'b0;
            midi_event_latched   <= 32'd0;
            midi_event_valid     <= 1'b0;
        end else begin
            midi_request_sync <= {midi_request_sync[0], usb_midi_event_toggle};
            midi_event_valid <= 1'b0;
            if ((midi_request_sync[1] != midi_event_ack_toggle) &&
                raw_midi_event_ready) begin
                midi_event_latched <= usb_midi_event_data;
                midi_event_valid <= 1'b1;
                midi_event_ack_toggle <= midi_request_sync[1];
            end
        end
    end

    wire sample_tick;
    wire signed [15:0] synth_sample;
    wire signed [15:0] effected_sample;
    wire [4:0] active_voice_count;

    wire [13:0] pitch_bend;
    wire [6:0]  vibrato_depth;
    wire [6:0]  vibrato_rate;
    wire [6:0]  portamento_time;
    wire [6:0]  chorus_mix;
    wire [6:0]  delay_mix;
    wire [6:0]  delay_time;
    wire [6:0]  reverb_mix;

    midi_performance_control u_midi_performance_control (
        .clk(clk),
        .reset_n(audio_reset_n),
        .midi_event_data(midi_event_latched),
        .midi_event_valid(midi_event_valid),
        .pitch_bend(pitch_bend),
        .vibrato_depth(vibrato_depth),
        .vibrato_rate(vibrato_rate),
        .portamento_time(portamento_time),
        .chorus_mix(chorus_mix),
        .delay_mix(delay_mix),
        .delay_time(delay_time),
        .reverb_mix(reverb_mix)
    );

    // The BL616 UART remains connected to the USB soft CPU for text logs.
    // Its RX signal is also decoded here, so the same 115200-baud COM port
    // can download additive timbre and ADSR settings from the PC editor.
    wire [31:0] amp_attack_samples;
    wire [31:0] amp_attack_phase_inc;
    wire [31:0] amp_decay_samples;
    wire [31:0] amp_decay_phase_inc;
    wire [15:0] amp_sustain_q16;
    wire [31:0] amp_release_samples;
    wire [31:0] amp_release_phase_inc;
    wire [31:0] pitch_envelope_samples;
    wire [31:0] pitch_envelope_phase_inc;
    wire        envelope_write_valid;
    wire [1:0]  envelope_write_table;
    wire [7:0]  envelope_write_address;
    wire [15:0] envelope_write_value;
    wire        harmonic_write_valid;
    wire        harmonic_write_decay;
    wire [1:0]  harmonic_write_anchor;
    wire [3:0]  harmonic_write_index;
    wire [23:0] harmonic_write_value;
    wire        harmonic_commit;
    wire        filter_write_valid;
    wire [1:0]  filter_write_anchor;
    wire [5:0]  filter_write_address;
    wire [7:0]  filter_write_value;
    wire        filter_time_write_valid;
    wire [1:0]  filter_time_write_anchor;
    wire [31:0] filter_time_write_phase_inc;
    wire        filter_enable;
    wire        filter_two_pole;
    wire        note_gain_write_valid;
    wire [6:0]  note_gain_write_address;
    wire [15:0] note_gain_write_value;
    wire        note_gain_commit;
    wire        strum_mode;
    wire [15:0] strum_interval_ms;
    wire [15:0] strum_gate_ms;
    wire [6:0]  strum_velocity_speed_depth;
    wire [6:0]  strum_velocity_brightness_depth;
    wire [31:0] uart_midi_event_data;
    wire        uart_midi_event_valid;
    wire [6:0]  uart_midi_event_brightness;
    wire        uart_midi_event_ready;
    wire        uart_packet_received;
    wire        uart_packet_error;

    synth_uart_control #(
        .AMP_ATTACK_SAMPLES(`TONE_AMP_ATTACK_SAMPLES),
        .AMP_ATTACK_PHASE_INC(`TONE_AMP_ATTACK_PHASE_INC),
        .AMP_DECAY_SAMPLES(`TONE_AMP_DECAY_SAMPLES),
        .AMP_DECAY_PHASE_INC(`TONE_AMP_DECAY_PHASE_INC),
        .AMP_SUSTAIN_Q16(`TONE_AMP_SUSTAIN_Q16),
        .AMP_RELEASE_SAMPLES(`TONE_AMP_RELEASE_SAMPLES),
        .AMP_RELEASE_PHASE_INC(`TONE_AMP_RELEASE_PHASE_INC),
        .PITCH_ENVELOPE_SAMPLES(`TONE_PITCH_ENVELOPE_SAMPLES),
        .PITCH_ENVELOPE_PHASE_INC(`TONE_PITCH_ENVELOPE_PHASE_INC),
        .FILTER_ENABLE(`TONE_DYNAMIC_FILTER_ENABLE),
        .FILTER_TWO_POLE(`TONE_DYNAMIC_FILTER_TWO_POLE)
    ) u_synth_uart_control (
        .clk(clk),
        .reset_n(audio_reset_n),
        .uart_rx_pin(uart_rx_pin),
        .amp_attack_samples(amp_attack_samples),
        .amp_attack_phase_inc(amp_attack_phase_inc),
        .amp_decay_samples(amp_decay_samples),
        .amp_decay_phase_inc(amp_decay_phase_inc),
        .amp_sustain_q16(amp_sustain_q16),
        .amp_release_samples(amp_release_samples),
        .amp_release_phase_inc(amp_release_phase_inc),
        .pitch_envelope_samples(pitch_envelope_samples),
        .pitch_envelope_phase_inc(pitch_envelope_phase_inc),
        .envelope_write_valid(envelope_write_valid),
        .envelope_write_table(envelope_write_table),
        .envelope_write_address(envelope_write_address),
        .envelope_write_value(envelope_write_value),
        .harmonic_write_valid(harmonic_write_valid),
        .harmonic_write_decay(harmonic_write_decay),
        .harmonic_write_anchor(harmonic_write_anchor),
        .harmonic_write_index(harmonic_write_index),
        .harmonic_write_value(harmonic_write_value),
        .harmonic_commit(harmonic_commit),
        .filter_write_valid(filter_write_valid),
        .filter_write_anchor(filter_write_anchor),
        .filter_write_address(filter_write_address),
        .filter_write_value(filter_write_value),
        .filter_time_write_valid(filter_time_write_valid),
        .filter_time_write_anchor(filter_time_write_anchor),
        .filter_time_write_phase_inc(filter_time_write_phase_inc),
        .filter_enable(filter_enable),
        .filter_two_pole(filter_two_pole),
        .note_gain_write_valid(note_gain_write_valid),
        .note_gain_write_address(note_gain_write_address),
        .note_gain_write_value(note_gain_write_value),
        .note_gain_commit(note_gain_commit),
        .strum_mode(strum_mode),
        .strum_interval_ms(strum_interval_ms),
        .strum_gate_ms(strum_gate_ms),
        .strum_velocity_speed_depth(strum_velocity_speed_depth),
        .strum_velocity_brightness_depth(strum_velocity_brightness_depth),
        .midi_event_data(uart_midi_event_data),
        .midi_event_valid(uart_midi_event_valid),
        .midi_event_brightness(uart_midi_event_brightness),
        .midi_event_ready(uart_midi_event_ready),
        .packet_received(uart_packet_received),
        .packet_error(uart_packet_error)
    );

    wire [31:0] strum_midi_event_data;
    wire        strum_midi_event_valid;
    wire        strum_midi_event_ready;
    wire [6:0]  strum_midi_event_brightness;
    wire [31:0] synth_midi_event_data;
    wire        synth_midi_event_valid;
    wire        synth_midi_event_ready;
    wire [6:0]  synth_midi_event_brightness;
    wire [2:0]  held_note_count;
    wire        strum_active;
    wire [6:0]  synth_portamento_time = strum_mode ? 7'd0
                                                   : portamento_time;

    // Keep performance interpretation separate from sound generation.  The
    // controller is transparent in normal mode; in strum mode it turns a held
    // chord plus Pad 1/Pad 2 into timed low-to-high/high-to-low MIDI events.
    midi_strum_controller u_midi_strum_controller (
        .clk(clk),
        .reset_n(audio_reset_n),
        .sample_tick(sample_tick),
        .midi_connected(usb_midi_connected),
        .raw_event_data(midi_event_latched),
        .raw_event_valid(midi_event_valid),
        .raw_event_ready(raw_midi_event_ready),
        .synth_event_data(strum_midi_event_data),
        .synth_event_valid(strum_midi_event_valid),
        .synth_event_brightness(strum_midi_event_brightness),
        .synth_event_ready(strum_midi_event_ready),
        .strum_mode(strum_mode),
        .strum_interval_ms(strum_interval_ms),
        .strum_gate_ms(strum_gate_ms),
        .velocity_speed_depth(strum_velocity_speed_depth),
        .velocity_brightness_depth(strum_velocity_brightness_depth),
        .held_note_count(held_note_count),
        .strum_active(strum_active)
    );

    // UART playback is injected after the guitar-strum controller.  This
    // means a PC song remains ordinary note playback even if the hardware
    // keyboard is currently in strum mode.  Live USB events get priority so
    // keys, MODE volume and the performance controls remain responsive even
    // during dense PC playback.  A UART event is held until the live-input
    // slot is free, therefore the lower priority source is not discarded.
    assign synth_midi_event_data = strum_midi_event_valid
                                 ? strum_midi_event_data
                                 : uart_midi_event_data;
    assign synth_midi_event_valid = strum_midi_event_valid
                                  | uart_midi_event_valid;
    assign synth_midi_event_brightness = strum_midi_event_valid
                                       ? strum_midi_event_brightness
                                       : uart_midi_event_brightness;
    assign strum_midi_event_ready = synth_midi_event_ready
                                  & strum_midi_event_valid;
    assign uart_midi_event_ready = synth_midi_event_ready
                                 & ~strum_midi_event_valid;

    // Twelve voices is the timing-safe 25K profile.  The engine itself
    // remains parameterised; Mega 60K can raise this to 32 after widening the
    // voice index and increasing the two-lane render budget.
    midi_poly_synth #(.VOICE_COUNT(12)) u_midi_poly_synth (
        .clk(clk),
        .reset_n(audio_reset_n),
        .sample_tick(sample_tick),
        .midi_event_data(synth_midi_event_data),
        .midi_event_valid(synth_midi_event_valid),
        .midi_event_brightness(synth_midi_event_brightness),
        .midi_event_ready(synth_midi_event_ready),
        .amp_attack_samples(amp_attack_samples),
        .amp_attack_phase_inc(amp_attack_phase_inc),
        .amp_decay_samples(amp_decay_samples),
        .amp_decay_phase_inc(amp_decay_phase_inc),
        .amp_sustain_q16(amp_sustain_q16),
        .amp_release_samples(amp_release_samples),
        .amp_release_phase_inc(amp_release_phase_inc),
        .pitch_envelope_phase_inc(pitch_envelope_phase_inc),
        .envelope_write_valid(envelope_write_valid),
        .envelope_write_table(envelope_write_table),
        .envelope_write_address(envelope_write_address),
        .envelope_write_value(envelope_write_value),
        .harmonic_write_valid(harmonic_write_valid),
        .harmonic_write_decay(harmonic_write_decay),
        .harmonic_write_anchor(harmonic_write_anchor),
        .harmonic_write_index(harmonic_write_index),
        .harmonic_write_value(harmonic_write_value),
        .harmonic_commit(harmonic_commit),
        .filter_write_valid(filter_write_valid),
        .filter_write_anchor(filter_write_anchor),
        .filter_write_address(filter_write_address),
        .filter_write_value(filter_write_value),
        .filter_time_write_valid(filter_time_write_valid),
        .filter_time_write_anchor(filter_time_write_anchor),
        .filter_time_write_phase_inc(filter_time_write_phase_inc),
        .filter_enable(filter_enable),
        .filter_two_pole(filter_two_pole),
        .note_gain_write_valid(note_gain_write_valid),
        .note_gain_write_address(note_gain_write_address),
        .note_gain_write_value(note_gain_write_value),
        .note_gain_commit(note_gain_commit),
        .pitch_bend(pitch_bend),
        .vibrato_depth(vibrato_depth),
        .vibrato_rate(vibrato_rate),
        .portamento_time(synth_portamento_time),
        .sample_out(synth_sample),
        .active_voice_count(active_voice_count)
    );

    audio_effects u_audio_effects (
        .clk(clk),
        .reset_n(audio_reset_n),
        .sample_tick(sample_tick),
        .dry_sample(synth_sample),
        .chorus_mix(chorus_mix),
        .reverb_mix(reverb_mix),
        .sample_out(effected_sample)
    );

    // 50 MHz / (2 * 8) = 3.125 MHz BCLK; 64 BCLK/frame = 48.828125 kHz.
    i2s_tx #(.BCLK_HALF_DIV(8)) u_i2s_tx (
        .clk(clk),
        .reset_n(audio_reset_n),
        .sample_left(effected_sample),
        .sample_right(effected_sample),
        .sample_tick(sample_tick),
        .i2s_bclk(i2s_bclk),
        .i2s_lrclk(i2s_lrclk),
        .i2s_din(i2s_din)
    );

    wire [4:0] unused_active_voice_count = active_voice_count;
    wire [2:0] unused_held_note_count = held_note_count;
    wire unused_strum_active = strum_active;
    wire unused_uart_packet_received = uart_packet_received;
    wire unused_uart_packet_error = uart_packet_error;
    wire unused_usb_debug_reset_n = usb_debug_reset_n;
    wire unused_usb_debug_cpu_fetch = usb_debug_cpu_fetch;
    wire [31:0] unused_usb_debug_cpu_addr = usb_debug_cpu_addr;

endmodule
