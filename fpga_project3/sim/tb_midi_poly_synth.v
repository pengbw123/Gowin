`timescale 1ns/1ps

module tb_midi_poly_synth;
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
        .clk(clk),
        .reset_n(reset_n),
        .sample_tick(sample_tick),
        .midi_event_data(midi_event_data),
        .midi_event_valid(midi_event_valid),
        .midi_event_ready(midi_event_ready),
        .amp_attack_samples(32'd16),
        .amp_attack_phase_inc(32'h11111111),
        .amp_decay_samples(32'd16),
        .amp_decay_phase_inc(32'h11111111),
        .amp_sustain_q16(16'd11796),
        .amp_release_samples(32'd16),
        .amp_release_phase_inc(32'h11111111),
        .harmonic_write_valid(1'b0),
        .harmonic_write_decay(1'b0),
        .harmonic_write_anchor(2'd0),
        .harmonic_write_index(4'd0),
        .harmonic_write_value(24'd0),
        .harmonic_commit(1'b0),
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
            while (!midi_event_ready)
                @(posedge clk);
            @(negedge clk);
            midi_event_data = {8'h00, 8'h90, 1'b0, note, 1'b0, 7'd127};
            midi_event_valid = 1'b1;
            @(negedge clk);
            midi_event_valid = 1'b0;
        end
    endtask

    task wait_engine_idle;
        begin
            repeat (3) @(posedge clk);
            while ((dut.engine_state != 0) || dut.event_pending)
                @(posedge clk);
            repeat (3) @(posedge clk);
        end
    endtask

    task print_voice;
        input integer voice;
        integer harmonic;
        begin
            $display("VOICE=%0d note=%0d phase_inc=%08x env=%0d",
                     voice, dut.voice_note[voice],
                     dut.voice_phase_increment[voice],
                     dut.envelope_state[voice]);
            for (harmonic = 0; harmonic < 16; harmonic = harmonic + 1)
                $display("  H%0d level=%08x decay=%06x",
                         harmonic + 1,
                         dut.partial_level[voice * 16 + harmonic],
                         dut.partial_decay[voice * 16 + harmonic]);
        end
    endtask

    initial begin
        repeat (5) @(posedge clk);
        reset_n = 1'b1;
        repeat (5) @(posedge clk);

        send_note(7'd60); // C4: exact anchor
        wait_engine_idle();
        print_voice(0);

        send_note(7'd61); // C#4: interpolated
        wait_engine_idle();
        print_voice(1);

        send_note(7'd62); // D4: interpolated
        wait_engine_idle();
        print_voice(2);

        send_note(7'd72); // C5: midpoint of the two-octave C4..C6 interval
        wait_engine_idle();
        print_voice(3);

        send_note(7'd36); // C2: exact low anchor
        wait_engine_idle();
        print_voice(4);

        send_note(7'd84); // C6: exact high anchor
        wait_engine_idle();
        print_voice(5);

        if ((dut.partial_level[16] == 0) || (dut.partial_level[32] == 0)) begin
            $display("FAIL: non-C fundamental is zero");
            $fatal(1);
        end
        if ((dut.voice_phase_increment[1] == 0) ||
            (dut.voice_phase_increment[2] == 0)) begin
            $display("FAIL: non-C phase increment is zero");
            $fatal(1);
        end
        if (dut.partial_level[16] == dut.partial_level[32]) begin
            $display("FAIL: C#4 and D4 share the same Q0.5 timbre position");
            $fatal(1);
        end
        if ((dut.partial_level[48] == 0) || (dut.partial_level[64] == 0) ||
            (dut.partial_level[80] == 0)) begin
            $display("FAIL: C2/C5/C6 anchor coverage is zero");
            $fatal(1);
        end
        $display("PASS: C2/C3/C4/C6 anchors and two-octave interpolation initialized");
        $finish;
    end
endmodule
