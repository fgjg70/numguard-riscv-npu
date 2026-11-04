`timescale 1ns/1ps

module vision_dcache #(
    parameter integer INDEX_BITS = 8,
    parameter DDR_PRESENT = 1
) (
    input wire clk,
    input wire reset,
    input wire cpu_read,
    input wire cpu_write,
    input wire [31:0] cpu_address,
    input wire [31:0] cpu_wdata,
    input wire [3:0] cpu_wstrb,
    output wire cpu_ready,
    output reg [31:0] cpu_rdata,
    output wire memory_read,
    output wire memory_write,
    output wire [31:0] memory_address,
    output wire [31:0] memory_wdata,
    output wire [3:0] memory_wstrb,
    input wire memory_ready,
    input wire [31:0] memory_rdata
);
    localparam IDLE=0, LOOKUP=1, BYPASS=2, FILL=3, WRITE=4, RESPONSE=5;
    localparam LINES = 1 << INDEX_BITS;
    localparam TAG_BITS = 28 - INDEX_BITS;
    reg [2:0] state;
    reg [LINES-1:0] valid_lines;
    reg [127:0] data_lines [0:LINES-1];
    reg [TAG_BITS-1:0] tag_lines [0:LINES-1];
    reg [127:0] lookup_data;
    reg [TAG_BITS-1:0] lookup_tag;
    reg lookup_valid;
    reg [31:0] held_address;
    reg [31:0] held_wdata;
    reg [3:0] held_wstrb;
    reg held_write;
    reg [1:0] fill_word;
    reg [127:0] fill_data;
    reg cache_enabled;
    reg [31:0] hits;
    reg [31:0] misses;
    reg [31:0] bypasses;
    wire [INDEX_BITS-1:0] read_index = state == IDLE ? cpu_address[INDEX_BITS+3:4] : held_address[INDEX_BITS+3:4];
    wire [INDEX_BITS-1:0] held_index = held_address[INDEX_BITS+3:4];
    wire hit = lookup_valid && lookup_tag == held_address[31:INDEX_BITS+4];
    wire cacheable = DDR_PRESENT && cpu_address[31:28] == 4'hc && cache_enabled;
    wire local_register = cpu_address[31:4] == 28'ha100010;
    reg [127:0] merged_line;
    reg [127:0] completed_line;
    integer byte_index;

    assign cpu_ready = state == RESPONSE;
    assign memory_read = state == FILL || (state == BYPASS && !held_write);
    assign memory_write = state == WRITE || (state == BYPASS && held_write);
    assign memory_address = state == FILL ? {held_address[31:4], fill_word, 2'b00} : held_address;
    assign memory_wdata = held_wdata;
    assign memory_wstrb = held_wstrb;

    always @* begin
        merged_line = lookup_data;
        for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1)
            if (held_wstrb[byte_index]) merged_line[held_address[3:2]*32 + byte_index*8 +: 8] = held_wdata[byte_index*8 +: 8];
        completed_line = fill_data;
        completed_line[fill_word*32 +: 32] = memory_rdata;
    end

    always @(posedge clk) begin
        lookup_data <= data_lines[read_index];
        lookup_tag <= tag_lines[read_index];
        lookup_valid <= valid_lines[read_index];
        if (state == LOOKUP && hit && held_write) data_lines[held_index] <= merged_line;
        if (state == FILL && memory_ready && fill_word == 3) begin
            data_lines[held_index] <= completed_line;
            tag_lines[held_index] <= held_address[31:INDEX_BITS+4];
        end
    end

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            state <= IDLE;
            valid_lines <= 0;
            held_address <= 0;
            held_wdata <= 0;
            held_wstrb <= 0;
            held_write <= 0;
            fill_word <= 0;
            fill_data <= 0;
            cpu_rdata <= 0;
            cache_enabled <= 1;
            hits <= 0;
            misses <= 0;
            bypasses <= 0;
        end else begin
            case (state)
                IDLE: if (cpu_read || cpu_write) begin
                    held_address <= cpu_address;
                    held_wdata <= cpu_wdata;
                    held_wstrb <= cpu_wstrb;
                    held_write <= cpu_write;
                    if (local_register) begin
                        case (cpu_address[3:2])
                            0: begin
                                cpu_rdata <= {30'd0, 1'b0, cache_enabled};
                                if (cpu_write && cpu_wstrb == 15) begin
                                    cache_enabled <= cpu_wdata[0];
                                    valid_lines <= 0;
                                    if (cpu_wdata[2]) begin hits <= 0; misses <= 0; bypasses <= 0; end
                                end
                            end
                            1: cpu_rdata <= hits;
                            2: cpu_rdata <= misses;
                            3: cpu_rdata <= bypasses;
                        endcase
                        state <= RESPONSE;
                    end else if (cacheable) state <= LOOKUP;
                    else begin bypasses <= bypasses + 1'b1; state <= BYPASS; end
                end
                LOOKUP: begin
                    if (hit) begin
                        hits <= hits + 1'b1;
                        cpu_rdata <= lookup_data[held_address[3:2]*32 +: 32];
                        state <= held_write ? WRITE : RESPONSE;
                    end else begin
                        misses <= misses + 1'b1;
                        fill_word <= 0;
                        fill_data <= 0;
                        state <= held_write ? WRITE : FILL;
                    end
                end
                BYPASS, WRITE: if (memory_ready) begin cpu_rdata <= memory_rdata; state <= RESPONSE; end
                FILL: if (memory_ready) begin
                    fill_data <= completed_line;
                    if (fill_word == 3) begin
                        valid_lines[held_index] <= 1;
                        cpu_rdata <= completed_line[held_address[3:2]*32 +: 32];
                        state <= RESPONSE;
                    end else fill_word <= fill_word + 1'b1;
                end
                RESPONSE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end
endmodule
