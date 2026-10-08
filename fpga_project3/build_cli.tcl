# Command-line build script for Gowin gw_sh.
# Run this script from the project root so $readmemh relative paths resolve.

add_file -type verilog "src/i2s_tx.v"
add_file -type verilog "src/uart_rx.v"
add_file -type verilog "src/synth_uart_control.v"
add_file -type verilog "src/midi_poly_synth.v"
add_file -type verilog "src/midi_performance_control.v"
add_file -type verilog "src/audio_effects.v"
add_file -type verilog "src/usb_midi/rtl/rv32i.v"
add_file -type verilog "src/usb_midi/rtl/usb11_phy.v"
add_file -type verilog "src/usb_midi/rtl/usb11_regs.v"
add_file -type verilog "src/usb_midi/rtl/usb11_sie.v"
add_file -type verilog "src/usb_midi/rtl/usb_pll.v"
add_file -type verilog "src/usb_midi/rtl/usb_midi_host.v"
add_file -type verilog "src/top.v"
add_file -type cst "src/fpga_project.cst"
add_file -type sdc "src/fpga_project.sdc"

set_device GW5A-LV25MG121NC1/I0 -device_version B
set_option -synthesis_tool gowinsynthesis
set_option -top_module top
set_option -output_base_name fpga_project
set_option -verilog_std sysv2017
set_option -use_sspi_as_gpio 1
set_option -use_cpu_as_gpio 1

run all
