`timescale 1ns/1ps

module btb (
	clk,
	rst,
	pred_pc,
	pred_hit,
	pred_taken,
	pred_target,
	pred_is_ret,
	p2_pc,
	p2_pc_pht,
	p2_hold,
	q_hit,
	q_taken,
	q_target,
	q_is_ret,
	upd_en,
	upd_pc,
	upd_taken,
	upd_target,
	upd_is_ret,
	upd_is_cond,
	align_err_cnt
);
	parameter [31:0] IDX_BITS = 9;
	parameter [31:0] PC_TOP = 14;
	localparam [31:0] rv32_pkg_IMEM_BASE = 32'h80000000;
	parameter [31:0] PC_BASE = rv32_pkg_IMEM_BASE;
	parameter [31:0] PHT_BITS = 13;
	parameter [31:0] GH_BITS = 13;
	parameter [0:0] HIST_ALL_CTRL = 1'b1;
	input wire clk;
	input wire rst;
	input wire [31:0] pred_pc;
	output wire pred_hit;
	output wire pred_taken;
	output wire [31:0] pred_target;
	output wire pred_is_ret;
	input wire [31:0] p2_pc;
	input wire [31:0] p2_pc_pht;
	input wire p2_hold;
	output wire q_hit;
	output wire q_taken;
	output wire [31:0] q_target;
	output wire q_is_ret;
	input wire upd_en;
	input wire [31:0] upd_pc;
	input wire upd_taken;
	input wire [31:0] upd_target;
	input wire upd_is_ret;
	input wire upd_is_cond;
	output wire [15:0] align_err_cnt;
	localparam [31:0] N = 1 << IDX_BITS;
	localparam [31:0] TAG_BITS = (PC_TOP - IDX_BITS) - 2;
	localparam [31:0] TGT_BITS = PC_TOP;
	reg vld [0:N - 1];
	reg [TAG_BITS - 1:0] tag [0:N - 1];
	reg [TGT_BITS - 1:0] tgt [0:N - 1];
	reg is_ret [0:N - 1];
	reg is_cond [0:N - 1];
	wire [IDX_BITS - 1:0] uidx = upd_pc[IDX_BITS + 1:2];
	wire [TAG_BITS - 1:0] utag = upd_pc[PC_TOP - 1:IDX_BITS + 2];
	wire [IDX_BITS - 1:0] pidx = p2_pc[IDX_BITS + 1:2];
	wire [TAG_BITS - 1:0] ptag = p2_pc[PC_TOP - 1:IDX_BITS + 2];
	reg q_vld;
	reg q_isret_r;
	reg q_cond_r;
	reg [TAG_BITS - 1:0] q_tag;
	reg [TAG_BITS - 1:0] q_tagexp;
	reg [TGT_BITS - 1:0] q_tgt;
	wire [1:0] q_ctr;
	wire [31:0] pht_pc = p2_pc_pht;
	bht #(
		.IDX_BITS(PHT_BITS),
		.GH_BITS(GH_BITS),
		.PC_TOP(PC_TOP),
		.HIST_ALL_CTRL(HIST_ALL_CTRL)
	) u_bht(
		.clk(clk),
		.rst(rst),
		.pred_pc(pht_pc),
		.ctr_q(q_ctr),
		.upd_en(upd_en),
		.upd_pc(upd_pc),
		.upd_taken(upd_taken),
		.upd_is_cond(upd_is_cond),
		.align_err_cnt(align_err_cnt)
	);
	assign q_hit = q_vld && (q_tag == q_tagexp);
	assign q_target = {PC_BASE[31:PC_TOP], q_tgt};
	assign q_is_ret = q_hit && q_isret_r;
	assign q_taken = q_hit && (q_cond_r ? q_ctr[1] : 1'b1);
	assign pred_hit = 1'b0;
	assign pred_taken = 1'b0;
	assign pred_target = 32'd0;
	assign pred_is_ret = 1'b0;
	integer i;
	initial begin
		$readmemh("firmware/btb_zero.hex", vld);
		$readmemh("firmware/btb_zero.hex", tag);
		$readmemh("firmware/btb_zero.hex", tgt);
		$readmemh("firmware/btb_zero.hex", is_ret);
		$readmemh("firmware/btb_zero.hex", is_cond);
	end
	always @(posedge clk)
		if (rst) begin
			q_vld <= 1'b0;
			q_isret_r <= 1'b0;
			q_cond_r <= 1'b0;
			q_tag <= 1'sb0;
			q_tagexp <= 1'sb0;
			q_tgt <= 1'sb0;
		end
		else begin
			if (upd_en) begin
				vld[uidx] <= 1'b1;
				tag[uidx] <= utag;
				tgt[uidx] <= upd_target[PC_TOP - 1:0];
				is_ret[uidx] <= upd_is_ret;
				is_cond[uidx] <= upd_is_cond;
			end
			if (!p2_hold) begin
				q_vld <= vld[pidx];
				q_tag <= tag[pidx];
				q_tagexp <= ptag;
				q_tgt <= tgt[pidx];
				q_isret_r <= is_ret[pidx];
				q_cond_r <= is_cond[pidx];
			end
		end
endmodule
