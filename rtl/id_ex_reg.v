`timescale 1ns/1ps

module id_ex_reg (
	clk,
	rst,
	flush,
	stall,
	bubble,
	valid_in,
	instr_in,
	ctrl_in,
	pc_in,
	pc_plus4_in,
	rs1_data_in,
	rs2_data_in,
	imm_in,
	rs1_addr_in,
	rs2_addr_in,
	rd_addr_in,
	pred_taken_in,
	pred_target_in,
	ctrl,
	valid,
	instr,
	pc,
	pc_plus4,
	rs1_data,
	rs2_data,
	imm,
	rs1_addr,
	rs2_addr,
	rd_addr,
	pred_taken,
	pred_target
);
	input wire clk;
	input wire rst;
	input wire flush;
	input wire stall;
	input wire bubble;
	input wire valid_in;
	input wire [31:0] instr_in;
	input wire [55:0] ctrl_in;
	input wire [31:0] pc_in;
	input wire [31:0] pc_plus4_in;
	input wire [31:0] rs1_data_in;
	input wire [31:0] rs2_data_in;
	input wire [31:0] imm_in;
	input wire [4:0] rs1_addr_in;
	input wire [4:0] rs2_addr_in;
	input wire [4:0] rd_addr_in;
	input wire pred_taken_in;
	input wire [31:0] pred_target_in;
	output reg [55:0] ctrl;
	output reg valid;
	output reg [31:0] instr;
	output reg [31:0] pc;
	output reg [31:0] pc_plus4;
	output reg [31:0] rs1_data;
	output reg [31:0] rs2_data;
	output reg [31:0] imm;
	output reg [4:0] rs1_addr;
	output reg [4:0] rs2_addr;
	output reg [4:0] rd_addr;
	output reg pred_taken;
	output reg [31:0] pred_target;
	always @(posedge clk or posedge rst)
		if (rst) begin
			ctrl <= 1'sb0;
			valid <= 1'b0;
			instr <= 1'sb0;
			pc <= 1'sb0;
			pc_plus4 <= 1'sb0;
			rs1_data <= 1'sb0;
			rs2_data <= 1'sb0;
			imm <= 1'sb0;
			rs1_addr <= 1'sb0;
			rs2_addr <= 1'sb0;
			rd_addr <= 1'sb0;
			pred_taken <= 1'b0;
			pred_target <= 1'sb0;
		end
		else if (flush || bubble) begin
			ctrl <= 1'sb0;
			valid <= 1'b0;
			instr <= 1'sb0;
			pc <= 1'sb0;
			pc_plus4 <= 1'sb0;
			rs1_data <= 1'sb0;
			rs2_data <= 1'sb0;
			imm <= 1'sb0;
			rs1_addr <= 1'sb0;
			rs2_addr <= 1'sb0;
			rd_addr <= 1'sb0;
			pred_taken <= 1'b0;
			pred_target <= 1'sb0;
		end
		else if (!stall) begin
			ctrl <= ctrl_in;
			valid <= valid_in;
			instr <= instr_in;
			pc <= pc_in;
			pc_plus4 <= pc_plus4_in;
			rs1_data <= rs1_data_in;
			rs2_data <= rs2_data_in;
			imm <= imm_in;
			rs1_addr <= rs1_addr_in;
			rs2_addr <= rs2_addr_in;
			rd_addr <= rd_addr_in;
			pred_taken <= pred_taken_in;
			pred_target <= pred_target_in;
		end
endmodule
