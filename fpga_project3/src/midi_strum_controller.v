// MIDI performance front-end for normal keyboard and guitar-strum modes.
//
// In normal mode every MIDI event passes through unchanged.  In strum mode
// keyboard Note On/Off messages maintain a silent chord.  Pad 1 (channel 10,
// note 0x28) plays the chord low-to-high and Pad 2 (note 0x29) plays it
// high-to-low.  Generated notes are separated in time and receive individual
// Note Off messages so the existing synth ADSR remains in charge of the tail.
module midi_strum_controller #(
    parameter integer SAMPLE_RATE_HZ = 48828,
    parameter integer CHORD_SLOTS = 6
) (
    input  wire        clk,
    input  wire        reset_n,
    input  wire        sample_tick,
    input  wire        midi_connected,

    input  wire [31:0] raw_event_data,
    input  wire        raw_event_valid,
    output wire        raw_event_ready,

    output wire [31:0] synth_event_data,
    output wire        synth_event_valid,
    output wire [6:0]  synth_event_brightness,
    input  wire        synth_event_ready,

    input  wire        strum_mode,
    input  wire [15:0] strum_interval_ms,
    input  wire [15:0] strum_gate_ms,
    input  wire [6:0]  velocity_speed_depth,
    input  wire [6:0]  velocity_brightness_depth,

    output reg  [2:0]  held_note_count,
    output wire        strum_active
);

    localparam [6:0] PAD_DOWN_NOTE = 7'h28;
    localparam [6:0] PAD_UP_NOTE   = 7'h29;

    reg [31:0] input_event;
    reg        input_pending;
    reg [31:0] output_event;
    reg [6:0]  output_brightness;
    reg        output_pending;

    assign raw_event_ready = ~input_pending;
    assign synth_event_data = output_event;
    assign synth_event_brightness = output_brightness;
    assign synth_event_valid = output_pending;

    wire [7:0] input_status = input_event[23:16];
    wire [6:0] input_data1  = input_event[14:8];
    wire [6:0] input_data2  = input_event[6:0];
    wire [3:0] input_type   = input_status[7:4];
    wire [3:0] input_channel = input_status[3:0];
    wire input_note_on = (input_type == 4'h9) && (input_data2 != 0);
    wire input_note_off = (input_type == 4'h8) ||
                          ((input_type == 4'h9) && (input_data2 == 0));
    wire input_is_drum_note = (input_channel == 4'h9) &&
                              (input_note_on || input_note_off);
    wire input_is_pad = input_is_drum_note &&
                        ((input_data1 == PAD_DOWN_NOTE) ||
                         (input_data1 == PAD_UP_NOTE));
    wire input_is_chord_note = (input_channel != 4'h9) &&
                               (input_note_on || input_note_off);
    wire input_is_pad_aftertouch = (input_status == 8'hd9);
    wire input_needs_passthrough = !strum_mode ||
                                   (!input_is_chord_note &&
                                    !input_is_drum_note &&
                                    !input_is_pad_aftertouch);

    reg [CHORD_SLOTS-1:0] held_valid;
    reg [6:0] held_note [0:CHORD_SLOTS-1];
    reg [6:0] held_velocity [0:CHORD_SLOTS-1];

    reg [CHORD_SLOTS-1:0] stroke_valid;
    reg [CHORD_SLOTS-1:0] stroke_used;
    reg [6:0] stroke_note [0:CHORD_SLOTS-1];
    reg [2:0] stroke_remaining;
    reg       stroke_direction_up;
    reg [6:0] stroke_velocity;
    reg [6:0] stroke_brightness;
    reg [15:0] stroke_interval;
    reg [15:0] interval_counter;
    reg        next_note_due;
    reg        stroke_busy;
    reg [6:0]  prepared_note;
    reg [2:0]  prepared_release_index;
    reg        note_release_lookup_pending;
    reg        note_emit_pending;
    assign strum_active = stroke_busy;

    // Every generated note owns an independent release timer. Retriggering
    // the same note replaces its old timer, preventing an old Note Off from
    // cutting a newly strummed note short.
    reg [CHORD_SLOTS-1:0] release_active;
    reg [CHORD_SLOTS-1:0] release_due;
    reg [6:0] release_note [0:CHORD_SLOTS-1];
    reg [15:0] release_counter [0:CHORD_SLOTS-1];

    reg [31:0] millisecond_accumulator;
    wire millisecond_pulse = sample_tick &&
        (millisecond_accumulator + 32'd1000 >= SAMPLE_RATE_HZ);

    reg [1:0] connected_sync;
    reg mode_previous;
    reg connected_previous;
    reg panic_pending;

    integer scan_index;
    reg held_match_found;
    reg [2:0] held_match_index;
    reg held_free_found;
    reg [2:0] held_free_index;
    always @* begin
        held_match_found = 1'b0;
        held_match_index = 3'd0;
        held_free_found = 1'b0;
        held_free_index = 3'd0;
        for (scan_index = 0; scan_index < CHORD_SLOTS;
             scan_index = scan_index + 1) begin
            if (!held_match_found && held_valid[scan_index] &&
                (held_note[scan_index] == input_data1)) begin
                held_match_found = 1'b1;
                held_match_index = scan_index[2:0];
            end
            if (!held_free_found && !held_valid[scan_index]) begin
                held_free_found = 1'b1;
                held_free_index = scan_index[2:0];
            end
        end
    end

    integer select_index;
    reg selected_found;
    reg [2:0] selected_index;
    reg [6:0] selected_note;
    always @* begin
        selected_found = 1'b0;
        selected_index = 3'd0;
        selected_note = stroke_direction_up ? 7'd127 : 7'd0;
        for (select_index = 0; select_index < CHORD_SLOTS;
             select_index = select_index + 1) begin
            if (stroke_valid[select_index] && !stroke_used[select_index]) begin
                if (!selected_found ||
                    (!stroke_direction_up &&
                     (stroke_note[select_index] < selected_note)) ||
                    (stroke_direction_up &&
                     (stroke_note[select_index] > selected_note))) begin
                    selected_found = 1'b1;
                    selected_index = select_index[2:0];
                    selected_note = stroke_note[select_index];
                end
            end
        end
    end

    integer due_index;
    reg release_due_found;
    reg [2:0] release_due_index;
    always @* begin
        release_due_found = 1'b0;
        release_due_index = 3'd0;
        for (due_index = 0; due_index < CHORD_SLOTS;
             due_index = due_index + 1)
            if (!release_due_found && release_due[due_index]) begin
                release_due_found = 1'b1;
                release_due_index = due_index[2:0];
            end
    end

    integer release_scan_index;
    reg release_same_found;
    reg [2:0] release_same_index;
    reg release_free_found;
    reg [2:0] release_free_index;
    reg [2:0] selected_release_index;
    always @* begin
        release_same_found = 1'b0;
        release_same_index = 3'd0;
        release_free_found = 1'b0;
        release_free_index = 3'd0;
        for (release_scan_index = 0; release_scan_index < CHORD_SLOTS;
             release_scan_index = release_scan_index + 1) begin
            if (!release_same_found &&
                (release_active[release_scan_index] ||
                 release_due[release_scan_index]) &&
                (release_note[release_scan_index] == prepared_note)) begin
                release_same_found = 1'b1;
                release_same_index = release_scan_index[2:0];
            end
            if (!release_free_found && !release_active[release_scan_index] &&
                !release_due[release_scan_index]) begin
                release_free_found = 1'b1;
                release_free_index = release_scan_index[2:0];
            end
        end
        if (release_same_found)
            selected_release_index = release_same_index;
        else if (release_free_found)
            selected_release_index = release_free_index;
        else
            selected_release_index = 3'd0;
    end

    function [15:0] interval_for_velocity;
        input [15:0] base_interval;
        input [6:0] velocity;
        input [6:0] depth;
        reg [15:0] reduction;
        begin
            // Quantized shift/add mapping deliberately avoids consuming a
            // DSP block for a control-rate calculation on the DSP-full 25K.
            reduction = 16'd0;
            case (depth[6:5])
                2'd1: case (velocity[6:5])
                    2'd1: reduction = base_interval >> 4; // 1/16
                    2'd2: reduction = base_interval >> 3; // 1/8
                    2'd3: reduction = (base_interval >> 3) +
                                      (base_interval >> 4); // 3/16
                    default: reduction = 16'd0;
                endcase
                2'd2: case (velocity[6:5])
                    2'd1: reduction = base_interval >> 3; // 1/8
                    2'd2: reduction = base_interval >> 2; // 1/4
                    2'd3: reduction = (base_interval >> 2) +
                                      (base_interval >> 3); // 3/8
                    default: reduction = 16'd0;
                endcase
                2'd3: case (velocity[6:5])
                    2'd1: reduction = base_interval >> 3; // 1/8
                    2'd2: reduction = base_interval >> 2; // 1/4
                    2'd3: reduction = base_interval >> 1; // 1/2
                    default: reduction = 16'd0;
                endcase
                default: reduction = 16'd0;
            endcase
            if (base_interval <= reduction)
                interval_for_velocity = 16'd1;
            else if ((base_interval - reduction) == 0)
                interval_for_velocity = 16'd1;
            else
                interval_for_velocity = base_interval - reduction;
        end
    endfunction

    function [6:0] brightness_for_velocity;
        input [6:0] velocity;
        input [6:0] depth;
        begin
            // Four perceptually useful ranges, again implemented without a
            // multiplier. More depth makes pad velocity affect the spectral
            // tilt more strongly.
            case (depth[6:5])
                2'd0: brightness_for_velocity = 7'd127;
                2'd1: brightness_for_velocity = 7'd96 +
                                                     {2'd0, velocity[6:2]};
                2'd2: brightness_for_velocity = 7'd64 +
                                                     {1'd0, velocity[6:1]};
                default: brightness_for_velocity = velocity;
            endcase
        end
    endfunction

    wire output_slot_available = !output_pending || synth_event_ready;

    integer state_index;
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            input_event <= 32'd0;
            input_pending <= 1'b0;
            output_event <= 32'd0;
            output_brightness <= 7'd127;
            output_pending <= 1'b0;
            held_valid <= {CHORD_SLOTS{1'b0}};
            held_note_count <= 3'd0;
            stroke_valid <= {CHORD_SLOTS{1'b0}};
            stroke_used <= {CHORD_SLOTS{1'b0}};
            stroke_remaining <= 3'd0;
            stroke_direction_up <= 1'b0;
            stroke_velocity <= 7'd100;
            stroke_brightness <= 7'd127;
            stroke_interval <= 16'd18;
            interval_counter <= 16'd0;
            next_note_due <= 1'b0;
            stroke_busy <= 1'b0;
            prepared_note <= 7'd0;
            prepared_release_index <= 3'd0;
            note_release_lookup_pending <= 1'b0;
            note_emit_pending <= 1'b0;
            release_active <= {CHORD_SLOTS{1'b0}};
            release_due <= {CHORD_SLOTS{1'b0}};
            millisecond_accumulator <= 32'd0;
            connected_sync <= 2'b00;
            mode_previous <= 1'b0;
            connected_previous <= 1'b0;
            panic_pending <= 1'b0;
            for (state_index = 0; state_index < CHORD_SLOTS;
                 state_index = state_index + 1) begin
                held_note[state_index] <= 7'd0;
                held_velocity[state_index] <= 7'd0;
                stroke_note[state_index] <= 7'd0;
                release_note[state_index] <= 7'd0;
                release_counter[state_index] <= 16'd0;
            end
        end else begin
            connected_sync <= {connected_sync[0], midi_connected};
            mode_previous <= strum_mode;
            connected_previous <= connected_sync[1];

            if (sample_tick) begin
                if (millisecond_pulse)
                    millisecond_accumulator <= millisecond_accumulator +
                        32'd1000 - SAMPLE_RATE_HZ;
                else
                    millisecond_accumulator <= millisecond_accumulator + 32'd1000;
            end

            if (raw_event_valid && raw_event_ready) begin
                input_event <= raw_event_data;
                input_pending <= 1'b1;
            end

            if (output_pending && synth_event_ready)
                output_pending <= 1'b0;

            if (millisecond_pulse) begin
                if (interval_counter != 0) begin
                    if (interval_counter == 1) begin
                        interval_counter <= 16'd0;
                        next_note_due <= 1'b1;
                    end else begin
                        interval_counter <= interval_counter - 1'b1;
                    end
                end
                for (state_index = 0; state_index < CHORD_SLOTS;
                     state_index = state_index + 1) begin
                    if (release_active[state_index]) begin
                        if (release_counter[state_index] <= 1) begin
                            release_counter[state_index] <= 16'd0;
                            release_active[state_index] <= 1'b0;
                            release_due[state_index] <= 1'b1;
                        end else begin
                            release_counter[state_index] <=
                                release_counter[state_index] - 1'b1;
                        end
                    end
                end
            end

            // Changing performance mode or losing the USB keyboard cancels
            // every local timer and asks the synth to release all voices.
            if ((mode_previous != strum_mode) ||
                (connected_previous && !connected_sync[1])) begin
                input_pending <= 1'b0;
                held_valid <= {CHORD_SLOTS{1'b0}};
                held_note_count <= 3'd0;
                stroke_valid <= {CHORD_SLOTS{1'b0}};
                stroke_used <= {CHORD_SLOTS{1'b0}};
                stroke_remaining <= 3'd0;
                stroke_busy <= 1'b0;
                next_note_due <= 1'b0;
                interval_counter <= 16'd0;
                note_release_lookup_pending <= 1'b0;
                note_emit_pending <= 1'b0;
                release_active <= {CHORD_SLOTS{1'b0}};
                release_due <= {CHORD_SLOTS{1'b0}};
                panic_pending <= 1'b1;
            end else begin
                // Chord and pad messages are consumed locally in strum mode.
                if (input_pending && strum_mode && input_is_chord_note) begin
                    input_pending <= 1'b0;
                    if (input_note_on) begin
                        if (held_match_found) begin
                            held_velocity[held_match_index] <= input_data2;
                        end else if (held_free_found) begin
                            held_valid[held_free_index] <= 1'b1;
                            held_note[held_free_index] <= input_data1;
                            held_velocity[held_free_index] <= input_data2;
                            held_note_count <= held_note_count + 1'b1;
                        end
                    end else if (held_match_found) begin
                        held_valid[held_match_index] <= 1'b0;
                        if (held_note_count != 0)
                            held_note_count <= held_note_count - 1'b1;
                    end
                end else if (input_pending && strum_mode && input_is_pad) begin
                    input_pending <= 1'b0;
                    if (input_note_on && (held_note_count != 0) &&
                        !stroke_busy) begin
                        for (state_index = 0; state_index < CHORD_SLOTS;
                             state_index = state_index + 1) begin
                            stroke_valid[state_index] <= held_valid[state_index];
                            stroke_note[state_index] <= held_note[state_index];
                        end
                        stroke_used <= {CHORD_SLOTS{1'b0}};
                        stroke_remaining <= held_note_count;
                        stroke_direction_up <= (input_data1 == PAD_UP_NOTE);
                        stroke_velocity <= (input_data2 == 0) ? 7'd1 : input_data2;
                        stroke_brightness <= brightness_for_velocity(
                            input_data2, velocity_brightness_depth);
                        stroke_interval <= interval_for_velocity(
                            (strum_interval_ms == 0) ? 16'd1 : strum_interval_ms,
                            input_data2, velocity_speed_depth);
                        interval_counter <= 16'd0;
                        next_note_due <= 1'b1;
                        stroke_busy <= 1'b1;
                    end
                end else if (input_pending && strum_mode &&
                             input_is_drum_note) begin
                    // Other pads are reserved for future guitar techniques;
                    // do not let their channel-10 notes sound as pitched keys.
                    input_pending <= 1'b0;
                end else if (input_pending && strum_mode &&
                             input_is_pad_aftertouch) begin
                    input_pending <= 1'b0;
                end

                // Register the three decisions on separate cycles.  The
                // original one-cycle select+release-allocation path was more
                // than 30 LUT levels and could not meet 50 MHz on the 25K.
                if (next_note_due && selected_found &&
                    !note_release_lookup_pending && !note_emit_pending) begin
                    prepared_note <= selected_note;
                    stroke_used[selected_index] <= 1'b1;
                    next_note_due <= 1'b0;
                    note_release_lookup_pending <= 1'b1;
                end else if (note_release_lookup_pending) begin
                    prepared_release_index <= selected_release_index;
                    note_release_lookup_pending <= 1'b0;
                    note_emit_pending <= 1'b1;
                end

                if (output_slot_available) begin
                    if (panic_pending) begin
                        output_event <= {8'h00, 8'hb0, 8'h7b, 8'h00};
                        output_brightness <= 7'd127;
                        output_pending <= 1'b1;
                        panic_pending <= 1'b0;
                    end else if (release_due_found) begin
                        output_event <= {8'h00, 8'h80, 1'b0,
                                         release_note[release_due_index], 8'h40};
                        output_brightness <= 7'd127;
                        output_pending <= 1'b1;
                        release_due[release_due_index] <= 1'b0;
                    end else if (note_emit_pending) begin
                        output_event <= {8'h00, 8'h90, 1'b0, prepared_note,
                                         1'b0, stroke_velocity};
                        output_brightness <= stroke_brightness;
                        output_pending <= 1'b1;
                        note_emit_pending <= 1'b0;
                        release_active[prepared_release_index] <= 1'b1;
                        release_due[prepared_release_index] <= 1'b0;
                        release_note[prepared_release_index] <= prepared_note;
                        release_counter[prepared_release_index] <=
                            (strum_gate_ms == 0) ? 16'd1 : strum_gate_ms;
                        if (stroke_remaining <= 1) begin
                            stroke_remaining <= 3'd0;
                            stroke_busy <= 1'b0;
                        end else begin
                            stroke_remaining <= stroke_remaining - 1'b1;
                            interval_counter <= stroke_interval;
                        end
                    end else if (input_pending && input_needs_passthrough) begin
                        output_event <= input_event;
                        output_brightness <= 7'd127;
                        output_pending <= 1'b1;
                        input_pending <= 1'b0;
                    end
                end
            end
        end
    end

endmodule
