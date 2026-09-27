`include "src/generated/tone_params.vh"

// Resource-efficient 1..8 voice unison wavetable oscillator.
// A single synchronous ROM port is time-multiplexed at 50 MHz.  Eight voices
// take fewer than 20 clocks, far below one 48.8 kHz audio-sample period.
module unison_oscillator (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire               gate,
    input  wire [31:0]        base_phase_increment,
    input  wire [3:0]         voice_count,
    input  wire [31:0]        detune_step,
    output reg signed [15:0]  sample_out
);

    reg signed [15:0] wavetable [0:2047];
    initial $readmemh(`TONE_WAVETABLE_MEM, wavetable);

    reg [10:0] rom_address;
    reg signed [15:0] rom_data;
    always @(posedge clk) begin
        rom_data <= wavetable[rom_address];
    end

    function signed [4:0] voice_offset;
        input [3:0] count;
        input [3:0] index;
        begin
            case (count)
                4'd1: voice_offset = 5'sd0;
                4'd2: voice_offset = (index == 0) ? -5'sd1 : 5'sd1;
                4'd3: case (index) 0: voice_offset = -5'sd1; 1: voice_offset = 5'sd0; default: voice_offset = 5'sd1; endcase
                4'd4: case (index) 0: voice_offset = -5'sd2; 1: voice_offset = -5'sd1; 2: voice_offset = 5'sd1; default: voice_offset = 5'sd2; endcase
                4'd5: voice_offset = $signed({1'b0, index}) - 5'sd2;
                4'd6: case (index) 0: voice_offset = -5'sd3; 1: voice_offset = -5'sd2; 2: voice_offset = -5'sd1; 3: voice_offset = 5'sd1; 4: voice_offset = 5'sd2; default: voice_offset = 5'sd3; endcase
                4'd7: voice_offset = $signed({1'b0, index}) - 5'sd3;
                default: case (index) 0: voice_offset = -5'sd4; 1: voice_offset = -5'sd3; 2: voice_offset = -5'sd2; 3: voice_offset = -5'sd1; 4: voice_offset = 5'sd1; 5: voice_offset = 5'sd2; 6: voice_offset = 5'sd3; default: voice_offset = 5'sd4; endcase
            endcase
        end
    endfunction

    function [31:0] phase_increment_for;
        input [3:0] index;
        input [3:0] count;
        input [31:0] base_increment;
        input [31:0] step;
        reg signed [4:0] offset;
        reg [33:0] magnitude;
        reg [33:0] wide_value;
        begin
            offset = voice_offset(count, index);
            case (offset)
                -5'sd4, 5'sd4: magnitude = {2'b00, step} << 2;
                -5'sd3, 5'sd3: magnitude = ({2'b00, step} << 1) + {2'b00, step};
                -5'sd2, 5'sd2: magnitude = {2'b00, step} << 1;
                -5'sd1, 5'sd1: magnitude = {2'b00, step};
                default:        magnitude = 34'd0;
            endcase

            if (offset < 0) begin
                if ({2'b00, base_increment} <= magnitude)
                    phase_increment_for = 32'd1;
                else
                    phase_increment_for = base_increment - magnitude[31:0];
            end else begin
                wide_value = {2'b00, base_increment} + magnitude;
                if (wide_value > 34'h0FFFFFFFF)
                    phase_increment_for = 32'hFFFFFFFF;
                else
                    phase_increment_for = wide_value[31:0];
            end
        end
    endfunction

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

    reg [31:0] phase [0:7];
    reg [31:0] phase_step [0:7];
    reg [10:0] address_latch [0:7];
    reg [3:0] active_count;
    reg [3:0] voice_index;
    reg signed [19:0] accumulator;
    reg gate_delayed;
    reg [1:0] state;
    integer i;

    localparam IDLE = 2'd0;
    localparam WAIT_ROM = 2'd1;
    localparam ACCUMULATE = 2'd2;

    wire signed [19:0] sum_with_rom = accumulator + {{4{rom_data[15]}}, rom_data};
    reg signed [19:0] normalized_value;
    always @(*) begin
        case (active_count)
            4'd1: normalized_value = sum_with_rom;
            4'd2: normalized_value = sum_with_rom >>> 1;
            4'd3, 4'd4: normalized_value = sum_with_rom >>> 2;
            default: normalized_value = sum_with_rom >>> 3;
        endcase
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            for (i = 0; i < 8; i = i + 1) begin
                phase[i] <= 32'd0;
                phase_step[i] <= base_phase_increment;
                address_latch[i] <= 11'd0;
            end
            rom_address <= 11'd0;
            active_count <= 4'd1;
            voice_index <= 4'd0;
            accumulator <= 20'sd0;
            gate_delayed <= 1'b0;
            state <= IDLE;
            sample_out <= 16'sd0;
        end else begin
            // Register detuned increments so the phase accumulator path only
            // contains one 32-bit addition. UART changes settle in one clock.
            for (i = 0; i < 8; i = i + 1)
                phase_step[i] <= phase_increment_for(i[3:0], voice_count, base_phase_increment, detune_step);

            case (state)
                IDLE: begin
                    if (sample_tick) begin
                        gate_delayed <= gate;
                        active_count <= (voice_count < 1) ? 4'd1 : ((voice_count > 8) ? 4'd8 : voice_count);
                        voice_index <= 4'd0;
                        accumulator <= 20'sd0;

                        for (i = 0; i < 8; i = i + 1) begin
                            if (gate && !gate_delayed) begin
                                phase[i] <= 32'd0;
                                address_latch[i] <= 11'd0;
                            end else begin
                                address_latch[i] <= phase[i][31:21];
                                phase[i] <= phase[i] + phase_step[i];
                            end
                        end

                        rom_address <= (gate && !gate_delayed) ? 11'd0 : phase[0][31:21];
                        state <= WAIT_ROM;
                    end
                end

                // One wait clock is required for the synchronous ROM output.
                WAIT_ROM: state <= ACCUMULATE;

                ACCUMULATE: begin
                    if (voice_index + 1 >= active_count) begin
                        sample_out <= saturate16({{12{normalized_value[19]}}, normalized_value});
                        state <= IDLE;
                    end else begin
                        accumulator <= sum_with_rom;
                        voice_index <= voice_index + 1'b1;
                        case (voice_index + 1'b1)
                            4'd1: rom_address <= address_latch[1];
                            4'd2: rom_address <= address_latch[2];
                            4'd3: rom_address <= address_latch[3];
                            4'd4: rom_address <= address_latch[4];
                            4'd5: rom_address <= address_latch[5];
                            4'd6: rom_address <= address_latch[6];
                            default: rom_address <= address_latch[7];
                        endcase
                        state <= WAIT_ROM;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end

endmodule
