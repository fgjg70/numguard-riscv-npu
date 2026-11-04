`timescale 1ns/1ps

module debug_keys #(
    parameter integer DEBOUNCE_CYCLES = 540000
) (
    input wire clk,
    input wire reset,
    input wire [5:0] keys_n,
    output reg [5:0] pressed,
    output reg [5:0] press_pulse
);
    localparam integer COUNTER_BITS = DEBOUNCE_CYCLES < 2 ? 1 : $clog2(DEBOUNCE_CYCLES);
    reg [5:0] sync_first;
    reg [5:0] sync_second;
    reg [COUNTER_BITS-1:0] debounce [0:5];
    integer key_index;

    always @(posedge clk) begin
        if (reset) begin
            sync_first <= 6'h3f;
            sync_second <= 6'h3f;
            pressed <= 0;
            press_pulse <= 0;
            for (key_index = 0; key_index < 6; key_index = key_index + 1)
                debounce[key_index] <= 0;
        end else begin
            sync_first <= keys_n;
            sync_second <= sync_first;
            press_pulse <= 0;
            for (key_index = 0; key_index < 6; key_index = key_index + 1) begin
                if (pressed[key_index] == !sync_second[key_index]) begin
                    debounce[key_index] <= 0;
                end else if (debounce[key_index] == DEBOUNCE_CYCLES - 1) begin
                    pressed[key_index] <= !sync_second[key_index];
                    press_pulse[key_index] <= !sync_second[key_index];
                    debounce[key_index] <= 0;
                end else begin
                    debounce[key_index] <= debounce[key_index] + 1'b1;
                end
            end
        end
    end
endmodule
