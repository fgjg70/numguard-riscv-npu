`timescale 1ns/1ps

module camera_dvp_capture #(
    parameter integer WIDTH = 320,
    parameter integer HEIGHT = 240,
    parameter integer TIMEOUT_CYCLES = 50000000,
    parameter integer WARMUP_FRAMES = 2
) (
    input wire clk,
    input wire reset,
    input wire configured,
    input wire freeze,
    input wire frame_ack,
    input wire pclk,
    input wire href,
    input wire vsync,
    input wire [7:0] data,
    output reg pixel_write,
    output reg [14:0] pixel_address,
    output reg [15:0] pixel_data,
    output reg frame_done,
    output reg [15:0] frame_count,
    output reg [15:0] timeout_count,
    output reg [15:0] vsync_count,
    output reg [15:0] pclk_count,
    output reg [15:0] line_count,
    output reg [15:0] bad_frame_count,
    output reg [3:0] state,
    output wire [1:0] sync_levels
);
    localparam [3:0] WAIT_FRAME = 0, CAPTURE = 1, WAIT_ACK = 2;
    reg [2:0] pclk_sync;
    reg [2:0] href_sync;
    reg [2:0] vsync_sync;
    reg [7:0] data_meta;
    reg [7:0] data_sync;
    reg [9:0] column;
    reg [8:0] row_count;
    reg [7:0] high_byte;
    reg second_byte;
    reg frame_bad;
    reg [15:0] stored_pixels;
    reg [31:0] watchdog;
    reg [7:0] warmup_count;
    wire pclk_rise = pclk_sync[1] && !pclk_sync[2];
    wire href_rise = href_sync[1] && !href_sync[2];
    wire href_fall = !href_sync[1] && href_sync[2];
    wire vsync_rise = vsync_sync[1] && !vsync_sync[2];
    assign sync_levels = {pclk_sync[1], href_sync[1]};

    always @(posedge clk) begin
        if (reset) begin
            pclk_sync <= 0;
            href_sync <= 0;
            vsync_sync <= 0;
            data_meta <= 0;
            data_sync <= 0;
            pixel_write <= 0;
            pixel_address <= 0;
            pixel_data <= 0;
            frame_done <= 0;
            frame_count <= 0;
            timeout_count <= 0;
            vsync_count <= 0;
            pclk_count <= 0;
            line_count <= 0;
            bad_frame_count <= 0;
            state <= WAIT_FRAME;
            column <= 0;
            row_count <= 0;
            high_byte <= 0;
            second_byte <= 0;
            frame_bad <= 0;
            stored_pixels <= 0;
            watchdog <= 0;
            warmup_count <= 0;
        end else begin
            pclk_sync <= {pclk_sync[1:0], pclk};
            href_sync <= {href_sync[1:0], href};
            vsync_sync <= {vsync_sync[1:0], vsync};
            data_meta <= data;
            data_sync <= data_meta;
            pixel_write <= 0;
            frame_done <= 0;
            if (vsync_rise) vsync_count <= vsync_count + 1'b1;
            if (pclk_rise) pclk_count <= pclk_count + 1'b1;
            if (href_rise) line_count <= line_count + 1'b1;
            if (!configured) begin
                state <= WAIT_FRAME;
                column <= 0;
                row_count <= 0;
                second_byte <= 0;
                frame_bad <= 0;
                stored_pixels <= 0;
                watchdog <= 0;
                warmup_count <= 0;
            end else if (state == WAIT_ACK) begin
                watchdog <= 0;
                if (frame_ack) state <= WAIT_FRAME;
            end else if (freeze) begin
                state <= WAIT_FRAME;
                watchdog <= 0;
                column <= 0;
                row_count <= 0;
                second_byte <= 0;
                stored_pixels <= 0;
                frame_bad <= 0;
            end else if (vsync_rise) begin
                watchdog <= 0;
                column <= 0;
                row_count <= 0;
                second_byte <= 0;
                stored_pixels <= 0;
                frame_bad <= 0;
                if (state == CAPTURE && !frame_bad && row_count == HEIGHT &&
                    !href_sync[1] && !second_byte && stored_pixels == (WIDTH / 2) * (HEIGHT / 2)) begin
                    frame_done <= 1;
                    frame_count <= frame_count + 1'b1;
                    state <= WAIT_ACK;
                end else begin
                    if (state == CAPTURE) bad_frame_count <= bad_frame_count + 1'b1;
                    if (warmup_count < WARMUP_FRAMES) begin
                        warmup_count <= warmup_count + 1'b1;
                        state <= WAIT_FRAME;
                    end else state <= CAPTURE;
                end
            end else if (watchdog == TIMEOUT_CYCLES - 1) begin
                timeout_count <= timeout_count + 1'b1;
                watchdog <= 0;
                state <= WAIT_FRAME;
                column <= 0;
                row_count <= 0;
                second_byte <= 0;
                stored_pixels <= 0;
                frame_bad <= 0;
            end else begin
                watchdog <= watchdog + 1'b1;
                if (state == CAPTURE) begin
                    if (href_fall) begin
                        if (column != WIDTH || second_byte) frame_bad <= 1;
                        if (row_count < HEIGHT + 1) row_count <= row_count + 1'b1;
                        column <= 0;
                        second_byte <= 0;
                    end
                    if (pclk_rise && href_sync[1]) begin
                        if (vsync_sync[1]) frame_bad <= 1;
                        if (!second_byte) begin
                            high_byte <= data_sync;
                            second_byte <= 1;
                        end else begin
                            second_byte <= 0;
                            if (column < WIDTH + 1) column <= column + 1'b1;
                            if (column >= WIDTH || row_count >= HEIGHT) frame_bad <= 1;
                            else if (!column[0] && !row_count[0] && !frame_bad) begin
                                pixel_write <= 1;
                                pixel_address <= stored_pixels[14:0];
                                pixel_data <= {high_byte, data_sync};
                                stored_pixels <= stored_pixels + 1'b1;
                            end
                        end
                    end
                end
            end
        end
    end
endmodule
