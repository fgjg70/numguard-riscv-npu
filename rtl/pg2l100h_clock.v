`timescale 1ns/1ps

module pg2l100h_clock (
    input wire clk_50m,
    input wire reset,
    output wire clk_27m,
    output wire locked
);
    GTP_GPLL #(
        .CLKIN_FREQ(50.0),
        .LOCK_MODE(0),
        .STATIC_RATIOI(2),
        .STATIC_RATIOM(1),
        .STATIC_RATIOF(27.0),
        .STATIC_RATIO0(25.0),
        .STATIC_DUTYF(27),
        .STATIC_DUTY0(25),
        .STATIC_RATIO1(24), .STATIC_DUTY1(24),
        .STATIC_RATIO2(24), .STATIC_DUTY2(24),
        .STATIC_RATIO3(24), .STATIC_DUTY3(24),
        .STATIC_RATIO4(24), .STATIC_DUTY4(24),
        .STATIC_RATIO5(24), .STATIC_DUTY5(24),
        .STATIC_RATIO6(24), .STATIC_DUTY6(24),
        .INTERNAL_FB("CLKOUTF"),
        .EXTERNAL_FB("DISABLE"),
        .BANDWIDTH("OPTIMIZED")
    ) pll (
        .CLKIN1(clk_50m), .CLKIN2(1'b0), .CLKIN_SEL(1'b0),
        .CLKFB(1'b0), .RST(reset), .PLL_PWD(1'b0),
        .CLKOUT0(clk_27m), .LOCK(locked),
        .DPS_CLK(1'b0), .DPS_EN(1'b0), .DPS_DIR(1'b0),
        .CLKOUT0_SYN(1'b0), .CLKOUT1_SYN(1'b0), .CLKOUT2_SYN(1'b0),
        .CLKOUT3_SYN(1'b0), .CLKOUT4_SYN(1'b0), .CLKOUT5_SYN(1'b0),
        .CLKOUT6_SYN(1'b0), .CLKOUTF_SYN(1'b0),
        .APB_CLK(1'b0), .APB_RST_N(1'b0), .APB_ADDR(5'b0),
        .APB_SEL(1'b0), .APB_EN(1'b0), .APB_WRITE(1'b0), .APB_WDATA(16'b0)
    );
endmodule
