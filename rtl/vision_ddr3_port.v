`timescale 1ns/1ps

module vision_ddr3_port #(
    parameter integer DQ_WIDTH = 32,
    parameter integer ADDRESS_BITS = 28,
    parameter integer TIMEOUT_CYCLES = 1048576
) (
    input wire clk,
    input wire reset,
    input wire initialized,
    input wire request_read,
    input wire request_write,
    input wire [31:0] request_address,
    input wire [31:0] request_wdata,
    input wire [3:0] request_wstrb,
    output wire request_ready,
    output reg [31:0] response_data,
    output reg fault,
    output wire [ADDRESS_BITS-1:0] axi_awaddr,
    output wire [3:0] axi_awlen,
    output wire axi_awvalid,
    input wire axi_awready,
    output wire [DQ_WIDTH*8-1:0] axi_wdata,
    output wire [DQ_WIDTH-1:0] axi_wstrb,
    input wire axi_wready,
    output wire [ADDRESS_BITS-1:0] axi_araddr,
    output wire [3:0] axi_arlen,
    output wire axi_arvalid,
    input wire axi_arready,
    input wire [DQ_WIDTH*8-1:0] axi_rdata,
    input wire [3:0] axi_rid,
    input wire axi_rlast,
    input wire axi_rvalid
);
    localparam IDLE=0, WRITE=1, READ=2, RESPONSE=3;
    localparam BYTE_BITS = $clog2(DQ_WIDTH);
    localparam WORD_SHIFT = $clog2(DQ_WIDTH / 8);
    reg [1:0] state;
    reg [31:0] address;
    reg [31:0] write_data;
    reg [3:0] strobes;
    reg address_accepted;
    reg data_accepted;
    reg [31:0] watchdog;
    wire [31:0] native_address = (address >> WORD_SHIFT) & 32'hfffffff8;

    assign request_ready = state == RESPONSE;
    assign axi_awaddr = native_address[ADDRESS_BITS-1:0];
    assign axi_araddr = native_address[ADDRESS_BITS-1:0];
    assign axi_awlen = 0;
    assign axi_arlen = 0;
    assign axi_awvalid = state == WRITE && !address_accepted;
    assign axi_arvalid = state == READ && !address_accepted;
    assign axi_wdata = {DQ_WIDTH/4{write_data}};
    assign axi_wstrb = state == WRITE && !data_accepted ? {{(DQ_WIDTH-4){1'b0}}, strobes} << {address[BYTE_BITS-1:2], 2'b00} : {DQ_WIDTH{1'b0}};

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            state <= IDLE;
            address <= 0;
            write_data <= 0;
            strobes <= 0;
            address_accepted <= 0;
            data_accepted <= 0;
            watchdog <= 0;
            response_data <= 0;
            fault <= 0;
        end else begin
            case (state)
                IDLE: if (request_read || request_write) begin
                    address <= request_address;
                    write_data <= request_wdata;
                    strobes <= request_wstrb;
                    address_accepted <= 0;
                    data_accepted <= 0;
                    watchdog <= 0;
                    if (!initialized || fault) begin response_data <= 32'hdd300001; state <= RESPONSE; end
                    else state <= request_write ? WRITE : READ;
                end
                WRITE, READ: begin
                    watchdog <= watchdog + 1'b1;
                    if (!initialized || watchdog == TIMEOUT_CYCLES - 1) begin
                        fault <= 1;
                        response_data <= 32'hdd300002;
                        state <= RESPONSE;
                    end else if (state == WRITE) begin
                        if (axi_awready) address_accepted <= 1;
                        if (axi_wready) data_accepted <= 1;
                        if ((address_accepted || axi_awready) && (data_accepted || axi_wready)) begin
                            response_data <= 0;
                            state <= RESPONSE;
                        end
                    end else begin
                        if (axi_arready) address_accepted <= 1;
                        if (axi_rvalid && (address_accepted || axi_arready)) begin
                            if (axi_rid != 0 || !axi_rlast) begin fault <= 1; response_data <= 32'hdd300003; end
                            else response_data <= axi_rdata[address[BYTE_BITS-1:2]*32 +: 32];
                            state <= RESPONSE;
                        end
                    end
                end
                RESPONSE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
