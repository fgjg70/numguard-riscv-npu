`timescale 1ns/1ps

module camera_sccb #(
    parameter integer CLOCK_HZ = 27000000,
    parameter integer BUS_HZ = 100000
) (
    input wire clk,
    input wire reset,
    input wire start,
    input wire read_enable,
    input wire [7:0] register_address,
    input wire [7:0] write_data,
    output reg busy,
    output reg done,
    output reg error,
    output reg [7:0] read_data,
    output reg scl,
    inout wire sda
);
    localparam integer DIVIDER = CLOCK_HZ / (BUS_HZ * 4);
    localparam integer TIMER_BITS = DIVIDER < 2 ? 1 : $clog2(DIVIDER);
    localparam [4:0] START_A=0, START_B=1, BIT_A=2, BIT_B=3, BIT_C=4,
        BIT_D=5, ACK_A=6, ACK_B=7, ACK_C=8, ACK_D=9,
        STOP_A=10, STOP_B=11, STOP_C=12, STOP_D=13;
    reg [4:0] state;
    reg [TIMER_BITS-1:0] timer;
    reg sda_low;
    reg [1:0] sda_sync;
    reg [2:0] byte_index;
    reg [2:0] bit_index;
    reg [7:0] shift;
    reg [7:0] address_latched;
    reg [7:0] data_latched;
    reg read_latched;
    reg restart_read;
    wire receiving = byte_index == 4;
    assign sda = sda_low ? 1'b0 : 1'bz;

    always @(posedge clk) begin
        if (reset) begin
            busy <= 0;
            done <= 0;
            error <= 0;
            read_data <= 0;
            scl <= 1;
            sda_low <= 0;
            sda_sync <= 2'b11;
            state <= START_A;
            timer <= 0;
            byte_index <= 0;
            bit_index <= 7;
            shift <= 0;
            address_latched <= 0;
            data_latched <= 0;
            read_latched <= 0;
            restart_read <= 0;
        end else begin
            sda_sync <= {sda_sync[0], sda};
            done <= 0;
            if (!busy) begin
                scl <= 1;
                sda_low <= 0;
                if (start) begin
                    busy <= 1;
                    error <= 0;
                    read_data <= 0;
                    address_latched <= register_address;
                    data_latched <= write_data;
                    read_latched <= read_enable;
                    restart_read <= 0;
                    byte_index <= 0;
                    bit_index <= 7;
                    shift <= 8'h42;
                    state <= START_A;
                    timer <= DIVIDER - 1;
                end
            end else if (timer != 0) begin
                timer <= timer - 1'b1;
            end else begin
                timer <= DIVIDER - 1;
                case (state)
                    START_A: begin scl <= 1; sda_low <= 0; state <= START_B; end
                    START_B: begin sda_low <= 1; state <= BIT_A; end
                    BIT_A: begin
                        scl <= 0;
                        sda_low <= receiving ? 1'b0 : !shift[bit_index];
                        state <= BIT_B;
                    end
                    BIT_B: begin scl <= 1; state <= BIT_C; end
                    BIT_C: begin
                        if (receiving) read_data[bit_index] <= sda_sync[1];
                        state <= BIT_D;
                    end
                    BIT_D: begin
                        scl <= 0;
                        if (bit_index == 0) state <= ACK_A;
                        else begin bit_index <= bit_index - 1'b1; state <= BIT_A; end
                    end
                    ACK_A: begin sda_low <= 0; state <= ACK_B; end
                    ACK_B: begin scl <= 1; state <= ACK_C; end
                    ACK_C: begin
                        if (!receiving && sda_sync[1]) error <= 1;
                        state <= ACK_D;
                    end
                    ACK_D: begin
                        scl <= 0;
                        bit_index <= 7;
                        if (error || receiving || (byte_index == 2)) state <= STOP_A;
                        else if (byte_index == 0) begin
                            byte_index <= 1;
                            shift <= address_latched;
                            state <= BIT_A;
                        end else if (byte_index == 1 && read_latched) begin
                            restart_read <= 1;
                            state <= STOP_A;
                        end else if (byte_index == 1) begin
                            byte_index <= 2;
                            shift <= data_latched;
                            state <= BIT_A;
                        end else begin
                            byte_index <= 4;
                            state <= BIT_A;
                        end
                    end
                    STOP_A: begin scl <= 0; sda_low <= 1; state <= STOP_B; end
                    STOP_B: begin scl <= 1; state <= STOP_C; end
                    STOP_C: begin sda_low <= 0; state <= STOP_D; end
                    STOP_D: begin
                        if (restart_read && !error) begin
                            restart_read <= 0;
                            byte_index <= 3;
                            shift <= 8'h43;
                            state <= START_A;
                        end else begin busy <= 0; done <= 1; end
                    end
                    default: begin busy <= 0; error <= 1; done <= 1; end
                endcase
            end
        end
    end
endmodule
