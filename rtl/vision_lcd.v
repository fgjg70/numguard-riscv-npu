`timescale 1ns/1ps

module vision_lcd (
    input wire clk,
    input wire reset,
    input wire show_bars,
    input wire frame_valid,
    input wire configured,
    input wire failed,
    input wire overlay_valid,
    input wire [116:0] extra_boxes,
    input wire [9:0] box_left,
    input wire [8:0] box_top,
    input wire [9:0] box_right,
    input wire [8:0] box_bottom,
    input wire [5:0] label,
    input wire [31:0] panel_status,
    input wire [31:0] pass_count,
    input wire [31:0] reject_count,
    input wire [127:0] camera_diagnostics,
    input wire [15:0] image_pixel,
    output wire [14:0] image_address,
    output wire frame_tick,
    output wire dclk,
    output reg hsync,
    output reg vsync,
    output reg de,
    output reg [4:0] red,
    output reg [5:0] green,
    output reg [4:0] blue
);
    reg [1:0] phase;
    reg clock_pos;
    reg clock_neg;
    reg [9:0] horizontal;
    reg [8:0] vertical;
    wire pixel_enable = phase == 1;
    wire active = horizontal >= 43 && horizontal < 523 && vertical >= 12 && vertical < 284;
    wire [9:0] pixel_x = horizontal - 10'd43;
    wire [8:0] pixel_y = vertical - 9'd12;
    wire image_area = active && pixel_x >= 80 && pixel_x < 400 && pixel_y >= 16 && pixel_y < 256;
    wire [7:0] image_x = (pixel_x - 10'd80) >> 1;
    wire [6:0] image_y = (pixel_y - 9'd16) >> 1;
    wire main_rectangle = overlay_valid && !panel_status[25] && frame_valid && image_area && !show_bars &&
        pixel_x >= box_left && pixel_x <= box_right && pixel_y >= box_top && pixel_y <= box_bottom &&
        (pixel_x < box_left + 2 || pixel_x + 2 > box_right || pixel_y < box_top + 2 || pixel_y + 2 > box_bottom);
    wire [2:0] extra_rectangle;
    wire rectangle = main_rectangle || |extra_rectangle;
    genvar extra_index;
    generate for (extra_index = 0; extra_index < 3; extra_index = extra_index + 1) begin: extra_overlay
        wire [38:0] box_data = extra_boxes[extra_index * 39 +: 39];
        wire [9:0] extra_left = box_data[37:28];
        wire [8:0] extra_top = box_data[27:19];
        wire [9:0] extra_right = box_data[18:9];
        wire [8:0] extra_bottom = box_data[8:0];
        assign extra_rectangle[extra_index] = overlay_valid && box_data[38] && !panel_status[25] && frame_valid && image_area && !show_bars &&
            pixel_x >= extra_left && pixel_x <= extra_right && pixel_y >= extra_top && pixel_y <= extra_bottom &&
            (pixel_x < extra_left + 2 || pixel_x + 2 > extra_right || pixel_y < extra_top + 2 || pixel_y + 2 > extra_bottom);
    end endgenerate
    wire top_text = active && pixel_y < 16;
    wire bottom_text = active && pixel_y >= 256;
    wire side_text = active && pixel_x < 72 && pixel_y >= 32 && pixel_y < 48;
    wire diagnostic_text = active && pixel_x < 72 && pixel_y >= 64 && pixel_y < 240;
    wire benchmark_mode = panel_status[24:23] == 2;
    wire benchmark_text = benchmark_mode && active && pixel_y >= 112 && pixel_y < 128;
    wire text_area = top_text || bottom_text || side_text || diagnostic_text || benchmark_text;
    wire [5:0] text_column = pixel_x / 12;
    wire [2:0] glyph_column = (pixel_x % 12) >> 1;
    wire [2:0] glyph_row = (pixel_y % 16) >> 1;
    reg [7:0] font [0:1023];
    reg [7:0] font_row;
    reg [79:0] label_text;
    reg [63:0] state_text;
    reg [15:0] state_color;
    reg [319:0] header_text;
    reg [319:0] footer_text;
    reg [319:0] benchmark_line;
    reg [47:0] score_text;
    reg [47:0] diagnostic_line;
    reg [7:0] character;
    reg [15:0] selected_color;

    initial $readmemh("model/font5x7.hex", font);

    function [7:0] hex_digit;
        input [3:0] value;
        begin hex_digit = value < 10 ? 8'd48 + value : 8'd55 + value; end
    endfunction

    function [15:0] decimal_pair;
        input [3:0] value;
        begin
            if (value == 10) decimal_pair = "10";
            else if (value < 10) decimal_pair = {8'd32, hex_digit(value)};
            else decimal_pair = "  ";
        end
    endfunction

    function [47:0] coremark_text;
        input [31:0] value;
        begin coremark_text = {hex_digit(value[19:16]), ".", hex_digit(value[15:12]), hex_digit(value[11:8]), hex_digit(value[7:4]), hex_digit(value[3:0])}; end
    endfunction

    function [63:0] throughput_text;
        input [31:0] value;
        begin throughput_text = {hex_digit(value[27:24]), hex_digit(value[23:20]), hex_digit(value[19:16]), hex_digit(value[15:12]), hex_digit(value[11:8]), ".", hex_digit(value[7:4]), hex_digit(value[3:0])}; end
    endfunction

    function [39:0] count_text;
        input [31:0] value;
        begin
            count_text = {hex_digit(value[19:16]), hex_digit(value[15:12]), hex_digit(value[11:8]), hex_digit(value[7:4]), hex_digit(value[3:0])};
            if (value > 32'hfffff) count_text = "FULL ";
        end
    endfunction

    assign image_address = image_area ? image_y * 15'd160 + image_x : 15'd0;
    assign frame_tick = pixel_enable && horizontal == 0 && vertical == 0;
    assign dclk = clock_pos | clock_neg;

    always @* begin
        case (label)
            2: label_text = "HAND 10   ";
            3: label_text = "HAND 1    ";
            4: label_text = "HAND 2    ";
            5: label_text = "HAND 3    ";
            6: label_text = "HAND 4    ";
            7: label_text = "HAND 5    ";
            8: label_text = "HAND 6    ";
            9: label_text = "HAND 7    ";
            10: label_text = "HAND 8    ";
            11: label_text = "HAND 9    ";
            22: label_text = "CHICKPEA  ";
            23: label_text = "CASHEW    ";
            24: label_text = "ALMOND    ";
            25: label_text = "WALNUT    ";
            default: label_text = "UNKNOWN   ";
        endcase
        if (!overlay_valid || show_bars || panel_status[25]) label_text = "NO TARGET ";
        case (panel_status[2:0])
            0: begin state_text = "READY   "; state_color = 16'h07ff; end
            1: begin state_text = "WAIT    "; state_color = 16'hffe0; end
            2: begin state_text = panel_status[24:23] == 1 ? "MATCH   " : "FOUND   "; state_color = 16'h07e0; end
            3: begin state_text = "WRONG   "; state_color = 16'hf800; end
            4: begin state_text = "UNSURE  "; state_color = 16'hffe0; end
            5: begin state_text = "REMOVE  "; state_color = 16'hffe0; end
            default: begin state_text = "FAULT   "; state_color = 16'hf800; end
        endcase
        if (panel_status[25]) begin state_text = "NO MODEL"; state_color = 16'hffe0; end
        if (!frame_valid) begin state_text = "NO FRAME"; state_color = 16'hffe0; end
        if (!configured) begin state_text = "CAM WAIT"; state_color = 16'hffe0; end
        if (failed) begin state_text = "CAM ERR "; state_color = 16'hf800; end
        header_text = {"NUTS YOLO      COUNT ", hex_digit({1'b0, panel_status[30:28]}), "  ", state_text, "        "};
        if (panel_status[24:23] == 1) header_text = {"GESTURE  EXP ", decimal_pair(panel_status[7:4]), " SEEN ", decimal_pair(panel_status[11:8]), " ", state_text, "        "};
        if (panel_status[24:23] >= 2) header_text = {"UNAVAILABLE    COUNT ", hex_digit({1'b0, panel_status[30:28]}), "  ", state_text, "        "};
        footer_text = {"OK ", count_text(pass_count), " BAD ", count_text(reject_count), "            ", label_text};
        if (panel_status[24:23] != 1) footer_text = {"FRM ", count_text(pass_count), " EMPTY ", count_text(reject_count), "         ", label_text};
        if (panel_status[25]) footer_text = "MODEL NOT LOADED - SW6 HAND SW4 BENCH   ";
        if (!configured || failed) footer_text = "DVP18 CHECK POWER SCL SDA MCLK RESET    ";
        if (benchmark_mode) begin
            case (panel_status[2:0])
                1: begin state_text = "RUNNING "; state_color = 16'hffe0; end
                2: begin state_text = "VALID   "; state_color = 16'h07e0; end
                4: begin state_text = "SHORT   "; state_color = 16'hffe0; end
                default: begin state_text = "FAILED  "; state_color = 16'hf800; end
            endcase
            header_text = {"COREMARK CPU BENCHMARK  ", state_text, "        "};
            footer_text = {"COREMARK ", throughput_text(reject_count), "  ", state_text, "             "};
        end
        benchmark_line = {"COREMARK/MHZ ", coremark_text(pass_count), "                     "};
        score_text = {"Q7 ", hex_digit({1'b0, panel_status[22:20]}), hex_digit(panel_status[19:16]), " "};
        case (pixel_y[7:4])
            4: diagnostic_line = {"ID ", hex_digit(camera_diagnostics[79:76]), hex_digit(camera_diagnostics[75:72]), " "};
            5: diagnostic_line = {"VR ", hex_digit(camera_diagnostics[71:68]), hex_digit(camera_diagnostics[67:64]), " "};
            6: diagnostic_line = {"ST ", hex_digit(camera_diagnostics[63:60]), hex_digit(camera_diagnostics[59:56]), " "};
            7: diagnostic_line = {"RX ", hex_digit(camera_diagnostics[55:52]), hex_digit(camera_diagnostics[51:48]), " "};
            8: diagnostic_line = {"V ", hex_digit(camera_diagnostics[47:44]), hex_digit(camera_diagnostics[43:40]), hex_digit(camera_diagnostics[39:36]), hex_digit(camera_diagnostics[35:32])};
            9: diagnostic_line = {"F ", hex_digit(camera_diagnostics[15:12]), hex_digit(camera_diagnostics[11:8]), hex_digit(camera_diagnostics[7:4]), hex_digit(camera_diagnostics[3:0])};
            10: diagnostic_line = {"T ", hex_digit(camera_diagnostics[31:28]), hex_digit(camera_diagnostics[27:24]), hex_digit(camera_diagnostics[23:20]), hex_digit(camera_diagnostics[19:16])};
            11: diagnostic_line = {"C", configured ? 8'd49 : 8'd48, " E", failed ? 8'd49 : 8'd48, " "};
            12: diagnostic_line = {"P ", hex_digit(camera_diagnostics[127:124]), hex_digit(camera_diagnostics[123:120]), hex_digit(camera_diagnostics[119:116]), hex_digit(camera_diagnostics[115:112])};
            13: diagnostic_line = {"L ", hex_digit(camera_diagnostics[111:108]), hex_digit(camera_diagnostics[107:104]), hex_digit(camera_diagnostics[103:100]), hex_digit(camera_diagnostics[99:96])};
            default: diagnostic_line = {"B ", hex_digit(camera_diagnostics[95:92]), hex_digit(camera_diagnostics[91:88]), hex_digit(camera_diagnostics[87:84]), hex_digit(camera_diagnostics[83:80])};
        endcase
        character = 8'd32;
        if (top_text && text_column < 40) character = header_text[319 - text_column * 8 -: 8];
        if (bottom_text && text_column < 40) character = footer_text[319 - text_column * 8 -: 8];
        if (side_text && !benchmark_mode && text_column < 6) character = score_text[47 - text_column * 8 -: 8];
        if (diagnostic_text && !benchmark_mode && text_column < 6) character = diagnostic_line[47 - text_column * 8 -: 8];
        if (benchmark_text && text_column < 40) character = benchmark_line[319 - text_column * 8 -: 8];
    end

    always @(posedge clk) font_row <= font[{character[6:0], glyph_row}];

    always @* begin
        selected_color = 16'h18e3;
        if (image_area) selected_color = frame_valid ? image_pixel : 16'h0000;
        if (show_bars) begin
            case (pixel_x / 60)
                0: selected_color = 16'hffff;
                1: selected_color = 16'hffe0;
                2: selected_color = 16'h07ff;
                3: selected_color = 16'h07e0;
                4: selected_color = 16'hf81f;
                5: selected_color = 16'hf800;
                6: selected_color = 16'h001f;
                default: selected_color = 0;
            endcase
        end
        if (rectangle) selected_color = 16'hf800;
        if (benchmark_mode) selected_color = 0;
        if (text_area) selected_color = glyph_column < 5 && glyph_row < 7 && font_row[4 - glyph_column] ? (top_text ? state_color : 16'hffff) : 16'h0000;
        if (!active) selected_color = 0;
    end

    always @(negedge clk) begin
        if (reset) clock_neg <= 0;
        else clock_neg <= clock_pos;
    end

    always @(posedge clk) begin
        if (reset) begin
            phase <= 0;
            clock_pos <= 0;
            horizontal <= 0;
            vertical <= 0;
            hsync <= 1;
            vsync <= 1;
            de <= 0;
            red <= 0;
            green <= 0;
            blue <= 0;
        end else begin
            if (phase == 2) begin phase <= 0; clock_pos <= 1; end
            else begin phase <= phase + 1'b1; clock_pos <= 0; end
            if (pixel_enable) begin
                hsync <= horizontal >= 41;
                vsync <= vertical >= 10;
                de <= active;
                red <= selected_color[15:11];
                green <= selected_color[10:5];
                blue <= selected_color[4:0];
                if (horizontal == 524) begin
                    horizontal <= 0;
                    if (vertical == 285) vertical <= 0;
                    else vertical <= vertical + 1'b1;
                end else horizontal <= horizontal + 1'b1;
            end
        end
    end
endmodule
