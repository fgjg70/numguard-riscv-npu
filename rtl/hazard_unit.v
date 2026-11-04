`timescale 1ns/1ps

module hazard_unit (
	id_ex_mem_read,
	id_ex_reg_write,
	id_ex_rd,
	ex_mem_reg_write,
	ex_mem_mem_read,
	ex_mem_rd,
	if_id_rs1,
	if_id_rs2,
	lsu_stall,
	ex_busy,
	redirect_raw,
	fwd_a_pre,
	fwd_b_pre,
	pipe_stall,
	id_ex_bubble,
	hold_mem,
	redirect
);
	reg _sv2v_0;
	input wire id_ex_mem_read;
	input wire id_ex_reg_write;
	input wire [4:0] id_ex_rd;
	input wire ex_mem_reg_write;
	input wire ex_mem_mem_read;
	input wire [4:0] ex_mem_rd;
	input wire [4:0] if_id_rs1;
	input wire [4:0] if_id_rs2;
	input wire lsu_stall;
	input wire ex_busy;
	input wire redirect_raw;
	output reg [1:0] fwd_a_pre;
	output reg [1:0] fwd_b_pre;
	output wire pipe_stall;
	output wire id_ex_bubble;
	output wire hold_mem;
	output wire redirect;
	reg load_use_ex;
	reg load_use_mem;
	reg load_use;
	always @(*) begin
		if (_sv2v_0)
			;
		load_use_ex = (id_ex_mem_read && (id_ex_rd != 5'd0)) && ((id_ex_rd == if_id_rs1) || (id_ex_rd == if_id_rs2));
		load_use_mem = 1'b0;
		load_use = load_use_ex || load_use_mem;
	end
	assign pipe_stall = (load_use || lsu_stall) || ex_busy;
	assign id_ex_bubble = load_use && !(lsu_stall || ex_busy);
	assign hold_mem = lsu_stall || ex_busy;
	assign redirect = redirect_raw && !((lsu_stall || ex_busy) || load_use_ex);
	reg pre_ex_a;
	reg pre_wb_a;
	reg pre_ex_b;
	reg pre_wb_b;
	always @(*) begin
		if (_sv2v_0)
			;
		pre_ex_a = ((id_ex_reg_write && !id_ex_mem_read) && (id_ex_rd != 5'd0)) && (id_ex_rd == if_id_rs1);
		pre_wb_a = (ex_mem_reg_write && (ex_mem_rd != 5'd0)) && (ex_mem_rd == if_id_rs1);
		pre_ex_b = ((id_ex_reg_write && !id_ex_mem_read) && (id_ex_rd != 5'd0)) && (id_ex_rd == if_id_rs2);
		pre_wb_b = (ex_mem_reg_write && (ex_mem_rd != 5'd0)) && (ex_mem_rd == if_id_rs2);
		fwd_a_pre = (pre_ex_a ? 2'd1 : (pre_wb_a ? 2'd2 : 2'd0));
		fwd_b_pre = (pre_ex_b ? 2'd1 : (pre_wb_b ? 2'd2 : 2'd0));
	end
	initial _sv2v_0 = 0;
endmodule
