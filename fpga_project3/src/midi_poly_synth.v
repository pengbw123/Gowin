`include "src/generated/tone_params.vh"

// Eight-voice, sixteen-partial additive synthesizer.
//
// Each MIDI note owns an independent phase, ADSR state, velocity and sixteen
// decaying harmonic levels.  A single sine ROM and arithmetic pipeline are
// time-multiplexed across all 8 x 16 partials.  Four timbre anchors
// (C2/C3/C4/C6)
// are interpolated when a note starts. Notes below/above that range clamp to
// the nearest anchor. Harmonics above 0.45 * sample rate are muted to prevent
// aliasing.
module midi_poly_synth #(
    parameter integer VOICE_COUNT = 8
) (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire [31:0]        midi_event_data,
    input  wire               midi_event_valid,
    output wire               midi_event_ready,

    input  wire [31:0]        amp_attack_samples,
    input  wire [31:0]        amp_attack_phase_inc,
    input  wire [31:0]        amp_decay_samples,
    input  wire [31:0]        amp_decay_phase_inc,
    input  wire [15:0]        amp_sustain_q16,
    input  wire [31:0]        amp_release_samples,
    input  wire [31:0]        amp_release_phase_inc,

    input  wire               harmonic_write_valid,
    input  wire               harmonic_write_decay,
    input  wire [1:0]         harmonic_write_anchor,
    input  wire [3:0]         harmonic_write_index,
    input  wire [23:0]        harmonic_write_value,
    input  wire               harmonic_commit,

    input  wire [13:0]        pitch_bend,
    input  wire [6:0]         vibrato_depth,
    input  wire [6:0]         vibrato_rate,
    input  wire [6:0]         portamento_time,

    output reg signed [15:0]  sample_out,
    output reg [3:0]          active_voice_count
);

    localparam integer HARMONIC_COUNT = 16;
    localparam [31:0] HARMONIC_PHASE_LIMIT = 32'h73333333; // 0.45 * Fs

    localparam [2:0] ENV_IDLE    = 3'd0;
    localparam [2:0] ENV_ATTACK  = 3'd1;
    localparam [2:0] ENV_DECAY   = 3'd2;
    localparam [2:0] ENV_SUSTAIN = 3'd3;
    localparam [2:0] ENV_RELEASE = 3'd4;

    localparam [4:0] ENGINE_IDLE               = 5'd0;
    localparam [4:0] ENGINE_ALLOC_SCAN         = 5'd1;
    localparam [4:0] ENGINE_ALLOC_COMMIT       = 5'd2;
    localparam [4:0] ENGINE_INIT_LEFT_ADDRESS  = 5'd3;
    localparam [4:0] ENGINE_INIT_LEFT_WAIT     = 5'd4;
    localparam [4:0] ENGINE_INIT_LEFT_CAPTURE  = 5'd5;
    localparam [4:0] ENGINE_INIT_RIGHT_WAIT    = 5'd6;
    localparam [4:0] ENGINE_INIT_WRITE         = 5'd7;
    localparam [4:0] ENGINE_VOICE_PREPARE      = 5'd8;
    localparam [4:0] ENGINE_SINE_ADDRESS       = 5'd9;
    localparam [4:0] ENGINE_SINE_WAIT          = 5'd10;
    localparam [4:0] ENGINE_PARTIAL_MULT       = 5'd11;
    localparam [4:0] ENGINE_PARTIAL_ACCUM      = 5'd12;
    localparam [4:0] ENGINE_ENV_MULT           = 5'd13;
    localparam [4:0] ENGINE_VELOCITY_MULT      = 5'd14;
    localparam [4:0] ENGINE_VOICE_ACCUM        = 5'd15;
    localparam [4:0] ENGINE_ENVELOPE_CALC      = 5'd16;
    localparam [4:0] ENGINE_ENVELOPE_APPLY     = 5'd17;
    localparam [4:0] ENGINE_PITCH_APPLY        = 5'd18;
    localparam [4:0] ENGINE_GLIDE_CALC         = 5'd19;
    localparam [4:0] ENGINE_PITCH_MULT         = 5'd20;

    localparam [6:0] DEFAULT_MASTER_VOLUME =
        ((`TONE_MASTER_GAIN_Q15 * 32'd127) + 32'd16384) >> 15;

    reg signed [15:0] sine_table [0:2047];
    reg [31:0] midi_phase_increment [0:127];
    reg [15:0] attack_curve [0:255];
    reg [15:0] decay_curve [0:255];
    reg [15:0] release_curve [0:255];
    // Q1.16 ratios covering -2..+2 semitones in 1/128-semitone steps.
    // Pitch bend and vibrato share this ROM, so only one phase-increment
    // multiplier is needed for all voices.
    reg [16:0] pitch_ratio_table [0:511];

    // 4 anchors x 16 harmonics. Existing notes copy these values into their
    // private partial state at Note On, so UART edits affect newly triggered
    // notes without disturbing notes that are already sounding.
    reg [15:0] harmonic_amp_active [0:63];
    reg [23:0] harmonic_decay_active [0:63];

    initial begin
        $readmemh("src/generated/additive_sine_2048.mem", sine_table);
        $readmemh("src/generated/midi_phase_inc.mem", midi_phase_increment);
        $readmemh(`TONE_AMP_ATTACK_CURVE_MEM, attack_curve);
        $readmemh(`TONE_AMP_DECAY_CURVE_MEM, decay_curve);
        $readmemh(`TONE_AMP_RELEASE_CURVE_MEM, release_curve);
        $readmemb("src/generated/pitch_ratio_q16.mem", pitch_ratio_table);
        $readmemh("src/generated/additive_harmonic_amp.mem", harmonic_amp_active);
        $readmemh("src/generated/additive_harmonic_decay.mem", harmonic_decay_active);
    end

    wire [5:0] harmonic_write_address =
        {harmonic_write_anchor, harmonic_write_index};
    reg [5:0] harmonic_read_address;
    reg [15:0] harmonic_amp_read;
    reg [23:0] harmonic_decay_read;
    always @(posedge clk) begin
        // One deterministic synchronous read port is used for both anchors.
        // The previous code indexed the same table at two unrelated addresses
        // in one cycle.  That construct cannot map to a single-port BSRAM and
        // caused non-C notes (which need interpolation) to receive bad data.
        harmonic_amp_read <= harmonic_amp_active[harmonic_read_address];
        harmonic_decay_read <= harmonic_decay_active[harmonic_read_address];
        if (harmonic_write_valid) begin
            if (harmonic_write_decay)
                harmonic_decay_active[harmonic_write_address] <= harmonic_write_value;
            else
                harmonic_amp_active[harmonic_write_address] <= harmonic_write_value[15:0];
        end
    end
    wire unused_harmonic_commit = harmonic_commit;

    reg                 voice_active [0:VOICE_COUNT-1];
    reg                 voice_gate [0:VOICE_COUNT-1];
    reg                 voice_key_down [0:VOICE_COUNT-1];
    reg [6:0]           voice_note [0:VOICE_COUNT-1];
    reg [6:0]           voice_velocity [0:VOICE_COUNT-1];
    reg [31:0]          voice_phase [0:VOICE_COUNT-1];
    reg [31:0]          voice_phase_increment [0:VOICE_COUNT-1];
    reg [31:0]          voice_current_increment [0:VOICE_COUNT-1];
    reg [31:0]          voice_glide_step [0:VOICE_COUNT-1];
    reg                 voice_glide_up [0:VOICE_COUNT-1];
    reg [2:0]           envelope_state [0:VOICE_COUNT-1];
    reg [31:0]          envelope_count [0:VOICE_COUNT-1];
    reg [31:0]          envelope_phase [0:VOICE_COUNT-1];
    reg [15:0]          envelope_start [0:VOICE_COUNT-1];
    reg [15:0]          envelope_level [0:VOICE_COUNT-1];

    // Q1.31 level and Q0.24 exponential decay alpha for every partial.
    reg [31:0] partial_level [0:VOICE_COUNT*HARMONIC_COUNT-1];
    reg [23:0] partial_decay [0:VOICE_COUNT*HARMONIC_COUNT-1];

    reg sustain_pedal;
    reg sample_pending;
    reg [31:0] pending_event;
    reg event_pending;
    assign midi_event_ready = ~event_pending;

    reg [4:0] engine_state;
    reg [2:0] voice_index;
    reg [3:0] harmonic_index;
    reg [3:0] init_harmonic_index;
    reg [3:0] audible_voice_count;
    reg signed [38:0] mix_accumulator;
    reg signed [31:0] voice_harmonic_accumulator;

    reg [31:0] fundamental_phase_work;
    reg [31:0] fundamental_increment_work;
    reg [31:0] harmonic_phase_work;
    reg [35:0] harmonic_increment_work;
    reg [31:0] pitch_base_increment_reg;
    reg [48:0] pitch_product_reg;
    reg [10:0] sine_address;
    reg signed [15:0] sine_data;
    reg [2:0] allocation_index;
    always @(posedge clk)
        sine_data <= sine_table[sine_address];

    wire [6:0] partial_address = {voice_index, harmonic_index};
    wire [6:0] init_partial_address = {allocation_index, init_harmonic_index};

    // Synchronous single-port state memories. Every partial of an allocated
    // voice is initialized before that voice can be rendered, so these RAMs
    // intentionally need no expensive 128-word reset network.
    reg [6:0] partial_ram_address;
    reg [31:0] partial_level_read;
    reg [23:0] partial_decay_read;

    reg signed [32:0] partial_product_reg;
    reg [55:0] decay_product_reg;
    wire [31:0] decay_delta = decay_product_reg[55:24];
    wire signed [31:0] partial_sample = partial_product_reg >>> 15;
    wire signed [32:0] harmonic_sum_next =
        $signed({voice_harmonic_accumulator[31], voice_harmonic_accumulator}) +
        $signed({partial_sample[31], partial_sample});

    reg signed [48:0] envelope_product_reg;
    wire signed [32:0] envelope_scaled = envelope_product_reg >>> 16;
    wire [15:0] velocity_gain_q15 = voice_velocity[voice_index] * 9'd258;
    reg signed [49:0] velocity_product_reg;
    wire signed [34:0] voice_sample_scaled = velocity_product_reg >>> 15;
    wire signed [38:0] mix_with_voice =
        mix_accumulator + {{4{voice_sample_scaled[34]}}, voice_sample_scaled};

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

    wire [7:0] envelope_curve_address = envelope_phase[voice_index][31:24];
    wire [15:0] selected_curve =
        (envelope_state[voice_index] == ENV_ATTACK)  ? attack_curve[envelope_curve_address] :
        (envelope_state[voice_index] == ENV_DECAY)   ? decay_curve[envelope_curve_address] :
        (envelope_state[voice_index] == ENV_RELEASE) ? release_curve[envelope_curve_address] : 16'd0;

    reg [15:0] render_envelope_level;
    reg [15:0] envelope_curve_reg;
    reg [15:0] envelope_start_reg;
    reg [2:0]  envelope_state_reg;
    reg        envelope_gate_reg;
    reg [15:0] calculated_envelope_level;

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
        reg [5:0] left_weight;
        reg [5:0] right_weight;
        reg [21:0] left_product;
        reg [21:0] right_product;
        reg [22:0] weighted_sum;
        begin
            // Use an all-unsigned convex combination.  Besides being easier
            // to inspect, this cannot wrap when the right anchor is smaller
            // than the left one (which is common for upper harmonics).
            right_weight = {1'b0, fraction};
            left_weight = 6'd32 - right_weight;
            left_product = left_value * left_weight;
            right_product = right_value * right_weight;
            weighted_sum = left_product + right_product;
            interpolate_u16 = weighted_sum[20:5];
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

    // 0 dB at MIDI volume 127. Gain does not change with voice count, so adding
    // a note cannot cause compressor pumping. Saturation only protects the
    // final I2S boundary from impossible/worst-case in-phase sums.
    function signed [15:0] apply_volume_and_saturate;
        input signed [38:0] value;
        input [6:0] volume;
        reg signed [46:0] volume_product;
        reg signed [46:0] scaled;
        begin
            volume_product = value * $signed({1'b0, volume});
            scaled = volume_product >>> 7;
            if (scaled > 47'sd32767)
                apply_volume_and_saturate = 16'sd32767;
            else if (scaled < -47'sd32768)
                apply_volume_and_saturate = -16'sd32768;
            else
                apply_volume_and_saturate = scaled[15:0];
        end
    endfunction

    integer i;
    integer key_scan_index;
    reg [2:0] allocation_scan_index;
    reg       same_note_found;
    reg [2:0] same_note_index;
    reg       free_voice_found;
    reg [2:0] free_voice_index;
    reg [15:0] quietest_level;
    reg [2:0] quietest_index;
    reg [6:0] allocation_note;
    reg [6:0] allocation_velocity;
    reg [1:0] init_anchor_left;
    reg [4:0] init_anchor_fraction;
    reg [1:0] init_anchor_right;
    reg [5:0] init_left_address;
    reg [5:0] init_right_address;
    reg [15:0] init_left_amp;
    reg [23:0] init_left_decay;
    reg [6:0] master_volume_target;
    reg [6:0] master_volume_current;
    reg [7:0] event_status;
    reg [6:0] event_data1;
    reg [6:0] event_data2;
    reg [2:0] last_voice_index;
    reg allocation_legato;
    reg any_key_down;
    reg [31:0] glide_next_increment;

    wire [31:0] allocation_target_increment =
        midi_phase_increment[allocation_note];
    wire [31:0] legato_start_increment =
        voice_current_increment[last_voice_index];
    wire [31:0] allocation_glide_difference =
        (allocation_target_increment >= legato_start_increment)
        ? allocation_target_increment - legato_start_increment
        : legato_start_increment - allocation_target_increment;
    wire [31:0] allocation_glide_step_shifted =
        allocation_glide_difference >> portamento_shift(portamento_time);
    wire [31:0] allocation_glide_step =
        (allocation_glide_step_shifted == 0) ? 32'd1
                                             : allocation_glide_step_shifted;

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
            glide_next_increment = voice_phase_increment[voice_index];
        end else if (voice_glide_up[voice_index]) begin
            if ((voice_current_increment[voice_index] >=
                 voice_phase_increment[voice_index]) ||
                ((voice_phase_increment[voice_index] -
                  voice_current_increment[voice_index]) <=
                 voice_glide_step[voice_index]))
                glide_next_increment = voice_phase_increment[voice_index];
            else
                glide_next_increment = voice_current_increment[voice_index] +
                                       voice_glide_step[voice_index];
        end else begin
            if ((voice_current_increment[voice_index] <=
                 voice_phase_increment[voice_index]) ||
                ((voice_current_increment[voice_index] -
                  voice_phase_increment[voice_index]) <=
                 voice_glide_step[voice_index]))
                glide_next_increment = voice_phase_increment[voice_index];
            else
                glide_next_increment = voice_current_increment[voice_index] -
                                       voice_glide_step[voice_index];
        end
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            sample_out          <= 16'sd0;
            active_voice_count  <= 4'd0;
            sustain_pedal       <= 1'b0;
            sample_pending      <= 1'b0;
            pending_event       <= 32'd0;
            event_pending       <= 1'b0;
            engine_state        <= ENGINE_IDLE;
            voice_index         <= 3'd0;
            harmonic_index      <= 4'd0;
            init_harmonic_index <= 4'd0;
            audible_voice_count <= 4'd0;
            mix_accumulator     <= 39'sd0;
            voice_harmonic_accumulator <= 32'sd0;
            fundamental_phase_work <= 32'd0;
            fundamental_increment_work <= 32'd0;
            harmonic_phase_work <= 32'd0;
            harmonic_increment_work <= 36'd0;
            pitch_base_increment_reg <= 32'd0;
            pitch_product_reg   <= 49'd0;
            vibrato_phase       <= 32'd0;
            pitch_ratio_address <= 9'd256;
            pitch_ratio_q16     <= 17'd65536;
            sine_address        <= 11'd0;
            partial_ram_address <= 7'd0;
            partial_level_read  <= 32'd0;
            partial_decay_read  <= 24'd0;
            partial_product_reg <= 33'sd0;
            decay_product_reg   <= 56'd0;
            envelope_product_reg <= 49'sd0;
            velocity_product_reg <= 50'sd0;
            render_envelope_level <= 16'd0;
            envelope_curve_reg <= 16'd0;
            envelope_start_reg <= 16'd0;
            envelope_state_reg <= ENV_IDLE;
            envelope_gate_reg <= 1'b0;
            calculated_envelope_level <= 16'd0;
            allocation_index      <= 3'd0;
            allocation_scan_index <= 3'd0;
            same_note_found       <= 1'b0;
            same_note_index       <= 3'd0;
            free_voice_found      <= 1'b0;
            free_voice_index      <= 3'd0;
            quietest_level        <= 16'hffff;
            quietest_index        <= 3'd0;
            allocation_note       <= 7'd0;
            allocation_velocity   <= 7'd0;
            init_anchor_left       <= 2'd0;
            init_anchor_fraction   <= 5'd0;
            harmonic_read_address  <= 6'd0;
            init_left_amp          <= 16'd0;
            init_left_decay        <= 24'd0;
            master_volume_target   <= DEFAULT_MASTER_VOLUME;
            master_volume_current  <= DEFAULT_MASTER_VOLUME;
            last_voice_index       <= 3'd0;
            allocation_legato      <= 1'b0;

            for (i = 0; i < VOICE_COUNT; i = i + 1) begin
                voice_active[i]          <= 1'b0;
                voice_gate[i]            <= 1'b0;
                voice_key_down[i]        <= 1'b0;
                voice_note[i]            <= 7'd0;
                voice_velocity[i]        <= 7'd0;
                voice_phase[i]           <= 32'd0;
                voice_phase_increment[i] <= 32'd0;
                voice_current_increment[i] <= 32'd0;
                voice_glide_step[i]      <= 32'd1;
                voice_glide_up[i]        <= 1'b1;
                envelope_state[i]        <= ENV_IDLE;
                envelope_count[i]        <= 32'd0;
                envelope_phase[i]        <= 32'd0;
                envelope_start[i]        <= 16'd0;
                envelope_level[i]        <= 16'd0;
            end
        end else begin
            partial_level_read <= partial_level[partial_ram_address];
            partial_decay_read <= partial_decay[partial_ram_address];

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
                event_pending <= 1'b1;
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
                            allocation_legato     <= (portamento_time != 0) &&
                                                     any_key_down &&
                                                     voice_active[last_voice_index];
                            allocation_scan_index <= 3'd0;
                            same_note_found       <= 1'b0;
                            same_note_index       <= 3'd0;
                            free_voice_found      <= 1'b0;
                            free_voice_index      <= 3'd0;
                            quietest_level        <= 16'hffff;
                            quietest_index        <= 3'd0;
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
                        voice_index         <= 3'd0;
                        audible_voice_count <= 4'd0;
                        mix_accumulator     <= 39'sd0;
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
                    voice_velocity[allocation_index]        <= allocation_velocity;
                    voice_phase_increment[allocation_index] <= allocation_target_increment;
                    last_voice_index <= allocation_index;
                    if (allocation_legato) begin
                        voice_glide_step[allocation_index] <=
                            allocation_glide_step;
                        voice_glide_up[allocation_index] <=
                            (allocation_target_increment >= legato_start_increment);
                    end else begin
                        voice_phase[allocation_index] <= 32'd0;
                        voice_current_increment[allocation_index] <=
                            allocation_target_increment;
                        voice_glide_step[allocation_index] <= 32'd1;
                        voice_glide_up[allocation_index] <= 1'b1;
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
                    init_left_decay <= harmonic_decay_read;
                    harmonic_read_address <= init_right_address;
                    engine_state <= ENGINE_INIT_RIGHT_WAIT;
                end

                ENGINE_INIT_RIGHT_WAIT:
                    engine_state <= ENGINE_INIT_WRITE;

                ENGINE_INIT_WRITE: begin
                    partial_level[init_partial_address] <= {
                        interpolate_u16(
                            init_left_amp,
                            harmonic_amp_read,
                            init_anchor_fraction),
                        16'd0
                    };
                    partial_decay[init_partial_address] <= interpolate_u24(
                        init_left_decay,
                        harmonic_decay_read,
                        init_anchor_fraction);
                    if (init_harmonic_index == HARMONIC_COUNT - 1) begin
                        engine_state <= ENGINE_IDLE;
                    end else begin
                        init_harmonic_index <= init_harmonic_index + 1'b1;
                        engine_state <= ENGINE_INIT_LEFT_ADDRESS;
                    end
                end

                ENGINE_VOICE_PREPARE: begin
                    if (!voice_active[voice_index]) begin
                        if (voice_index == VOICE_COUNT - 1) begin
                            sample_out <= apply_volume_and_saturate(
                                mix_accumulator, master_volume_current);
                            active_voice_count <= audible_voice_count;
                            engine_state <= ENGINE_IDLE;
                        end else begin
                            voice_index <= voice_index + 1'b1;
                        end
                    end else begin
                        if (envelope_level[voice_index] > 16'd1024)
                            audible_voice_count <= audible_voice_count + 1'b1;
                        render_envelope_level <= envelope_level[voice_index];
                        envelope_curve_reg <= selected_curve;
                        envelope_start_reg <= envelope_start[voice_index];
                        envelope_state_reg <= envelope_state[voice_index];
                        envelope_gate_reg <= voice_gate[voice_index];
                        engine_state <= ENGINE_ENVELOPE_CALC;
                    end
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
                                if (envelope_count[voice_index] >= amp_attack_samples - 1) begin
                                    envelope_level[voice_index] <= 16'hffff;
                                    envelope_state[voice_index] <= ENV_DECAY;
                                    envelope_count[voice_index] <= 32'd0;
                                    envelope_phase[voice_index] <= 32'd0;
                                end else begin
                                    envelope_count[voice_index] <= envelope_count[voice_index] + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        envelope_phase[voice_index], amp_attack_phase_inc);
                                end
                            end
                            ENV_DECAY: begin
                                envelope_level[voice_index] <= calculated_envelope_level;
                                if (envelope_count[voice_index] >= amp_decay_samples - 1) begin
                                    envelope_level[voice_index] <= amp_sustain_q16;
                                    envelope_state[voice_index] <= ENV_SUSTAIN;
                                    envelope_count[voice_index] <= 32'd0;
                                    envelope_phase[voice_index] <= 32'd0;
                                end else begin
                                    envelope_count[voice_index] <= envelope_count[voice_index] + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        envelope_phase[voice_index], amp_decay_phase_inc);
                                end
                            end
                            ENV_SUSTAIN: envelope_level[voice_index] <= amp_sustain_q16;
                            ENV_RELEASE: begin
                                envelope_level[voice_index] <= calculated_envelope_level;
                                if (envelope_count[voice_index] >= amp_release_samples - 1) begin
                                    envelope_level[voice_index] <= 16'd0;
                                    envelope_state[voice_index] <= ENV_IDLE;
                                    voice_active[voice_index] <= 1'b0;
                                end else begin
                                    envelope_count[voice_index] <= envelope_count[voice_index] + 1'b1;
                                    envelope_phase[voice_index] <= saturated_phase_add(
                                        envelope_phase[voice_index], amp_release_phase_inc);
                                end
                            end
                            default: begin
                                envelope_level[voice_index] <= 16'd0;
                                voice_active[voice_index] <= 1'b0;
                            end
                        endcase
                    end

                    harmonic_index <= 4'd0;
                    voice_harmonic_accumulator <= 32'sd0;
                    fundamental_phase_work <= voice_phase[voice_index];
                    harmonic_phase_work <= voice_phase[voice_index];
                    engine_state <= ENGINE_GLIDE_CALC;
                end

                ENGINE_GLIDE_CALC: begin
                    voice_current_increment[voice_index] <= glide_next_increment;
                    pitch_base_increment_reg <= glide_next_increment;
                    engine_state <= ENGINE_PITCH_MULT;
                end

                ENGINE_PITCH_MULT: begin
                    pitch_product_reg <= pitch_base_increment_reg * pitch_ratio_q16;
                    engine_state <= ENGINE_PITCH_APPLY;
                end

                ENGINE_PITCH_APPLY: begin
                    fundamental_increment_work <= modulated_increment;
                    harmonic_increment_work <= {4'd0, modulated_increment};
                    voice_phase[voice_index] <= voice_phase[voice_index] +
                                                modulated_increment;
                    engine_state <= ENGINE_SINE_ADDRESS;
                end

                ENGINE_SINE_ADDRESS: begin
                    sine_address <= harmonic_phase_work[31:21];
                    partial_ram_address <= partial_address;
                    engine_state <= ENGINE_SINE_WAIT;
                end

                ENGINE_SINE_WAIT: engine_state <= ENGINE_PARTIAL_MULT;

                ENGINE_PARTIAL_MULT: begin
                    if ((harmonic_increment_work < {4'd0, HARMONIC_PHASE_LIMIT}) &&
                        (partial_level_read[31:16] != 0))
                        partial_product_reg <= $signed(sine_data) *
                            $signed({1'b0, partial_level_read[31:16]});
                    else
                        partial_product_reg <= 33'sd0;
                    decay_product_reg <= partial_level_read * partial_decay_read;
                    engine_state <= ENGINE_PARTIAL_ACCUM;
                end

                ENGINE_PARTIAL_ACCUM: begin
                    voice_harmonic_accumulator <= harmonic_sum_next[31:0];
                    if (decay_delta >= partial_level_read)
                        partial_level[partial_ram_address] <= 32'd0;
                    else
                        partial_level[partial_ram_address] <=
                            partial_level_read - decay_delta;

                    if (harmonic_index == HARMONIC_COUNT - 1) begin
                        engine_state <= ENGINE_ENV_MULT;
                    end else begin
                        harmonic_index <= harmonic_index + 1'b1;
                        harmonic_phase_work <= harmonic_phase_work + fundamental_phase_work;
                        harmonic_increment_work <= harmonic_increment_work +
                                                   {4'd0, fundamental_increment_work};
                        engine_state <= ENGINE_SINE_ADDRESS;
                    end
                end

                ENGINE_ENV_MULT: begin
                    envelope_product_reg <= voice_harmonic_accumulator *
                        $signed({1'b0, render_envelope_level});
                    engine_state <= ENGINE_VELOCITY_MULT;
                end

                ENGINE_VELOCITY_MULT: begin
                    velocity_product_reg <= envelope_scaled *
                        $signed({1'b0, velocity_gain_q15});
                    engine_state <= ENGINE_VOICE_ACCUM;
                end

                ENGINE_VOICE_ACCUM: begin
                    if (voice_index == VOICE_COUNT - 1) begin
                        sample_out <= apply_volume_and_saturate(
                            mix_with_voice, master_volume_current);
                        active_voice_count <= audible_voice_count;
                        engine_state <= ENGINE_IDLE;
                    end else begin
                        mix_accumulator <= mix_with_voice;
                        voice_index <= voice_index + 1'b1;
                        engine_state <= ENGINE_VOICE_PREPARE;
                    end
                end

                default: engine_state <= ENGINE_IDLE;
            endcase
        end
    end

endmodule
