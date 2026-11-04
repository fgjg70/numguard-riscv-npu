`timescale 1ns/1ps

module branch_unit (
	rs1,
	rs2,
	branch_type,
	taken
);
	reg _sv2v_0;
	parameter [31:0] XLEN = 32;
	input wire [XLEN - 1:0] rs1;
	input wire [XLEN - 1:0] rs2;
	input wire [2:0] branch_type;
	output reg taken;
	wire eq;
	wire lt;
	wire ltu;
	assign eq = rs1 == rs2;
	assign lt = $signed(rs1) < $signed(rs2);
	assign ltu = rs1 < rs2;
	always @(*) begin
		if (_sv2v_0)
			;
		case (branch_type)
			3'b000: taken = eq;
			3'b001: taken = ~eq;
			3'b100: taken = lt;
			3'b101: taken = ~lt;
			3'b110: taken = ltu;
			3'b111: taken = ~ltu;
			default: taken = 1'b0;
		endcase
	end
	initial _sv2v_0 = 0;
endmodule
