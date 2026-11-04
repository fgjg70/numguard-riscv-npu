`timescale 1ns/1ps

module regfile (
	clk,
	we,
	waddr,
	wdata,
	raddr1,
	raddr2,
	rdata1,
	rdata2,
	dbg_raddr,
	dbg_rdata
);
	parameter [31:0] XLEN = 32;
	parameter [31:0] NUM_REGS = 32;
	input wire clk;
	input wire we;
	input wire [$clog2(NUM_REGS) - 1:0] waddr;
	input wire [XLEN - 1:0] wdata;
	input wire [$clog2(NUM_REGS) - 1:0] raddr1;
	input wire [$clog2(NUM_REGS) - 1:0] raddr2;
	output wire [XLEN - 1:0] rdata1;
	output wire [XLEN - 1:0] rdata2;
	input wire [$clog2(NUM_REGS) - 1:0] dbg_raddr;
	output wire [XLEN - 1:0] dbg_rdata;
	(* syn_ramstyle = "select_ram" *) reg [XLEN - 1:0] mem [0:NUM_REGS - 1];
	initial $readmemh("firmware/regfile_zero.hex", mem);
	always @(posedge clk)
		if (we && (waddr != {$clog2(NUM_REGS) {1'sb0}}))
			mem[waddr] <= wdata;
	assign rdata1 = (raddr1 == {$clog2(NUM_REGS) {1'sb0}} ? {XLEN {1'sb0}} : (we && (waddr == raddr1) ? wdata : mem[raddr1]));
	assign rdata2 = (raddr2 == {$clog2(NUM_REGS) {1'sb0}} ? {XLEN {1'sb0}} : (we && (waddr == raddr2) ? wdata : mem[raddr2]));
	assign dbg_rdata = mem[dbg_raddr];
endmodule
