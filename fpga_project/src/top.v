`include "src/generated/tone_params.vh"

// Tang Primer 25K synthesizer demo.
// S1 plays the note, S2 resets, and the Dock USB-UART changes parameters.
module top (
    input  wire clk,          // Dock oscillator: 50 MHz
    input  wire key_s1,       // Board S1, active high: play/hold note
    input  wire key_s2,       // Board S2, active high: reset
    input  wire uart_rx_pin,  // Dock USB-UART -> FPGA, 115200 8-N-1
    output wire i2s_bclk,
    output wire i2s_lrclk,
    output wire i2s_din
);

    wire reset_n = ~key_s2;
    wire note_gate;
    button_debounce #(.STABLE_CYCLES(50000)) u_s1_debounce (
        .clk(clk), .reset_n(reset_n), .button_in(key_s1), .button_out(note_gate)
    );

    wire [31:0] amp_attack_samples, amp_attack_phase_inc;
    wire [31:0] amp_decay_samples, amp_decay_phase_inc;
    wire [15:0] amp_sustain_q16;
    wire [31:0] amp_release_samples, amp_release_phase_inc;
    wire [31:0] filter_attack_samples, filter_attack_phase_inc;
    wire [31:0] filter_decay_samples, filter_decay_phase_inc;
    wire [15:0] filter_sustain_q16;
    wire [31:0] filter_release_samples, filter_release_phase_inc;
    wire        filter_follow_amp;
    wire [3:0]  unison_count;
    wire [31:0] unison_detune_step;
    wire        uart_packet_received;
    wire        uart_packet_error;

    synth_uart_control #(
        .AMP_ATTACK_SAMPLES       (`TONE_AMP_ATTACK_SAMPLES),
        .AMP_ATTACK_PHASE_INC     (`TONE_AMP_ATTACK_PHASE_INC),
        .AMP_DECAY_SAMPLES        (`TONE_AMP_DECAY_SAMPLES),
        .AMP_DECAY_PHASE_INC      (`TONE_AMP_DECAY_PHASE_INC),
        .AMP_SUSTAIN_Q16          (`TONE_AMP_SUSTAIN_Q16),
        .AMP_RELEASE_SAMPLES      (`TONE_AMP_RELEASE_SAMPLES),
        .AMP_RELEASE_PHASE_INC    (`TONE_AMP_RELEASE_PHASE_INC),
        .FILTER_ATTACK_SAMPLES    (`TONE_FILTER_ATTACK_SAMPLES),
        .FILTER_ATTACK_PHASE_INC  (`TONE_FILTER_ATTACK_PHASE_INC),
        .FILTER_DECAY_SAMPLES     (`TONE_FILTER_DECAY_SAMPLES),
        .FILTER_DECAY_PHASE_INC   (`TONE_FILTER_DECAY_PHASE_INC),
        .FILTER_SUSTAIN_Q16       (`TONE_FILTER_SUSTAIN_Q16),
        .FILTER_RELEASE_SAMPLES   (`TONE_FILTER_RELEASE_SAMPLES),
        .FILTER_RELEASE_PHASE_INC (`TONE_FILTER_RELEASE_PHASE_INC),
        .FILTER_FOLLOW_AMP        (`TONE_FILTER_FOLLOW_AMP),
        .UNISON_COUNT             (3'd1),
        .UNISON_DETUNE_STEP       (32'd80000)
    ) u_synth_uart_control (
        .clk(clk), .reset_n(reset_n), .uart_rx_pin(uart_rx_pin),
        .amp_attack_samples(amp_attack_samples), .amp_attack_phase_inc(amp_attack_phase_inc),
        .amp_decay_samples(amp_decay_samples), .amp_decay_phase_inc(amp_decay_phase_inc),
        .amp_sustain_q16(amp_sustain_q16),
        .amp_release_samples(amp_release_samples), .amp_release_phase_inc(amp_release_phase_inc),
        .filter_attack_samples(filter_attack_samples), .filter_attack_phase_inc(filter_attack_phase_inc),
        .filter_decay_samples(filter_decay_samples), .filter_decay_phase_inc(filter_decay_phase_inc),
        .filter_sustain_q16(filter_sustain_q16),
        .filter_release_samples(filter_release_samples), .filter_release_phase_inc(filter_release_phase_inc),
        .filter_follow_amp(filter_follow_amp),
        .unison_count(unison_count), .unison_detune_step(unison_detune_step),
        .packet_received(uart_packet_received), .packet_error(uart_packet_error)
    );

    wire        sample_tick;
    wire signed [15:0] mono_sample;

    // 50 MHz / (2 * 8) = 3.125 MHz BCLK; 64 BCLKs/frame = 48.828125 kHz.
    i2s_tx #(.BCLK_HALF_DIV(8)) u_i2s_tx (
        .clk(clk), .reset_n(reset_n),
        .sample_left(mono_sample), .sample_right(mono_sample),
        .sample_tick(sample_tick), .i2s_bclk(i2s_bclk),
        .i2s_lrclk(i2s_lrclk), .i2s_din(i2s_din)
    );

    note_synth u_note_synth (
        .clk(clk), .reset_n(reset_n), .sample_tick(sample_tick), .gate(note_gate),
        .amp_attack_samples(amp_attack_samples), .amp_attack_phase_inc(amp_attack_phase_inc),
        .amp_decay_samples(amp_decay_samples), .amp_decay_phase_inc(amp_decay_phase_inc),
        .amp_sustain_q16(amp_sustain_q16),
        .amp_release_samples(amp_release_samples), .amp_release_phase_inc(amp_release_phase_inc),
        .filter_attack_samples(filter_attack_samples), .filter_attack_phase_inc(filter_attack_phase_inc),
        .filter_decay_samples(filter_decay_samples), .filter_decay_phase_inc(filter_decay_phase_inc),
        .filter_sustain_q16(filter_sustain_q16),
        .filter_release_samples(filter_release_samples), .filter_release_phase_inc(filter_release_phase_inc),
        .filter_follow_amp(filter_follow_amp),
        .unison_count(unison_count), .unison_detune_step(unison_detune_step),
        .sample(mono_sample)
    );

endmodule
