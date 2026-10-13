`timescale 1ns/1ps

// Worst-case control-schedule check for the production 12-voice build.
// All voices are active and the two-pole dynamic filter is enabled.
module tb_midi_poly_pipeline_schedule;
    reg clk = 1'b0;
    reg reset_n = 1'b0;
    reg sample_tick = 1'b0;
    reg [31:0] midi_event_data = 32'd0;
    reg midi_event_valid = 1'b0;
    wire midi_event_ready;
    wire signed [15:0] sample_out;
    wire [4:0] active_voice_count;

    always #10 clk = ~clk;

    midi_poly_synth #(.VOICE_COUNT(12)) dut (
        .clk(clk), .reset_n(reset_n), .sample_tick(sample_tick),
        .midi_event_data(midi_event_data),
        .midi_event_valid(midi_event_valid),
        .midi_event_brightness(7'd127),
        .midi_event_ready(midi_event_ready),
        .amp_attack_samples(32'd64),
        .amp_attack_phase_inc(32'h04000000),
        .amp_decay_samples(32'd128),
        .amp_decay_phase_inc(32'h02000000),
        .amp_sustain_q16(16'd32768),
        .amp_release_samples(32'd128),
        .amp_release_phase_inc(32'h02000000),
        .pitch_envelope_phase_inc(32'h01000000),
        .envelope_write_valid(1'b0),
        .envelope_write_table(2'd0),
        .envelope_write_address(8'd0),
        .envelope_write_value(16'd0),
        .harmonic_write_valid(1'b0),
        .harmonic_write_decay(1'b0),
        .harmonic_write_anchor(2'd0),
        .harmonic_write_index(4'd0),
        .harmonic_write_value(24'd0),
        .harmonic_commit(1'b0),
        .filter_write_valid(1'b0),
        .filter_write_anchor(2'd0),
        .filter_write_address(6'd0),
        .filter_write_value(8'd0),
        .filter_time_write_valid(1'b0),
        .filter_time_write_anchor(2'd0),
        .filter_time_write_phase_inc(32'd0),
        .filter_enable(1'b1),
        .filter_two_pole(1'b1),
        .note_gain_write_valid(1'b0),
        .note_gain_write_address(7'd0),
        .note_gain_write_value(16'd0),
        .note_gain_commit(1'b0),
        .pitch_bend(14'd8192),
        .vibrato_depth(7'd0),
        .vibrato_rate(7'd64),
        .portamento_time(7'd0),
        .sample_out(sample_out),
        .active_voice_count(active_voice_count)
    );

    task send_note;
        input [6:0] note;
        begin
            while (!midi_event_ready) @(posedge clk);
            @(negedge clk);
            midi_event_data = {8'h00, 8'h90, 1'b0, note, 8'h7f};
            midi_event_valid = 1'b1;
            @(negedge clk);
            midi_event_valid = 1'b0;
            while ((dut.engine_state != 0) || dut.event_pending)
                @(posedge clk);
        end
    endtask

    integer note_index;
    integer render_cycles;
    integer internal_active_count;
    initial begin
        repeat (5) @(posedge clk);
        reset_n = 1'b1;
        repeat (5) @(posedge clk);

        for (note_index = 0; note_index < 12; note_index = note_index + 1)
            send_note(7'd48 + note_index[6:0]);

        @(negedge clk);
        sample_tick = 1'b1;
        @(negedge clk);
        sample_tick = 1'b0;

        while (dut.engine_state == 0) @(posedge clk);
        render_cycles = 0;
        while (dut.engine_state != 0) begin
            render_cycles = render_cycles + 1;
            @(posedge clk);
        end
        repeat (2) @(posedge clk);

        internal_active_count = 0;
        for (note_index = 0; note_index < 12; note_index = note_index + 1)
            if (dut.voice_active[note_index])
                internal_active_count = internal_active_count + 1;
        $display("PIPELINE_RENDER_CYCLES=%0d active_state=%0d audible=%0d sample=%0d",
                 render_cycles, internal_active_count,
                 active_voice_count, sample_out);
        if (render_cycles > 400) begin
            $display("FAIL: 12-voice render exceeded 400 clocks");
            $fatal(1);
        end
        if (internal_active_count != 12) begin
            $display("FAIL: expected 12 allocated voices");
            $fatal(1);
        end
        $display("PASS: 12-voice true-pipeline schedule");
        $finish;
    end

    initial begin
        #20000000;
        $display("FAIL: timeout");
        $fatal(1);
    end
endmodule
