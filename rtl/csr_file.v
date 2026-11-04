`timescale 1ns/1ps

module csr_file (
	clk,
	rst,
	raddr,
	rdata,
	we,
	waddr,
	wdata,
	trap_req,
	trap_cause,
	trap_pc,
	trap_tval,
	trap_enter,
	trap_vector,
	mret_req,
	mret_taken,
	mret_pc,
	irq_timer,
	irq_ext,
	irq_pending,
	irq_cause
);
	reg _sv2v_0;
	parameter [31:0] MTVEC_RESET = 32'h80000100;
	parameter [31:0] MISA_VALUE = 32'h40001100;
	input wire clk;
	input wire rst;
	input wire [11:0] raddr;
	output reg [31:0] rdata;
	input wire we;
	input wire [11:0] waddr;
	input wire [31:0] wdata;
	input wire trap_req;
	input wire [4:0] trap_cause;
	input wire [31:0] trap_pc;
	input wire [31:0] trap_tval;
	output wire trap_enter;
	output wire [31:0] trap_vector;
	input wire mret_req;
	output wire mret_taken;
	output wire [31:0] mret_pc;
	input wire irq_timer;
	input wire irq_ext;
	output wire irq_pending;
	output wire [4:0] irq_cause;
	localparam [11:0] CSR_MSTATUS = 12'h300;
	localparam [11:0] CSR_MISA = 12'h301;
	localparam [11:0] CSR_MIE = 12'h304;
	localparam [11:0] CSR_MTVEC = 12'h305;
	localparam [11:0] CSR_MSCRATCH = 12'h340;
	localparam [11:0] CSR_MEPC = 12'h341;
	localparam [11:0] CSR_MCAUSE = 12'h342;
	localparam [11:0] CSR_MTVAL = 12'h343;
	localparam [11:0] CSR_MIP = 12'h344;
	localparam [11:0] CSR_MVENDORID = 12'hf11;
	localparam [11:0] CSR_MARCHID = 12'hf12;
	localparam [11:0] CSR_MIMPID = 12'hf13;
	localparam [11:0] CSR_MHARTID = 12'hf14;
	localparam signed [31:0] MSTATUS_MIE = 3;
	localparam signed [31:0] MSTATUS_MPIE = 7;
	localparam signed [31:0] MSTATUS_MPP = 11;
	localparam signed [31:0] IRQ_MTIP = 7;
	localparam signed [31:0] IRQ_MEIP = 11;
	reg st_mie;
	reg st_mpie;
	reg [1:0] st_mpp;
	reg [31:0] mtvec;
	reg [31:0] mscratch;
	reg [31:0] mepc;
	reg [31:0] mcause;
	reg [31:0] mtval;
	reg ie_mtie;
	reg ie_meie;
	wire [31:0] mip_val = {20'b00000000000000000000, irq_ext, 3'b000, irq_timer, 7'b0000000};
	always @(*) begin
		if (_sv2v_0)
			;
		case (raddr)
			CSR_MSTATUS: rdata = {19'b0000000000000000000, st_mpp, 3'b000, st_mpie, 3'b000, st_mie, 3'b000};
			CSR_MISA: rdata = MISA_VALUE;
			CSR_MIE: rdata = {20'b00000000000000000000, ie_meie, 3'b000, ie_mtie, 7'b0000000};
			CSR_MTVEC: rdata = mtvec;
			CSR_MSCRATCH: rdata = mscratch;
			CSR_MEPC: rdata = mepc;
			CSR_MCAUSE: rdata = mcause;
			CSR_MTVAL: rdata = mtval;
			CSR_MIP: rdata = mip_val;
			CSR_MVENDORID, CSR_MARCHID, CSR_MIMPID, CSR_MHARTID: rdata = 32'd0;
			default: rdata = 32'd0;
		endcase
	end
	wire mtip = irq_timer;
	wire meip = irq_ext;
	wire timer_irq_req = (st_mie && ie_mtie) && mtip;
	wire ext_irq_req = (st_mie && ie_meie) && meip;
	assign irq_pending = timer_irq_req || ext_irq_req;
	localparam [3:0] rv32_pkg_IRQ_EXT = 4'd11;
	localparam [3:0] rv32_pkg_IRQ_TIMER = 4'd7;
	assign irq_cause = (timer_irq_req ? {1'b1, rv32_pkg_IRQ_TIMER} : {1'b1, rv32_pkg_IRQ_EXT});
	assign trap_enter = trap_req;
	assign trap_vector = {mtvec[31:2], 2'b00};
	assign mret_taken = mret_req;
	assign mret_pc = mepc;
	always @(posedge clk or posedge rst)
		if (rst) begin
			st_mie <= 1'b0;
			st_mpie <= 1'b0;
			st_mpp <= 2'b11;
			mtvec <= MTVEC_RESET;
			mscratch <= 32'd0;
			mepc <= 32'd0;
			mcause <= 32'd0;
			mtval <= 32'd0;
			ie_mtie <= 1'b0;
			ie_meie <= 1'b0;
		end
		else if (trap_req) begin
			mepc <= trap_pc;
			mcause <= {trap_cause[4], 27'b000000000000000000000000000, trap_cause[3:0]};
			mtval <= trap_tval;
			st_mpie <= st_mie;
			st_mie <= 1'b0;
			st_mpp <= 2'b11;
		end
		else if (mret_req) begin
			st_mie <= st_mpie;
			st_mpie <= 1'b1;
			st_mpp <= 2'b00;
		end
		else if (we)
			case (waddr)
				CSR_MSTATUS: begin
					st_mie <= wdata[MSTATUS_MIE];
					st_mpie <= wdata[MSTATUS_MPIE];
					st_mpp <= wdata[MSTATUS_MPP+:2];
				end
				CSR_MIE: begin
					ie_mtie <= wdata[IRQ_MTIP];
					ie_meie <= wdata[IRQ_MEIP];
				end
				CSR_MTVEC: mtvec <= {wdata[31:2], 2'b00};
				CSR_MSCRATCH: mscratch <= wdata;
				CSR_MEPC: mepc <= {wdata[31:2], 2'b00};
				CSR_MCAUSE: mcause <= wdata;
				CSR_MTVAL: mtval <= wdata;
				default:
					;
			endcase
	initial _sv2v_0 = 0;
endmodule
