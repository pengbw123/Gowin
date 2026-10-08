// Global performance controls decoded from the same USB-MIDI event stream
// that feeds the polyphonic synthesizer.  All values intentionally stay in
// the MIDI 0..127 range so the hardware keyboard and a future UART UI can use
// the same control model.
module midi_performance_control (
    input  wire        clk,
    input  wire        reset_n,
    input  wire [31:0] midi_event_data,
    input  wire        midi_event_valid,

    output reg  [13:0] pitch_bend,
    output reg  [6:0]  vibrato_depth,
    output reg  [6:0]  vibrato_rate,
    output reg  [6:0]  portamento_time,
    output reg  [6:0]  chorus_mix,
    output reg  [6:0]  delay_mix,
    output reg  [6:0]  delay_time,
    output reg  [6:0]  reverb_mix
);

    wire [7:0] status = midi_event_data[23:16];
    wire [6:0] data1  = midi_event_data[14:8];
    wire [6:0] data2  = midi_event_data[6:0];

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            pitch_bend      <= 14'd8192;
            vibrato_depth   <= 7'd0;
            vibrato_rate    <= 7'd64;
            portamento_time <= 7'd0;
            chorus_mix      <= 7'd0;
            delay_mix       <= 7'd0;
            delay_time      <= 7'd64;
            reverb_mix      <= 7'd0;
        end else if (midi_event_valid) begin
            // Standard 14-bit MIDI pitch bend: data1 is LSB, data2 is MSB.
            if ((status & 8'hf0) == 8'he0) begin
                pitch_bend <= {data2, data1};
            end else if ((status & 8'hf0) == 8'hb0) begin
                case (data1)
                    // Standard modulation wheel/strip.
                    7'd1:  vibrato_depth <= data2;

                    // The keyboard's eight knobs send CC20..CC27.  CC20 is
                    // already master volume inside midi_poly_synth.
                    7'd21: chorus_mix      <= data2; // OCT knob
                    7'd22: portamento_time <= data2; // LATCH knob; zero=off
                    7'd23: delay_mix       <= data2; // GATE knob
                    7'd24: reverb_mix      <= data2; // SWING knob
                    7'd25: delay_time      <= data2; // TEMPO knob
                    7'd26: vibrato_rate    <= data2; // RATE knob

                    // Also accept General-MIDI effect-send controllers so a
                    // different class-compliant keyboard can control them.
                    7'd91: reverb_mix      <= data2;
                    7'd93: chorus_mix      <= data2;
                    default: begin end
                endcase
            end
        end
    end

endmodule
