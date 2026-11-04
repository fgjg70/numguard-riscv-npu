`timescale 1ns/1ps

module vision_servo_pwm #(
    parameter integer CLOCK_HZ = 50000000
) (
    input wire clk,
    input wire reset,
    input wire write,
    input wire [7:0] address,
    input wire [31:0] write_data,
    output reg [31:0] read_data,
    output wire [6:0] servo_pwm
);
    localparam integer TICK_CYCLES = CLOCK_HZ / 1000000;
    localparam integer DIVIDER_BITS = TICK_CYCLES > 1 ? $clog2(TICK_CYCLES) : 1;
    reg [DIVIDER_BITS-1:0] divider;
    reg [14:0] frame_us;
    reg [6:0] enabled;
    reg [6:0] active_enabled;
    reg [6:0] pwm_levels;
    reg [10:0] pulse_us [0:6];
    reg [10:0] active_pulse_us [0:6];
    wire frame_end = divider == TICK_CYCLES - 1 && frame_us == 15'd19999;
    wire pulse_address = address >= 8'he0 && address <= 8'hf8 && address[1:0] == 2'b00;
    integer channel;

    assign servo_pwm = reset ? 7'd0 : pwm_levels;

    always @* begin
        case (address)
            8'hd0: read_data = {25'd0, enabled};
            8'hd4: read_data = 32'd20000;
            8'hd8: read_data = {25'd0, active_enabled};
            default: begin
                read_data = 0;
                if (pulse_address) read_data = {21'd0, pulse_us[address[4:2]]};
            end
        endcase
    end

    always @(posedge clk) begin
        if (reset) begin
            divider <= 0;
            frame_us <= 0;
            enabled <= 0;
            active_enabled <= 0;
            pwm_levels <= 0;
            for (channel = 0; channel < 7; channel = channel + 1) begin
                pulse_us[channel] <= 11'd1500;
                active_pulse_us[channel] <= 11'd1500;
            end
        end else begin
            if (divider == TICK_CYCLES - 1) begin
                divider <= 0;
                if (frame_end) begin
                    frame_us <= 0;
                    active_enabled <= enabled;
                    pwm_levels <= enabled;
                    for (channel = 0; channel < 7; channel = channel + 1)
                        active_pulse_us[channel] <= pulse_us[channel];
                end else begin
                    frame_us <= frame_us + 1'b1;
                    for (channel = 0; channel < 7; channel = channel + 1)
                        if (frame_us == active_pulse_us[channel] - 1'b1) pwm_levels[channel] <= 0;
                end
            end else divider <= divider + 1'b1;
            if (write && address == 8'hd0) begin
                enabled <= write_data[6:0];
                active_enabled <= (frame_end ? enabled : active_enabled) & write_data[6:0];
                for (channel = 0; channel < 7; channel = channel + 1)
                    if (!write_data[channel]) pwm_levels[channel] <= 0;
            end
            if (write && pulse_address) begin
                if (write_data < 32'd1000) pulse_us[address[4:2]] <= 11'd1000;
                else if (write_data > 32'd2000) pulse_us[address[4:2]] <= 11'd2000;
                else pulse_us[address[4:2]] <= write_data[10:0];
            end
        end
    end
endmodule
