// Lightweight ST7735 160x80 visual feedback for the one-PMOD LCD board.
// The LCD's own GRAM is the framebuffer: FPGA memory stores only 80 waveform
// heights. This keeps the display independent from the audio scheduler and
// avoids the large framebuffer/FFT cost on the 25K device.
module fm_status_lcd (
    input  wire               clk,
    input  wire               reset_n,
    input  wire               sample_tick,
    input  wire signed [15:0] audio_sample,
    input  wire [4:0]         active_voice_count,
    input  wire [4:0]         preset_index,
    input  wire [4:0]         algorithm,
    output wire               lcd_resetn,
    output wire               lcd_clk,
    output wire               lcd_cs,
    output wire               lcd_dc,
    output wire               lcd_data
);
    localparam ST_RESET = 3'd0;
    localparam ST_POST_RESET = 3'd1;
    localparam ST_WAKE = 3'd2;
    localparam ST_WAKE_WAIT = 3'd3;
    localparam ST_INIT = 3'd4;
    localparam ST_PIXEL_HI = 3'd5;
    localparam ST_PIXEL_LO = 3'd6;

    reg [11:0] init_memory [0:87];
    initial $readmemh("src/generated/st7735_160x80_init.mem", init_memory);

    reg [6:0] waveform_memory [0:79];
    reg [6:0] capture_address;
    reg [3:0] capture_divider;
    reg [6:0] waveform_read_address;
    reg [6:0] waveform_read_data;
    integer init_i;

    function [6:0] sample_to_y;
        input signed [15:0] value;
        integer y;
        begin
            y = 44 - (value >>> 10);
            if (y < 10) sample_to_y = 7'd10;
            else if (y > 78) sample_to_y = 7'd78;
            else sample_to_y = y[6:0];
        end
    endfunction

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            capture_address <= 0;
            capture_divider <= 0;
            waveform_read_data <= 7'd44;
            for (init_i = 0; init_i < 80; init_i = init_i + 1)
                waveform_memory[init_i] <= 7'd44;
        end else begin
        waveform_read_data <= waveform_memory[waveform_read_address];
        if (sample_tick) begin
            if (capture_divider == 7) begin
                capture_divider <= 0;
                waveform_memory[capture_address] <= sample_to_y(audio_sample);
                capture_address <= (capture_address == 79)
                    ? 0 : capture_address + 1'b1;
            end else capture_divider <= capture_divider + 1'b1;
        end
        end
    end

    reg [2:0] state;
    reg [31:0] delay_counter;
    reg [6:0] init_index;
    reg [7:0] pixel_x;
    reg [6:0] pixel_y;
    reg [15:0] pixel_color;

    reg spi_busy;
    reg spi_clock;
    reg spi_cs;
    reg spi_dc;
    reg [7:0] spi_shift;
    reg [2:0] spi_bit;
    reg spi_done;
    reg tx_start;
    reg [7:0] tx_data;
    reg tx_dc;
    reg lcd_reset_reg;

    assign lcd_resetn = lcd_reset_reg;
    assign lcd_clk = spi_clock;
    assign lcd_cs = spi_cs;
    assign lcd_dc = spi_dc;
    assign lcd_data = spi_shift[7];

    wire [7:0] history_sum = {1'b0, pixel_x[7:1]} + capture_address;
    wire [6:0] history_address = (history_sum >= 80)
        ? history_sum - 80 : history_sum[6:0];
    wire waveform_pixel = (pixel_y + 1 >= waveform_read_data) &&
                          (pixel_y <= waveform_read_data + 1);
    wire top_preset_pixel = (pixel_y < 4) &&
                            (pixel_x < ({3'd0, preset_index} + 1'b1) * 5);
    wire voice_pixel = (pixel_y >= 5 && pixel_y < 8) &&
                       (pixel_x < active_voice_count * 10);
    wire algorithm_pixel = (pixel_x >= 150) &&
                           (pixel_y < ({2'd0, algorithm[4:2]} + 1'b1) * 8);
    wire grid_pixel = (pixel_x == 40 || pixel_x == 80 || pixel_x == 120 ||
                       pixel_y == 24 || pixel_y == 44 || pixel_y == 64);

    always @* begin
        if (top_preset_pixel) pixel_color = 16'h07e0;
        else if (voice_pixel) pixel_color = 16'hf800;
        else if (algorithm_pixel) pixel_color = 16'hffe0;
        else if (waveform_pixel) pixel_color = 16'h07ff;
        else if (grid_pixel) pixel_color = 16'h18c3;
        else pixel_color = 16'h0008;
    end

    // SPI mode 0, SCK=25 MHz. Data changes only on falling edges and is
    // sampled by the LCD on rising edges.
    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            spi_busy <= 0;
            spi_clock <= 0;
            spi_cs <= 1;
            spi_dc <= 1;
            spi_shift <= 8'hff;
            spi_bit <= 0;
            spi_done <= 0;
        end else begin
            spi_done <= 0;
            if (!spi_busy) begin
                spi_clock <= 0;
                if (tx_start) begin
                    spi_busy <= 1;
                    spi_cs <= 0;
                    spi_dc <= tx_dc;
                    spi_shift <= tx_data;
                    spi_bit <= 0;
                end
            end else if (!spi_clock) begin
                spi_clock <= 1;
            end else begin
                spi_clock <= 0;
                if (spi_bit == 7) begin
                    spi_busy <= 0;
                    spi_cs <= 1;
                    spi_done <= 1;
                end else begin
                    spi_shift <= {spi_shift[6:0], 1'b0};
                    spi_bit <= spi_bit + 1'b1;
                end
            end
        end
    end

    always @(posedge clk or negedge reset_n) begin
        if (!reset_n) begin
            state <= ST_RESET;
            delay_counter <= 0;
            init_index <= 0;
            pixel_x <= 0;
            pixel_y <= 0;
            waveform_read_address <= 0;
            lcd_reset_reg <= 0;
            tx_start <= 0;
            tx_data <= 0;
            tx_dc <= 1;
        end else begin
            tx_start <= 0;
            case (state)
                ST_RESET: begin
                    if (delay_counter == 32'd4_999_999) begin
                        delay_counter <= 0;
                        lcd_reset_reg <= 1;
                        state <= ST_POST_RESET;
                    end else delay_counter <= delay_counter + 1'b1;
                end
                ST_POST_RESET: begin
                    if (delay_counter == 32'd9_999_999) begin
                        delay_counter <= 0;
                        state <= ST_WAKE;
                    end else delay_counter <= delay_counter + 1'b1;
                end
                ST_WAKE: if (!spi_busy && !tx_start) begin
                    tx_data <= 8'h11; tx_dc <= 0; tx_start <= 1;
                    state <= ST_WAKE_WAIT;
                end
                ST_WAKE_WAIT: begin
                    if (spi_done) delay_counter <= 0;
                    else if (!spi_busy && !tx_start) begin
                        if (delay_counter == 32'd5_999_999) begin
                            delay_counter <= 0;
                            init_index <= 1;
                            state <= ST_INIT;
                        end else delay_counter <= delay_counter + 1'b1;
                    end
                end
                ST_INIT: if (!spi_busy && !tx_start) begin
                    tx_data <= init_memory[init_index][7:0];
                    tx_dc <= init_memory[init_index][8];
                    tx_start <= 1;
                    if (init_index == 87) begin
                        pixel_x <= 0;
                        pixel_y <= 0;
                        waveform_read_address <= capture_address;
                        state <= ST_PIXEL_HI;
                    end else init_index <= init_index + 1'b1;
                end
                ST_PIXEL_HI: if (!spi_busy && !tx_start) begin
                    tx_data <= pixel_color[15:8]; tx_dc <= 1; tx_start <= 1;
                    state <= ST_PIXEL_LO;
                end
                ST_PIXEL_LO: if (!spi_busy && !tx_start) begin
                    tx_data <= pixel_color[7:0]; tx_dc <= 1; tx_start <= 1;
                    if (pixel_x == 159) begin
                        pixel_x <= 0;
                        pixel_y <= (pixel_y == 79) ? 0 : pixel_y + 1'b1;
                    end else pixel_x <= pixel_x + 1'b1;
                    waveform_read_address <= history_address;
                    state <= ST_PIXEL_HI;
                end
                default: state <= ST_RESET;
            endcase
        end
    end
endmodule
