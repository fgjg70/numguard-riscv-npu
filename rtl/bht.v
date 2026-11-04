`timescale 1ns/1ps

module bht (
	clk,
	rst,
	pred_pc,
	ctr_q,
	upd_en,
	upd_pc,
	upd_taken,
	upd_is_cond,
	align_err_cnt
);
	parameter [31:0] IDX_BITS = 13;
	parameter [31:0] GH_BITS = 13;
	parameter [31:0] PC_TOP = 14;
	parameter [0:0] HIST_ALL_CTRL = 1'b1;
	input wire clk;
	input wire rst;
	input wire [31:0] pred_pc;
	output reg [1:0] ctr_q;
	input wire upd_en;
	input wire [31:0] upd_pc;
	input wire upd_taken;
	input wire upd_is_cond;
	output reg [15:0] align_err_cnt;
	localparam [31:0] N = 1 << IDX_BITS;
	localparam [31:0] PW = PC_TOP - 2;
	localparam [IDX_BITS - 1:0] GH_MASK = (GH_BITS >= IDX_BITS ? {IDX_BITS {1'b1}} : {IDX_BITS {1'b1}} >> (IDX_BITS - GH_BITS));
	(* ram_style = "block" *) reg [1:0] mem [0:N - 1];
	reg [IDX_BITS - 1:0] ghr;
	wire [IDX_BITS - 1:0] rd_idx = pred_pc[IDX_BITS:1] ^ (ghr & GH_MASK);
	wire [PW - 1:0] pc_w = pred_pc[PC_TOP - 1:2];
	reg [IDX_BITS - 1:0] idx_d1;
	reg [IDX_BITS - 1:0] idx_d2;
	reg [PW - 1:0] pcw_d1;
	reg [PW - 1:0] pcw_d2;
	reg [1:0] ctr_d1;
	wire align_bad = pcw_d2 != upd_pc[PC_TOP - 1:2];
	wire [IDX_BITS - 1:0] wr_idx = (align_bad ? upd_pc[IDX_BITS:1] ^ (ghr & GH_MASK) : idx_d2);
	wire [1:0] ctr_new = (upd_taken ? (ctr_d1 == 2'b11 ? 2'b11 : ctr_d1 + 2'b01) : (ctr_d1 == 2'b00 ? 2'b00 : ctr_d1 - 2'b01));
	integer i;
	initial $readmemh("firmware/bht_init.hex", mem);
	always @(posedge clk)
		if (rst) begin
			ctr_q <= 2'b01;
			ghr <= 1'sb0;
			idx_d1 <= 1'sb0;
			idx_d2 <= 1'sb0;
			pcw_d1 <= 1'sb0;
			pcw_d2 <= 1'sb0;
			align_err_cnt <= 16'd0;
			ctr_d1 <= 2'b01;
		end
		else begin
			if (upd_en)
				mem[wr_idx] <= ctr_new;
			ctr_q <= mem[rd_idx];
			idx_d1 <= rd_idx;
			idx_d2 <= idx_d1;
			pcw_d1 <= pc_w;
			pcw_d2 <= pcw_d1;
			ctr_d1 <= ctr_q;
			if (upd_en) begin
				if (HIST_ALL_CTRL || upd_is_cond)
					ghr <= {ghr[IDX_BITS - 2:0], upd_taken};
				if (align_bad)
					align_err_cnt <= align_err_cnt + 16'd1;
			end
		end
endmodule
