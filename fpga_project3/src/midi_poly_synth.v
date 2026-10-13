`include "src/generated/tone_params.vh"

// Sixteen-voice, sixteen-partial additive synthesizer.
//
// Each MIDI note owns an independent phase, ADSR state, velocity and sixteen
// harmonic amplitudes.  A two-lane, continuously-issued arithmetic pipeline
// is time-multiplexed across all voices. Four timbre and dynamic-filter anchors
// (C2/C3/C4/C6)
// are interpolated when a note starts. Notes below/above that range clamp to
// the nearest anchor. Harmonics above 0.45 * sample rate are muted to prevent
// aliasing.
module midi_poly_synth #(
    parameter integer VOICE_COUNT = 16
) (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire [31:0]        midi_event_data,
    input  wire               midi_event_valid,
    input  wire [6:0]         midi_event_brightness,
    output wire               midi_event_ready,

    input  wire [31:0]        amp_attack_samples,
    input  wire [31:0]        amp_attack_phase_inc,
    input  wire [31:0]        amp_decay_samples,
    input  wire [31:0]        amp_decay_phase_inc,
    input  wire [15:0]        amp_sustain_q16,
    input  wire [31:0]        amp_release_samples,
    input  wire [31:0]        amp_release_phase_inc,
    input  wire [31:0]        pitch_envelope_phase_inc,

    input  wire               envelope_write_valid,
    input  wire [1:0]         envelope_write_table,
    input  wire [7:0]         envelope_write_address,
    input  wire [15:0]        envelope_write_value,

    input  wire               harmonic_write_valid,
    input  wire               harmonic_write_decay,
    input  wire [1:0]         harmonic_write_anchor,
    input  wire [3:0]         harmonic_write_index,
    input  wire [23:0]        harmonic_write_value,
    input  wire               harmonic_commit,

    input  wire               filter_write_valid,
    input  wire [1:0]         filter_write_anchor,
    input  wire [5:0]         filter_write_address,
    input  wire [7:0]         filter_write_value,
    input  wire               filter_time_write_valid,
    input  wire [1:0]         filter_time_write_anchor,
    input  wire [31:0]        filter_time_write_phase_inc,
    input  wire               filter_enable,
    input  wire               filter_two_pole,
    input  wire               note_gain_write_valid,
    input  wire [6:0]         note_gain_write_address,
    input  wire [15:0]        note_gain_write_value,
    input  wire               note_gain_commit,

    input  wire [13:0]        pitch_bend,
    input  wire [6:0]         vibrato_depth,
    input  wire [6:0]         vibrato_rate,
    input  wire [6:0]         portamento_time,

    output reg signed [15:0]  sample_out,
    output reg [4:0]          active_voice_count
);

    localparam integer HARMONIC_COUNT = 16;
    localparam [31:0] HARMONIC_PHASE_LIMIT = 32'h73333333; // 0.45 * Fs

    localparam [2:0] ENV_IDLE    = 3'd0;
    localparam [2:0] ENV_ATTACK  = 3'd1;
    localparam [2:0] ENV_DECAY   = 3'd2;
    localparam [2:0] ENV_SUSTAIN = 3'd3;
    localparam [2:0] ENV_RELEASE = 3'd4;

    localparam [5:0] ENGINE_IDLE               = 6'd0;
    localparam [5:0] ENGINE_ALLOC_SCAN         = 6'd1;
    localparam [5:0] ENGINE_ALLOC_COMMIT       = 6'd2;
    localparam [5:0] ENGINE_INIT_LEFT_ADDRESS  = 6'd3;
    localparam [5:0] ENGINE_INIT_LEFT_WAIT     = 6'd4;
    localparam [5:0] ENGINE_INIT_LEFT_CAPTURE  = 6'd5;
    localparam [5:0] ENGINE_INIT_RIGHT_WAIT    = 6'd6;
    localparam [5:0] ENGINE_INIT_WRITE         = 6'd7;
    localparam [5:0] ENGINE_VOICE_PREPARE      = 6'd8;
    localparam [5:0] ENGINE_ENV_MULT           = 6'd13;
    localparam [5:0] ENGINE_VELOCITY_MULT      = 6'd14;
    localparam [5:0] ENGINE_VOICE_ACCUM        = 6'd15;
    localparam [5:0] ENGINE_ENVELOPE_CALC      = 6'd16;
    localparam [5:0] ENGINE_ENVELOPE_APPLY     = 6'd17;
    localparam [5:0] ENGINE_PITCH_APPLY        = 6'd18;
    localparam [5:0] ENGINE_GLIDE_CALC         = 6'd19;
    localparam [5:0] ENGINE_PITCH_MULT         = 6'd20;
    localparam [5:0] ENGINE_INIT_BRIGHTNESS    = 6'd21;
    localparam [5:0] ENGINE_ENVELOPE_FETCH     = 6'd22;
    localparam [5:0] ENGINE_PITCH_ENV_MULT     = 6'd23;
    localparam [5:0] ENGINE_GLIDE_APPLY        = 6'd24;
    localparam [5:0] ENGINE_ENVELOPE_WAIT      = 6'd25;
    localparam [5:0] ENGINE_FILTER_INTERPOLATE = 6'd26;
    localparam [5:0] ENGINE_FILTER_ALPHA_WAIT  = 6'd27;
    localparam [5:0] ENGINE_FILTER_1_MULT      = 6'd28;
    localparam [5:0] ENGINE_FILTER_1_APPLY     = 6'd29;
    localparam [5:0] ENGINE_FILTER_2_MULT      = 6'd30;
    localparam [5:0] ENGINE_FILTER_2_APPLY     = 6'd31;
    localparam [5:0] ENGINE_PIPELINE_ISSUE     = 6'd32;
    localparam [5:0] ENGINE_PIPELINE_DRAIN     = 6'd33;
    localparam [5:0] ENGINE_POST_PREPARE       = 6'd34;
    localparam [5:0] ENGINE_PIPELINE_LOAD      = 6'd35;
    localparam [5:0] ENGINE_OUTPUT_COMMIT      = 6'd36;

    localparam [6:0] DEFAULT_MASTER_VOLUME =
        ((`TONE_MASTER_GAIN_Q15 * 32'd127) + 32'd16384) >> 15;

    reg [31:0] midi_phase_increment [0:127];
    // Attack/decay/release share one 768x16 BSRAM. Pitch keeps one independent
    // 256x16 BSRAM so both values can be fetched in the same engine cycle.
    reg [15:0] amp_envelope_table [0:767];
    reg [15:0] pitch_envelope_ratio [0:255];
    // Q1.16 ratios covering -2..+2 semitones in 1/128-semitone steps.
    // Pitch bend and vibrato share this ROM, so only one phase-increment
    // multiplier is needed for all voices.
    reg [16:0] pitch_ratio_table [0:511];

    // 4 anchors x 16 harmonics. Existing notes copy these values into their
    // private partial state at Note On, so UART edits affect newly triggered
    // notes without disturbing notes that are already sounding.
    reg [15:0] harmonic_amp_active [0:63];

    // Four 64-point log-cutoff envelopes.  A value of 0..255 addresses a
    // logarithmic alpha LUT spanning 80 Hz..12 kHz.
    reg [7:0] filter_envelope_a [0:255];
    reg [7:0] filter_envelope_b [0:255];
    reg [15:0] filter_alpha_table [0:255];
    reg [31:0] filter_anchor_phase_inc [0:3];

    initial begin
        $readmemh("src/generated/midi_phase_inc.mem", midi_phase_increment);
        $readmemh(`TONE_AMP_ENVELOPE_TABLE_MEM, amp_envelope_table);
        $readmemh(`TONE_PITCH_ENVELOPE_MEM, pitch_envelope_ratio);
        $readmemb("src/generated/pitch_ratio_q16.mem", pitch_ratio_table);
        $readmemh("src/generated/additive_harmonic_amp.mem", harmonic_amp_active);
        $readmemh(`TONE_FILTER_ENVELOPE_MEM, filter_envelope_a);
        $readmemh(`TONE_FILTER_ENVELOPE_MEM, filter_envelope_b);
        $readmemh(`TONE_FILTER_ALPHA_MEM, filter_alpha_table);
        filter_anchor_phase_inc[0] = `TONE_FILTER_0_PHASE_INC;
        filter_anchor_phase_inc[1] = `TONE_FILTER_1_PHASE_INC;
        filter_anchor_phase_inc[2] = `TONE_FILTER_2_PHASE_INC;
        filter_anchor_phase_inc[3] = `TONE_FILTER_3_PHASE_INC;
    end

    wire [5:0] harmonic_write_address =
        {harmonic_write_anchor, harmonic_write_index};
    reg [5:0] harmonic_read_address;
    reg [15:0] harmonic_amp_read;
    always @(posedge clk) begin
        // One deterministic synchronous read port is used for both anchors.
        // The previous code indexed the same table at two unrelated addresses
        // in one cycle.  That construct cannot map to a single-port BSRAM and
        // caused non-C notes (which need interpolation) to receive bad data.
        harmonic_amp_read <= harmonic_amp_active[harmonic_read_address];
        if (harmonic_write_valid) begin
            if (!harmonic_write_decay)
                harmonic_amp_active[harmonic_write_address] <= harmonic_write_value[15:0];
        end
        if (filter_write_valid)
            begin
                filter_envelope_a[{filter_write_anchor, filter_write_address}] <=
                    filter_write_value;
                filter_envelope_b[{filter_write_anchor, filter_write_address}] <=
                    filter_write_value;
            end
        if (filter_time_write_valid)
            filter_anchor_phase_inc[filter_time_write_anchor] <=
                filter_time_write_phase_inc;
    end
    wire unused_harmonic_commit = harmonic_commit;

    reg                 voice_active [0:VOICE_COUNT-1];
    reg                 voice_gate [0:VOICE_COUNT-1];
    reg                 voice_key_down [0:VOICE_COUNT-1];
    reg [6:0]           voice_note [0:VOICE_COUNT-1];
    // Q2.15 combined velocity and per-note loudness compensation.  The extra
    // integer bit permits low-note boost without modifying harmonic ratios.
    reg [16:0]          voice_velocity_gain [0:VOICE_COUNT-1];
    reg [31:0]          voice_phase [0:VOICE_COUNT-1];
    reg [31:0]          voice_phase_increment [0:VOICE_COUNT-1];
    reg [31:0]          voice_current_increment [0:VOICE_COUNT-1];
    reg [2:0]           envelope_state [0:VOICE_COUNT-1];
    reg [31:0]          envelope_count [0:VOICE_COUNT-1];
    reg [31:0]          envelope_phase [0:VOICE_COUNT-1];
    reg [15:0]          envelope_start [0:VOICE_COUNT-1];
    reg [15:0]          envelope_level [0:VOICE_COUNT-1];
    reg [31:0]          pitch_envelope_phase [0:VOICE_COUNT-1];

    reg sustain_pedal;
    reg sample_pending;
    reg [31:0] pending_event;
    reg [6:0] pending_event_brightness;
    reg event_pending;
    assign midi_event_ready = ~event_pending;

    reg [5:0] engine_state;
    reg [15:0] engine_watchdog;
    reg [3:0] voice_index;
    reg [3:0] init_harmonic_index;
    reg [4:0] audible_voice_count;
    reg signed [38:0] mix_accumulator;
    reg signed [38:0] final_mix_reg;
    reg signed [31:0] voice_harmonic_accumulator;

    // The packed envelope RAM has two synchronous read ports: amplitude and
    // pitch can therefore be fetched together without four separate BSRAMs.
    reg [31:0] render_envelope_count;
    reg [31:0] render_envelope_phase;
    reg [31:0] render_pitch_envelope_phase;
    reg [2:0]  envelope_state_reg;
    wire [7:0] envelope_curve_address = render_envelope_phase[31:24];
    wire [7:0] pitch_envelope_address = render_pitch_envelope_phase[31:24];
    wire [9:0] amplitude_curve_address = {
        (envelope_state_reg == ENV_ATTACK) ? 2'd0 :
        (envelope_state_reg == ENV_DECAY) ? 2'd1 : 2'd2,
        envelope_curve_address};
    reg [15:0] amplitude_curve_read;
    reg [15:0] pitch_envelope_ratio_read;
    always @(posedge clk) begin
        amplitude_curve_read <= amp_envelope_table[amplitude_curve_address];
        pitch_envelope_ratio_read <= pitch_envelope_ratio[pitch_envelope_address];
        if (envelope_write_valid) begin
            if (envelope_write_table < 3)
                amp_envelope_table[{envelope_write_table, envelope_write_address}] <=
                    envelope_write_value;
            else
                pitch_envelope_ratio[envelope_write_address] <= envelope_write_value;
        end
    end

    reg [31:0] pitch_base_increment_reg;
    reg [48:0] pitch_product_reg;
    reg [3:0] allocation_index;

    // The control engine first prepares a coherent snapshot for every voice.
    // It then submits eight 2-harmonic groups per voice to the tagged pipeline.
    // Consecutive tagged results update the same per-voice accumulator once
    // per clock, which is safe because each registered result is visible to
    // the following clock's accumulation.
    reg [31:0]                  render_increment_snapshot [0:VOICE_COUNT-1];
    reg signed [31:0]           voice_harmonic_sum [0:VOICE_COUNT-1];
    reg signed [31:0]           pipeline_voice_accumulator;
    reg [3:0]                   pipeline_issue_voice;
    reg [2:0]                   pipeline_issue_group;
    reg [3:0]                   render_epoch;

    wire pipeline_amp_write_valid = (engine_state == ENGINE_INIT_BRIGHTNESS);
    wire [15:0] pipeline_amp_write_value;
    reg [31:0]                   pipeline_fundamental_phase;
    reg [31:0]                   pipeline_fundamental_increment;
    reg [31:0]                   pipeline_first_phase;
    reg [35:0]                   pipeline_first_increment;
    wire pipeline_in_valid = (engine_state == ENGINE_PIPELINE_ISSUE);
    wire pipeline_out_valid;
    wire [3:0] pipeline_out_voice;
    wire [2:0] pipeline_out_group;
    wire [3:0] pipeline_out_epoch;
    wire pipeline_out_last_group;
    wire signed [31:0] pipeline_out_group_sample;
    wire pipeline_busy;
    wire signed [32:0] pipeline_voice_sum_next =
        $signed({pipeline_voice_accumulator[31], pipeline_voice_accumulator}) +
        $signed({pipeline_out_group_sample[31], pipeline_out_group_sample});

    harmonic_render_pipeline #(
        .VOICE_INDEX_BITS(4),
        .EPOCH_BITS(4),
        .HARMONIC_PHASE_LIMIT(HARMONIC_PHASE_LIMIT),
        .SINE_MEM(`TONE_QUARTER_SINE_MEM)
    ) u_harmonic_render_pipeline (
        .clk(clk),
        .reset_n(reset_n),
        .amp_write_valid(pipeline_amp_write_valid),
        .amp_write_voice(allocation_index),
        .amp_write_harmonic(init_harmonic_index),
        .amp_write_value(pipeline_amp_write_value),
        .in_valid(pipeline_in_valid),
        .in_voice_id(pipeline_issue_voice),
        .in_group(pipeline_issue_group),
        .in_epoch(render_epoch),
        .in_first_phase(pipeline_first_phase),
        .in_first_increment(pipeline_first_increment),
        .in_fundamental_phase(pipeline_fundamental_phase),
        .in_fundamental_increment(pipeline_fundamental_increment),
        .out_valid(pipeline_out_valid),
        .out_voice_id(pipeline_out_voice),
        .out_group(pipeline_out_group),
        .out_epoch(pipeline_out_epoch),
        .out_last_group(pipeline_out_last_group),
        .out_group_sample(pipeline_out_group_sample),
        .pipeline_busy(pipeline_busy)
    );

    // Per-voice dynamic low-pass state.  The second one-pole stage can be
    // bypassed for a gentler 6 dB/oct response or enabled for 12 dB/oct.
    reg [31:0] voice_filter_phase [0:VOICE_COUNT-1];
    reg [31:0] voice_filter_phase_inc [0:VOICE_COUNT-1];
    reg [1:0] voice_filter_anchor [0:VOICE_COUNT-1];
    reg [4:0] voice_filter_fraction [0:VOICE_COUNT-1];
    reg signed [31:0] voice_filter_y1 [0:VOICE_COUNT-1];
    reg signed [31:0] voice_filter_y2 [0:VOICE_COUNT-1];
    reg signed [31:0] voice_filter_y1_read;
    reg signed [31:0] voice_filter_y2_read;
    reg [7:0] filter_envelope_left_read;
    reg [7:0] filter_envelope_right_read;
    reg [7:0] filter_alpha_address;
    reg [15:0] filter_alpha_read;
    reg signed [48:0] filter_product_reg;
    reg signed [31:0] filter_output_sample;
    wire [1:0] render_filter_right_anchor =
        (voice_filter_anchor[voice_index] == 2'd3)
        ? 2'd3 : voice_filter_anchor[voice_index] + 1'b1;
    wire [5:0] render_filter_point = voice_filter_phase[voice_index][31:26];
    wire [7:0] render_filter_left_address =
        {voice_filter_anchor[voice_index], render_filter_point};
    wire [7:0] render_filter_right_address =
        {render_filter_right_anchor, render_filter_point};
    wire signed [32:0] filter_delta = filter_product_reg[48:16];
    wire signed [32:0] filter_y1_sum =
        $signed({voice_filter_y1_read[31], voice_filter_y1_read}) +
        filter_delta;
    wire signed [32:0] filter_y2_sum =
        $signed({voice_filter_y2_read[31], voice_filter_y2_read}) +
        filter_delta;
    wire signed [31:0] filter_y1_next = filter_y1_sum[31:0];
    wire signed [31:0] filter_y2_next = filter_y2_sum[31:0];
    always @(posedge clk) begin
        filter_envelope_left_read <= filter_envelope_a[render_filter_left_address];
        filter_envelope_right_read <= filter_envelope_b[render_filter_right_address];
        filter_alpha_read <= filter_alpha_table[filter_alpha_address];
    end

    reg signed [48:0] envelope_product_reg;
    wire signed [32:0] envelope_scaled = envelope_product_reg >>> 16;
    reg [16:0] render_velocity_gain;
    reg signed [50:0] velocity_product_reg;
    wire signed [35:0] voice_sample_scaled = velocity_product_reg >>> 15;
    wire signed [38:0] mix_with_voice =
        mix_accumulator + {{3{voice_sample_scaled[35]}}, voice_sample_scaled};

    // One small synchronous ROM supplies an independent keyboard loudness
    // curve.  Harmonic anchors continue to describe timbre only.  allocation_note
    // is stable throughout the voice scan, so the ROM value is ready long
    // before ENGINE_ALLOC_COMMIT consumes it.
    reg [15:0] note_gain_bank_0 [0:127];
    reg [15:0] note_gain_bank_1 [0:127];
    reg        note_gain_active_bank;
    reg [15:0] allocation_note_gain_q14;
    initial begin
        $readmemh("src/generated/midi_note_gain_q14.mem", note_gain_bank_0);
        $readmemh("src/generated/midi_note_gain_q14.mem", note_gain_bank_1);
    end
    always @(posedge clk) begin
        allocation_note_gain_q14 <= note_gain_active_bank
                                  ? note_gain_bank_1[allocation_note]
                                  : note_gain_bank_0[allocation_note];
        // UART always writes the inactive bank.  A complete 128-point update
        // is made visible by one bank toggle, so Note On never observes a
        // partially transferred curve.
        if (note_gain_write_valid) begin
            if (note_gain_active_bank)
                note_gain_bank_0[note_gain_write_address] <= note_gain_write_value;
            else
                note_gain_bank_1[note_gain_write_address] <= note_gain_write_value;
        end
    end

    wire [15:0] allocation_velocity_q15 =
        {1'b0, allocation_velocity, 8'd0} +
        {8'd0, allocation_velocity, 1'b0};
    wire [31:0] allocation_velocity_gain_product =
        allocation_velocity_q15 * allocation_note_gain_q14;
    wire [16:0] allocation_compensated_gain_q15 =
        allocation_velocity_gain_product[30:14];

    // Global LFO.  0..127 RATE maps to roughly 3..9 Hz.  MOD depth maps to
    // 0..half a semitone.  Adding it to the 14-bit bend address means both
    // controls follow the same accurate exponential pitch-ratio table.
    reg [31:0] vibrato_phase;
    reg [8:0] pitch_ratio_address;
    reg [16:0] pitch_ratio_q16;
    wire [31:0] vibrato_rate_extended = {25'd0, vibrato_rate};
    wire [31:0] vibrato_phase_increment = 32'd263882 +
        (vibrato_rate_extended << 12) + (vibrato_rate_extended << 5) +
        (vibrato_rate_extended << 4) + (vibrato_rate_extended << 3) +
        (vibrato_rate_extended << 1) + vibrato_rate_extended;
    wire [7:0] vibrato_ramp = vibrato_phase[30:23];
    wire signed [8:0] vibrato_triangle = vibrato_phase[31]
        ? (9'sd127 - $signed({1'b0, vibrato_ramp}))
        : ($signed({1'b0, vibrato_ramp}) - 9'sd127);
    wire signed [16:0] vibrato_depth_product =
        vibrato_triangle * $signed({1'b0, vibrato_depth});
    wire signed [8:0] vibrato_index_offset = vibrato_depth_product[16:8];
    wire signed [10:0] combined_pitch_index =
        $signed({2'b00, pitch_bend[13:5]}) +
        $signed({{2{vibrato_index_offset[8]}}, vibrato_index_offset});
    wire [8:0] limited_pitch_index =
        (combined_pitch_index < 0) ? 9'd0 :
        (combined_pitch_index > 11'sd511) ? 9'd511 : combined_pitch_index[8:0];

    wire [32:0] modulated_increment_wide = pitch_product_reg[48:16];
    wire [31:0] modulated_increment = modulated_increment_wide[32]
        ? 32'hffffffff : modulated_increment_wide[31:0];

    reg [47:0] pitch_envelope_product_reg;
    wire [35:0] pitch_enveloped_increment_wide =
        pitch_envelope_product_reg[47:12];
    wire [31:0] pitch_enveloped_increment =
        (pitch_enveloped_increment_wide[35:32] != 0)
        ? 32'hffffffff : pitch_enveloped_increment_wide[31:0];

    wire [15:0] selected_curve_read = amplitude_curve_read;

    reg [15:0] render_envelope_level;
    reg [15:0] envelope_curve_reg;
    reg [15:0] envelope_start_reg;
    reg        envelope_gate_reg;
    reg [15:0] calculated_envelope_level;
    reg [15:0] pitch_envelope_ratio_reg;

    wire [16:0] attack_range = 17'd65535 - {1'b0, envelope_start_reg};
    wire [32:0] attack_product = attack_range * envelope_curve_reg;
    wire [16:0] attack_value =
        {1'b0, envelope_start_reg} + attack_product[32:16];

    wire [16:0] decay_range = 17'd65535 - {1'b0, amp_sustain_q16};
    wire [32:0] envelope_decay_product = decay_range * envelope_curve_reg;
    wire [16:0] decay_value = 17'd65535 - envelope_decay_product[32:16];

    wire [15:0] release_remaining = 16'hffff - envelope_curve_reg;
    wire [31:0] release_product = envelope_start_reg * release_remaining;
    wire [15:0] release_value = release_product[31:16];

    function [31:0] saturated_phase_add;
        input [31:0] value;
        input [31:0] increment;
        begin
            saturated_phase_add = (value > (32'hffffffff - increment))
                                ? 32'hffffffff : value + increment;
        end
    endfunction

    // Portamento knob ranges are intentionally musical rather than linear.
    // The chosen right shift makes a complete glide take approximately
    // 0.7, 2.6, 10, 42, 84, 168, 336 or 671 ms at this sample rate.
    function [4:0] portamento_shift;
        input [6:0] value;
        begin
            case (value[6:4])
                3'd0: portamento_shift = 5'd5;
                3'd1: portamento_shift = 5'd7;
                3'd2: portamento_shift = 5'd9;
                3'd3: portamento_shift = 5'd11;
                3'd4: portamento_shift = 5'd12;
                3'd5: portamento_shift = 5'd13;
                3'd6: portamento_shift = 5'd14;
                default: portamento_shift = 5'd15;
            endcase
        end
    endfunction

    // Q0.5 interpolation replaces an expensive divide-by-12 network.  Five
    // fraction bits also give every semitone in the 24-note C4..C6 span its
    // own timbre position instead of making neighbouring notes share one.
    function [4:0] semitone_fraction_q5;
        input [3:0] semitone;
        begin
            case (semitone)
                4'd0:  semitone_fraction_q5 = 5'd0;
                4'd1:  semitone_fraction_q5 = 5'd3;
                4'd2:  semitone_fraction_q5 = 5'd5;
                4'd3:  semitone_fraction_q5 = 5'd8;
                4'd4:  semitone_fraction_q5 = 5'd11;
                4'd5:  semitone_fraction_q5 = 5'd13;
                4'd6:  semitone_fraction_q5 = 5'd16;
                4'd7:  semitone_fraction_q5 = 5'd19;
                4'd8:  semitone_fraction_q5 = 5'd21;
                4'd9:  semitone_fraction_q5 = 5'd24;
                4'd10: semitone_fraction_q5 = 5'd27;
                default: semitone_fraction_q5 = 5'd29;
            endcase
        end
    endfunction

    // Q0.5 position across the 24-semitone C4..C6 interval.  The table is
    // round(semitone * 32 / 24), written explicitly so synthesis needs no
    // divider.  C5 lands exactly at 16/32, the midpoint of C4 and C6.
    function [4:0] two_octave_fraction_q5;
        input [4:0] semitone;
        begin
            case (semitone)
                5'd0:  two_octave_fraction_q5 = 5'd0;
                5'd1:  two_octave_fraction_q5 = 5'd1;
                5'd2:  two_octave_fraction_q5 = 5'd3;
                5'd3:  two_octave_fraction_q5 = 5'd4;
                5'd4:  two_octave_fraction_q5 = 5'd5;
                5'd5:  two_octave_fraction_q5 = 5'd7;
                5'd6:  two_octave_fraction_q5 = 5'd8;
                5'd7:  two_octave_fraction_q5 = 5'd9;
                5'd8:  two_octave_fraction_q5 = 5'd11;
                5'd9:  two_octave_fraction_q5 = 5'd12;
                5'd10: two_octave_fraction_q5 = 5'd13;
                5'd11: two_octave_fraction_q5 = 5'd15;
                5'd12: two_octave_fraction_q5 = 5'd16;
                5'd13: two_octave_fraction_q5 = 5'd17;
                5'd14: two_octave_fraction_q5 = 5'd19;
                5'd15: two_octave_fraction_q5 = 5'd20;
                5'd16: two_octave_fraction_q5 = 5'd21;
                5'd17: two_octave_fraction_q5 = 5'd23;
                5'd18: two_octave_fraction_q5 = 5'd24;
                5'd19: two_octave_fraction_q5 = 5'd25;
                5'd20: two_octave_fraction_q5 = 5'd27;
                5'd21: two_octave_fraction_q5 = 5'd28;
                5'd22: two_octave_fraction_q5 = 5'd29;
                default: two_octave_fraction_q5 = 5'd31;
            endcase
        end
    endfunction

    function [15:0] interpolate_u16;
        input [15:0] left_value;
        input [15:0] right_value;
        input [4:0] fraction;
        reg signed [16:0] delta;
        reg signed [21:0] adjustment;
        reg signed [22:0] result;
        begin
            delta = $signed({1'b0, right_value}) - $signed({1'b0, left_value});
            adjustment = 0;
            if (fraction[0]) adjustment = adjustment + delta;
            if (fraction[1]) adjustment = adjustment + (delta <<< 1);
            if (fraction[2]) adjustment = adjustment + (delta <<< 2);
            if (fraction[3]) adjustment = adjustment + (delta <<< 3);
            if (fraction[4]) adjustment = adjustment + (delta <<< 4);
            result = $signed({1'b0, left_value}) + (adjustment >>> 5);
            interpolate_u16 = result[15:0];
        end
    endfunction

    function [23:0] interpolate_u24;
        input [23:0] left_value;
        input [23:0] right_value;
        input [4:0] fraction;
        reg [5:0] left_weight;
        reg [5:0] right_weight;
        reg [29:0] left_product;
        reg [29:0] right_product;
        reg [30:0] weighted_sum;
        begin
            right_weight = {1'b0, fraction};
            left_weight = 6'd32 - right_weight;
            left_product = left_value * left_weight;
            right_product = right_value * right_weight;
            weighted_sum = left_product + right_product;
            interpolate_u24 = weighted_sum[28:5];
        end
    endfunction

    function [7:0] interpolate_u8;
        input [7:0] left_value;
        input [7:0] right_value;
        input [4:0] fraction;
        reg signed [8:0] delta;
        reg signed [13:0] adjustment;
        reg signed [14:0] result;
        begin
            delta = $signed({1'b0, right_value}) - $signed({1'b0, left_value});
            adjustment = 0;
            if (fraction[0]) adjustment = adjustment + delta;
            if (fraction[1]) adjustment = adjustment + (delta <<< 1);
            if (fraction[2]) adjustment = adjustment + (delta <<< 2);
            if (fraction[3]) adjustment = adjustment + (delta <<< 3);
            if (fraction[4]) adjustment = adjustment + (delta <<< 4);
            result = $signed({1'b0, left_value}) + (adjustment >>> 5);
            interpolate_u8 = result[7:0];
        end
    endfunction

    function [31:0] interpolate_u32;
        input [31:0] left_value;
        input [31:0] right_value;
        input [4:0] fraction;
        reg signed [32:0] delta;
        reg signed [37:0] adjustment;
        reg signed [38:0] result;
        begin
            delta = $signed({1'b0, right_value}) - $signed({1'b0, left_value});
            adjustment = 0;
            if (fraction[0]) adjustment = adjustment + delta;
            if (fraction[1]) adjustment = adjustment + (delta <<< 1);
            if (fraction[2]) adjustment = adjustment + (delta <<< 2);
            if (fraction[3]) adjustment = adjustment + (delta <<< 3);
            if (fraction[4]) adjustment = adjustment + (delta <<< 4);
            result = $signed({1'b0, left_value}) + (adjustment >>> 5);
            interpolate_u32 = result[31:0];
        end
    endfunction

    // 0 dB at MIDI volume 127. Gain does not change with voice count, so adding
    // a note cannot cause compressor pumping. Saturation only protects the
    // final I2S boundary from impossible/worst-case in-phase sums.
    function signed [15:0] apply_volume_and_saturate;
        input signed [38:0] value;
        input [6:0] volume;
        reg signed [40:0] scaled;
        begin
            // Eight monotonic gain bands avoid a wide 39x7 multiplier. The
            // physical knob still spans 0..127; only its audible gain law is
            // quantised, which is preferable to spending a scarce DSP here.
            if (volume == 0)
                scaled = 0;
            else case (volume[6:4])
                3'd0: scaled = value >>> 4;
                3'd1: scaled = value >>> 3;
                3'd2: scaled = (value >>> 3) + (value >>> 4);
                3'd3: scaled = value >>> 2;
                3'd4: scaled = (value >>> 2) + (value >>> 3);
                3'd5: scaled = value >>> 1;
                3'd6: scaled = (value >>> 1) + (value >>> 2);
                default: scaled = value;
            endcase
            if (scaled > 41'sd32767)
                apply_volume_and_saturate = 16'sd32767;
            else if (scaled < -41'sd32768)
                apply_volume_and_saturate = -16'sd32768;
            else
                apply_volume_and_saturate = scaled[15:0];
        end
    endfunction

    integer i;
    integer key_scan_index;
    reg [3:0] allocation_scan_index;
    reg       same_note_found;
    reg [3:0] same_note_index;
    reg       free_voice_found;
    reg [3:0] free_voice_index;
    reg [15:0] quietest_level;
    reg [3:0] quietest_index;
    reg [6:0] allocation_note;
    reg [6:0] allocation_velocity;
    reg [6:0] allocation_brightness;
    reg [1:0] init_anchor_left;
    reg [4:0] init_anchor_fraction;
    reg [1:0] init_anchor_right;
    reg [5:0] init_left_address;
    reg [5:0] init_right_address;
    reg [15:0] init_left_amp;
    reg [15:0] init_interpolated_amp;
    reg [6:0] master_volume_target;
    reg [6:0] master_volume_current;
    reg [7:0] event_status;
    reg [6:0] event_data1;
    reg [6:0] event_data2;
    reg [3:0] last_voice_index;
    reg allocation_legato;
    reg any_key_down;
    reg [31:0] glide_next_increment;
    reg [31:0] glide_current_reg;
    reg [31:0] glide_target_reg;

    wire [31:0] allocation_target_increment =
        midi_phase_increment[allocation_note];
    // A soft pick suppresses upper partials while preserving the fundamental.
    // Shift-only gain bands avoid adding a DSP to the already DSP-full 25K.
    reg [1:0] init_brightness_shift;
    always @* begin
        init_brightness_shift = 2'd0;
        if (init_harmonic_index != 0) begin
            if (allocation_brightness >= 7'd96) begin
                if (init_harmonic_index >= 12)
                    init_brightness_shift = 2'd1;
            end else if (allocation_brightness >= 7'd64) begin
                if (init_harmonic_index >= 14)
                    init_brightness_shift = 2'd2;
                else if (init_harmonic_index >= 8)
                    init_brightness_shift = 2'd1;
            end else if (allocation_brightness >= 7'd32) begin
                if (init_harmonic_index >= 10)
                    init_brightness_shift = 2'd2;
                else if (init_harmonic_index >= 4)
                    init_brightness_shift = 2'd1;
            end else begin
                if (init_harmonic_index >= 13)
                    init_brightness_shift = 2'd3;
                else if (init_harmonic_index >= 7)
                    init_brightness_shift = 2'd2;
                else if (init_harmonic_index >= 2)
                    init_brightness_shift = 2'd1;
            end
        end
    end

    assign pipeline_amp_write_value =
        init_interpolated_amp >> init_brightness_shift;

    always @* begin
        init_anchor_right = (init_anchor_left == 2'd3)
                          ? 2'd3 : init_anchor_left + 1'b1;
        init_left_address = {init_anchor_left, init_harmonic_index};
        init_right_address = {init_anchor_right, init_harmonic_index};
    end

    always @* begin
        any_key_down = 1'b0;
        for (key_scan_index = 0; key_scan_index < VOICE_COUNT;
             key_scan_index = key_scan_index + 1)
            if (voice_key_down[key_scan_index])
                any_key_down = 1'b1;

        if (portamento_time == 0) begin
            glide_next_increment = glide_target_reg;
        end else if (glide_current_reg < glide_target_reg) begin
            if ((glide_target_reg - glide_current_reg) <= 32'd1)
                glide_next_increment = glide_target_reg;
            else
                glide_next_increment = glide_current_reg +
                    (((glide_target_reg - glide_current_reg) >>
                       portamento_shift(portamento_time)) == 0 ? 32'd1 :
                     ((glide_target_reg - glide_current_reg) >>
                       portamento_shift(portamento_time)));
        end else begin
            if ((glide_current_reg - glide_target_reg) <= 32'd1)
                glide_next_increment = glide_target_reg;
            else
                glide_next_increment = glide_current_reg -
                    (((glide_current_reg - glide_target_reg) >>
                       portamento_shift(portamento_time)) == 0 ? 32'd1 :
                     ((glide_current_reg - glide_target_reg) >>
                       portamento_shift(portamento_time)));
        end
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sample_out          <= 16'sd0;
            active_voice_count  <= 5'd0;
            sustain_pedal       <= 1'b0;
            sample_pending      <= 1'b0;
            pending_event       <= 32'd0;
            pending_event_brightness <= 7'd127;
            event_pending       <= 1'b0;
            engine_state        <= ENGINE_IDLE;
            engine_watchdog     <= 16'd0;
            voice_index         <= 4'd0;
            init_harmonic_index <= 4'd0;
            audible_voice_count <= 5'd0;
            mix_accumulator     <= 39'sd0;
            final_mix_reg       <= 39'sd0;
            voice_harmonic_accumulator <= 32'sd0;
            pitch_base_increment_reg <= 32'd0;
            pitch_product_reg   <= 49'd0;
            pitch_envelope_product_reg <= 48'd0;
            vibrato_phase       <= 32'd0;
            pitch_ratio_address <= 9'd256;
            pitch_ratio_q16     <= 17'd65536;
            pipeline_issue_voice <= 4'd0;
            pipeline_issue_group <= 3'd0;
            pipeline_fundamental_phase <= 32'd0;
            pipeline_fundamental_increment <= 32'd0;
            pipeline_first_phase <= 32'd0;
            pipeline_first_increment <= 36'd0;
            pipeline_voice_accumulator <= 32'sd0;
            render_epoch        <= 4'd0;
            filter_alpha_address <= 8'd0;
            filter_product_reg  <= 49'sd0;
            filter_output_sample <= 32'sd0;
            voice_filter_y1_read <= 32'sd0;
            voice_filter_y2_read <= 32'sd0;
            envelope_product_reg <= 49'sd0;
            velocity_product_reg <= 51'sd0;
            render_envelope_level <= 16'd0;
            render_velocity_gain <= 17'd0;
            note_gain_active_bank <= 1'b0;
            envelope_curve_reg <= 16'd0;
            envelope_start_reg <= 16'd0;
            envelope_state_reg <= ENV_IDLE;
            envelope_gate_reg <= 1'b0;
            render_envelope_count <= 32'd0;
            render_envelope_phase <= 32'd0;
            render_pitch_envelope_phase <= 32'd0;
            calculated_envelope_level <= 16'd0;
            pitch_envelope_ratio_reg <= 16'h1000;
            allocation_index      <= 4'd0;
            allocation_scan_index <= 4'd0;
            same_note_found       <= 1'b0;
            same_note_index       <= 4'd0;
            free_voice_found      <= 1'b0;
            free_voice_index      <= 4'd0;
            quietest_level        <= 16'hffff;
            quietest_index        <= 4'd0;
            allocation_note       <= 7'd0;
            allocation_velocity   <= 7'd0;
            allocation_brightness <= 7'd127;
            init_anchor_left       <= 2'd0;
            init_anchor_fraction   <= 5'd0;
            harmonic_read_address  <= 6'd0;
            init_left_amp          <= 16'd0;
            init_interpolated_amp  <= 16'd0;
            master_volume_target   <= DEFAULT_MASTER_VOLUME;
            master_volume_current  <= DEFAULT_MASTER_VOLUME;
            last_voice_index       <= 4'd0;
            allocation_legato      <= 1'b0;
            glide_current_reg      <= 32'd0;
            glide_target_reg       <= 32'd0;

            for (i = 0; i < VOICE_COUNT; i = i + 1) begin
                voice_active[i]          <= 1'b0;
                voice_gate[i]            <= 1'b0;
                voice_key_down[i]        <= 1'b0;
                voice_note[i]            <= 7'd0;
                voice_velocity_gain[i]   <= 17'd0;
                voice_phase[i]           <= 32'd0;
                voice_phase_increment[i] <= 32'd0;
                voice_current_increment[i] <= 32'd0;
                envelope_state[i]        <= ENV_IDLE;
                envelope_count[i]        <= 32'd0;
                envelope_phase[i]        <= 32'd0;
                envelope_start[i]        <= 16'd0;
                envelope_level[i]        <= 16'd0;
                pitch_envelope_phase[i]  <= 32'd0;
                voice_filter_phase[i]    <= 32'd0;
                voice_filter_phase_inc[i] <= 32'hffffffff;
                voice_filter_anchor[i]   <= 2'd0;
                voice_filter_fraction[i] <= 5'd0;
            end
        end else begin
            if (note_gain_commit)
                note_gain_active_bank <= ~note_gain_active_bank;
            // Synchronous state reads allow the two 16x32 filter histories to
            // infer compact RAM instead of 1024 resettable flip-flops.
            voice_filter_y1_read <= voice_filter_y1[voice_index];
            voice_filter_y2_read <= voice_filter_y2[voice_index];
            // A complete worst-case 16-voice render remains below 1024
            // clocks.  If the engine fails to revisit IDLE for about 1.3 ms,
            // recover locally instead of leaving the MIDI mailbox and USB
            // firmware blocked until the FPGA is reconfigured.
            if (engine_state == ENGINE_IDLE)
                engine_watchdog <= 16'd0;
            else if (engine_watchdog != 16'hffff)
                engine_watchdog <= engine_watchdog + 1'b1;

            if (sample_tick) begin
                sample_pending <= 1'b1;
                vibrato_phase <= vibrato_phase + vibrato_phase_increment;
                pitch_ratio_address <= limited_pitch_index;
                pitch_ratio_q16 <= pitch_ratio_table[pitch_ratio_address];
                if (master_volume_current < master_volume_target)
                    master_volume_current <= master_volume_current + 1'b1;
                else if (master_volume_current > master_volume_target)
                    master_volume_current <= master_volume_current - 1'b1;
            end

            if (midi_event_valid && midi_event_ready) begin
                pending_event <= midi_event_data;
                pending_event_brightness <= midi_event_brightness;
                event_pending <= 1'b1;
            end

            // Voice-major scheduling makes the eight groups of one voice
            // return consecutively.  One shared accumulator is therefore
            // sufficient; only the completed H1..H16 sum is written once.
            if (pipeline_out_valid && (pipeline_out_epoch == render_epoch)) begin
                if (pipeline_out_group == 3'd0) begin
                    pipeline_voice_accumulator <= pipeline_out_group_sample;
                end else begin
                    pipeline_voice_accumulator <= pipeline_voice_sum_next[31:0];
                    if (pipeline_out_last_group)
                        voice_harmonic_sum[pipeline_out_voice] <=
                            pipeline_voice_sum_next[31:0];
                end
            end

            case (engine_state)
                ENGINE_IDLE: begin
                    if (event_pending) begin
                        event_pending <= 1'b0;
                        event_status = pending_event[23:16];
                        event_data1  = pending_event[14:8];
                        event_data2  = pending_event[6:0];

                        if (((event_status & 8'hf0) == 8'h90) && (event_data2 != 0)) begin
                            allocation_note       <= event_data1;
                            allocation_velocity   <= event_data2;
                            allocation_brightness <= pending_event_brightness;
                            allocation_legato     <= (portamento_time != 0) &&
                                                     any_key_down &&
                                                     voice_active[last_voice_index];
                            allocation_scan_index <= 4'd0;
                            same_note_found       <= 1'b0;
                            same_note_index       <= 4'd0;
                            free_voice_found      <= 1'b0;
                            free_voice_index      <= 4'd0;
                            quietest_level        <= 16'hffff;
                            quietest_index        <= 4'd0;
                            engine_state          <= ENGINE_ALLOC_SCAN;
                        end else if (((event_status & 8'hf0) == 8'h80) ||
                                     (((event_status & 8'hf0) == 8'h90) && (event_data2 == 0))) begin
                            for (i = 0; i < VOICE_COUNT; i = i + 1) begin
                                if (voice_active[i] && (voice_note[i] == event_data1)) begin
                                    voice_key_down[i] <= 1'b0;
                                    if (!sustain_pedal)
                                        voice_gate[i] <= 1'b0;
                                end
                            end
                        end else if ((event_status & 8'hf0) == 8'hb0) begin
                            if (event_data1 == 7'h14) begin
                                master_volume_target <= event_data2;
                            end else if (event_data1 == 7'd64) begin
                                if (event_data2 >= 7'd64) begin
                                    sustain_pedal <= 1'b1;
                                end else begin
                                    sustain_pedal <= 1'b0;
                                    for (i = 0; i < VOICE_COUNT; i = i + 1)
                                        if (voice_active[i] && !voice_key_down[i])
                                            voice_gate[i] <= 1'b0;
                                end
                            end else if (event_data1 == 7'd123) begin
                                for (i = 0; i < VOICE_COUNT; i = i + 1) begin
                                    voice_key_down[i] <= 1'b0;
                                    voice_gate[i] <= 1'b0;
                                end
                            end
                        end
                    end else if (sample_pending) begin
                        sample_pending      <= 1'b0;
                        voice_index         <= 4'd0;
                        audible_voice_count <= 5'd0;
                        mix_accumulator     <= 39'sd0;
                        render_epoch        <= render_epoch + 1'b1;
                        engine_state        <= ENGINE_VOICE_PREPARE;
                    end
                end

                ENGINE_ALLOC_SCAN: begin
                    if (!same_note_found && voice_active[allocation_scan_index] &&
                        (voice_note[allocation_scan_index] == allocation_note)) begin
                        same_note_found <= 1'b1;
                        same_note_index <= allocation_scan_index;
                    end
                    if (!free_voice_found && !voice_active[allocation_scan_index]) begin
                        free_voice_found <= 1'b1;
                        free_voice_index <= allocation_scan_index;
                    end
                    if (envelope_level[allocation_scan_index] <= quietest_level) begin
                        quietest_level <= envelope_level[allocation_scan_index];
                        quietest_index <= allocation_scan_index;
                    end
                    if (allocation_scan_index == VOICE_COUNT - 1)
                        engine_state <= ENGINE_ALLOC_COMMIT;
                    else
                        allocation_scan_index <= allocation_scan_index + 1'b1;
                end

                ENGINE_ALLOC_COMMIT: begin
                    if (allocation_legato)
                        allocation_index = last_voice_index;
                    else if (same_note_found)
                        allocation_index = same_note_index;
                    else if (free_voice_found)
                        allocation_index = free_voice_index;
                    else
                        allocation_index = quietest_index;

                    voice_active[allocation_index]          <= 1'b1;
                    voice_gate[allocation_index]            <= 1'b1;
                    voice_key_down[allocation_index]        <= 1'b1;
                    voice_note[allocation_index]            <= allocation_note;
                    // Fold velocity and keyboard loudness into one Q2.15 gain
                    // at Note On.  Rendering therefore keeps the same single
                    // per-voice gain multiply and the same sample-cycle cost.
                    voice_velocity_gain[allocation_index] <=
                        allocation_compensated_gain_q15;
                    voice_phase_increment[allocation_index] <= allocation_target_increment;
                    // A pitch envelope is a one-shot modulation started by
                    // every Note On, including a legato note transition.
                    pitch_envelope_phase[allocation_index] <= 32'd0;
                    voice_filter_phase[allocation_index] <= 32'd0;
                    voice_filter_y1[allocation_index] <= 32'sd0;
                    voice_filter_y2[allocation_index] <= 32'sd0;
                    last_voice_index <= allocation_index;
                    if (!allocation_legato) begin
                        voice_phase[allocation_index] <= 32'd0;
                        voice_current_increment[allocation_index] <=
                            allocation_target_increment;
                        envelope_state[allocation_index] <= ENV_ATTACK;
                        envelope_count[allocation_index] <= 32'd0;
                        envelope_phase[allocation_index] <= 32'd0;
                        envelope_start[allocation_index] <= 16'd0;
                        envelope_level[allocation_index] <= 16'd0;
                    end

                    if (allocation_note <= 7'd36) begin
                        init_anchor_left <= 2'd0;
                        init_anchor_fraction <= 5'd0;
                    end else if (allocation_note < 7'd48) begin
                        init_anchor_left <= 2'd0;
                        init_anchor_fraction <= semitone_fraction_q5(allocation_note - 7'd36);
                    end else if (allocation_note < 7'd60) begin
                        init_anchor_left <= 2'd1;
                        init_anchor_fraction <= semitone_fraction_q5(allocation_note - 7'd48);
                    end else if (allocation_note < 7'd84) begin
                        init_anchor_left <= 2'd2;
                        init_anchor_fraction <= two_octave_fraction_q5(allocation_note - 7'd60);
                    end else begin
                        init_anchor_left <= 2'd3;
                        init_anchor_fraction <= 5'd0;
                    end
                    init_harmonic_index <= 4'd0;
                    engine_state <= ENGINE_INIT_LEFT_ADDRESS;
                end

                // Read the two neighbouring anchors on separate clock cycles.
                // Two wait/capture stages account for the synchronous BSRAM
                // read latency and make C as well as all interpolated notes use
                // the same verified data path.
                ENGINE_INIT_LEFT_ADDRESS: begin
                    harmonic_read_address <= init_left_address;
                    engine_state <= ENGINE_INIT_LEFT_WAIT;
                end

                ENGINE_INIT_LEFT_WAIT:
                    engine_state <= ENGINE_INIT_LEFT_CAPTURE;

                ENGINE_INIT_LEFT_CAPTURE: begin
                    init_left_amp <= harmonic_amp_read;
                    harmonic_read_address <= init_right_address;
                    engine_state <= ENGINE_INIT_RIGHT_WAIT;
                end

                ENGINE_INIT_RIGHT_WAIT:
                    engine_state <= ENGINE_INIT_WRITE;

                ENGINE_INIT_WRITE: begin
                    init_interpolated_amp <= interpolate_u16(
                        init_left_amp,
                        harmonic_amp_read,
                        init_anchor_fraction);
                    engine_state <= ENGINE_INIT_BRIGHTNESS;
                end

                ENGINE_INIT_BRIGHTNESS: begin
                    if (init_harmonic_index == HARMONIC_COUNT - 1) begin
                        voice_filter_anchor[allocation_index] <= init_anchor_left;
                        voice_filter_fraction[allocation_index] <= init_anchor_fraction;
                        voice_filter_phase_inc[allocation_index] <= interpolate_u32(
                            filter_anchor_phase_inc[init_anchor_left],
                            filter_anchor_phase_inc[init_anchor_right],
                            init_anchor_fraction);
                        engine_state <= ENGINE_IDLE;
                    end else begin
                        init_harmonic_index <= init_harmonic_index + 1'b1;
                        engine_state <= ENGINE_INIT_LEFT_ADDRESS;
                    end
                end

                ENGINE_VOICE_PREPARE: begin
                    if (!voice_active[voice_index]) begin
                        if (voice_index == VOICE_COUNT - 1) begin
                            pipeline_issue_voice <= 4'd0;
                            pipeline_issue_group <= 3'd0;
                            engine_state <= ENGINE_PIPELINE_LOAD;
                        end else begin
                            voice_index <= voice_index + 1'b1;
                        end
                    end else begin
                        if (envelope_level[voice_index] > 16'd1024)
                            audible_voice_count <= audible_voice_count + 1'b1;
                        render_envelope_level <= envelope_level[voice_index];
                        render_velocity_gain <= voice_velocity_gain[voice_index];
                        envelope_start_reg <= envelope_start[voice_index];
                        envelope_state_reg <= envelope_state[voice_index];
                        envelope_gate_reg <= voice_gate[voice_index];
                        render_envelope_count <= envelope_count[voice_index];
                        render_envelope_phase <= envelope_phase[voice_index];
                        render_pitch_envelope_phase <=
                            pitch_envelope_phase[voice_index];
                        engine_state <= ENGINE_ENVELOPE_WAIT;
                    end
                end

                ENGINE_ENVELOPE_WAIT:
                    engine_state <= ENGINE_ENVELOPE_FETCH;

                // One wait/capture stage accounts for the synchronous curve
                // RAMs.  Each active voice therefore receives the A/D/R and
                // pitch value belonging to its own independent phase.
                ENGINE_ENVELOPE_FETCH: begin
                    envelope_curve_reg <= selected_curve_read;
                    pitch_envelope_ratio_reg <= pitch_envelope_ratio_read;
                    engine_state <= ENGINE_ENVELOPE_CALC;
                end

                ENGINE_ENVELOPE_CALC: begin
                    case (envelope_state_reg)
                        ENV_ATTACK:  calculated_envelope_level <= attack_value[15:0];
                        ENV_DECAY:   calculated_envelope_level <= decay_value[15:0];
                        ENV_SUSTAIN: calculated_envelope_level <= amp_sustain_q16;
                        ENV_RELEASE: calculated_envelope_level <= release_value;
                        default:     calculated_envelope_level <= 16'd0;
                    endcase
                    engine_state <= ENGINE_ENVELOPE_APPLY;
                end

                ENGINE_ENVELOPE_APPLY: begin
                    if (!envelope_gate_reg && (envelope_state_reg != ENV_RELEASE)) begin
                        envelope_state[voice_index] <= ENV_RELEASE;
                        envelope_start[voice_index] <= render_envelope_level;
                        envelope_count[voice_index] <= 32'd0;
                        envelope_phase[voice_index] <= 32'd0;
                    end else begin
                        case (envelope_state_reg)
                            ENV_ATTACK: begin
                                envelope_level[voice_index] <= calculated_envelope_level;
                                if (render_envelope_count >= amp_attack_samples - 1) begin
                                    envelope_level[voice_index] <= 16'hffff;
                                    envelope_state[voice_index] <= ENV_DECAY;
                                    envelope_count[voice_index] <= 32'd0;
                                    envelope_phase[voice_index] <= 32'd0;
                                end else begin
                                    envelope_count[voice_index] <= render_envelope_count + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        render_envelope_phase, amp_attack_phase_inc);
                                end
                            end
                            ENV_DECAY: begin
                                envelope_level[voice_index] <= calculated_envelope_level;
                                if (render_envelope_count >= amp_decay_samples - 1) begin
                                    envelope_level[voice_index] <= amp_sustain_q16;
                                    envelope_state[voice_index] <= ENV_SUSTAIN;
                                    envelope_count[voice_index] <= 32'd0;
                                    envelope_phase[voice_index] <= 32'd0;
                                end else begin
                                    envelope_count[voice_index] <= render_envelope_count + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        render_envelope_phase, amp_decay_phase_inc);
                                end
                            end
                            ENV_SUSTAIN: envelope_level[voice_index] <= amp_sustain_q16;
                            ENV_RELEASE: begin
                                envelope_level[voice_index] <= calculated_envelope_level;
                                if (render_envelope_count >= amp_release_samples - 1) begin
                                    envelope_level[voice_index] <= 16'd0;
                                    envelope_state[voice_index] <= ENV_IDLE;
                                    voice_active[voice_index] <= 1'b0;
                                end else begin
                                    envelope_count[voice_index] <= render_envelope_count + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        render_envelope_phase, amp_release_phase_inc);
                                end
                            end
                            default: begin
                                envelope_level[voice_index] <= 16'd0;
                                voice_active[voice_index] <= 1'b0;
                            end
                        endcase
                    end

                    engine_state <= ENGINE_GLIDE_CALC;
                end

                ENGINE_GLIDE_CALC: begin
                    glide_current_reg <= voice_current_increment[voice_index];
                    glide_target_reg <= voice_phase_increment[voice_index];
                    engine_state <= ENGINE_GLIDE_APPLY;
                end

                ENGINE_GLIDE_APPLY: begin
                    voice_current_increment[voice_index] <= glide_next_increment;
                    pitch_base_increment_reg <= glide_next_increment;
                    engine_state <= ENGINE_PITCH_MULT;
                end

                ENGINE_PITCH_MULT: begin
                    pitch_product_reg <= pitch_base_increment_reg * pitch_ratio_q16;
                    engine_state <= ENGINE_PITCH_ENV_MULT;
                end

                ENGINE_PITCH_ENV_MULT: begin
                    pitch_envelope_product_reg <=
                        modulated_increment * pitch_envelope_ratio_reg;
                    engine_state <= ENGINE_PITCH_APPLY;
                end

                ENGINE_PITCH_APPLY: begin
                    render_increment_snapshot[voice_index] <=
                        pitch_enveloped_increment;
                    if (render_pitch_envelope_phase == 32'hffffffff) begin
                        // Hold the final table value until the ADSR makes the
                        // voice inactive.  If ADSR finishes first, this voice
                        // is no longer rendered and the pitch envelope ends.
                        pitch_envelope_phase[voice_index] <= 32'hffffffff;
                    end else begin
                        pitch_envelope_phase[voice_index] <= saturated_phase_add(
                            render_pitch_envelope_phase,
                            pitch_envelope_phase_inc);
                    end
                    if (voice_index == VOICE_COUNT - 1) begin
                        pipeline_issue_voice <= 4'd0;
                        pipeline_issue_group <= 3'd0;
                        engine_state <= ENGINE_PIPELINE_LOAD;
                    end else begin
                        voice_index <= voice_index + 1'b1;
                        engine_state <= ENGINE_VOICE_PREPARE;
                    end
                end

                ENGINE_PIPELINE_LOAD: begin
                    pipeline_issue_group <= 3'd0;
                    pipeline_fundamental_phase <=
                        voice_phase[pipeline_issue_voice];
                    pipeline_first_phase <= voice_phase[pipeline_issue_voice];
                    pipeline_fundamental_increment <=
                        render_increment_snapshot[pipeline_issue_voice];
                    pipeline_first_increment <= {4'd0,
                        render_increment_snapshot[pipeline_issue_voice]};
                    engine_state <= ENGINE_PIPELINE_ISSUE;
                end

                // Eight two-harmonic groups for one voice are launched on
                // consecutive clocks. The first-harmonic cursor advances by
                // two fundamentals, avoiding a wide group-number multiplier.
                ENGINE_PIPELINE_ISSUE: begin
                    if (pipeline_issue_group == 3'd7) begin
                        if (pipeline_issue_voice == VOICE_COUNT - 1) begin
                            engine_state <= ENGINE_PIPELINE_DRAIN;
                        end else begin
                            pipeline_issue_voice <= pipeline_issue_voice + 1'b1;
                            pipeline_issue_group <= 3'd0;
                            engine_state <= ENGINE_PIPELINE_LOAD;
                        end
                    end else begin
                        pipeline_issue_group <= pipeline_issue_group + 1'b1;
                        pipeline_first_phase <= pipeline_first_phase +
                            (pipeline_fundamental_phase << 1);
                        pipeline_first_increment <= pipeline_first_increment +
                            ({4'd0, pipeline_fundamental_increment} << 1);
                    end
                end

                ENGINE_PIPELINE_DRAIN: begin
                    if (pipeline_out_valid &&
                        (pipeline_out_epoch == render_epoch) &&
                        (pipeline_out_voice == VOICE_COUNT - 1) &&
                        pipeline_out_last_group) begin
                        voice_index <= 4'd0;
                        mix_accumulator <= 39'sd0;
                        engine_state <= ENGINE_POST_PREPARE;
                    end
                end

                ENGINE_POST_PREPARE: begin
                    if (!voice_active[voice_index]) begin
                        if (voice_index == VOICE_COUNT - 1) begin
                            final_mix_reg <= mix_accumulator;
                            engine_state <= ENGINE_OUTPUT_COMMIT;
                        end else begin
                            voice_index <= voice_index + 1'b1;
                        end
                    end else begin
                        voice_harmonic_accumulator <=
                            voice_harmonic_sum[voice_index];
                        render_envelope_level <= envelope_level[voice_index];
                        render_velocity_gain <= voice_velocity_gain[voice_index];
                        voice_phase[voice_index] <= voice_phase[voice_index] +
                            render_increment_snapshot[voice_index];
                        if (filter_enable) begin
                            engine_state <= ENGINE_FILTER_INTERPOLATE;
                        end else begin
                            filter_output_sample <=
                                voice_harmonic_sum[voice_index];
                            engine_state <= ENGINE_ENV_MULT;
                        end
                    end
                end

                ENGINE_FILTER_INTERPOLATE: begin
                    filter_alpha_address <= interpolate_u8(
                        filter_envelope_left_read,
                        filter_envelope_right_read,
                        voice_filter_fraction[voice_index]);
                    voice_filter_phase[voice_index] <= saturated_phase_add(
                        voice_filter_phase[voice_index],
                        voice_filter_phase_inc[voice_index]);
                    engine_state <= ENGINE_FILTER_ALPHA_WAIT;
                end

                ENGINE_FILTER_ALPHA_WAIT:
                    engine_state <= ENGINE_FILTER_1_MULT;

                ENGINE_FILTER_1_MULT: begin
                    filter_product_reg <=
                        ($signed(voice_harmonic_accumulator) -
                         $signed(voice_filter_y1_read)) *
                        $signed({1'b0, filter_alpha_read});
                    engine_state <= ENGINE_FILTER_1_APPLY;
                end

                ENGINE_FILTER_1_APPLY: begin
                    voice_filter_y1[voice_index] <= filter_y1_next;
                    filter_output_sample <= filter_y1_next;
                    if (filter_two_pole)
                        engine_state <= ENGINE_FILTER_2_MULT;
                    else begin
                        filter_output_sample <= filter_y1_next;
                        engine_state <= ENGINE_ENV_MULT;
                    end
                end

                ENGINE_FILTER_2_MULT: begin
                    filter_product_reg <=
                        ($signed(filter_output_sample) -
                         $signed(voice_filter_y2_read)) *
                        $signed({1'b0, filter_alpha_read});
                    engine_state <= ENGINE_FILTER_2_APPLY;
                end

                ENGINE_FILTER_2_APPLY: begin
                    voice_filter_y2[voice_index] <= filter_y2_next;
                    filter_output_sample <= filter_y2_next;
                    engine_state <= ENGINE_ENV_MULT;
                end

                ENGINE_ENV_MULT: begin
                    envelope_product_reg <= filter_output_sample *
                        $signed({1'b0, render_envelope_level});
                    engine_state <= ENGINE_VELOCITY_MULT;
                end

                ENGINE_VELOCITY_MULT: begin
                    velocity_product_reg <= envelope_scaled *
                        $signed({1'b0, render_velocity_gain});
                    engine_state <= ENGINE_VOICE_ACCUM;
                end

                ENGINE_VOICE_ACCUM: begin
                    if (voice_index == VOICE_COUNT - 1) begin
                        final_mix_reg <= mix_with_voice;
                        engine_state <= ENGINE_OUTPUT_COMMIT;
                    end else begin
                        mix_accumulator <= mix_with_voice;
                        voice_index <= voice_index + 1'b1;
                        engine_state <= ENGINE_POST_PREPARE;
                    end
                end

                // Isolate the wide per-voice mix adder from master-gain and
                // saturation logic.  This register cuts the former critical
                // path from mix_accumulator directly to sample_out.
                ENGINE_OUTPUT_COMMIT: begin
                    sample_out <= apply_volume_and_saturate(
                        final_mix_reg, master_volume_current);
                    active_voice_count <= audible_voice_count;
                    engine_state <= ENGINE_IDLE;
                end

                default: engine_state <= ENGINE_IDLE;
            endcase

            if (engine_watchdog == 16'hfffe) begin
                engine_state       <= ENGINE_IDLE;
                engine_watchdog    <= 16'd0;
                sample_pending     <= 1'b0;
                event_pending      <= 1'b0;
                sample_out         <= 16'sd0;
                active_voice_count <= 5'd0;
                sustain_pedal      <= 1'b0;
                for (i = 0; i < VOICE_COUNT; i = i + 1) begin
                    voice_active[i]   <= 1'b0;
                    voice_gate[i]     <= 1'b0;
                    voice_key_down[i] <= 1'b0;
                    envelope_state[i] <= ENV_IDLE;
                    envelope_level[i] <= 16'd0;
                end
            end
        end
    end

endmodule
