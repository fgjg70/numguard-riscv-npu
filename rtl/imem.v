`timescale 1ns/1ps

module imem (
	clk,
	addr,
	rdata
);
	localparam [31:0] rv32_pkg_IMEM_BASE = 32'h80000000;
	parameter [31:0] BASE = rv32_pkg_IMEM_BASE;
	localparam [31:0] rv32_pkg_IMEM_BYTES = 16384;
	parameter [31:0] BYTES = rv32_pkg_IMEM_BYTES;
	input wire clk;
	input wire [31:0] addr;
	output reg [31:0] rdata;
	localparam [31:0] WORDS = BYTES / 4;
	localparam [31:0] AW = $clog2(WORDS);
	reg [31:0] mem [0:WORDS - 1];
	initial $readmemh("firmware/vision_imem.hex", mem);
	always @(posedge clk) rdata <= mem[addr[AW + 1:2]];
endmodule
