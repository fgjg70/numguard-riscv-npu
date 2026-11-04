`timescale 1ns/1ps

module vision_snapshot (
    input wire clk,
    input wire reset,
    input wire [1:0] model_select,
    input wire enabled,
    input wire busy,
    input wire done,
    input wire pixel_write,
    input wire [14:0] pixel_address,
    input wire [15:0] pixel_data,
    input wire frame_done,
    output reg input_write,
    output reg [13:0] input_address,
    output reg [7:0] input_data,
    output reg start
);
    localparam PAD=0, WAIT_FRAME=1, CAPTURE=2, WAIT_NPU=3;
    reg [1:0] state;
    reg [11:0] pad_index;
    reg [7:0] column;
    reg [6:0] row;
    reg [2:0] column_phase;
    reg [2:0] row_phase;
    reg [5:0] target_column;
    reg [5:0] target_row;
    reg [1:0] pending_channels;
    reg [6:0] pending_green;
    reg [6:0] pending_blue;
    reg frame_ending;
    wire hand_model = model_select == 2'd1;
    wire [7:0] red = {pixel_data[15:11], pixel_data[15:13]};
    wire [7:0] green = {pixel_data[10:5], pixel_data[10:9]};
    wire [7:0] blue = {pixel_data[4:0], pixel_data[4:2]};
    wire skin = red > 50 && green > 25 && blue > 10 &&
                {1'b0, red} > {1'b0, green} + 9'd5 && {1'b0, red} > {1'b0, blue} + 9'd15;
    wire first_pixel = pixel_address == 0;
    wire selected_row = first_pixel || row_phase == 0 || row_phase == 2;
    wire selected_column = first_pixel || column_phase == 0 || column_phase == 2;
    wire begin_frame = state == WAIT_FRAME && enabled && !busy && !done && pixel_write && first_pixel;
    wire capture_pixel = pixel_write && enabled && !frame_ending && (state == CAPTURE || begin_frame);

    always @(posedge clk) begin
        if (reset) begin
            state <= PAD;
            pad_index <= 0;
            row <= 0;
            column <= 0;
            row_phase <= 0;
            column_phase <= 0;
            target_row <= 0;
            target_column <= 0;
            pending_channels <= 0;
            pending_green <= 0;
            pending_blue <= 0;
            frame_ending <= 0;
            input_write <= 0;
            input_address <= 0;
            input_data <= 0;
            start <= 0;
        end else begin
            input_write <= 0;
            start <= 0;
            if (pixel_write) begin
                if (first_pixel) begin
                    column <= 1;
                    row <= 0;
                    column_phase <= 1;
                    row_phase <= 0;
                    target_column <= 1;
                    target_row <= 0;
                end else if (column == 159) begin
                    column <= 0;
                    row <= row + 1'b1;
                    column_phase <= 0;
                    row_phase <= row_phase == 4 ? 3'd0 : row_phase + 1'b1;
                    target_column <= 0;
                    if (selected_row) target_row <= target_row + 1'b1;
                end else begin
                    column <= column + 1'b1;
                    column_phase <= column_phase == 4 ? 3'd0 : column_phase + 1'b1;
                    if (selected_column) target_column <= target_column + 1'b1;
                end
            end
            if (pending_channels != 0) begin
                input_write <= 1;
                input_address <= input_address + 1'b1;
                input_data <= {1'b0, pending_channels == 2 ? pending_green : pending_blue};
                pending_channels <= pending_channels - 1'b1;
            end
            case (state)
                PAD: if (!busy && !done) begin
                    input_write <= 1;
                    input_data <= 0;
                    if (hand_model) begin
                        input_address <= pad_index < 128 ? {2'b0, pad_index} : 14'd3968 + (pad_index - 12'd128);
                        if (pad_index == 255) begin state <= WAIT_FRAME; pad_index <= 0; end
                        else pad_index <= pad_index + 1'b1;
                    end else begin
                        input_address <= pad_index < 1536 ? {2'b0, pad_index} : 14'd10752 + (pad_index - 12'd1536);
                        if (pad_index == 3071) begin state <= WAIT_FRAME; pad_index <= 0; end
                        else pad_index <= pad_index + 1'b1;
                    end
                end
                WAIT_FRAME: if (begin_frame) begin state <= CAPTURE; frame_ending <= 0; end
                CAPTURE: begin
                    if (!enabled) begin state <= PAD; pending_channels <= 0; frame_ending <= 0; end
                    else if (frame_done || frame_ending) begin
                        frame_ending <= 1;
                        if (pending_channels == 0) begin start <= 1; state <= WAIT_NPU; end
                    end
                end
                WAIT_NPU: if (!busy && !done && !start) begin state <= PAD; frame_ending <= 0; end
                default: state <= PAD;
            endcase
            if (capture_pixel) begin
                if (hand_model) begin
                    if (!column[0] && !row[0] && column >= 16 && column < 144 && !first_pixel) begin
                        input_write <= 1;
                        input_address <= ((row >> 1) + 14'd2) * 14'd64 + ((column - 8'd16) >> 1);
                        input_data <= skin ? 8'd127 : 8'd0;
                    end
                end else if (selected_row && selected_column) begin
                    if (pending_channels != 0) begin
                        state <= PAD;
                        pad_index <= 0;
                        pending_channels <= 0;
                        input_write <= 0;
                    end else begin
                        input_write <= 1;
                        input_address <= first_pixel ? 14'd1536 : (({8'd0, target_row} + 14'd8) * 14'd64 + target_column) * 14'd3;
                        input_data <= {1'b0, red[7:1]};
                        pending_green <= green[7:1];
                        pending_blue <= blue[7:1];
                        pending_channels <= 2;
                    end
                end
            end
        end
    end
endmodule
