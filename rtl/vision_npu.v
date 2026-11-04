`timescale 1ns/1ps

module vision_npu (
    input wire clk,
    input wire reset,
    input wire [1:0] model_select,
    input wire input_write,
    input wire [13:0] input_address,
    input wire [7:0] input_data,
    input wire start,
    input wire acknowledge,
    input wire [8:0] result_address,
    output reg signed [31:0] result_data,
    output wire busy,
    output reg done,
    output reg [31:0] cycles
);
    localparam IDLE=0, SETUP=1, BIAS_REQUEST=2, BIAS_LOAD=3, REQUEST=4, MAC=5, STORE=6;
    reg [2:0] state;
    reg [2:0] layer_index;
    reg active_model;
    reg [6:0] input_width;
    reg [5:0] input_channels;
    reg [5:0] output_channels;
    reg [5:0] output_width;
    reg [1:0] kernel_size;
    reg [1:0] stride;
    reg [12:0] weight_base;
    reg [5:0] bias_base;
    reg [5:0] output_row;
    reg [5:0] output_column;
    reg [5:0] output_channel;
    reg [5:0] input_channel;
    reg [1:0] kernel_row;
    reg [1:0] kernel_column;
    reg [1:0] store_lane;
    reg mac_last;
    reg signed [7:0] activation_read;
    reg [7:0] shifts [0:11];
    reg [7:0] activation_first [0:16383];
    reg [7:0] activation_second [0:8191];
    reg signed [31:0] results [0:511];
    wire [127:0] scaled_lanes;
    wire signed [31:0] scaled = scaled_lanes[store_lane * 32 +: 32];
    wire [7:0] activated = scaled < 0 ? 8'd0 : scaled > 127 ? 8'd127 : scaled[7:0];
    wire [3:0] shift_index = active_model * 4'd6 + layer_index;
    wire [5:0] bias_index = bias_base + output_channel[5:2];
    wire read_enable = state == REQUEST || (state == MAC && !mac_last);
    wire last_tap = input_channel + 1 == input_channels && kernel_column + 1 == kernel_size && kernel_row + 1 == kernel_size;
    integer source_row;
    integer source_column;
    reg [13:0] activation_index;
    reg [12:0] weight_index;
    reg [12:0] output_index;
    reg padding;
    genvar lane;
    generate for (lane = 0; lane < 4; lane = lane + 1) begin: mac_lanes
        reg signed [7:0] weights [0:16383];
        reg signed [31:0] biases [0:127];
        reg signed [7:0] weight_read;
        reg signed [31:0] bias_read;
        reg signed [31:0] accumulator;
        wire signed [15:0] product = activation_read * weight_read;
        assign scaled_lanes[lane * 32 +: 32] = accumulator >>> shifts[shift_index];
        if (lane == 0) begin: rom0
            initial begin $readmemh("model/weights_0.hex", weights); $readmemh("model/biases_0.hex", biases); end
        end else if (lane == 1) begin: rom1
            initial begin $readmemh("model/weights_1.hex", weights); $readmemh("model/biases_1.hex", biases); end
        end else if (lane == 2) begin: rom2
            initial begin $readmemh("model/weights_2.hex", weights); $readmemh("model/biases_2.hex", biases); end
        end else begin: rom3
            initial begin $readmemh("model/weights_3.hex", weights); $readmemh("model/biases_3.hex", biases); end
        end
        always @(posedge clk) begin
            if (read_enable) weight_read <= weights[{active_model, weight_index}];
            if (state == BIAS_REQUEST) bias_read <= biases[{active_model, bias_index}];
            if (reset) accumulator <= 0;
            else if (state == BIAS_LOAD) accumulator <= bias_read;
            else if (state == MAC) accumulator <= accumulator + product;
        end
    end endgenerate

    initial $readmemh("model/shifts.hex", shifts);
    assign busy = state != IDLE;

    always @* begin
        source_row = output_row * stride + kernel_row - (kernel_size == 3 ? 1 : 0);
        source_column = output_column * stride + kernel_column - (kernel_size == 3 ? 1 : 0);
        padding = source_row < 0 || source_column < 0 || source_row >= input_width || source_column >= input_width;
        activation_index = padding ? 14'd0 : (source_row * input_width + source_column) * input_channels + input_channel;
        weight_index = weight_base + ((output_channel[5:2] * kernel_size + kernel_row) * kernel_size + kernel_column) * input_channels + input_channel;
        output_index = (output_row * output_width + output_column) * output_channels + output_channel + store_lane;
    end

    always @(posedge clk) begin
        result_data <= results[result_address];
        if (read_enable)
            activation_read <= padding ? 8'sd0 : layer_index[0] ? activation_second[activation_index[12:0]] : activation_first[activation_index];
        if (!reset && state == STORE) begin
            if (layer_index == 5) results[output_index[8:0]] <= scaled;
            else if (!layer_index[0]) activation_second[output_index] <= activated;
        end
        if (!reset && state == STORE && layer_index != 5 && layer_index[0])
            activation_first[output_index] <= activated;
        else if (!reset && input_write && !busy && !done && input_address < 14'd12288)
            activation_first[input_address] <= input_data;
    end

    always @(posedge clk) begin
        if (reset) begin
            state <= IDLE;
            layer_index <= 0;
            active_model <= 0;
            input_width <= 64;
            input_channels <= 1;
            output_channels <= 8;
            output_width <= 32;
            kernel_size <= 3;
            stride <= 2;
            weight_base <= 0;
            bias_base <= 0;
            output_row <= 0;
            output_column <= 0;
            output_channel <= 0;
            input_channel <= 0;
            kernel_row <= 0;
            kernel_column <= 0;
            store_lane <= 0;
            mac_last <= 0;
            done <= 0;
            cycles <= 0;
        end else begin
            if (busy) cycles <= cycles + 1'b1;
            if (acknowledge && !busy) done <= 0;
            if (read_enable) begin
                mac_last <= last_tap;
                if (input_channel + 1 < input_channels) input_channel <= input_channel + 1'b1;
                else begin
                    input_channel <= 0;
                    if (kernel_column + 1 < kernel_size) kernel_column <= kernel_column + 1'b1;
                    else begin kernel_column <= 0; kernel_row <= kernel_row + 1'b1; end
                end
            end
            case (state)
                IDLE: if (start && !done && model_select < 2'd2) begin
                    active_model <= model_select[0];
                    layer_index <= 0;
                    cycles <= 0;
                    state <= SETUP;
                end
                SETUP: begin
                    case (layer_index)
                        0: begin input_width <= 64; input_channels <= active_model ? 1 : 3; output_channels <= 8; output_width <= 32; kernel_size <= 3; stride <= 2; weight_base <= 0; bias_base <= 0; end
                        1: begin input_width <= 32; input_channels <= 8; output_channels <= 16; output_width <= 16; kernel_size <= 3; stride <= 2; weight_base <= active_model ? 18 : 54; bias_base <= 2; end
                        2: begin input_width <= 16; input_channels <= 16; output_channels <= 24; output_width <= 8; kernel_size <= 3; stride <= 2; weight_base <= active_model ? 306 : 342; bias_base <= 6; end
                        3: begin input_width <= 8; input_channels <= 24; output_channels <= 32; output_width <= 4; kernel_size <= 3; stride <= 2; weight_base <= active_model ? 1170 : 1206; bias_base <= 12; end
                        4: begin input_width <= 4; input_channels <= 32; output_channels <= 32; output_width <= 4; kernel_size <= 3; stride <= 1; weight_base <= active_model ? 2898 : 2934; bias_base <= 20; end
                        5: begin input_width <= 4; input_channels <= 32; output_channels <= 27; output_width <= 4; kernel_size <= 1; stride <= 1; weight_base <= active_model ? 5202 : 5238; bias_base <= 28; end
                        default: state <= IDLE;
                    endcase
                    output_row <= 0;
                    output_column <= 0;
                    output_channel <= 0;
                    store_lane <= 0;
                    state <= BIAS_REQUEST;
                end
                BIAS_REQUEST: state <= BIAS_LOAD;
                BIAS_LOAD: begin
                    input_channel <= 0;
                    kernel_row <= 0;
                    kernel_column <= 0;
                    state <= REQUEST;
                end
                REQUEST: state <= MAC;
                MAC: if (mac_last) state <= STORE;
                STORE: begin
                    if (store_lane != 3 && output_channel + store_lane + 1 < output_channels)
                        store_lane <= store_lane + 1'b1;
                    else begin
                        store_lane <= 0;
                        state <= BIAS_REQUEST;
                        if (output_channel + 4 < output_channels) output_channel <= output_channel + 3'd4;
                        else begin
                            output_channel <= 0;
                            if (output_column + 1 < output_width) output_column <= output_column + 1'b1;
                            else begin
                                output_column <= 0;
                                if (output_row + 1 < output_width) output_row <= output_row + 1'b1;
                                else if (layer_index == 5) begin done <= 1; state <= IDLE; end
                                else begin layer_index <= layer_index + 1'b1; state <= SETUP; end
                            end
                        end
                    end
                end
                default: state <= IDLE;
            endcase
        end
    end
endmodule
