`timescale 1ns/1ps

module tb_midi_strum_controller;
    reg clk = 1'b0;
    reg reset_n = 1'b0;
    reg sample_tick = 1'b1;
    reg midi_connected = 1'b0;
    reg [31:0] raw_event_data = 32'd0;
    reg raw_event_valid = 1'b0;
    wire raw_event_ready;
    wire [31:0] synth_event_data;
    wire synth_event_valid;
    wire [6:0] synth_event_brightness;
    reg synth_event_ready = 1'b1;
    reg strum_mode = 1'b0;
    wire [2:0] held_note_count;
    wire strum_active;

    integer note_on_count = 0;
    reg [6:0] observed_note [0:5];

    always #10 clk = ~clk;

    midi_strum_controller #(
        .SAMPLE_RATE_HZ(1000),
        .CHORD_SLOTS(6)
    ) dut (
        .clk(clk),
        .reset_n(reset_n),
        .sample_tick(sample_tick),
        .midi_connected(midi_connected),
        .raw_event_data(raw_event_data),
        .raw_event_valid(raw_event_valid),
        .raw_event_ready(raw_event_ready),
        .synth_event_data(synth_event_data),
        .synth_event_valid(synth_event_valid),
        .synth_event_brightness(synth_event_brightness),
        .synth_event_ready(synth_event_ready),
        .strum_mode(strum_mode),
        .strum_interval_ms(16'd2),
        .strum_gate_ms(16'd10),
        .velocity_speed_depth(7'd0),
        .velocity_brightness_depth(7'd96),
        .held_note_count(held_note_count),
        .strum_active(strum_active)
    );

    task send_event;
        input [7:0] status;
        input [6:0] data1;
        input [6:0] data2;
        begin
            while (!raw_event_ready)
                @(posedge clk);
            @(negedge clk);
            raw_event_data = {8'd0, status, 1'b0, data1, 1'b0, data2};
            raw_event_valid = 1'b1;
            @(negedge clk);
            raw_event_valid = 1'b0;
        end
    endtask

    always @(posedge clk) begin
        if (synth_event_valid && synth_event_ready &&
            (synth_event_data[23:20] == 4'h9) &&
            (synth_event_data[6:0] != 0)) begin
            observed_note[note_on_count] <= synth_event_data[14:8];
            note_on_count <= note_on_count + 1;
        end
    end

    initial begin
        repeat (4) @(posedge clk);
        reset_n = 1'b1;
        midi_connected = 1'b1;
        repeat (5) @(posedge clk);

        // Normal mode is transparent.
        send_event(8'h90, 7'd60, 7'd90);
        repeat (5) @(posedge clk);
        if (note_on_count != 1 || observed_note[0] != 60)
            $fatal(1, "normal mode did not pass Note On");

        strum_mode = 1'b1;
        repeat (8) @(posedge clk); // consume mode-change All Notes Off
        note_on_count = 0;

        send_event(8'h90, 7'd60, 7'd70);
        send_event(8'h90, 7'd64, 7'd70);
        send_event(8'h90, 7'd67, 7'd70);
        repeat (4) @(posedge clk);
        if (held_note_count != 3 || note_on_count != 0)
            $fatal(1, "strum mode did not capture a silent three-note chord");

        // Pad 1: downstroke, low to high.
        send_event(8'h99, 7'h28, 7'd100);
        wait (note_on_count == 3);
        repeat (2) @(posedge clk);
        if (observed_note[0] != 60 || observed_note[1] != 64 ||
            observed_note[2] != 67)
            $fatal(1, "downstroke order is wrong");
        if (synth_event_brightness >= 127)
            $fatal(1, "velocity brightness sideband was not reduced");

        repeat (15) @(posedge clk);
        note_on_count = 0;
        // Pad 2: upstroke, high to low.
        send_event(8'h99, 7'h29, 7'd100);
        wait (note_on_count == 3);
        repeat (2) @(posedge clk);
        if (observed_note[0] != 67 || observed_note[1] != 64 ||
            observed_note[2] != 60)
            $fatal(1, "upstroke order is wrong");

        $display("PASS: normal passthrough, chord capture, down/up strum");
        $finish;
    end
endmodule
