`timescale 1ns/1ps

module vision_video_bridge #(
    parameter integer PAYLOAD_BITS = 144
) (
    input wire system_clk,
    input wire system_reset,
    input wire [PAYLOAD_BITS-1:0] source_payload,
    input wire video_clk,
    input wire video_reset,
    input wire video_frame_tick,
    output reg [PAYLOAD_BITS-1:0] display_payload,
    output wire system_frame_tick
);
    reg [PAYLOAD_BITS-1:0] held_payload;
    reg [PAYLOAD_BITS-1:0] pending_payload;
    reg initialized;
    reg request_toggle;
    reg acknowledge_toggle;
    reg acknowledge_meta;
    reg acknowledge_sync;
    reg request_meta;
    reg request_sync;
    reg pending;
    reg frame_toggle;
    reg frame_meta;
    reg frame_sync;
    reg frame_previous;

    assign system_frame_tick = frame_sync ^ frame_previous;

    always @(posedge system_clk) begin
        if (system_reset) begin
            held_payload <= 0;
            initialized <= 0;
            request_toggle <= 0;
            acknowledge_meta <= 0;
            acknowledge_sync <= 0;
            frame_meta <= 0;
            frame_sync <= 0;
            frame_previous <= 0;
        end else begin
            acknowledge_meta <= acknowledge_toggle;
            acknowledge_sync <= acknowledge_meta;
            frame_meta <= frame_toggle;
            frame_sync <= frame_meta;
            frame_previous <= frame_sync;
            if (request_toggle == acknowledge_sync && (!initialized || source_payload != held_payload)) begin
                held_payload <= source_payload;
                initialized <= 1;
                request_toggle <= !request_toggle;
            end
        end
    end

    always @(posedge video_clk) begin
        if (video_reset) begin
            request_meta <= 0;
            request_sync <= 0;
            acknowledge_toggle <= 0;
            pending_payload <= 0;
            pending <= 0;
            display_payload <= 0;
            frame_toggle <= 0;
        end else begin
            request_meta <= request_toggle;
            request_sync <= request_meta;
            if (video_frame_tick) begin
                frame_toggle <= !frame_toggle;
                if (pending) begin
                    display_payload <= pending_payload;
                    pending <= 0;
                end
            end
            if (request_sync != acknowledge_toggle) begin
                pending_payload <= held_payload;
                pending <= 1;
                acknowledge_toggle <= request_sync;
            end
        end
    end
endmodule
