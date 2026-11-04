`timescale 1ns/1ps

module mem_wb_reg (
	clk,
	rst,
	stall,
	ctrl_in,
	alu_out_in,
	load_data_in,
	pc_plus4_in,
	rd_addr_in,
	ctrl,
	alu_out,
	load_data,
	pc_plus4,
	rd_addr
);
	input wire clk;
	input wire rst;
	input wire stall;
	input wire [55:0] ctrl_in;
	input wire [31:0] alu_out_in;
	input wire [31:0] load_data_in;
	input wire [31:0] pc_plus4_in;
	input wire [4:0] rd_addr_in;
	output reg [55:0] ctrl;
	output reg [31:0] alu_out;
	output reg [31:0] load_data;
	output reg [31:0] pc_plus4;
	output reg [4:0] rd_addr;
	always @(posedge clk or posedge rst)
		if (rst) begin
			ctrl <= 1'sb0;
			alu_out <= 1'sb0;
			load_data <= 1'sb0;
			pc_plus4 <= 1'sb0;
			rd_addr <= 1'sb0;
		end
		else if (!stall) begin
			ctrl <= ctrl_in;
			alu_out <= alu_out_in;
			load_data <= load_data_in;
			pc_plus4 <= pc_plus4_in;
			rd_addr <= rd_addr_in;
		end
endmodule
