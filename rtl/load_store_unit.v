`timescale 1ns/1ps

module load_store_unit (
	clk,
	rst,
	stall_external,
	req_read,
	req_write,
	req_size,
	req_unsigned,
	req_addr,
	req_wdata,
	amo_en,
	amo_newdata,
	amo_lr,
	amo_sc,
	dmem_addr,
	dmem_wdata,
	dmem_wstrb,
	dmem_we,
	dmem_rdata,
	load_data,
	lsu_stall,
	amo_write_phase,
	amo_old_out
);
	reg _sv2v_0;
	input wire clk;
	input wire rst;
	input wire stall_external;
	input wire req_read;
	input wire req_write;
	input wire [1:0] req_size;
	input wire req_unsigned;
	input wire [31:0] req_addr;
	input wire [31:0] req_wdata;
	input wire amo_en;
	input wire [31:0] amo_newdata;
	input wire amo_lr;
	input wire amo_sc;
	output wire [31:0] dmem_addr;
	output wire [31:0] dmem_wdata;
	output wire [3:0] dmem_wstrb;
	output wire dmem_we;
	input wire [31:0] dmem_rdata;
	output reg [31:0] load_data;
	output wire lsu_stall;
	output wire amo_write_phase;
	output wire [31:0] amo_old_out;
	localparam [1:0] S_IDLE = 2'd0;
	localparam [1:0] S_HEAD = 2'd1;
	localparam [1:0] S_TAIL = 2'd2;
	localparam [1:0] S_AMO = 2'd3;
	wire amo_wr;
	reg amo_phase;
	reg amo_wait;
	reg amo_active;
	reg [31:0] amo_old;
	reg [31:0] amo_addr_r;
	reg amo_mode_r;
	reg [1:0] state;
	wire [1:0] off;
	wire [31:0] size_bytes;
	wire needs_split;
	wire [31:0] head_addr;
	wire [31:0] tail_addr;
	wire [63:0] store_shifted;
	wire [7:0] size_mask;
	wire [7:0] strobe8;
	wire [3:0] head_strb;
	wire [3:0] tail_strb;
	reg [31:0] head_word_r;
	reg [1:0] off_wb;
	reg [1:0] size_wb;
	reg uns_wb;
	assign off = req_addr[1:0];
	assign size_bytes = (req_size == 2'd0 ? 32'd1 : (req_size == 2'd1 ? 32'd2 : 32'd4));
	assign needs_split = (req_read || req_write) && (({30'd0, off} + size_bytes) > 32'd4);
	assign head_addr = {req_addr[31:2], 2'b00};
	assign tail_addr = head_addr + 32'd4;
	assign dmem_addr = (state == S_IDLE ? head_addr : tail_addr);
	assign store_shifted = {32'b00000000000000000000000000000000, req_wdata} << {off, 3'b000};
	assign size_mask = (8'd1 << size_bytes[3:0]) - 8'd1;
	assign strobe8 = size_mask << off;
	assign head_strb = strobe8[3:0];
	assign tail_strb = strobe8[7:4];
	assign dmem_wdata = (state == S_AMO ? amo_newdata : (state == S_IDLE ? store_shifted[31:0] : store_shifted[63:32]));
	wire amo_no_write = amo_lr || amo_sc;
	assign dmem_wstrb = (state == S_AMO ? (amo_wr && !amo_no_write ? 4'b1111 : 4'b0000) : (req_write && (state != S_TAIL) ? (state == S_IDLE ? head_strb : tail_strb) : 4'b0000));
	assign dmem_we = (state == S_AMO ? amo_wr && !amo_no_write : req_write && (state != S_TAIL));
	assign lsu_stall = ((state == S_HEAD) || ((state == S_IDLE) && (needs_split || amo_en))) || ((state == S_AMO) && (amo_wait || !amo_phase));
	assign amo_wr = amo_active & amo_phase;
	assign amo_write_phase = amo_wr;
	assign amo_old_out = amo_old;
	wire [31:0] rdata_eff = dmem_rdata;
	reg [7:0] load_byte;
	reg [15:0] load_half;
	reg [31:0] load_word;
	always @(*) begin
		if (_sv2v_0)
			;
		case (off_wb)
			2'd0: begin
				load_byte = rdata_eff[7:0];
				load_half = rdata_eff[15:0];
				load_word = rdata_eff;
			end
			2'd1: begin
				load_byte = rdata_eff[15:8];
				load_half = rdata_eff[23:8];
				load_word = {rdata_eff[7:0], head_word_r[31:8]};
			end
			2'd2: begin
				load_byte = rdata_eff[23:16];
				load_half = rdata_eff[31:16];
				load_word = {rdata_eff[15:0], head_word_r[31:16]};
			end
			default: begin
				load_byte = rdata_eff[31:24];
				load_half = {rdata_eff[7:0], head_word_r[31:24]};
				load_word = {rdata_eff[23:0], head_word_r[31:24]};
			end
		endcase
		case (size_wb)
			2'd0: load_data = {{24{!uns_wb && load_byte[7]}}, load_byte};
			2'd1: load_data = {{16{!uns_wb && load_half[15]}}, load_half};
			default: load_data = load_word;
		endcase
	end
	always @(posedge clk or posedge rst)
		if (rst) begin
			state <= S_IDLE;
			amo_active <= 1'b0;
			amo_wait <= 1'b0;
			amo_phase <= 1'b0;
			amo_old <= 32'd0;
			amo_addr_r <= 32'd0;
			amo_mode_r <= 1'b0;
			head_word_r <= 32'd0;
			off_wb <= 2'd0;
			size_wb <= 2'd2;
			uns_wb <= 1'b0;
		end
		else if (!stall_external) begin
			case (state)
				S_IDLE:
					if (amo_en)
						state <= S_AMO;
					else if (needs_split)
						state <= S_HEAD;
				S_HEAD: state <= S_TAIL;
				S_AMO: state <= (amo_phase ? S_IDLE : S_AMO);
				default: state <= S_IDLE;
			endcase
			if ((state == S_IDLE) && amo_en) begin
				amo_active <= 1'b1;
				amo_wait <= 1'b1;
				amo_phase <= 1'b0;
				amo_addr_r <= head_addr;
			end
			else if (state == S_AMO) begin
				if (amo_wait)
					amo_wait <= 1'b0;
				else if (!amo_phase)
					amo_phase <= 1'b1;
				else
					amo_active <= 1'b0;
			end
			if (state == S_HEAD)
				head_word_r <= dmem_rdata;
			if ((state == S_AMO) && amo_wait)
				amo_old <= dmem_rdata;
			if (amo_en) begin
				off_wb <= 2'd0;
				size_wb <= 2'd2;
				uns_wb <= 1'b0;
				amo_mode_r <= 1'b1;
			end
			else begin
				if (amo_active)
					amo_mode_r <= 1'b1;
				else
					amo_mode_r <= 1'b0;
				if ((req_read || req_write) && !lsu_stall) begin
					off_wb <= off;
					size_wb <= req_size;
					uns_wb <= req_unsigned;
				end
			end
		end
	initial _sv2v_0 = 0;
endmodule
