`timescale 1ns/1ps

module vision_ddr3_bist #(
    parameter integer TEST_WORDS = 1024,
    parameter integer ADDRESS_BITS = 28
) (
    input wire clk,
    input wire reset,
    input wire start,
    output wire request_read,
    output wire request_write,
    output wire [31:0] request_address,
    output wire [31:0] request_wdata,
    output wire [3:0] request_wstrb,
    input wire request_ready,
    input wire [31:0] response_data,
    input wire response_fault,
    output reg busy,
    output reg done,
    output reg passed,
    output reg [31:0] error_address,
    output reg [31:0] error_expected,
    output reg [31:0] error_actual
);
    localparam IDLE=0, REQUEST=1, RELEASE=2, FINISHED=3;
    localparam LOCATIONS = TEST_WORDS + ADDRESS_BITS - 2;
    reg [1:0] state;
    reg [1:0] phase;
    reg [31:0] location;
    wire [31:0] address = location < TEST_WORDS ? location << 2 : 32'd1 << (location - TEST_WORDS + 2);
    wire [31:0] pattern = (32'ha5963c69 ^ address) ^ (phase[1] ? 32'hffffffff : 32'd0);
    assign request_read = state == REQUEST && phase[0];
    assign request_write = state == REQUEST && !phase[0];
    assign request_address = address;
    assign request_wdata = pattern;
    assign request_wstrb = 15;

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            state <= IDLE;
            phase <= 0;
            location <= 0;
            busy <= 0;
            done <= 0;
            passed <= 0;
            error_address <= 0;
            error_expected <= 0;
            error_actual <= 0;
        end else begin
            case (state)
                IDLE: if (start) begin
                    state <= REQUEST;
                    phase <= 0;
                    location <= 0;
                    busy <= 1;
                    done <= 0;
                    passed <= 0;
                end
                REQUEST: if (request_ready) begin
                    if (response_fault || (phase[0] && response_data != pattern)) begin
                        error_address <= address;
                        error_expected <= pattern;
                        error_actual <= response_data;
                        passed <= 0;
                        state <= FINISHED;
                    end else state <= RELEASE;
                end
                RELEASE: if (location == LOCATIONS - 1) begin
                    location <= 0;
                    if (phase == 3) begin passed <= 1; state <= FINISHED; end
                    else begin phase <= phase + 1'b1; state <= REQUEST; end
                end else begin location <= location + 1'b1; state <= REQUEST; end
                FINISHED: begin busy <= 0; done <= 1; state <= IDLE; end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
