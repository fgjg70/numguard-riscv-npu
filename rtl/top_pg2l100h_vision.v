`timescale 1ns/1ps

module top_pg2l100h_vision (
    input wire sys_clk,
    input wire reset_n,
    input wire [3:0] keys_n,
    input wire uart_rxd,
    output wire uart_txd,
    output wire [6:0] servo_pwm,
    output wire [3:0] led,
    output wire [7:0] lcd_r,
    output wire [7:0] lcd_g,
    output wire [7:0] lcd_b,
    output wire lcd_dclk,
    output wire lcd_hsync,
    output wire lcd_vsync,
    output wire lcd_de,
    output wire touch_sclk,
    output wire touch_cs_n,
    output wire touch_mosi,
    input wire touch_busy,
    output wire cam_scl,
    inout wire cam_sda,
    input wire cam_vsync,
    input wire cam_href,
    input wire cam_pclk,
    input wire [7:0] cam_data,
    output wire cam_xclk,
    output wire cam_reset_n,
    output wire cam_pwdn
);
    wire clk_50m = sys_clk;
    wire clk_27m;
    wire clk_125m;
    wire cpu_pll_locked;
    reg [2:0] cpu_reset_sync;
    wire cpu_reset = cpu_reset_sync[2];
    wire pll_locked;
    reg [2:0] reset_sync;
    reg [15:0] powerup = 0;
    wire reset = !(&powerup);
    wire cpu_reset_request = reset || !cpu_pll_locked;
    reg [2:0] video_reset_sync;
    wire video_reset = video_reset_sync[2];
    reg [24:0] heartbeat;
    wire configured;
    wire failed;
    wire [7:0] camera_pid;
    wire [7:0] camera_version;
    wire [7:0] camera_step;
    wire [3:0] capture_state;
    wire [15:0] camera_vsync_count;
    wire [15:0] camera_timeout_count;
    reg [1:0] camera_sda_sync;
    wire [127:0] camera_diagnostics;
    wire [15:0] camera_pclk_count;
    wire [15:0] camera_line_count;
    wire [15:0] camera_bad_frame_count;
    wire [1:0] camera_levels;
    reg [1:0] camera_clock_phase;
    reg [15:0] camera_reset_timer;
    wire frozen;
    wire show_bars;
    wire restart;
    wire pixel_write;
    wire [14:0] write_address;
    wire [15:0] write_data;
    wire frame_done;
    wire frame_ack;
    wire frame_valid;
    wire frame_tick;
    wire video_frame_tick;
    wire display_valid;
    wire [15:0] frame_count;
    wire [14:0] read_address;
    wire [15:0] read_data;
    wire overlay_valid;
    wire [116:0] extra_boxes;
    wire [116:0] video_extra_boxes;
    wire [9:0] box_left;
    wire [8:0] box_top;
    wire [9:0] box_right;
    wire [8:0] box_bottom;
    wire [5:0] label;
    wire [31:0] panel_status;
    wire [31:0] pass_count;
    wire [31:0] reject_count;
    wire inference_busy;
    wire cpu_illegal;
    wire [4:0] red;
    wire [5:0] green;
    wire [4:0] blue;
    wire video_bars;
    wire video_configured;
    wire video_failed;
    wire video_overlay;
    wire [9:0] video_left;
    wire [8:0] video_top;
    wire [9:0] video_right;
    wire [8:0] video_bottom;
    wire [5:0] video_label;
    wire [31:0] video_status;
    wire [31:0] video_pass;
    wire [31:0] video_reject;
    wire [127:0] video_camera_diagnostics;

    assign camera_diagnostics = {camera_pclk_count, camera_line_count, camera_bad_frame_count,
        camera_pid, camera_version, camera_step,
        camera_levels, cam_scl, camera_sda_sync[1], capture_state,
        camera_vsync_count, camera_timeout_count, frame_count};
    assign cam_xclk = camera_clock_phase[1];
    assign cam_reset_n = !reset && camera_reset_timer == 16'd50000;
    assign cam_pwdn = 1'b0;

    pg2l100h_clock clock_gen (.clk_50m(sys_clk), .reset(!reset_n), .clk_27m(clk_27m), .locked(pll_locked));
    vision_cpu_clock cpu_clock_gen (.clk_50m(sys_clk), .reset(!reset_n), .clk_125m(clk_125m), .locked(cpu_pll_locked));
    always @(posedge clk_125m or posedge cpu_reset_request) begin
        if (cpu_reset_request) cpu_reset_sync <= 3'b111;
        else cpu_reset_sync <= {cpu_reset_sync[1:0], 1'b0};
    end
    always @(posedge clk_50m or negedge reset_n) begin
        if (!reset_n) begin reset_sync <= 0; powerup <= 0; end
        else begin
            reset_sync <= {reset_sync[1:0], pll_locked && cpu_pll_locked};
            if (!reset_sync[2]) powerup <= 0;
            else if (!(&powerup)) powerup <= powerup + 1'b1;
        end
    end
    always @(posedge clk_27m or posedge reset) begin
        if (reset) video_reset_sync <= 3'b111;
        else video_reset_sync <= {video_reset_sync[1:0], 1'b0};
    end
    always @(posedge clk_50m) begin
        if (reset) begin
            heartbeat <= 0;
            camera_sda_sync <= 2'b11;
            camera_clock_phase <= 0;
            camera_reset_timer <= 0;
        end else begin
            heartbeat <= heartbeat + 1'b1;
            camera_sda_sync <= {camera_sda_sync[0], cam_sda};
            camera_clock_phase <= camera_clock_phase + 1'b1;
            if (camera_reset_timer != 16'd50000) camera_reset_timer <= camera_reset_timer + 1'b1;
        end
    end
    assign led = {cpu_illegal || failed || panel_status[2:0] == 3, inference_busy, configured, heartbeat[24]};
    assign lcd_r = {red, red[4:2]};
    assign lcd_g = {green, green[5:4]};
    assign lcd_b = {blue, blue[4:2]};
    assign touch_sclk = 0;
    assign touch_cs_n = 1;
    assign touch_mosi = 0;

    camera_config #(.CLOCK_HZ(50000000), .BUS_HZ(50000), .SETTLE_CYCLES(2500000), .SENSOR_CLKRC(8'h03), .SENSOR_MVFP(8'h37)) sensor_configuration (
        .clk(clk_50m), .reset(reset || !cam_reset_n), .restart(restart), .sensor_bars(1'b0),
        .configured(configured), .failed(failed), .pid(camera_pid), .version(camera_version),
        .step(camera_step), .scl(cam_scl), .sda(cam_sda)
    );
    camera_dvp_capture #(.TIMEOUT_CYCLES(50000000)) capture (
        .clk(clk_50m), .reset(reset || restart || !cam_reset_n), .configured(configured), .freeze(frozen),
        .frame_ack(frame_ack), .vsync(cam_vsync), .href(cam_href), .pclk(cam_pclk), .data(cam_data),
        .pixel_write(pixel_write), .pixel_address(write_address), .pixel_data(write_data),
        .frame_done(frame_done), .frame_count(frame_count), .timeout_count(camera_timeout_count),
        .vsync_count(camera_vsync_count), .state(capture_state), .sync_levels(camera_levels),
        .pclk_count(camera_pclk_count), .line_count(camera_line_count), .bad_frame_count(camera_bad_frame_count)
    );
    vision_framebuffer framebuffer (
        .write_clk(clk_50m), .read_clk(clk_27m), .reset(reset || restart || !configured),
        .pixel_write(pixel_write), .write_address(write_address), .write_data(write_data),
        .frame_done(frame_done), .frame_tick(video_frame_tick), .read_address(read_address),
        .read_data(read_data), .frame_valid(frame_valid), .frame_ack(frame_ack), .display_valid(display_valid)
    );
    vision_soc system (
        .clk(clk_50m), .reset(reset), .keys_n(keys_n), .uart_rxd(uart_rxd), .uart_txd(uart_txd),
        .servo_pwm(servo_pwm),
        .memory_read(), .memory_write(), .memory_address(), .memory_wdata(), .memory_wstrb(),
        .memory_ready(1'b1), .memory_rdata(32'hdd300bad), .memory_status(32'd0),
        .cpu_clk(clk_125m), .cpu_reset(cpu_reset),
        .configured(configured), .failed(failed), .frame_valid(frame_valid), .frame_count(frame_count),
        .frame_tick(frame_tick), .pixel_write(pixel_write), .pixel_address(write_address),
        .pixel_data(write_data), .frame_done(frame_done), .frozen(frozen), .show_bars(show_bars),
        .restart(restart), .overlay_valid(overlay_valid), .extra_boxes(extra_boxes), .box_left(box_left), .box_top(box_top),
        .box_right(box_right), .box_bottom(box_bottom), .label(label), .panel_status(panel_status),
        .pass_count(pass_count), .reject_count(reject_count), .inference_busy(inference_busy), .cpu_illegal(cpu_illegal)
    );
    vision_video_bridge #(.PAYLOAD_BITS(389)) video_bridge (
        .system_clk(clk_50m), .system_reset(reset),
        .source_payload({extra_boxes, camera_diagnostics, show_bars, configured, failed, overlay_valid, box_left, box_top, box_right, box_bottom, label, panel_status, pass_count, reject_count}),
        .video_clk(clk_27m), .video_reset(video_reset), .video_frame_tick(video_frame_tick),
        .display_payload({video_extra_boxes, video_camera_diagnostics, video_bars, video_configured, video_failed, video_overlay, video_left, video_top, video_right, video_bottom, video_label, video_status, video_pass, video_reject}),
        .system_frame_tick(frame_tick)
    );
    vision_lcd video (
        .clk(clk_27m), .reset(video_reset), .show_bars(video_bars), .frame_valid(display_valid),
        .configured(video_configured), .failed(video_failed), .overlay_valid(video_overlay), .extra_boxes(video_extra_boxes), .box_left(video_left),
        .box_top(video_top), .box_right(video_right), .box_bottom(video_bottom), .label(video_label),
        .panel_status(video_status), .pass_count(video_pass), .reject_count(video_reject),
        .camera_diagnostics(video_camera_diagnostics),
        .image_pixel(read_data), .image_address(read_address), .frame_tick(video_frame_tick),
        .dclk(lcd_dclk), .hsync(lcd_hsync), .vsync(lcd_vsync), .de(lcd_de), .red(red), .green(green), .blue(blue)
    );
endmodule
