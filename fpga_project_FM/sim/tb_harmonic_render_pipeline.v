`timescale 1ns/1ps

module tb_harmonic_render_pipeline;
    reg clk = 1'b0;
    reg reset_n = 1'b0;
    always #10 clk = ~clk;

    reg amp_write_valid = 1'b0;
    reg [3:0] amp_write_voice = 4'd0;
    reg [3:0] amp_write_harmonic = 4'd0;
    reg [15:0] amp_write_value = 16'd0;
    reg in_valid = 1'b0;
    reg [3:0] in_voice_id = 4'd0;
    reg [2:0] in_group = 3'd0;
    reg [3:0] in_epoch = 4'd0;
    reg [31:0] in_first_phase = 32'd0;
    reg [35:0] in_first_increment = 36'd1000000;
    reg [31:0] in_fundamental_phase = 32'd0;
    reg [31:0] in_fundamental_increment = 32'd1000000;

    wire out_valid;
    wire [3:0] out_voice_id;
    wire [2:0] out_group;
    wire [3:0] out_epoch;
    wire out_last_group;
    wire signed [31:0] out_group_sample;
    wire pipeline_busy;

    harmonic_render_pipeline dut (
        .clk(clk), .reset_n(reset_n),
        .amp_write_valid(amp_write_valid),
        .amp_write_voice(amp_write_voice),
        .amp_write_harmonic(amp_write_harmonic),
        .amp_write_value(amp_write_value),
        .in_valid(in_valid), .in_voice_id(in_voice_id),
        .in_group(in_group), .in_epoch(in_epoch),
        .in_first_phase(in_first_phase),
        .in_first_increment(in_first_increment),
        .in_fundamental_phase(in_fundamental_phase),
        .in_fundamental_increment(in_fundamental_increment),
        .out_valid(out_valid), .out_voice_id(out_voice_id),
        .out_group(out_group), .out_epoch(out_epoch),
        .out_last_group(out_last_group),
        .out_group_sample(out_group_sample),
        .pipeline_busy(pipeline_busy)
    );

    integer voice;
    integer harmonic;
    integer output_count = 0;
    integer expected_voice;
    integer expected_group;

    task write_amplitude;
        input [3:0] voice_id;
        input [3:0] harmonic_id;
        input [15:0] value;
        begin
            @(negedge clk);
            amp_write_valid = 1'b1;
            amp_write_voice = voice_id;
            amp_write_harmonic = harmonic_id;
            amp_write_value = value;
            @(negedge clk);
            amp_write_valid = 1'b0;
        end
    endtask

    always @(posedge clk) begin
        if (out_valid) begin
            expected_group = output_count / 12;
            expected_voice = output_count % 12;
            if (out_group !== expected_group[2:0] ||
                out_voice_id !== expected_voice[3:0] || out_epoch !== 4'hA) begin
                $display("FAIL tag count=%0d voice=%0d group=%0d epoch=%0h",
                    output_count, out_voice_id, out_group, out_epoch);
                $fatal;
            end
            if (expected_group == 0 && out_group_sample < 32000) begin
                $display("FAIL expected fundamental near full scale: %0d",
                    out_group_sample);
                $fatal;
            end
            if (expected_group != 0 && out_group_sample !== 0) begin
                $display("FAIL zero-amplitude group produced %0d", out_group_sample);
                $fatal;
            end
            output_count = output_count + 1;
        end
    end

    initial begin
        repeat (4) @(negedge clk);
        reset_n = 1'b1;

        // Clear twelve voices and enable only their fundamentals.
        for (voice = 0; voice < 12; voice = voice + 1)
            for (harmonic = 0; harmonic < 16; harmonic = harmonic + 1)
                write_amplitude(voice[3:0], harmonic[3:0],
                    harmonic == 0 ? 16'h7fff : 16'd0);

        // The complete production schedule enters on 96 consecutive clocks:
        // group-major, then voice.  Outputs must retain exactly that order.
        for (harmonic = 0; harmonic < 8; harmonic = harmonic + 1) begin
            for (voice = 0; voice < 12; voice = voice + 1) begin
                @(negedge clk);
                in_valid = 1'b1;
                in_voice_id = voice[3:0];
                in_group = harmonic[2:0];
                in_epoch = 4'hA;
                in_first_phase = 32'h40000000; // sin(90 deg)
                in_fundamental_phase = 32'h40000000;
            end
        end
        @(negedge clk);
        in_valid = 1'b0;

        wait (output_count == 96);
        repeat (3) @(posedge clk);
        if (pipeline_busy) begin
            $display("FAIL pipeline did not drain");
            $fatal;
        end
        $display("PASS harmonic_render_pipeline: 96 consecutive tagged outputs");
        $finish;
    end

    initial begin
        #100000;
        $display("FAIL timeout");
        $fatal;
    end
endmodule
