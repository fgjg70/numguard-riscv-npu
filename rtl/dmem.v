`timescale 1ns/1ps

module dmem (
	clk,
	addr,
	wdata,
	wstrb,
	we,
	rdata
);
	localparam [31:0] rv32_pkg_DMEM_BASE = 32'h90000000;
	parameter [31:0] BASE = rv32_pkg_DMEM_BASE;
	localparam [31:0] rv32_pkg_DMEM_BYTES = 16384;
	parameter [31:0] BYTES = rv32_pkg_DMEM_BYTES;
	input wire clk;
	input wire [31:0] addr;
	input wire [31:0] wdata;
	input wire [3:0] wstrb;
	input wire we;
	output reg [31:0] rdata;
	localparam [31:0] WORDS = BYTES / 4;
	localparam [31:0] AW = $clog2(WORDS);
	reg [31:0] mem [0:WORDS - 1];
	initial $readmemh("firmware/vision_dmem.hex", mem);
	always @(posedge clk) begin
		if (we) begin
			if (wstrb[0])
				mem[addr[AW + 1:2]][7:0] <= wdata[7:0];
			if (wstrb[1])
				mem[addr[AW + 1:2]][15:8] <= wdata[15:8];
			if (wstrb[2])
				mem[addr[AW + 1:2]][23:16] <= wdata[23:16];
			if (wstrb[3])
				mem[addr[AW + 1:2]][31:24] <= wdata[31:24];
		end
		rdata <= mem[addr[AW + 1:2]];
	end
endmodule
