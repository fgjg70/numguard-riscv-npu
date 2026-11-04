`timescale 1ns/1ps

module camera_config #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer BUS_HZ = 100000,
    parameter integer SETTLE_CYCLES = 270000,
    parameter [7:0] SENSOR_CLKRC = 8'h01,
    parameter [7:0] SENSOR_MVFP = 8'h07
) (
    input wire clk,
    input wire reset,
    input wire restart,
    input wire sensor_bars,
    output reg configured,
    output reg failed,
    output reg [7:0] pid,
    output reg [7:0] version,
    output reg [7:0] step,
    output wire scl,
    inout wire sda
);
    localparam [3:0] POWER_WAIT=0, LAUNCH=1, WAIT_BUS=2, RESET_WAIT=3,
        PID_LAUNCH=4, PID_WAIT=5, VER_LAUNCH=6, VER_WAIT=7, FINISHED=8;
    localparam integer REGISTER_COUNT = 50;
    reg [3:0] state;
    reg [31:0] timer;
    reg bus_start;
    reg bus_read;
    reg [7:0] bus_address;
    reg [7:0] bus_data;
    wire bus_busy;
    wire bus_done;
    wire bus_error;
    wire [7:0] bus_result;

    function [15:0] setting;
        input [7:0] index;
        begin
            case (index)
                0: setting=16'h1280;
                1: setting={8'h11, SENSOR_CLKRC};
                2: setting=16'h1214;
                3: setting=16'h8c00;
                4: setting=16'h40d0;
                5: setting=16'h3a04;
                6: setting=16'h0400;
                7: setting=16'h0c04;
                8: setting=16'h3e19;
                9: setting=16'h703a;
                10: setting=16'h7135;
                11: setting=16'h7211;
                12: setting=16'h73f1;
                13: setting=16'ha202;
                14: setting=16'h1500;
                15: setting=16'h1713;
                16: setting=16'h1801;
                17: setting=16'h32b6;
                18: setting=16'h1902;
                19: setting=16'h1a7a;
                20: setting=16'h030a;
                21: setting=16'h13e7;
                22: setting=16'h0e61;
                23: setting=16'h0f4b;
                24: setting=16'h1602;
                25: setting={8'h1e, SENSOR_MVFP};
                26: setting=16'h2102;
                27: setting=16'h2291;
                28: setting=16'h2907;
                29: setting=16'h330b;
                30: setting=16'h350b;
                31: setting=16'h3701;
                32: setting=16'h3801;
                33: setting=16'h3c78;
                34: setting=16'h3d88;
                35: setting=16'h4f80;
                36: setting=16'h5080;
                37: setting=16'h5100;
                38: setting=16'h5222;
                39: setting=16'h535e;
                40: setting=16'h5480;
                41: setting=16'h589e;
                42: setting=16'h6a40;
                43: setting=16'h6b0a;
                44: setting=16'h6c0a;
                45: setting=16'h6d55;
                46: setting=16'h6e11;
                47: setting=16'h6f9f;
                48: setting=16'hb084;
                49: setting={8'h42, sensor_bars ? 8'h08 : 8'h00};
                default: setting=16'h1204;
            endcase
        end
    endfunction
    wire [15:0] selected_setting = setting(step);

    camera_sccb #(.CLOCK_HZ(CLOCK_HZ), .BUS_HZ(BUS_HZ)) bus (
        .clk(clk), .reset(reset || restart), .start(bus_start),
        .read_enable(bus_read), .register_address(bus_address), .write_data(bus_data),
        .busy(bus_busy), .done(bus_done), .error(bus_error), .read_data(bus_result),
        .scl(scl), .sda(sda)
    );

    always @(posedge clk) begin
        if (reset || restart) begin
            state <= POWER_WAIT;
            timer <= SETTLE_CYCLES - 1;
            configured <= 0;
            failed <= 0;
            pid <= 0;
            version <= 0;
            step <= 0;
            bus_start <= 0;
            bus_read <= 0;
            bus_address <= 0;
            bus_data <= 0;
        end else begin
            bus_start <= 0;
            case (state)
                POWER_WAIT, RESET_WAIT: begin
                    if (timer != 0) timer <= timer - 1'b1;
                    else state <= LAUNCH;
                end
                LAUNCH: begin
                    bus_address <= selected_setting[15:8];
                    bus_data <= selected_setting[7:0];
                    bus_read <= 0;
                    bus_start <= 1;
                    state <= WAIT_BUS;
                end
                WAIT_BUS: if (bus_done) begin
                    if (bus_error) begin failed <= 1; state <= FINISHED; end
                    else if (step == REGISTER_COUNT - 1) state <= PID_LAUNCH;
                    else begin
                        step <= step + 1'b1;
                        if (step == 0) begin timer <= SETTLE_CYCLES - 1; state <= RESET_WAIT; end
                        else state <= LAUNCH;
                    end
                end
                PID_LAUNCH: begin
                    bus_address <= 8'h0a;
                    bus_read <= 1;
                    bus_start <= 1;
                    state <= PID_WAIT;
                end
                PID_WAIT: if (bus_done) begin
                    pid <= bus_result;
                    if (bus_error) begin failed <= 1; state <= FINISHED; end
                    else state <= VER_LAUNCH;
                end
                VER_LAUNCH: begin
                    bus_address <= 8'h0b;
                    bus_read <= 1;
                    bus_start <= 1;
                    state <= VER_WAIT;
                end
                VER_WAIT: if (bus_done) begin
                    version <= bus_result;
                    configured <= !bus_error && pid == 8'h76 && bus_result == 8'h73;
                    failed <= bus_error || pid != 8'h76 || bus_result != 8'h73;
                    state <= FINISHED;
                end
                default: state <= FINISHED;
            endcase
        end
    end
endmodule
