`timescale 1ns/1ps

module debug_uart #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer BAUD = 115200
) (
    input wire clk,
    input wire reset,
    input wire rxd,
    output wire txd,
    output reg [7:0] rx_data,
    output reg rx_valid,
    output reg rx_frame_error,
    input wire [7:0] tx_data,
    input wire tx_start,
    output reg tx_busy
);
    localparam integer DIVISOR = (CLOCK_HZ + BAUD / 2) / BAUD;
    localparam integer TIMER_BITS = $clog2(DIVISOR + 1);
    localparam [1:0] RX_IDLE = 0, RX_START = 1, RX_BITS = 2, RX_STOP = 3;
    reg [1:0] rx_state;
    reg [1:0] rx_sync;
    reg [TIMER_BITS-1:0] rx_timer;
    reg [TIMER_BITS-1:0] tx_timer;
    reg [2:0] rx_bit;
    reg [3:0] tx_bit;
    reg [7:0] rx_shift;
    reg [9:0] tx_shift;
    reg wait_idle;

    assign txd = tx_busy ? tx_shift[0] : 1'b1;

    always @(posedge clk) begin
        if (reset) begin
            rx_sync <= 2'b11;
            rx_state <= RX_IDLE;
            rx_timer <= 0;
            rx_bit <= 0;
            rx_shift <= 0;
            rx_data <= 0;
            rx_valid <= 0;
            rx_frame_error <= 0;
            wait_idle <= 0;
        end else begin
            rx_sync <= {rx_sync[0], rxd};
            rx_valid <= 0;
            rx_frame_error <= 0;
            case (rx_state)
                RX_IDLE: begin
                    if (rx_sync[1]) wait_idle <= 0;
                    if (!rx_sync[1] && !wait_idle) begin
                        rx_timer <= DIVISOR / 2 - 1;
                        rx_state <= RX_START;
                    end
                end
                RX_START: begin
                    if (rx_timer != 0) rx_timer <= rx_timer - 1'b1;
                    else if (rx_sync[1]) rx_state <= RX_IDLE;
                    else begin
                        rx_timer <= DIVISOR - 1;
                        rx_bit <= 0;
                        rx_state <= RX_BITS;
                    end
                end
                RX_BITS: begin
                    if (rx_timer != 0) rx_timer <= rx_timer - 1'b1;
                    else begin
                        rx_shift[rx_bit] <= rx_sync[1];
                        rx_timer <= DIVISOR - 1;
                        if (rx_bit == 7) rx_state <= RX_STOP;
                        else rx_bit <= rx_bit + 1'b1;
                    end
                end
                RX_STOP: begin
                    if (rx_timer != 0) rx_timer <= rx_timer - 1'b1;
                    else begin
                        rx_state <= RX_IDLE;
                        if (rx_sync[1]) begin
                            rx_data <= rx_shift;
                            rx_valid <= 1;
                        end else begin
                            rx_frame_error <= 1;
                            wait_idle <= 1;
                        end
                    end
                end
                default: rx_state <= RX_IDLE;
            endcase
        end
    end

    always @(posedge clk) begin
        if (reset) begin
            tx_busy <= 0;
            tx_timer <= 0;
            tx_bit <= 0;
            tx_shift <= 10'h3ff;
        end else if (!tx_busy) begin
            if (tx_start) begin
                tx_shift <= {1'b1, tx_data, 1'b0};
                tx_timer <= DIVISOR - 1;
                tx_bit <= 0;
                tx_busy <= 1;
            end
        end else if (tx_timer != 0) begin
            tx_timer <= tx_timer - 1'b1;
        end else begin
            tx_timer <= DIVISOR - 1;
            if (tx_bit == 9) tx_busy <= 0;
            else begin
                tx_shift <= {1'b1, tx_shift[9:1]};
                tx_bit <= tx_bit + 1'b1;
            end
        end
    end
endmodule
