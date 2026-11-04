# hier_util.tcl -- report resources hierarchically: "what did this module become?"
#
# WHY: the flat synthesis report only gives totals, so it cannot tell whether an
#      array was inferred as BRAM, LUTRAM, or plain LUT logic. This script runs
#      synthesis + report_utilization -hierarchical and prints LUT / FF / LUTRAM /
#      BRAM / DSP per submodule.
#
# REAL CASE (measured in this project):
#   imem.sv used to be a combinational read and got synthesized into **1228 plain
#   LUTs** (neither BRAM nor LUTRAM), i.e. 31% of the whole core. The flat report
#   hides it completely: the Block RAM table only lists dmem, the Distributed RAM
#   table only lists btb/regfile, and imem appears nowhere.
#
# HOW TO RUN:
#   vivado -mode batch -nojournal -log hier_util.log -source tools/hier_util.tcl
#   NOTE: vivado is not on PATH -- call settings64.bat first. When the session env
#   is stripped you must also `set PROCESSOR_ARCHITECTURE=AMD64`, otherwise Vivado
#   fails with "32-bit platform is not supported".
#
# OUTPUT: fpga/hier_util.rpt
#
# KEEP THIS FILE PURE ASCII: Vivado 2018.3 mis-parses non-ASCII Tcl comments and
# reports "invalid command name" (PITFALLS R21 -- it destroyed a file once).

# E-02: derive the repo root from this script's own location (tools/ -> ..),
# instead of hardcoding an absolute path that breaks on any other machine.
set root [file normalize [file join [file dirname [info script]] ..]]
puts "HIER_ROOT=$root"

read_verilog -sv [list "$root/rv32_pkg.sv" "$root/alu.sv" "$root/branch_unit.sv" "$root/control_unit.sv" "$root/imm_gen.sv" "$root/pc.sv" "$root/regfile.sv" "$root/id_ex_reg.sv" "$root/ex_mem_reg.sv" "$root/mem_wb_reg.sv" "$root/hazard_unit.sv" "$root/load_store_unit.sv" "$root/imem.sv" "$root/dmem.sv" "$root/btb.sv" "$root/muldiv.sv" "$root/csr_file.sv" "$root/core.sv" "$root/fpga/mmio_subsys.sv" "$root/fpga/top_a100t.sv"]
# Both images MUST be defined, otherwise the whole datapath is constant-folded
# into a handful of LUTs (PITFALLS: "synthesis produced 25 LUTs" -- measured by
# replacing imem with constant NOP, the core dropped to 3 LUTs).
synth_design -top top_a100t -part xc7a100tfgg484-2 -verilog_define "IMEM_INIT_FILE=\"$root/sim/prog/cm_hw.hex\" DMEM_INIT_FILE=\"$root/sim/prog/cm_hw.dmem.hex\""
report_utilization -hierarchical -hierarchical_depth 3 -file hier_util.rpt
puts "HIER_OK"
