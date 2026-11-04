`timescale 1ns/1ps

module pc (
	clk,
	rst,
	stall,
	flush,
	flush_pc,
	pc_src,
	pc_target,
	pc,
	pc_plus4
);
	parameter [31:0] XLEN = 32;
	parameter [XLEN - 1:0] RESET_PC = 32'h00000000;
	input wire clk;
	input wire rst;
	input wire stall;
	input wire flush;
	input wire [XLEN - 1:0] flush_pc;
	input wire pc_src;
	input wire [XLEN - 1:0] pc_target;
	output reg [XLEN - 1:0] pc;
	output wire [XLEN - 1:0] pc_plus4;
	function automatic signed [XLEN - 1:0] sv2v_cast_1FFAE_signed;
		input reg signed [XLEN - 1:0] inp;
		sv2v_cast_1FFAE_signed = inp;
	endfunction
	assign pc_plus4 = pc + sv2v_cast_1FFAE_signed(4);
	always @(posedge clk or posedge rst)
		if (rst)
			pc <= RESET_PC;
		else if (flush)
			pc <= flush_pc;
		else if (!stall)
			pc <= (pc_src ? pc_target : pc_plus4);
endmodule
