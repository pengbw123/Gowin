// Two-lane, continuously-issued additive-harmonic rendering pipeline.
//
// One task describes two adjacent harmonics belonging to one voice.  A new
// task may be accepted on every clock.  After the pipeline fills, one tagged
// four-harmonic group sum is produced on every clock as well.
//
// This module deliberately owns only the regular, high-throughput datapath:
//
//   P0 phase/address generation
//   P1 synchronous sine/amplitude reads
//   P2 two parallel multiplies
//   P3 registered pair sum
//   P4 tagged group result
//
// Voice scheduling, ADSR, pitch control, per-voice accumulation and filtering
// remain outside the core.  Keeping explicit voice/group/epoch tags makes the
// core extensible with phase offsets, inharmonicity or partial decay later.
module harmonic_render_pipeline #(
    parameter integer VOICE_INDEX_BITS = 4,
    parameter integer EPOCH_BITS       = 4,
    parameter [31:0] HARMONIC_PHASE_LIMIT = 32'h73333333,
    parameter SINE_MEM = "src/generated/additive_sine_quarter_512.mem"
) (
    input  wire                         clk,
    input  wire                         reset_n,

    // Runtime amplitude update.  Harmonics 0..15 are split into two banks
    // by harmonic[0], so both amplitudes in one group can be read in a
    // single clock without duplicating the complete per-voice table.
    input  wire                         amp_write_valid,
    input  wire [VOICE_INDEX_BITS-1:0]  amp_write_voice,
    input  wire [3:0]                   amp_write_harmonic,
    input  wire [15:0]                  amp_write_value,

    // One task per clock. The scheduler supplies the first harmonic phase of
    // this pair plus the fundamental phase used to form the adjacent one.
    input  wire                         in_valid,
    input  wire [VOICE_INDEX_BITS-1:0]  in_voice_id,
    input  wire [2:0]                   in_group,
    input  wire [EPOCH_BITS-1:0]        in_epoch,
    input  wire [31:0]                  in_first_phase,
    input  wire [35:0]                  in_first_increment,
    input  wire [31:0]                  in_fundamental_phase,
    input  wire [31:0]                  in_fundamental_increment,

    output reg                          out_valid,
    output reg  [VOICE_INDEX_BITS-1:0]  out_voice_id,
    output reg  [2:0]                   out_group,
    output reg  [EPOCH_BITS-1:0]        out_epoch,
    output reg                          out_last_group,
    output reg signed [31:0]            out_group_sample,
    output wire                         pipeline_busy
);

    // Two physical ROM copies provide two independent synchronous reads.
    // Each copy stores only the first quadrant of a 2048-point waveform.
    reg signed [15:0] sine_quarter_0 [0:511];
    reg signed [15:0] sine_quarter_1 [0:511];

    // 16 harmonics are banked by lane.  Address = {voice, group}.
    reg [15:0] amplitude_bank_0 [0:127];
    reg [15:0] amplitude_bank_1 [0:127];

    initial begin
        $readmemh(SINE_MEM, sine_quarter_0);
        $readmemh(SINE_MEM, sine_quarter_1);
    end

    wire [6:0] amp_write_address = {amp_write_voice, amp_write_harmonic[3:1]};
    always @(posedge clk) begin
        if (amp_write_valid) begin
            if (amp_write_harmonic[0])
                amplitude_bank_1[amp_write_address] <= amp_write_value;
            else
                amplitude_bank_0[amp_write_address] <= amp_write_value;
        end
    end

    function [8:0] quarter_address;
        input [31:0] phase;
        begin
            quarter_address = phase[30]
                ? (9'd511 - phase[29:21])
                : phase[29:21];
        end
    endfunction

    // P0: phase/address generation and anti-alias decisions.
    reg                         valid_p0;
    reg [VOICE_INDEX_BITS-1:0]  voice_p0;
    reg [2:0]                   group_p0;
    reg [EPOCH_BITS-1:0]        epoch_p0;
    reg [6:0]                   amplitude_address_p0;
    reg [8:0]                   sine_address_0_p0;
    reg [8:0]                   sine_address_1_p0;
    reg [1:0]                   sine_negative_p0;
    reg [1:0]                   lane_enable_p0;

    wire [31:0] phase_1_comb = in_first_phase + in_fundamental_phase;
    wire [35:0] fundamental_increment_ext =
        {4'd0, in_fundamental_increment};

    // P1: synchronous table outputs and matching tags.
    reg                         valid_p1;
    reg [VOICE_INDEX_BITS-1:0]  voice_p1;
    reg [2:0]                   group_p1;
    reg [EPOCH_BITS-1:0]        epoch_p1;
    reg [1:0]                   lane_enable_p1;
    reg [1:0]                   sine_negative_p1;
    reg signed [15:0]           sine_data_0_p1;
    reg signed [15:0]           sine_data_1_p1;
    reg [15:0]                  amplitude_0_p1;
    reg [15:0]                  amplitude_1_p1;

    // P2: four independent signed multipliers.
    reg                         valid_p2;
    reg [VOICE_INDEX_BITS-1:0]  voice_p2;
    reg [2:0]                   group_p2;
    reg [EPOCH_BITS-1:0]        epoch_p2;
    reg signed [32:0]           product_0_p2;
    reg signed [32:0]           product_1_p2;

    wire signed [15:0] signed_sine_0_p1 = sine_negative_p1[0]
        ? -sine_data_0_p1 : sine_data_0_p1;
    wire signed [15:0] signed_sine_1_p1 = sine_negative_p1[1]
        ? -sine_data_1_p1 : sine_data_1_p1;
    // P3: registered pair sum.
    reg                         valid_p3;
    reg [VOICE_INDEX_BITS-1:0]  voice_p3;
    reg [2:0]                   group_p3;
    reg [EPOCH_BITS-1:0]        epoch_p3;
    reg signed [33:0]           pair_sum_01_p3;

    assign pipeline_busy = valid_p0 | valid_p1 | valid_p2 | valid_p3 |
                           out_valid;

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            valid_p0         <= 1'b0;
            valid_p1         <= 1'b0;
            valid_p2         <= 1'b0;
            valid_p3         <= 1'b0;
            out_valid        <= 1'b0;
            out_voice_id     <= {VOICE_INDEX_BITS{1'b0}};
            out_group        <= 3'd0;
            out_epoch        <= {EPOCH_BITS{1'b0}};
            out_last_group   <= 1'b0;
            out_group_sample <= 32'sd0;
        end else begin
            // P0 accepts a new task every clock.
            valid_p0 <= in_valid;
            if (in_valid) begin
                voice_p0 <= in_voice_id;
                group_p0 <= in_group;
                epoch_p0 <= in_epoch;
                amplitude_address_p0 <= {in_voice_id, in_group};
                sine_address_0_p0 <= quarter_address(in_first_phase);
                sine_address_1_p0 <= quarter_address(phase_1_comb);
                sine_negative_p0 <= {
                    phase_1_comb[31], in_first_phase[31]
                };
                lane_enable_p0[0] <= in_first_increment <
                                     {4'd0, HARMONIC_PHASE_LIMIT};
                lane_enable_p0[1] <= (in_first_increment +
                                     fundamental_increment_ext) <
                                     {4'd0, HARMONIC_PHASE_LIMIT};
            end

            // P1 synchronous memories.  Tags and data advance together.
            valid_p1 <= valid_p0;
            if (valid_p0) begin
                voice_p1 <= voice_p0;
                group_p1 <= group_p0;
                epoch_p1 <= epoch_p0;
                lane_enable_p1 <= lane_enable_p0;
                sine_negative_p1 <= sine_negative_p0;
                sine_data_0_p1 <= sine_quarter_0[sine_address_0_p0];
                sine_data_1_p1 <= sine_quarter_1[sine_address_1_p0];
                amplitude_0_p1 <= amplitude_bank_0[amplitude_address_p0];
                amplitude_1_p1 <= amplitude_bank_1[amplitude_address_p0];
            end

            // P2 four products are calculated in parallel.
            valid_p2 <= valid_p1;
            if (valid_p1) begin
                voice_p2 <= voice_p1;
                group_p2 <= group_p1;
                epoch_p2 <= epoch_p1;
                product_0_p2 <= lane_enable_p1[0]
                    ? signed_sine_0_p1 * $signed({1'b0, amplitude_0_p1})
                    : 33'sd0;
                product_1_p2 <= lane_enable_p1[1]
                    ? signed_sine_1_p1 * $signed({1'b0, amplitude_1_p1})
                    : 33'sd0;
            end

            // P3 first adder-tree level.
            valid_p3 <= valid_p2;
            if (valid_p2) begin
                voice_p3 <= voice_p2;
                group_p3 <= group_p2;
                epoch_p3 <= epoch_p2;
                pair_sum_01_p3 <= $signed(product_0_p2) +
                                  $signed(product_1_p2);
            end

            // P4 one tagged four-harmonic result per clock after fill.
            out_valid <= valid_p3;
            if (valid_p3) begin
                out_voice_id <= voice_p3;
                out_group <= group_p3;
                out_epoch <= epoch_p3;
                out_last_group <= (group_p3 == 3'd7);
                out_group_sample <= $signed(pair_sum_01_p3) >>> 15;
            end
        end
    end

endmodule
