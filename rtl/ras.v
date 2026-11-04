`timescale 1ns/1ps

module ras (
	clk,
	rst,
	push_en,
	push_pc,
	pop_en,
	top,
	empty
);
	parameter [31:0] DEPTH = 8;
	input wire clk;
	input wire rst;
	input wire push_en;
	input wire [31:0] push_pc;
	input wire pop_en;
	output wire [31:0] top;
	output wire empty;
	localparam [31:0] PW = $clog2(DEPTH + 1);
	reg [31:0] stk [0:DEPTH - 1];
	reg [PW - 1:0] sp;
	function automatic [PW - 1:0] sv2v_cast_D1740;
		input reg [PW - 1:0] inp;
		sv2v_cast_D1740 = inp;
	endfunction
	wire [PW - 1:0] full_sp = sv2v_cast_D1740(DEPTH);
	assign empty = sp == {PW {1'sb0}};
	assign top = stk[sp - 1'b1];
	integer i;
	initial begin
		for (i = 0; i < DEPTH; i = i + 1)
			stk[i] = 1'sb0;
		sp = 1'sb0;
	end
	always @(posedge clk)
		if (rst)
			sp <= 1'sb0;
		else if (pop_en && (sp != {PW {1'sb0}})) begin
			sp <= sp - 1'b1;
			if (push_en)
				stk[sp - 1'b1] <= push_pc;
		end
		else if (push_en && (sp != full_sp)) begin
			stk[sp] <= push_pc;
			sp <= sp + 1'b1;
		end
endmodule
