`timescale 1ns/1ps

module vision_framebuffer #(
    parameter integer PIXELS = 19200
) (
    input wire write_clk,
    input wire reset,
    input wire pixel_write,
    input wire [14:0] write_address,
    input wire [15:0] write_data,
    input wire frame_done,
    output reg frame_ack,
    output reg frame_valid,
    input wire read_clk,
    input wire frame_tick,
    input wire [14:0] read_address,
    output reg [15:0] read_data,
    output reg display_valid
);
    reg [15:0] memory [0:2*PIXELS-1];
    reg write_bank;
    reg read_bank;
    reg request_toggle;
    reg acknowledge_toggle;
    reg request_meta;
    reg request_sync;
    reg acknowledge_meta;
    reg acknowledge_sync;
    reg valid_meta;
    reg pending;
    reg [1:0] reset_sync;
    wire read_reset = reset_sync[1];
    wire [15:0] write_index = write_bank ? write_address + PIXELS : write_address;
    wire [15:0] read_index = read_bank ? read_address + PIXELS : read_address;

    always @(posedge read_clk or posedge reset) begin
        if (reset) reset_sync <= 2'b11;
        else reset_sync <= {reset_sync[0], 1'b0};
    end

    always @(posedge write_clk) begin
        if (pixel_write && !reset && !pending) memory[write_index] <= write_data;
        if (reset) begin
            write_bank <= 1;
            request_toggle <= 0;
            acknowledge_meta <= 0;
            acknowledge_sync <= 0;
            valid_meta <= 0;
            frame_valid <= 0;
            pending <= 0;
            frame_ack <= 0;
        end else begin
            acknowledge_meta <= acknowledge_toggle;
            acknowledge_sync <= acknowledge_meta;
            valid_meta <= display_valid;
            frame_valid <= valid_meta;
            frame_ack <= 0;
            if (frame_done && !pending) begin
                request_toggle <= !request_toggle;
                pending <= 1;
            end
            if (pending && acknowledge_sync == request_toggle) begin
                pending <= 0;
                write_bank <= !write_bank;
                frame_ack <= 1;
            end
        end
    end

    always @(posedge read_clk) begin
        read_data <= memory[read_index];
        if (read_reset) begin
            read_bank <= 0;
            request_meta <= 0;
            request_sync <= 0;
            acknowledge_toggle <= 0;
            display_valid <= 0;
        end else begin
            request_meta <= request_toggle;
            request_sync <= request_meta;
            if (frame_tick && request_sync != acknowledge_toggle) begin
                read_bank <= !read_bank;
                acknowledge_toggle <= request_sync;
                display_valid <= 1;
            end
        end
    end
endmodule
