`timescale 1ns/1ps

module vision_soc #(
    parameter integer CLOCK_HZ = 50000000,
    parameter integer UART_BAUD = 115200,
    parameter integer KEY_DEBOUNCE_CYCLES = CLOCK_HZ / 50,
    parameter MEMORY_ENABLE = 0
) (
    input wire clk,
    input wire reset,
    input wire cpu_clk,
    input wire cpu_reset,
    input wire [3:0] keys_n,
    input wire uart_rxd,
    output wire uart_txd,
    output wire [6:0] servo_pwm,
    output wire memory_read,
    output wire memory_write,
    output wire [31:0] memory_address,
    output wire [31:0] memory_wdata,
    output wire [3:0] memory_wstrb,
    input wire memory_ready,
    input wire [31:0] memory_rdata,
    input wire [31:0] memory_status,
    input wire configured,
    input wire failed,
    input wire frame_valid,
    input wire [15:0] frame_count,
    input wire frame_tick,
    input wire pixel_write,
    input wire [14:0] pixel_address,
    input wire [15:0] pixel_data,
    input wire frame_done,
    output wire frozen,
    output wire show_bars,
    output reg restart,
    output reg overlay_valid,
    output reg [116:0] extra_boxes,
    output reg [9:0] box_left,
    output reg [8:0] box_top,
    output reg [9:0] box_right,
    output reg [8:0] box_bottom,
    output reg [5:0] label,
    output reg [31:0] panel_status,
    output reg [31:0] pass_count,
    output reg [31:0] reject_count,
    output wire inference_busy,
    output wire cpu_illegal
);
    wire bus_write;
    wire bus_read;
    wire [31:0] bus_address;
    wire [31:0] bus_wdata;
    wire [3:0] bus_wstrb;
    reg [31:0] bus_rdata;
    reg [31:0] counter;
    reg [31:0] counter_high;
    reg [2:0] control;
    wire [5:0] pressed;
    wire [5:0] press_pulse;
    wire [7:0] rx_data;
    wire rx_valid;
    wire rx_error;
    wire tx_busy;
    reg [7:0] rx_fifo [0:31];
    reg [4:0] rx_read_pointer;
    reg [4:0] rx_write_pointer;
    reg [5:0] rx_count;
    wire rx_pending = rx_count != 0;
    wire [7:0] rx_latched = rx_pending ? rx_fifo[rx_read_pointer] : 8'd0;
    wire rx_pop = bus_read && bus_address == 32'ha1000088 && rx_pending;
    wire rx_push = rx_valid && (rx_count < 32 || rx_pop);
    reg [15:0] rx_errors;
    reg [8:0] result_address;
    wire signed [31:0] result_data;
    wire inference_done;
    wire [31:0] inference_cycles;
    wire input_write;
    wire [13:0] input_address;
    wire [7:0] input_data;
    wire inference_start;
    reg inference_ack;
    reg [31:0] pending_first;
    reg [31:0] pending_second;
    reg [31:0] pending_label;
    reg commit_pending;
    reg [31:0] pending_status;
    reg [31:0] pending_pass;
    reg [31:0] pending_reject;
    reg next_target_pending;
    reg [31:0] inference_start_cycle;
    reg [31:0] commit_first;
    reg [31:0] commit_second;
    reg [31:0] commit_label;
    reg [31:0] commit_status;
    reg [31:0] commit_pass;
    reg [31:0] commit_reject;
    reg [31:0] pending_extra_first [0:2];
    reg [31:0] pending_extra_second [0:2];
    reg [31:0] pending_extra_label [0:2];
    reg [116:0] commit_extra_boxes;
    integer extra_index;
    reg [7:0] sample_memory [0:16383];
    reg [13:0] sample_address;
    reg [7:0] sample_data;
    reg [31:0] benchmark_done;
    reg [31:0] completed_count;
    reg [1:0] application_mode;
    wire [1:0] model_select = application_mode;
    reg [1:0] available_models [0:0];
    wire model_ready = application_mode < 2'd2 && available_models[0][application_mode];
    reg [2:0] key_events;
    reg [2:0] coremark_state;
    reg [31:0] coremark_per_mhz;
    reg [31:0] coremark_score;
    reg [3:0] coremark_crc;
    wire full_write = bus_write && bus_wstrb == 4'hf;
    wire servo_select = bus_address[31:8] == 24'ha10000;
    wire [31:0] servo_rdata;
    wire model_write = full_write && bus_address == 32'ha100005c;
    wire accelerator_reset = reset || restart || !configured || model_write || !model_ready;

    initial $readmemh("model/available.hex", available_models);

    assign frozen = control[1];
    assign show_bars = control[2];

    vision_servo_pwm #(.CLOCK_HZ(CLOCK_HZ)) servos (
        .clk(clk), .reset(reset), .write(full_write && servo_select),
        .address(bus_address[7:0]), .write_data(bus_wdata), .read_data(servo_rdata), .servo_pwm(servo_pwm)
    );

    wire cpu_write;
    wire cpu_read;
    wire [31:0] cpu_address;
    wire [31:0] cpu_wdata;
    wire [3:0] cpu_wstrb;
    wire [31:0] cpu_rdata;
    wire cpu_ready;
    wire cached_write;
    wire cached_read;
    wire [31:0] cached_address;
    wire [31:0] cached_wdata;
    wire [3:0] cached_wstrb;
    wire [31:0] cached_rdata;
    wire cached_ready;
    wire memory_selected = MEMORY_ENABLE && bus_address[31:28] == 4'hc;
    wire peripheral_ready = !memory_selected || memory_ready;
    wire [31:0] peripheral_rdata = memory_selected ? memory_rdata : bus_rdata;

    assign memory_read = memory_selected && bus_read;
    assign memory_write = memory_selected && bus_write;
    assign memory_address = {4'd0, bus_address[27:0]};
    assign memory_wdata = bus_wdata;
    assign memory_wstrb = bus_wstrb;

    vision_dcache #(.DDR_PRESENT(MEMORY_ENABLE)) cache (
        .clk(cpu_clk), .reset(cpu_reset), .cpu_read(cpu_read), .cpu_write(cpu_write),
        .cpu_address(cpu_address), .cpu_wdata(cpu_wdata), .cpu_wstrb(cpu_wstrb),
        .cpu_ready(cpu_ready), .cpu_rdata(cpu_rdata),
        .memory_read(cached_read), .memory_write(cached_write), .memory_address(cached_address),
        .memory_wdata(cached_wdata), .memory_wstrb(cached_wstrb),
        .memory_ready(cached_ready), .memory_rdata(cached_rdata)
    );

    vision_mmio_bridge #(.WAIT_PERIPHERAL(1)) cpu_bus (
        .cpu_clk(cpu_clk), .cpu_reset(cpu_reset), .cpu_write(cached_write), .cpu_read(cached_read),
        .cpu_address(cached_address), .cpu_wdata(cached_wdata), .cpu_wstrb(cached_wstrb),
        .cpu_ready(cached_ready), .response_data(cached_rdata),
        .peripheral_clk(clk), .peripheral_reset(reset),
        .peripheral_write(bus_write), .peripheral_read(bus_read), .peripheral_address(bus_address),
        .peripheral_wdata(bus_wdata), .peripheral_wstrb(bus_wstrb), .peripheral_rdata(peripheral_rdata), .peripheral_ready(peripheral_ready)
    );
    core #(.EXTERNAL_WAIT(1), .IMEM_BYTES(65536)) processor (
        .clk(cpu_clk), .rst(cpu_reset), .dbg_raddr(5'd0), .dbg_illegal(cpu_illegal),
        .dbg_pc(), .dbg_id_pc(), .dbg_instr(), .dbg_rdata(),
        .dbg_mem_we(), .dbg_mem_addr(), .dbg_mem_wdata(), .dbg_stall(), .dbg_redirect(),
        .ext_we(cpu_write), .ext_re(cpu_read), .ext_addr(cpu_address),
        .ext_wdata(cpu_wdata), .ext_wstrb(cpu_wstrb), .ext_rdata(cpu_rdata), .ext_ready(cpu_ready), .irq_timer(1'b0)
    );

    debug_keys #(.DEBOUNCE_CYCLES(KEY_DEBOUNCE_CYCLES)) keys (.clk(clk), .reset(reset), .keys_n({2'b11, keys_n}), .pressed(pressed), .press_pulse(press_pulse));
    debug_uart #(.CLOCK_HZ(CLOCK_HZ), .BAUD(UART_BAUD)) uart (
        .clk(clk), .reset(reset), .rxd(uart_rxd), .txd(uart_txd), .rx_data(rx_data),
        .rx_valid(rx_valid), .rx_frame_error(rx_error), .tx_data(bus_wdata[7:0]),
        .tx_start(full_write && bus_address == 32'ha1000080), .tx_busy(tx_busy)
    );

    vision_snapshot snapshot (
        .clk(clk), .reset(accelerator_reset), .model_select(model_select), .enabled(model_ready && control[0] && !show_bars && !frozen),
        .busy(inference_busy), .done(inference_done), .pixel_write(pixel_write),
        .pixel_address(pixel_address), .pixel_data(pixel_data), .frame_done(frame_done),
        .input_write(input_write), .input_address(input_address), .input_data(input_data), .start(inference_start)
    );
    vision_npu accelerator (
        .clk(clk), .reset(accelerator_reset), .model_select(model_select), .input_write(input_write), .input_address(input_address),
        .input_data(input_data), .start(inference_start), .acknowledge(inference_ack),
        .result_address(result_address), .result_data(result_data), .busy(inference_busy),
        .done(inference_done), .cycles(inference_cycles)
    );

    always @* begin
        case (bus_address)
            32'ha1000000: bus_rdata = 32'h56530101;
            32'ha1000004: bus_rdata = {23'd0, !model_ready, show_bars, frozen, control[0], inference_done, inference_busy, frame_valid, failed, configured};
            32'ha1000008: bus_rdata = {16'd0, frame_count};
            32'ha100000c: bus_rdata = inference_cycles;
            32'ha1000010: bus_rdata = {23'd0, result_address};
            32'ha1000014: bus_rdata = result_data;
            32'ha1000018: bus_rdata = {29'd0, control};
            32'ha100001c: bus_rdata = counter;
            32'ha1000030: bus_rdata = completed_count;
            32'ha1000034: bus_rdata = panel_status;
            32'ha1000038: bus_rdata = pass_count;
            32'ha100003c: bus_rdata = reject_count;
            32'ha1000040: bus_rdata = {31'd0, next_target_pending};
            32'ha1000044: bus_rdata = inference_start_cycle;
            32'ha1000048: bus_rdata = counter_high;
            32'ha1000050: bus_rdata = benchmark_done;
            32'ha1000054: bus_rdata = {18'd0, sample_address};
            32'ha1000058: bus_rdata = {24'd0, sample_data};
            32'ha100005c: bus_rdata = {30'd0, application_mode};
            32'ha1000060: bus_rdata = {29'd0, key_events};
            32'ha1000064: bus_rdata = {30'd0, available_models[0]};
            32'ha100006c: bus_rdata = {29'd0, coremark_state};
            32'ha1000070: bus_rdata = coremark_per_mhz;
            32'ha1000074: bus_rdata = coremark_score;
            32'ha1000078: bus_rdata = {28'd0, coremark_crc};
            32'ha100007c: bus_rdata = MEMORY_ENABLE ? memory_status : 32'd0;
            32'ha1000084: bus_rdata = {31'd0, tx_busy};
            32'ha1000088: bus_rdata = {24'd0, rx_latched};
            32'ha100008c: bus_rdata = {15'd0, rx_errors, rx_pending};
            default: bus_rdata = bus_address[31:28] == 4'hc ? 32'hdd300bad : (servo_select ? servo_rdata : 32'd0);
        endcase
    end

    always @(posedge clk) begin
        if (input_write) sample_memory[input_address] <= input_data;
        sample_data <= sample_memory[sample_address];
    end

    always @(posedge clk) begin
        if (reset) begin
            counter <= 0;
            counter_high <= 0;
            control <= 3'b001;
            restart <= 0;
            inference_ack <= 0;
            result_address <= 0;
            rx_read_pointer <= 0;
            rx_write_pointer <= 0;
            rx_count <= 0;
            rx_errors <= 0;
            pending_first <= 0;
            pending_second <= 0;
            pending_label <= 0;
            extra_boxes <= 0;
            commit_extra_boxes <= 0;
            for (extra_index = 0; extra_index < 3; extra_index = extra_index + 1) begin
                pending_extra_first[extra_index] <= 0;
                pending_extra_second[extra_index] <= 0;
                pending_extra_label[extra_index] <= 0;
            end
            commit_pending <= 0;
            pending_status <= 32'h00000f01;
            pending_pass <= 0;
            pending_reject <= 0;
            panel_status <= 32'h00000f01;
            pass_count <= 0;
            reject_count <= 0;
            next_target_pending <= 0;
            inference_start_cycle <= 0;
            commit_first <= 0;
            commit_second <= 0;
            commit_label <= 0;
            commit_status <= 32'h00000f01;
            commit_pass <= 0;
            commit_reject <= 0;
            sample_address <= 0;
            benchmark_done <= 0;
            overlay_valid <= 0;
            box_left <= 0;
            box_top <= 0;
            box_right <= 0;
            box_bottom <= 0;
            label <= 0;
            completed_count <= 0;
            application_mode <= 0;
            key_events <= 0;
            coremark_state <= 0;
            coremark_per_mhz <= 0;
            coremark_score <= 0;
            coremark_crc <= 0;
        end else begin
            counter <= counter + 1'b1;
            if (counter == 32'hffffffff) counter_high <= counter_high + 1'b1;
            restart <= 0;
            inference_ack <= 0;
            if (inference_start) inference_start_cycle <= counter;
            if (bus_read && bus_address == 32'ha1000040) next_target_pending <= 0;
            if (rx_error || (rx_valid && !rx_push)) rx_errors <= rx_errors + 1'b1;
            if (rx_pop) rx_read_pointer <= rx_read_pointer + 1'b1;
            if (rx_push) begin
                rx_fifo[rx_write_pointer] <= rx_data;
                rx_write_pointer <= rx_write_pointer + 1'b1;
            end
            case ({rx_push, rx_pop})
                2'b10: rx_count <= rx_count + 1'b1;
                2'b01: rx_count <= rx_count - 1'b1;
                default: begin end
            endcase
            if (full_write) begin
                case (bus_address)
                    32'ha1000010: result_address <= bus_wdata[8:0];
                    32'ha1000018: begin
                        control <= bus_wdata[2:0];
                        restart <= bus_wdata[3];
                        inference_ack <= bus_wdata[4];
                        if (bus_wdata[4] && inference_done) completed_count <= completed_count + 1'b1;
                    end
                    32'ha1000020: pending_first <= bus_wdata;
                    32'ha1000024: pending_second <= bus_wdata;
                    32'ha1000028: pending_label <= bus_wdata;
                    32'ha100002c: begin
                        commit_first <= pending_first;
                        commit_second <= pending_second;
                        commit_label <= pending_label;
                        commit_status <= pending_status;
                        commit_pass <= pending_pass;
                        commit_reject <= pending_reject;
                        for (extra_index = 0; extra_index < 3; extra_index = extra_index + 1)
                            commit_extra_boxes[extra_index * 39 +: 39] <= {pending_extra_label[extra_index][31], pending_extra_first[extra_index][9:0], pending_extra_first[extra_index][24:16], pending_extra_second[extra_index][9:0], pending_extra_second[extra_index][24:16]};
                        commit_pending <= 1;
                    end
                    32'ha1000034: pending_status <= bus_wdata;
                    32'ha1000038: pending_pass <= bus_wdata;
                    32'ha100003c: pending_reject <= bus_wdata;
                    32'ha1000050: benchmark_done <= bus_wdata;
                    32'ha100006c: coremark_state <= bus_wdata[2:0];
                    32'ha1000070: coremark_per_mhz <= bus_wdata;
                    32'ha1000074: coremark_score <= bus_wdata;
                    32'ha1000078: coremark_crc <= bus_wdata[3:0];
                    32'ha1000054: sample_address <= bus_wdata[13:0];
                    32'ha100005c: begin
                        application_mode <= bus_wdata[1:0];
                        pending_first <= 0;
                        pending_second <= 0;
                        pending_label <= 0;
                        commit_pending <= 0;
                        overlay_valid <= 0;
                        extra_boxes <= 0;
                        commit_extra_boxes <= 0;
                        for (extra_index = 0; extra_index < 3; extra_index = extra_index + 1)
                            pending_extra_label[extra_index] <= 0;
                    end
                    32'ha10000a0: pending_extra_first[0] <= bus_wdata;
                    32'ha10000a4: pending_extra_second[0] <= bus_wdata;
                    32'ha10000a8: pending_extra_label[0] <= bus_wdata;
                    32'ha10000b0: pending_extra_first[1] <= bus_wdata;
                    32'ha10000b4: pending_extra_second[1] <= bus_wdata;
                    32'ha10000b8: pending_extra_label[1] <= bus_wdata;
                    32'ha10000c0: pending_extra_first[2] <= bus_wdata;
                    32'ha10000c4: pending_extra_second[2] <= bus_wdata;
                    32'ha10000c8: pending_extra_label[2] <= bus_wdata;
                    default: begin end
                endcase
            end
            if (commit_pending && frame_tick && !model_write) begin
                box_left <= commit_first[9:0];
                box_top <= commit_first[24:16];
                box_right <= commit_second[9:0];
                box_bottom <= commit_second[24:16];
                label <= commit_label[5:0];
                panel_status <= commit_status;
                pass_count <= commit_pass;
                reject_count <= commit_reject;
                overlay_valid <= commit_label[31];
                extra_boxes <= commit_extra_boxes;
                commit_pending <= full_write && bus_address == 32'ha100002c;
            end
            key_events <= (key_events & ~(full_write && bus_address == 32'ha1000060 ? bus_wdata[2:0] : 3'd0)) | press_pulse[2:0];
            if (press_pulse[3]) next_target_pending <= 1;
            if (!configured || failed || restart || !control[0] || show_bars || model_write) overlay_valid <= 0;
        end
    end
endmodule
