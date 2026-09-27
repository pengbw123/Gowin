`include "src/generated/tone_params.vh"

// One-note wavetable synthesizer with 1..8 detuned unison oscillators.
// Signal path: unison wavetable DDS -> dynamic one-pole IIR -> amplitude ADSR.
module note_synth (
    input  wire              clk,
    input  wire              reset_n,
    input  wire              sample_tick,
    input  wire              gate,

    input  wire [31:0]       amp_attack_samples,
    input  wire [31:0]       amp_attack_phase_inc,
    input  wire [31:0]       amp_decay_samples,
    input  wire [31:0]       amp_decay_phase_inc,
    input  wire [15:0]       amp_sustain_q16,
    input  wire [31:0]       amp_release_samples,
    input  wire [31:0]       amp_release_phase_inc,

    input  wire [31:0]       filter_attack_samples,
    input  wire [31:0]       filter_attack_phase_inc,
    input  wire [31:0]       filter_decay_samples,
    input  wire [31:0]       filter_decay_phase_inc,
    input  wire [15:0]       filter_sustain_q16,
    input  wire [31:0]       filter_release_samples,
    input  wire [31:0]       filter_release_phase_inc,
    input  wire              filter_follow_amp,

    input  wire [3:0]        unison_count,
    input  wire [31:0]       unison_detune_step,
    output reg signed [15:0] sample
);

    reg        [15:0] filter_alpha_lut [0:255];
    reg        [15:0] amp_attack_curve [0:255];
    reg        [15:0] amp_decay_curve [0:255];
    reg        [15:0] amp_release_curve [0:255];
    reg        [15:0] filter_attack_curve [0:255];
    reg        [15:0] filter_decay_curve [0:255];
    reg        [15:0] filter_release_curve [0:255];

    initial begin
        $readmemh(`TONE_FILTER_LUT_MEM, filter_alpha_lut);
        $readmemh(`TONE_AMP_ATTACK_CURVE_MEM, amp_attack_curve);
        $readmemh(`TONE_AMP_DECAY_CURVE_MEM, amp_decay_curve);
        $readmemh(`TONE_AMP_RELEASE_CURVE_MEM, amp_release_curve);
        $readmemh(`TONE_FILTER_ATTACK_CURVE_MEM, filter_attack_curve);
        $readmemh(`TONE_FILTER_DECAY_CURVE_MEM, filter_decay_curve);
        $readmemh(`TONE_FILTER_RELEASE_CURVE_MEM, filter_release_curve);
    end

    wire signed [15:0] oscillator_sample;
    unison_oscillator u_unison_oscillator (
        .clk(clk), .reset_n(reset_n), .sample_tick(sample_tick), .gate(gate),
        .base_phase_increment(`TONE_DEFAULT_PHASE_INC),
        .voice_count(unison_count), .detune_step(unison_detune_step),
        .sample_out(oscillator_sample)
    );

    function signed [15:0] saturate16;
        input signed [31:0] value;
        begin
            if (value > 32'sd32767)
                saturate16 = 16'sd32767;
            else if (value < -32'sd32768)
                saturate16 = -16'sd32768;
            else
                saturate16 = value[15:0];
        end
    endfunction

    wire [7:0] amp_curve_address;
    wire [15:0] amplitude_envelope;
    reg [15:0] amp_attack_curve_value;
    reg [15:0] amp_decay_curve_value;
    reg [15:0] amp_release_curve_value;
    adsr_envelope u_amplitude_envelope (
        .clk                 (clk), .reset_n(reset_n), .sample_tick(sample_tick), .gate(gate),
        .attack_samples      (amp_attack_samples), .attack_phase_inc(amp_attack_phase_inc),
        .decay_samples       (amp_decay_samples), .decay_phase_inc(amp_decay_phase_inc),
        .sustain_level       (amp_sustain_q16),
        .release_samples     (amp_release_samples), .release_phase_inc(amp_release_phase_inc),
        .attack_curve_value  (amp_attack_curve_value), .decay_curve_value(amp_decay_curve_value),
        .release_curve_value (amp_release_curve_value), .curve_address(amp_curve_address),
        .level               (amplitude_envelope)
    );

    wire [7:0] filter_curve_address;
    wire [15:0] independent_filter_envelope;
    reg  [15:0] filter_attack_curve_value;
    reg  [15:0] filter_decay_curve_value;
    reg  [15:0] filter_release_curve_value;
    adsr_envelope u_filter_envelope (
        .clk                 (clk), .reset_n(reset_n), .sample_tick(sample_tick), .gate(gate),
        .attack_samples      (filter_attack_samples), .attack_phase_inc(filter_attack_phase_inc),
        .decay_samples       (filter_decay_samples), .decay_phase_inc(filter_decay_phase_inc),
        .sustain_level       (filter_sustain_q16),
        .release_samples     (filter_release_samples), .release_phase_inc(filter_release_phase_inc),
        .attack_curve_value  (filter_attack_curve_value), .decay_curve_value(filter_decay_curve_value),
        .release_curve_value (filter_release_curve_value), .curve_address(filter_curve_address),
        .level               (independent_filter_envelope)
    );

    wire [15:0] selected_filter_envelope = filter_follow_amp
                                              ? amplitude_envelope
                                              : independent_filter_envelope;
    wire [31:0] filter_control_product = selected_filter_envelope * `TONE_FILTER_AMOUNT_Q16;
    wire [15:0] filter_control = filter_control_product >> 16;
    wire [7:0] filter_index_span = `TONE_FILTER_MAX_INDEX - `TONE_FILTER_MIN_INDEX;
    wire [23:0] filter_index_product = filter_control * filter_index_span;
    wire [8:0] filter_index_wide = {1'b0, `TONE_FILTER_MIN_INDEX} + {1'b0, filter_index_product[23:16]};
    wire [7:0] filter_index = filter_index_wide[7:0];
    reg [15:0] filter_alpha;
    wire signed [31:0] filtered_sample;

    dynamic_iir_lpf u_dynamic_iir_lpf (
        .clk(clk), .reset_n(reset_n), .sample_tick(sample_tick),
        .enable(`TONE_FILTER_ENABLE), .sample_in(oscillator_sample),
        .alpha_q16(filter_alpha), .sample_out(filtered_sample)
    );

    reg signed [63:0] amplitude_product;
    reg signed [63:0] gain_product;
    reg signed [31:0] scaled_output;
    always @(*) begin
        amplitude_product = filtered_sample * $signed({1'b0, amplitude_envelope});
        gain_product = (amplitude_product >>> 16) * $signed({1'b0, `TONE_MASTER_GAIN_Q15});
        scaled_output = $signed(gain_product[46:15]);
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            filter_alpha <= 16'd0;
            amp_attack_curve_value <= 0; amp_decay_curve_value <= 0; amp_release_curve_value <= 0;
            filter_attack_curve_value <= 0; filter_decay_curve_value <= 0; filter_release_curve_value <= 0;
            sample <= 16'sd0;
        end else if (sample_tick) begin
            filter_alpha <= filter_alpha_lut[filter_index];
            amp_attack_curve_value <= amp_attack_curve[amp_curve_address];
            amp_decay_curve_value <= amp_decay_curve[amp_curve_address];
            amp_release_curve_value <= amp_release_curve[amp_curve_address];
            filter_attack_curve_value <= filter_attack_curve[filter_curve_address];
            filter_decay_curve_value <= filter_decay_curve[filter_curve_address];
            filter_release_curve_value <= filter_release_curve[filter_curve_address];
            sample <= saturate16(scaled_output);
        end
    end

endmodule
