`timescale 1ns/1ps

module vision_mmio_bridge #(
    parameter WAIT_PERIPHERAL = 0
) (
    input wire cpu_clk,
    input wire cpu_reset,
    input wire cpu_write,
    input wire cpu_read,
    input wire [31:0] cpu_address,
    input wire [31:0] cpu_wdata,
    input wire [3:0] cpu_wstrb,
    output wire cpu_ready,
    output reg [31:0] response_data,
    input wire peripheral_clk,
    input wire peripheral_reset,
    output wire peripheral_write,
    output wire peripheral_read,
    output wire [31:0] peripheral_address,
    output wire [31:0] peripheral_wdata,
    output wire [3:0] peripheral_wstrb,
    input wire [31:0] peripheral_rdata,
    input wire peripheral_ready
);
    localparam IDLE = 0, WAIT_RESPONSE = 1, RETURN_RESPONSE = 2;
    reg [1:0] state;
    reg [69:0] held_request;
    reg request_toggle;
    (* async_reg = "true" *) reg request_meta;
    (* async_reg = "true" *) reg request_sync;
    reg request_seen;
    reg [69:0] active_request;
    reg execute_request;
    reg [31:0] held_response;
    reg response_toggle;
    (* async_reg = "true" *) reg response_meta;
    (* async_reg = "true" *) reg response_sync;

    assign cpu_ready = state == RETURN_RESPONSE;
    assign peripheral_write = execute_request && active_request[69];
    assign peripheral_read = execute_request && active_request[68];
    assign peripheral_address = active_request[67:36];
    assign peripheral_wdata = active_request[35:4];
    assign peripheral_wstrb = active_request[3:0];

    always @(posedge cpu_clk or posedge cpu_reset) begin
        if (cpu_reset) begin
            state <= IDLE;
            held_request <= 0;
            request_toggle <= 0;
            response_meta <= 0;
            response_sync <= 0;
            response_data <= 0;
        end else begin
            response_meta <= response_toggle;
            response_sync <= response_meta;
            case (state)
                IDLE: if (cpu_write || cpu_read) begin
                    held_request <= {cpu_write, cpu_read, cpu_address, cpu_wdata, cpu_wstrb};
                    request_toggle <= !request_toggle;
                    state <= WAIT_RESPONSE;
                end
                WAIT_RESPONSE: if (response_sync == request_toggle) begin
                    response_data <= held_response;
                    state <= RETURN_RESPONSE;
                end
                RETURN_RESPONSE: state <= IDLE;
                default: state <= IDLE;
            endcase
        end
    end

    always @(posedge peripheral_clk or posedge peripheral_reset) begin
        if (peripheral_reset) begin
            request_meta <= 0;
            request_sync <= 0;
            request_seen <= 0;
            active_request <= 0;
            execute_request <= 0;
            held_response <= 0;
            response_toggle <= 0;
        end else begin
            request_meta <= request_toggle;
            request_sync <= request_meta;
            if (execute_request) begin
                if (!WAIT_PERIPHERAL || peripheral_ready) begin
                    held_response <= peripheral_rdata;
                    response_toggle <= request_seen;
                    execute_request <= 0;
                end
            end else if (request_sync != request_seen) begin
                active_request <= held_request;
                request_seen <= request_sync;
                execute_request <= 1;
            end
        end
    end
endmodule
