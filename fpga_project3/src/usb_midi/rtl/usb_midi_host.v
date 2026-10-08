// Tang Primer 25K USB 1.1 full-speed host with a tiny RV32I firmware core.
`ifndef GW_IDE
`define GW_IDE
`endif
//
// The USB controller and CPU run at 48 MHz.  MIDI messages cross into the
// 50 MHz audio clock domain through a request/acknowledge toggle mailbox.
// The 32-bit mailbox word is the USB-MIDI 1.0 event packet in byte order:
//   [31:24] cable/CIN, [23:16] MIDI status, [15:8] data1, [7:0] data2.
//
// USB PHY/SIE/RV32I sources are derived from thisiseth/tang-primer-25k-spi-io
// under the MIT licence retained in ../LICENSE.third-party.txt.
module usb_midi_host (
    input  wire        clk_48m,
    input  wire        clk_cpu_bram_96m,
    input  wire        reset,

    inout  wire        usb_dp,
    inout  wire        usb_dn,

    output wire        cpu_uart_tx,
    input  wire        cpu_uart_rx,

    output reg  [31:0] midi_event_data,
    output reg         midi_event_toggle,
    input  wire        midi_event_ack_toggle,
    output reg         midi_connected,

    // Counter generated in the independent 50 MHz board-clock domain.
    input  wire [31:0] debug_pll_unlock_count,

    // Bring-up observability. These outputs do not participate in normal
    // operation and may be left unconnected by the production top level.
    output wire        debug_reset_n,
    output wire        debug_cpu_fetch,
    output wire        debug_uart_write,
    output wire [31:0] debug_cpu_addr
);

    reg [3:0] reset_sync = 4'b0000;
    wire reset_n = reset_sync[0];
    always @(posedge clk_48m)
        reset_sync <= {~reset, reset_sync[3:1]};

    wire [31:0] cpu_addr;
    wire [31:0] cpu_read_data;
    wire [31:0] cpu_write_data;
    wire [1:0]  cpu_request;
    wire        cpu_write = (cpu_request == 2'b01);
    wire        cpu_read  = (cpu_request == 2'b00);

    RV32I u_cpu (
        .CLK(clk_48m),
`ifdef GW_IDE
        .clk_2x(clk_cpu_bram_96m),
