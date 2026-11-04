`timescale 1ns/1ps

module vision_cpu_clock (
    input wire clk_50m,
    input wire reset,
    output wire clk_125m,
    output wire locked
);
    GTP_GPLL #(
        .CLKIN_FREQ(50.0), .LOCK_MODE(0),
        .STATIC_RATIOI(2), .STATIC_RATIOM(1), .STATIC_RATIOF(38.0),
        .STATIC_RATIO0(10.0), .STATIC_DUTYF(38), .STATIC_DUTY0(10),
        .STATIC_RATIO1(8), .STATIC_DUTY1(8),
        .STATIC_RATIO2(8), .STATIC_DUTY2(8),
        .STATIC_RATIO3(8), .STATIC_DUTY3(8),
        .STATIC_RATIO4(8), .STATIC_DUTY4(8),
        .STATIC_RATIO5(8), .STATIC_DUTY5(8),
        .STATIC_RATIO6(8), .STATIC_DUTY6(8),
        .INTERNAL_FB("CLKOUTF"), .EXTERNAL_FB("DISABLE"), .BANDWIDTH("OPTIMIZED")
    ) pll (
        .CLKIN1(clk_50m), .CLKIN2(1'b0), .CLKIN_SEL(1'b0),
        .CLKFB(1'b0), .RST(reset), .PLL_PWD(1'b0),
        .CLKOUT0(clk_125m), .LOCK(locked),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0),
        .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .APB_CLK(1'b0), .APB_RST_N(1'b0), .APB_ADDR(5'b0),
        .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0), .APB_WDATA(16'b0)
    );
endmodule
