// Resource-conscious mono effects chain for the Primer 25K.
//
//   dry synth -> modulated short delay (chorus)
//             -> feedback delay
//             -> three-comb + all-pass room simulation (reverb)
//
// The large sample histories infer BSRAM.  Coefficients and wet/dry mixes use
// shifts, not multipliers, because the additive engine already uses most DSPs.
module audio_effects (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire signed [15:0] dry_sample,
    input  wire [6:0]         chorus_mix,
    input  wire [6:0]         delay_mix,
    input  wire [6:0]         delay_time,
    input  wire [6:0]         reverb_mix,
    output reg  signed [15:0] sample_out
);

    // 2048 samples cover 41.9 ms at 48.828 kHz.  Chorus uses approximately
    // 9..17 ms and slowly moves the read tap through that interval.
    reg signed [15:0] chorus_memory [0:2047];
    reg [10:0] chorus_write_pointer;
    reg [31:0] chorus_lfo_phase;
    reg signed [15:0] chorus_tap;
    reg signed [15:0] chorus_stage;
    reg [11:0] chorus_fill;

    wire [7:0] chorus_triangle = chorus_lfo_phase[31]
        ? (8'hff - chorus_lfo_phase[30:23])
        : chorus_lfo_phase[30:23];
    wire [10:0] chorus_delay_samples = 11'd448 +
        {1'b0, chorus_triangle, 1'b0};
    wire [10:0] chorus_read_pointer = chorus_write_pointer -
        chorus_delay_samples;

    // 16384 samples give a maximum useful echo time of about 208 ms with the
    // selected knob mapping.  Feedback is fixed at 1/2 for a clear demo.
    reg signed [15:0] delay_memory [0:16383];
    reg [13:0] delay_write_pointer;
    reg signed [15:0] delay_tap;
    reg signed [15:0] delay_stage;
    reg [14:0] delay_fill;
    wire [13:0] delay_samples = 14'd2048 + {1'b0, delay_time, 6'b0};
    wire [13:0] delay_read_pointer = delay_write_pointer - delay_samples;

    // Three unequal comb delays create many differently spaced reflections.
    // The following all-pass section increases echo density and removes the
    // obvious "three separate echoes" character.
    reg signed [15:0] reverb_comb1 [0:1498];
    reg signed [15:0] reverb_comb2 [0:1776];
    reg signed [15:0] reverb_comb3 [0:2136];
    reg signed [15:0] reverb_allpass [0:520];
    reg [10:0] comb1_pointer;
    reg [10:0] comb2_pointer;
    reg [11:0] comb3_pointer;
    reg [9:0]  allpass_pointer;
    reg [10:0] comb1_write_pointer;
    reg [10:0] comb2_write_pointer;
    reg [11:0] comb3_write_pointer;
    reg [9:0]  allpass_write_pointer;
    reg signed [15:0] comb1_tap;
    reg signed [15:0] comb2_tap;
    reg signed [15:0] comb3_tap;
    reg signed [15:0] allpass_tap;
    reg signed [15:0] reverb_input_delayed;
    reg signed [15:0] allpass_input_delayed;
    reg signed [15:0] reverb_stage;
    reg [12:0] reverb_fill;
    reg comb_pipeline_valid;
    reg allpass_pipeline_valid;

    reg signed [31:0] comb_sum;
    wire signed [31:0] allpass_output_value =
        $signed(allpass_tap) - ($signed(allpass_input_delayed) >>> 1);

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

    // Four audible wet/dry ranges are enough for a physical 0..127 knob and
    // avoid consuming three extra DSP multipliers.  A literal zero is a true
    // bypass; the highest range remains 50% wet so the dry attack is retained.
    function signed [15:0] blend_effect;
        input signed [15:0] dry_value;
        input signed [15:0] wet_value;
        input [6:0] amount;
        reg signed [31:0] difference;
        reg signed [31:0] mixed;
        begin
            difference = $signed(wet_value) - $signed(dry_value);
            if (amount == 0)
                mixed = dry_value;
            else begin
                case (amount[6:5])
                    2'b00: mixed = $signed(dry_value) + (difference >>> 3);
                    2'b01: mixed = $signed(dry_value) + (difference >>> 2);
                    2'b10: mixed = $signed(dry_value) +
                                      (difference >>> 2) + (difference >>> 3);
                    default: mixed = ($signed(dry_value) +
                                      $signed(wet_value)) >>> 1;
                endcase
            end
            blend_effect = saturate16(mixed);
        end
    endfunction

    wire signed [15:0] chorus_wet = (chorus_fill[11]) ? chorus_tap : 16'sd0;
    wire signed [15:0] delay_wet  = (delay_fill[14]) ? delay_tap : 16'sd0;
    wire signed [15:0] comb1_wet  = (reverb_fill >= 13'd1499) ? comb1_tap : 16'sd0;
    wire signed [15:0] comb2_wet  = (reverb_fill >= 13'd1777) ? comb2_tap : 16'sd0;
    wire signed [15:0] comb3_wet  = (reverb_fill >= 13'd2137) ? comb3_tap : 16'sd0;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            chorus_write_pointer <= 11'd0;
            chorus_lfo_phase     <= 32'd0;
            chorus_tap           <= 16'sd0;
            chorus_stage         <= 16'sd0;
            chorus_fill          <= 12'd0;
            delay_write_pointer  <= 14'd0;
            delay_tap            <= 16'sd0;
            delay_stage          <= 16'sd0;
            delay_fill           <= 15'd0;
            comb1_pointer        <= 11'd0;
            comb2_pointer        <= 11'd0;
            comb3_pointer        <= 12'd0;
            allpass_pointer      <= 10'd0;
            comb1_write_pointer  <= 11'd0;
            comb2_write_pointer  <= 11'd0;
            comb3_write_pointer  <= 12'd0;
            allpass_write_pointer <= 10'd0;
            comb1_tap            <= 16'sd0;
            comb2_tap            <= 16'sd0;
            comb3_tap            <= 16'sd0;
            allpass_tap          <= 16'sd0;
            reverb_input_delayed <= 16'sd0;
            allpass_input_delayed <= 16'sd0;
            reverb_stage         <= 16'sd0;
            reverb_fill          <= 13'd0;
            comb_pipeline_valid  <= 1'b0;
            allpass_pipeline_valid <= 1'b0;
            comb_sum             <= 32'sd0;
            sample_out           <= 16'sd0;
        end else if (sample_tick) begin
            // CHORUS: one copy remains dry; another comes from a slowly moving
            // short-delay tap.  The moving delay continuously advances and
            // retards the copy's phase, which is heard as gentle detuning.
            chorus_memory[chorus_write_pointer] <= dry_sample;
            chorus_tap <= chorus_memory[chorus_read_pointer];
            chorus_stage <= blend_effect(dry_sample, chorus_wet, chorus_mix);
            chorus_write_pointer <= chorus_write_pointer + 1'b1;
            chorus_lfo_phase <= chorus_lfo_phase + 32'd30786; // about 0.35 Hz
            if (!chorus_fill[11])
                chorus_fill <= chorus_fill + 1'b1;

            // DELAY: write input plus half of the old echo back into the ring
            // buffer.  That feedback creates successively quieter repeats.
            delay_memory[delay_write_pointer] <= saturate16(
                $signed(chorus_stage) + ($signed(delay_wet) >>> 1));
            delay_tap <= delay_memory[delay_read_pointer];
            delay_stage <= blend_effect(chorus_stage, delay_wet, delay_mix);
            delay_write_pointer <= delay_write_pointer + 1'b1;
            if (!delay_fill[14])
                delay_fill <= delay_fill + 1'b1;

            // REVERB: parallel feedback delays model several wall-reflection
            // paths. Unequal lengths make their echoes overlap rather than
            // line up. The all-pass stage diffuses them into a dense tail.
            // BSRAM reads are synchronous.  Delay the write address and input
            // by one sample so each old cell value is fed back into that same
            // cell rather than accidentally into its neighbour.
            if (comb_pipeline_valid) begin
                reverb_comb1[comb1_write_pointer] <= saturate16(
                    $signed(reverb_input_delayed) +
                    ($signed(comb1_wet) >>> 1));
                reverb_comb2[comb2_write_pointer] <= saturate16(
                    $signed(reverb_input_delayed) +
                    ($signed(comb2_wet) >>> 1) -
                    ($signed(comb2_wet) >>> 4));
                reverb_comb3[comb3_write_pointer] <= saturate16(
                    $signed(reverb_input_delayed) +
                    ($signed(comb3_wet) >>> 2) +
                    ($signed(comb3_wet) >>> 3));
            end
            comb1_tap <= reverb_comb1[comb1_pointer];
            comb2_tap <= reverb_comb2[comb2_pointer];
            comb3_tap <= reverb_comb3[comb3_pointer];
            comb1_write_pointer <= comb1_pointer;
            comb2_write_pointer <= comb2_pointer;
            comb3_write_pointer <= comb3_pointer;
            reverb_input_delayed <= delay_stage;
            comb_pipeline_valid <= 1'b1;

            comb_sum <= ($signed(comb1_wet) + $signed(comb2_wet) +
                         $signed(comb3_wet)) >>> 2;
            allpass_tap <= reverb_allpass[allpass_pointer];
            allpass_write_pointer <= allpass_pointer;
            allpass_input_delayed <= saturate16(comb_sum);
            if (allpass_pipeline_valid) begin
                reverb_allpass[allpass_write_pointer] <= saturate16(
                    $signed(allpass_input_delayed) +
                    (allpass_output_value >>> 1));
                reverb_stage <= saturate16(allpass_output_value);
            end
            allpass_pipeline_valid <= 1'b1;
            sample_out <= blend_effect(delay_stage, reverb_stage, reverb_mix);

            if (comb1_pointer == 11'd1498)
                comb1_pointer <= 11'd0;
            else
                comb1_pointer <= comb1_pointer + 1'b1;
            if (comb2_pointer == 11'd1776)
                comb2_pointer <= 11'd0;
            else
                comb2_pointer <= comb2_pointer + 1'b1;
            if (comb3_pointer == 12'd2136)
                comb3_pointer <= 12'd0;
            else
                comb3_pointer <= comb3_pointer + 1'b1;
            if (allpass_pointer == 10'd520)
                allpass_pointer <= 10'd0;
            else
                allpass_pointer <= allpass_pointer + 1'b1;
            if (reverb_fill < 13'd4095)
                reverb_fill <= reverb_fill + 1'b1;
        end
    end

endmodule
