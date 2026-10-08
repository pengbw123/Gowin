`timescale 1ns/1ps

module tb_midi_poly_audio;
    reg clk = 1'b0;
    reg reset_n = 1'b0;
    reg sample_tick = 1'b0;
    reg [31:0] midi_event_data = 32'd0;
    reg midi_event_valid = 1'b0;
    wire midi_event_ready;
    wire signed [15:0] sample_out;
    wire [3:0] active_voice_count;

    always #10 clk = ~clk;

    midi_poly_synth #(.VOICE_COUNT(8)) dut (
        .clk(clk), .reset_n(reset_n), .sample_tick(sample_tick),
        .midi_event_data(midi_event_data),
        .midi_event_valid(midi_event_valid),
        .midi_event_ready(midi_event_ready),
        .amp_attack_samples(32'd16),
        .amp_attack_phase_inc(32'h11111111),
        .amp_decay_samples(32'd32),
        .amp_decay_phase_inc(32'h08421084),
        .amp_sustain_q16(16'd11796),
        .amp_release_samples(32'd16),
        .amp_release_phase_inc(32'h11111111),
        .harmonic_write_valid(1'b0), .harmonic_write_decay(1'b0),
        .harmonic_write_anchor(2'd0), .harmonic_write_index(4'd0),
        .harmonic_write_value(24'd0), .harmonic_commit(1'b0),
        .pitch_bend(14'd8192), .vibrato_depth(7'd0),
        .vibrato_rate(7'd64), .portamento_time(7'd0),
        .sample_out(sample_out), .active_voice_count(active_voice_count)
    );

    task reset_synth;
        begin
            reset_n = 1'b0;
            repeat (5) @(posedge clk);
            reset_n = 1'b1;
            repeat (5) @(posedge clk);
        end
    endtask

    task send_note;
        input [6:0] note;
        begin
            while (!midi_event_ready) @(posedge clk);
            @(negedge clk);
            midi_event_data = {8'h00, 8'h90, 1'b0, note, 8'h7f};
            midi_event_valid = 1'b1;
            @(negedge clk);
            midi_event_valid = 1'b0;
            repeat (3) @(posedge clk);
            while ((dut.engine_state != 0) || dut.event_pending) @(posedge clk);
        end
    endtask

    task render_and_measure;
        input [6:0] note;
        integer sample_number;
        integer absolute_sample;
        integer peak;
        begin
            reset_synth();
            send_note(note);
            peak = 0;
            for (sample_number = 0; sample_number < 64; sample_number = sample_number + 1) begin
                @(negedge clk);
                sample_tick = 1'b1;
                @(negedge clk);
                sample_tick = 1'b0;
                repeat (1022) @(posedge clk);
                absolute_sample = (sample_out < 0) ? -sample_out : sample_out;
                if (absolute_sample > peak)
                    peak = absolute_sample;
            end
            $display("AUDIO note=%0d peak=%0d final=%0d voices=%0d",
                     note, peak, sample_out, active_voice_count);
            if (peak < 1000) begin
                $display("FAIL: note %0d audio is unexpectedly small", note);
                $fatal(1);
            end
        end
    endtask

    initial begin
        render_and_measure(7'd36);
        render_and_measure(7'd48);
        render_and_measure(7'd60);
        render_and_measure(7'd61);
        render_and_measure(7'd72);
        render_and_measure(7'd84);
        $display("PASS: C2/C3/C4/C6 and C4-C6 midpoint traverse the audio pipeline");
        $finish;
    end
endmodule
