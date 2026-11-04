`timescale 1ns/1ps

module vision_ddr3_memory #(
    parameter integer DQ_WIDTH = 32,
    parameter integer ADDRESS_BITS = 28,
    parameter integer BIST_TEST_WORDS = 1024,
    parameter integer BIST_ADDRESS_BITS = 28
) (
    input wire system_clk,
    input wire system_reset,
    input wire ui_clk,
    input wire ui_reset,
    input wire initialized,
    input wire memory_read,
    input wire memory_write,
    input wire [31:0] memory_address,
    input wire [31:0] memory_wdata,
    input wire [3:0] memory_wstrb,
    output wire memory_ready,
    output wire [31:0] memory_rdata,
    output wire [31:0] memory_status,
    output wire [31:0] error_address,
    output wire [31:0] error_expected,
    output wire [31:0] error_actual,
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
    wire ui_read;
    wire ui_write;
    wire [31:0] ui_address;
    wire [31:0] ui_wdata;
    wire [3:0] ui_wstrb;
    wire bist_read;
    wire bist_write;
    wire [31:0] bist_address;
    wire [31:0] bist_wdata;
    wire [3:0] bist_wstrb;
    wire bist_busy;
    wire bist_done;
    wire bist_passed;
    reg bist_started;
    reg bist_start;
    wire port_ready;
    wire [31:0] port_rdata;
    wire port_fault;
    wire accessible = initialized && bist_done && bist_passed && !bist_busy;
    wire rejected = !initialized || (bist_done && !bist_passed) || port_fault;
    wire ui_ready = rejected || (accessible && port_ready);
    wire [31:0] ui_rdata = rejected ? 32'hdd300bad : port_rdata;
    wire [6:0] ui_status = {bist_done && !bist_passed, bist_busy, port_fault, bist_done, bist_passed, initialized, 1'b1};
    (* async_reg = "true" *) reg [6:0] status_meta;
    (* async_reg = "true" *) reg [6:0] status_sync;
    assign memory_status = {25'd0, status_sync};

    always @(posedge system_clk or posedge system_reset) begin
        if (system_reset) begin status_meta <= 0; status_sync <= 0; end
        else begin status_meta <= ui_status; status_sync <= status_meta; end
    end
    always @(posedge ui_clk or posedge ui_reset) begin
        if (ui_reset) begin bist_started <= 0; bist_start <= 0; end
        else begin
            bist_start <= 0;
            if (initialized && !bist_started) begin bist_started <= 1; bist_start <= 1; end
        end
    end
    vision_mmio_bridge #(.WAIT_PERIPHERAL(1)) crossing (
        .cpu_clk(system_clk), .cpu_reset(system_reset), .cpu_write(memory_write), .cpu_read(memory_read),
        .cpu_address(memory_address), .cpu_wdata(memory_wdata), .cpu_wstrb(memory_wstrb),
        .cpu_ready(memory_ready), .response_data(memory_rdata), .peripheral_clk(ui_clk), .peripheral_reset(ui_reset),
        .peripheral_write(ui_write), .peripheral_read(ui_read), .peripheral_address(ui_address),
        .peripheral_wdata(ui_wdata), .peripheral_wstrb(ui_wstrb), .peripheral_rdata(ui_rdata), .peripheral_ready(ui_ready)
    );
    vision_ddr3_bist #(.TEST_WORDS(BIST_TEST_WORDS), .ADDRESS_BITS(BIST_ADDRESS_BITS)) selftest (
        .clk(ui_clk), .reset(ui_reset), .start(bist_start),
        .request_read(bist_read), .request_write(bist_write), .request_address(bist_address),
        .request_wdata(bist_wdata), .request_wstrb(bist_wstrb), .request_ready(port_ready),
        .response_data(port_rdata), .response_fault(port_fault), .busy(bist_busy), .done(bist_done), .passed(bist_passed),
        .error_address(error_address), .error_expected(error_expected), .error_actual(error_actual)
    );
    vision_ddr3_port #(.DQ_WIDTH(DQ_WIDTH), .ADDRESS_BITS(ADDRESS_BITS)) port (
        .clk(ui_clk), .reset(ui_reset), .initialized(initialized),
        .request_read(accessible ? ui_read : bist_read), .request_write(accessible ? ui_write : bist_write),
        .request_address(accessible ? ui_address : bist_address), .request_wdata(accessible ? ui_wdata : bist_wdata),
        .request_wstrb(accessible ? ui_wstrb : bist_wstrb), .request_ready(port_ready), .response_data(port_rdata), .fault(port_fault),
        .axi_awaddr(axi_awaddr), .axi_awlen(axi_awlen), .axi_awvalid(axi_awvalid), .axi_awready(axi_awready),
        .axi_wdata(axi_wdata), .axi_wstrb(axi_wstrb), .axi_wready(axi_wready),
        .axi_araddr(axi_araddr), .axi_arlen(axi_arlen), .axi_arvalid(axi_arvalid), .axi_arready(axi_arready),
        .axi_rdata(axi_rdata), .axi_rid(axi_rid), .axi_rlast(axi_rlast), .axi_rvalid(axi_rvalid)
    );
endmodule