`endif
        .RST_X(reset_n),
        .w_mic_addr(cpu_addr),
        .w_data(cpu_read_data),
        .w_mic_wdata(cpu_write_data),
        .w_mic_req(cpu_request),
        .w_mic_ctrl(),
        .w_stall(1'b0)
    );

    wire usb_regs_select = (cpu_addr[31:8] == 24'h210000);
    wire uart_select     = (cpu_addr[31:8] == 24'h200000);
    wire midi_select     = (cpu_addr[31:8] == 24'h220000);

    assign debug_reset_n    = reset_n;
    assign debug_cpu_fetch  = (cpu_request == 2'b10);
    assign debug_uart_write = uart_select && cpu_write &&
                              (cpu_addr[5:2] == 4'h0);
    assign debug_cpu_addr   = cpu_addr;

    wire [31:0] usb_read_data;
    wire [31:0] uart_read_data;
    wire [31:0] midi_read_data;
    assign cpu_read_data = usb_regs_select ? usb_read_data :
                           uart_select     ? uart_read_data :
                           midi_select     ? midi_read_data : 32'd0;

    // USB full-speed PHY and serial interface engine.
    wire       utmi_txvalid;
    wire       utmi_txready;
    wire       utmi_rxvalid;
    wire       utmi_rxactive;
    wire       utmi_rxerror;
    wire       utmi_termselect;
    wire       utmi_dppulldown;
    wire       utmi_dmpulldown;
    wire [1:0] utmi_linestate;
    wire [1:0] utmi_op_mode;
    wire [1:0] utmi_xcvrselect;
    wire [7:0] utmi_data_out;
    wire [7:0] utmi_data_in;
    wire       unused_dp_pullup;
    wire       unused_dn_pullup;

    usb11_phy u_usb_phy (
        .clk_i(clk_48m),
        .rst_i(~reset_n),
        .utmi_data_out_i(utmi_data_out),
        .utmi_txvalid_i(utmi_txvalid),
        .utmi_txready_o(utmi_txready),
        .utmi_data_in_o(utmi_data_in),
        .utmi_rxvalid_o(utmi_rxvalid),
        .utmi_rxactive_o(utmi_rxactive),
        .utmi_rxerror_o(utmi_rxerror),
        .utmi_linestate_o(utmi_linestate),
        .utmi_op_mode_i(utmi_op_mode),
        .utmi_xcvrselect_i(utmi_xcvrselect),
        .utmi_termselect_i(utmi_termselect),
        .utmi_dppulldown_i(utmi_dppulldown),
        .utmi_dmpulldown_i(utmi_dmpulldown),
        .usb_fpga_dp(usb_dp),
        .usb_fpga_dn(usb_dn),
        .usb_fpga_pu_dp(unused_dp_pullup),
        .usb_fpga_pu_dn(unused_dn_pullup)
    );

    wire [7:0] unused_usb_leds;
    usb11_regs u_usb_regs (
        .clk_i(clk_48m),
        .rst_i(~reset_n),
        .led_o(unused_usb_leds),
        .m_sel(usb_regs_select),
        .m_addr(cpu_addr[5:2]),
        .m_data_i(cpu_write_data),
        .m_data_o(usb_read_data),
        .m_rd(cpu_read),
        .m_wr(cpu_write),
        .m_intr_o(),
        .utmi_data_in_i(utmi_data_in),
        .utmi_rxvalid_i(utmi_rxvalid),
        .utmi_rxactive_i(utmi_rxactive),
        .utmi_rxerror_i(utmi_rxerror),
        .utmi_linestate_i(utmi_linestate),
        .utmi_data_out_o(utmi_data_out),
        .utmi_txvalid_o(utmi_txvalid),
        .utmi_txready_i(utmi_txready),
        .utmi_op_mode_o(utmi_op_mode),
        .utmi_xcvrselect_o(utmi_xcvrselect),
        .utmi_termselect_o(utmi_termselect),
        .utmi_dppulldown_o(utmi_dppulldown),
        .utmi_dmpulldown_o(utmi_dmpulldown)
    );

    usb_debug_uart u_uart (
        .clk_i(clk_48m),
        .reset_n(reset_n),
        .m_select(uart_select),
        .m_addr(cpu_addr[5:2]),
        .m_data_i(cpu_write_data),
        .m_data_o(uart_read_data),
        .m_read(cpu_read),
        .m_write(cpu_write),
        .uart_rx(cpu_uart_rx),
        .uart_tx(cpu_uart_tx)
    );

    // Toggle mailbox.  The firmware waits for bit 0 of register 0 before
    // writing register 1, so no event can overwrite an unconsumed event.
    reg [1:0] ack_sync;
    wire mailbox_ready = (ack_sync[1] == midi_event_toggle);
    assign midi_read_data = (cpu_addr[5:2] == 4'd0)
                          ? {30'd0, midi_connected, mailbox_ready}
                          : (cpu_addr[5:2] == 4'd1) ? midi_event_data
                          : (cpu_addr[5:2] == 4'd2) ? debug_pll_unlock_count
                          : 32'd0;

    always @(posedge clk_48m or negedge reset_n) begin
        if (!reset_n) begin
            ack_sync          <= 2'b00;
            midi_event_data   <= 32'd0;
            midi_event_toggle <= 1'b0;
            midi_connected    <= 1'b0;
        end else begin
            ack_sync <= {ack_sync[0], midi_event_ack_toggle};
            if (midi_select && cpu_write) begin
                case (cpu_addr[5:2])
                    4'd0: midi_connected <= cpu_write_data[0];
                    4'd1: if (mailbox_ready) begin
                        midi_event_data   <= cpu_write_data;
                        midi_event_toggle <= ~midi_event_toggle;
                    end
                    default: ;
                endcase
            end
        end
    end

endmodule

// Minimal 115200-baud UART used only for USB enumeration/MIDI diagnostics.
module usb_debug_uart (
    input  wire        clk_i,
    input  wire        reset_n,
    input  wire        m_select,
    input  wire [3:0]  m_addr,
    input  wire [31:0] m_data_i,
    output wire [31:0] m_data_o,
    input  wire        m_read,
    input  wire        m_write,
    input  wire        uart_rx,
    output reg         uart_tx
);
    // 48 MHz / 417 = 115107.9 baud (-0.08% from 115200).
    localparam integer BAUD_COUNT = 417;
    wire data_register = m_select && (m_addr == 4'h0);
    wire write_data = data_register && m_write;

    reg [8:0]  tx_shift;
    reg [8:0]  tx_timer;
    reg [3:0]  tx_bit_count;
    reg        tx_empty;

    reg [7:0]  rx_data;
    reg [11:0] start_timer;
    reg [12:0] rx_timer;
    reg [3:0]  rx_state;
    wire       rx_full = (rx_state == 4'd8);

    reg [31:0] millisecond_counter;
    reg [15:0] millisecond_timer;

    assign m_data_o = (m_addr == 4'h0) ? {24'd0, rx_data} :
                      (m_addr == 4'h1) ? {29'd0, 1'b0, rx_full, tx_empty} :
                      (m_addr == 4'h2) ? millisecond_counter : 32'd0;

    always @(posedge clk_i or negedge reset_n) begin
        if (!reset_n) begin
            uart_tx     <= 1'b1;
            tx_empty    <= 1'b1;
            tx_shift    <= 9'h1ff;
            tx_timer    <= 9'd0;
            tx_bit_count <= 4'd0;
        end else if (tx_empty) begin
            uart_tx  <= 1'b1;
            tx_timer <= 9'd0;
            if (write_data) begin
                tx_empty     <= 1'b0;
                tx_shift     <= {m_data_i[7:0], 1'b0};
                tx_bit_count <= 4'd10;
            end
        end else if (tx_timer >= BAUD_COUNT - 1) begin
            uart_tx      <= tx_shift[0];
            tx_empty     <= (tx_bit_count == 1);
            tx_shift     <= {1'b1, tx_shift[8:1]};
            tx_timer     <= 9'd0;
            tx_bit_count <= tx_bit_count - 1'b1;
        end else begin
            tx_timer <= tx_timer + 1'b1;
        end
    end

    always @(posedge clk_i or negedge reset_n) begin
        if (!reset_n)
            start_timer <= 12'd0;
        else
            start_timer <= uart_rx ? 12'd0 : start_timer + 1'b1;
    end

    always @(posedge clk_i or negedge reset_n) begin
        if (!reset_n) begin
            rx_timer <= 13'd1;
            rx_state <= 4'd0;
            rx_data  <= 8'd0;
        end else if (rx_state == 4'd0) begin
            rx_timer <= BAUD_COUNT;
            if (start_timer == (BAUD_COUNT >> 1))
                rx_state <= 4'd1;
        end else if (rx_timer != BAUD_COUNT) begin
            rx_timer <= rx_timer + 1'b1;
        end else begin
            rx_state <= (rx_state == 4'd9) ? 4'd0 : rx_state + 1'b1;
            rx_data  <= {uart_rx, rx_data[7:1]};
            rx_timer <= 13'd1;
        end
    end

    always @(posedge clk_i or negedge reset_n) begin
        if (!reset_n) begin
            millisecond_counter <= 32'd0;
            millisecond_timer   <= 16'd0;
        end else if (millisecond_timer == 16'd47999) begin
            millisecond_timer   <= 16'd0;
            millisecond_counter <= millisecond_counter + 1'b1;
        end else begin
            millisecond_timer <= millisecond_timer + 1'b1;
        end
    end

    wire unused_read = m_read;
endmodule
