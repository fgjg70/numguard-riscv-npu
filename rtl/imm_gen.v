`timescale 1ns/1ps

module imm_gen (
	instr,
	fmt,
	imm_out
);
	reg _sv2v_0;
	parameter [31:0] XLEN = 32;
	input wire [XLEN - 1:0] instr;
	input wire [2:0] fmt;
	output reg [XLEN - 1:0] imm_out;
	wire [XLEN - 1:0] imm_i;
	wire [XLEN - 1:0] imm_s;
	wire [XLEN - 1:0] imm_b;
	wire [XLEN - 1:0] imm_u;
	wire [XLEN - 1:0] imm_j;
	assign imm_i = {{XLEN - 12 {instr[31]}}, instr[31:20]};
	assign imm_s = {{XLEN - 12 {instr[31]}}, instr[31:25], instr[11:7]};
	assign imm_b = {{XLEN - 13 {instr[31]}}, instr[31], instr[7], instr[30:25], instr[11:8], 1'b0};
	assign imm_u = {instr[31:12], {XLEN - 20 {1'b0}}};
	assign imm_j = {{XLEN - 21 {instr[31]}}, instr[31], instr[19:12], instr[20], instr[30:21], 1'b0};
	always @(*) begin
		if (_sv2v_0)
			;
		case (fmt)
			3'd0: imm_out = imm_i;
			3'd1: imm_out = imm_s;
			3'd2: imm_out = imm_b;
			3'd3: imm_out = imm_u;
			3'd4: imm_out = imm_j;
			default: imm_out = 1'sbx;
		endcase
	end
	initial _sv2v_0 = 0;
endmodule
