`timescale 1ns/1ps

module control_unit (
	instr,
	ctrl
);
	reg _sv2v_0;
	parameter [31:0] XLEN = 32;
	input wire [XLEN - 1:0] instr;
	output reg [55:0] ctrl;
	wire [6:0] opcode;
	wire [2:0] funct3;
	wire [6:0] funct7;
	assign opcode = instr[6:0];
	assign funct3 = instr[14:12];
	assign funct7 = instr[31:25];
	reg illegal;
	localparam [6:0] rv32_pkg_F7_ALT = 7'b0100000;
	localparam [6:0] rv32_pkg_F7_MULDIV = 7'b0000001;
	localparam [6:0] rv32_pkg_F7_ZERO = 7'b0000000;
	always @(*) begin
		if (_sv2v_0)
			;
		ctrl = 1'sb0;
		illegal = 1'b0;
		case (opcode)
			7'b0110011: begin
				ctrl[37] = 1'b1;
				ctrl[48] = 1'b0;
				ctrl[36-:2] = 2'd0;
				case (funct3)
					3'b000:
						case (funct7)
							rv32_pkg_F7_ZERO: ctrl[55-:5] = 5'd0;
							rv32_pkg_F7_ALT: ctrl[55-:5] = 5'd1;
							rv32_pkg_F7_MULDIV: ctrl[55-:5] = 5'd10;
							default: illegal = 1'b1;
						endcase
					3'b001:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd11;
						else begin
							ctrl[55-:5] = 5'd5;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
					3'b010:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd12;
						else begin
							ctrl[55-:5] = 5'd8;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
					3'b011:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd13;
						else begin
							ctrl[55-:5] = 5'd9;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
					3'b100:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd14;
						else begin
							ctrl[55-:5] = 5'd4;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
					3'b101:
						case (funct7)
							rv32_pkg_F7_ZERO: ctrl[55-:5] = 5'd6;
							rv32_pkg_F7_ALT: ctrl[55-:5] = 5'd7;
							rv32_pkg_F7_MULDIV: ctrl[55-:5] = 5'd15;
							default: illegal = 1'b1;
						endcase
					3'b110:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd16;
						else begin
							ctrl[55-:5] = 5'd3;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
					3'b111:
						if (funct7 == rv32_pkg_F7_MULDIV)
							ctrl[55-:5] = 5'd17;
						else begin
							ctrl[55-:5] = 5'd2;
							illegal = funct7 != rv32_pkg_F7_ZERO;
						end
				endcase
			end
			7'b0010011: begin
				ctrl[37] = 1'b1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd0;
				ctrl[36-:2] = 2'd0;
				case (funct3)
					3'b000: ctrl[55-:5] = 5'd0;
					3'b010: ctrl[55-:5] = 5'd8;
					3'b011: ctrl[55-:5] = 5'd9;
					3'b100: ctrl[55-:5] = 5'd4;
					3'b110: ctrl[55-:5] = 5'd3;
					3'b111: ctrl[55-:5] = 5'd2;
					3'b001: begin
						ctrl[55-:5] = 5'd5;
						illegal = funct7 != rv32_pkg_F7_ZERO;
					end
					3'b101:
						case (funct7)
							rv32_pkg_F7_ZERO: ctrl[55-:5] = 5'd6;
							rv32_pkg_F7_ALT: ctrl[55-:5] = 5'd7;
							default: illegal = 1'b1;
						endcase
				endcase
			end
			7'b0000011: begin
				ctrl[37] = 1'b1;
				ctrl[42] = 1'b1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd0;
				ctrl[36-:2] = 2'd1;
				ctrl[38] = 1'b0;
				case (funct3)
					3'b000: ctrl[40-:2] = 2'd0;
					3'b001: ctrl[40-:2] = 2'd1;
					3'b010: ctrl[40-:2] = 2'd2;
					3'b100: begin
						ctrl[40-:2] = 2'd0;
						ctrl[38] = 1'b1;
					end
					3'b101: begin
						ctrl[40-:2] = 2'd1;
						ctrl[38] = 1'b1;
					end
					default: illegal = 1'b1;
				endcase
			end
			7'b0100011: begin
				ctrl[41] = 1'b1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd1;
				ctrl[40-:2] = 2'd0;
				case (funct3)
					3'b000: ctrl[40-:2] = 2'd0;
					3'b001: ctrl[40-:2] = 2'd1;
					3'b010: ctrl[40-:2] = 2'd2;
					default: illegal = 1'b1;
				endcase
			end
			7'b1100011: begin
				ctrl[44-:2] = 2'd1;
				ctrl[50-:2] = 2'd1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd2;
				ctrl[47-:3] = 3'b000;
				case (funct3)
					3'b000: ctrl[47-:3] = 3'b000;
					3'b001: ctrl[47-:3] = 3'b001;
					3'b100: ctrl[47-:3] = 3'b100;
					3'b101: ctrl[47-:3] = 3'b101;
					3'b110: ctrl[47-:3] = 3'b110;
					3'b111: ctrl[47-:3] = 3'b111;
					default: illegal = 1'b1;
				endcase
			end
			7'b0110111: begin
				ctrl[37] = 1'b1;
				ctrl[50-:2] = 2'd2;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd3;
				ctrl[55-:5] = 5'd0;
				ctrl[36-:2] = 2'd0;
			end
			7'b0010111: begin
				ctrl[37] = 1'b1;
				ctrl[50-:2] = 2'd1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd3;
				ctrl[55-:5] = 5'd0;
				ctrl[36-:2] = 2'd0;
			end
			7'b1101111: begin
				ctrl[37] = 1'b1;
				ctrl[44-:2] = 2'd2;
				ctrl[50-:2] = 2'd1;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd4;
				ctrl[55-:5] = 5'd0;
				ctrl[36-:2] = 2'd2;
			end
			7'b1100111: begin
				ctrl[37] = 1'b1;
				ctrl[44-:2] = 2'd3;
				ctrl[50-:2] = 2'd0;
				ctrl[48] = 1'b1;
				ctrl[34-:3] = 3'd0;
				ctrl[55-:5] = 5'd0;
				ctrl[36-:2] = 2'd2;
				if (funct3 != 3'b000)
					illegal = 1'b1;
			end
			7'b0101111: illegal = 1'b1;
			7'b0001111:
				;
			7'b1110011:
				case (funct3)
					3'b000:
						case (instr[31:20])
							12'h000: ctrl[30-:3] = 3'd2;
							12'h001: ctrl[30-:3] = 3'd3;
							12'h302: ctrl[30-:3] = 3'd4;
							default: illegal = 1'b1;
						endcase
					3'b100: illegal = 1'b1;
					default: begin
						ctrl[30-:3] = 3'd1;
						ctrl[27-:12] = instr[31:20];
						ctrl[15-:2] = funct3[1:0];
						ctrl[13] = funct3[2];
						ctrl[12-:5] = instr[19:15];
						ctrl[37] = 1'b1;
						ctrl[36-:2] = 2'd3;
					end
				endcase
			default: illegal = 1'b1;
		endcase
		ctrl[31] = illegal;
		if (illegal) begin
			ctrl[37] = 1'b0;
			ctrl[42] = 1'b0;
			ctrl[41] = 1'b0;
			ctrl[44-:2] = 2'd0;
		end
	end
	initial _sv2v_0 = 0;
endmodule
