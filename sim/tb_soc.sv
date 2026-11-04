`timescale 1ns/1ps

// ============================================================================
// tb_soc.sv — SoC 级测试平台 (core + mmio_subsys)
// ----------------------------------------------------------------------------
// 与 tb_core 的区别: 这里接上外设, 并把 UART 的 TX 环回到 RX ——
// 一次仿真同时验证发送与接收两条通路, 不需要外接串口设备。
//
// 检查项:
//   1) tohost 协议: 1 = PASS, N+1 = 第 N 组失败
//   2) gpio_out 引脚实际值 == 0x5A5A5A5A
//   3) UART 环回收到的字节序列 == "OK\n"
//   4) dbg_illegal 不得置位
//
// 注意: 收尾判定放在独立 initial 块 (always_ff 里不允许 wait / 延时)。
// ============================================================================

module tb_soc;
  import rv32_pkg::*;

  localparam time TCLK_HALF  = 10ns;      // 50 MHz
  // 【W-11】超时周期数由 +max_cycles=<N> 覆盖 (矩阵给 tb_soc 传 400000)。
  // 原先这里完全没有 plusarg 解析, 矩阵的超时配置被静默忽略。
  int unsigned max_cycles = 400000;
  localparam time BIT_TIME   = 8680ns;    // 1/115200 s

  // tohost 约定地址 = DMEM_BASE + 0x2000 (见 sw/link.ld)
  localparam logic [31:0] TOHOST_ADDR     = DMEM_BASE + 32'h2000;
  localparam int unsigned TOHOST_WORD_IDX = (TOHOST_ADDR - DMEM_BASE) / 4;

  logic clk = 1'b0;
  logic rst = 1'b1;
  always #(TCLK_HALF) clk = ~clk;

  int unsigned cycles = 0;
  bit finished  = 1'b0;
  bit pass_seen = 1'b0;

  logic [31:0] dbg_pc, dbg_id_pc, dbg_instr, dbg_rdata, dbg_mem_addr, dbg_mem_wdata;
  logic        dbg_illegal, dbg_mem_we;
  logic        ext_we, ext_re, uart_txd, uart_rxd;
  logic [31:0] ext_addr, ext_wdata, ext_rdata;
  logic [3:0]  ext_wstrb;
  logic [31:0] gpio_out, tx_bytes, rx_bytes;
  // 声明必须在例化之前: 具名端口连接里的未声明标识符会被隐式声明成 net,
  // 之后显式声明就成了重复声明 (vlog-2388)。
  logic        irq_timer;

  core u_dut (
    .clk (clk), .rst (rst),
    .dbg_pc (dbg_pc), .dbg_id_pc (dbg_id_pc), .dbg_instr (dbg_instr), .dbg_illegal (dbg_illegal),
    .dbg_raddr (5'd0), .dbg_rdata (dbg_rdata),
    .dbg_mem_we (dbg_mem_we), .dbg_mem_addr (dbg_mem_addr), .dbg_mem_wdata (dbg_mem_wdata),
    .ext_we (ext_we), .ext_re (ext_re), .ext_addr (ext_addr), .ext_wdata (ext_wdata),
    .ext_wstrb (ext_wstrb), .ext_rdata (ext_rdata),
    .irq_timer (irq_timer)
  );

  mmio_subsys #(.CLK_HZ(50_000_000), .BAUD_HZ(115_200)) u_mmio (
    .clk (clk), .rst (rst),
    .we (ext_we), .re (ext_re), .addr (ext_addr), .wdata (ext_wdata), .wstrb (ext_wstrb),
    .rdata (ext_rdata),
    .gpio_out (gpio_out), .gpio_in (32'hDEADBEEF),
    .uart_txd (uart_txd), .uart_rxd (uart_rxd),
    .tx_byte_count (tx_bytes), .rx_byte_count (rx_bytes),
    .irq_timer (irq_timer)
  );

  assign uart_rxd = uart_txd;      // 环回: 发送直接进接收

  // ==========================================================================
  // 定时器中断诊断探针 (加 +irq_trace 启用, 平时零开销)
  // --------------------------------------------------------------------------
  // 排查"中断永远不来"这类问题, 光看信号不够 —— 必须能分辨是
  //   (a) mtime 没走到 mtimecmp, 还是
  //   (b) mstatus.MIE / mie.MTIE 没使能, 还是
  //   (c) 使能了但 irq_pending 没抬起来
  // 所以一次性把 (mtime, mtimecmp, MTIE, MIE, irq_timer, irq_pending) 打出来。
  // ==========================================================================
  // ==========================================================================
  // 诊断探针 (都用 plusarg 门控, 平时零开销、不影响任何测试结果)
  //   +irq_trace       定时器/中断状态 (mtime, mtimecmp, MTIE, MIE, irq_pending)
  //   +trap_csr_trace  陷阱 CSR 状态 (mcause/mepc/mtvec/mstatus + trap_req/cause)
  //   +pc_trace        每拍 PC/指令 + s3/s4 寄存器值
  // 教训: 排查"中断不来"这类问题, 光看一个信号不够 —— 必须能分清
  //   (a) mtime 没走到 mtimecmp  (b) MIE/MTIE 没使能  (c) 请求抬起了但没被接受
  //   (d) 接受了但 mcause 读回来不对
  // 本轮就是靠这一组探针定位到 R6 (mcause 位布局) 与 R7 (csrsi 5 位立即数)。
  // ==========================================================================
  `ifndef SYNTHESIS
  initial begin
    if ($test$plusargs("pc_trace")) begin
      $display("[pctrace] cycle -> dbg_id_pc / instr  (只在值变化时打印)");
      forever begin
        @(negedge clk);
        if (!rst) begin
          $display("[pctrace] %6d 0x%08x 0x%08x  s3=%0d s4=%0d",
                   cycles, dbg_id_pc, dbg_instr,
                   u_dut.u_regfile.mem[18], u_dut.u_regfile.mem[19]);
        end
      end
    end
  end
  `endif

  `ifndef SYNTHESIS
  initial begin
    if ($test$plusargs("trap_csr_trace")) begin
      $display("[csrtrace] cycle | mcause      mepc        mtvec       mstatus | trap_req trap_cause irq_cause");
      forever begin
        @(negedge clk);
        if (!rst) begin
          $display("[csrtrace] %6d | 0x%08x 0x%08x 0x%08x 0x%08x |    %b     0x%02x     0x%02x",
                   cycles, u_dut.u_csr.mcause, u_dut.u_csr.mepc, u_dut.u_csr.mtvec,
                   {19'b0, u_dut.u_csr.st_mpp, 3'b0, u_dut.u_csr.st_mpie, 3'b0,
                    u_dut.u_csr.st_mie, 3'b0},
                   u_dut.trap_req, u_dut.trap_cause, u_dut.irq_cause);
        end
      end
    end
  end
  `endif

  `ifndef SYNTHESIS
  initial begin
    if ($test$plusargs("irq_trace")) begin
      $display("[irqtrace]  cycle |     mtime      mtimecmp | MTIE MIE gt | irq_timer irq_pending");
      forever begin
        @(negedge clk);
        if (!rst) begin
          $display("[irqtrace] %6d | %10d %12d |  %b   %b   %b |     %b          %b",
                   cycles,
                   u_mmio.cycle_cnt[31:0], u_mmio.mtimecmp[31:0],
                   u_dut.u_csr.ie_mtie, u_dut.u_csr.st_mie,
                   (u_mmio.cycle_cnt >= u_mmio.mtimecmp),
                   irq_timer, u_dut.irq_pending);
          // 陷阱/中断裁决级观测: 分清"请求没到"与"到了没被接受"
          if (u_dut.irq_pending || u_dut.trap_enter || u_dut.irq_take)
            $display("           >> irq_take=%b trap_req=%b trap_enter=%b exc_req=%b stall=%b mret=%b | s2(x18)=%0d s3(x19)=%0d",
                     u_dut.irq_take, u_dut.trap_req, u_dut.trap_enter, u_dut.exc_req,
                     u_dut.pipe_stall, u_dut.mret_taken,
                     u_dut.u_regfile.mem[18], u_dut.u_regfile.mem[19]);
        end
      end
    end
  end
  `endif

  wire [31:0] tohost_word = u_dut.u_dmem.mem[TOHOST_WORD_IDX];

  // UART 读回记录 (程序写在这里, 由本 tb 做持久比对)
  localparam int unsigned RB_IDX = (32'h90001010 - DMEM_BASE) / 4;
  wire [31:0] rb0 = u_dut.u_dmem.mem[RB_IDX + 0];
  wire [31:0] rb1 = u_dut.u_dmem.mem[RB_IDX + 1];
  wire [31:0] rb2 = u_dut.u_dmem.mem[RB_IDX + 2];

  // ---- UART 监控: 解码 uart_txd 上的字节 ----
  logic [7:0] rx_sh;
  int         uart_recv = 0;
  bit         uart_ok   = 1'b1;

  initial begin : uart_monitor
    forever begin
      @(negedge uart_txd);
      #(BIT_TIME/2);
      if (uart_txd === 1'b0) begin
        for (int i = 0; i < 8; i++) begin
          #(BIT_TIME);
          rx_sh[i] = uart_txd;
        end
        #(BIT_TIME);                    // 停止位
        uart_recv++;
        $display("[tb_soc] UART byte #%0d = 0x%02x", uart_recv, rx_sh);
        if (uart_recv == 1 && rx_sh !== 8'h4F) uart_ok = 1'b0;   // 'O'
        if (uart_recv == 2 && rx_sh !== 8'h4B) uart_ok = 1'b0;   // 'K'
        if (uart_recv == 3 && rx_sh !== 8'h0A) uart_ok = 1'b0;   // LF
      end
    end
  end

  initial begin
    if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 400000;
    repeat (4) @(posedge clk);
    rst = 1'b0;
    $display("[tb_soc] reset released, PC=0x%08x, max_cycles=%0d", PC_RESET, max_cycles);
  end

  // ---- 主循环: 只看 tohost / 非法指令 / 超时, 不做延时 ----
  always_ff @(posedge clk) begin
    if (!rst && !finished) begin
      cycles <= cycles + 1;

      if (cycles > max_cycles) begin
        finished = 1'b1;
        $display("[tb_soc] FAIL: timeout (%0d cycles), PC=0x%08x instr=0x%08x",
                 max_cycles, dbg_pc, dbg_instr);
        $fatal(1);
      end

      if (dbg_illegal) begin
        finished = 1'b1;
        $display("[tb_soc] FAIL: illegal instruction id_pc=0x%08x instr=0x%08x (fetch_pc=0x%08x)",
                 dbg_id_pc, dbg_instr, dbg_pc);
        $fatal(1);
      end

      if (tohost_word == 32'd1) begin
        finished  = 1'b1;
        pass_seen = 1'b1;               // 收尾交给 finish_check
      end
      else if (tohost_word != 32'd0) begin
        finished = 1'b1;
        $display("[tb_soc] FAIL: test group %0d failed (tohost=%0d), PC=0x%08x",
                 tohost_word - 1, tohost_word, dbg_pc);
        $fatal(1);
      end
    end
  end

  // ---- 内部 RX 探针: 直接看 mmio_subsys 到底解出了什么 ----
  always @(posedge clk) begin
    if (!rst && u_mmio.rx_done)
      $display("[tb_soc] internal RX completed byte 0x%02x at %0t", u_mmio.rx_sh, $time);
  end

  // ---- MMIO 读探针: 监视 CPU 读 MMIO 时实际拿到的数据 ----
  always @(posedge clk) begin
    if (!rst && u_mmio.sel_uart_rx)
      $display("[tb_soc] CPU reads MMIO_UART_RX -> rdata=0x%08x (rx_byte=0x%02x rdy=%b)",
               ext_rdata, u_mmio.rx_byte, u_mmio.rx_rdy);
  end

  // ---- 时间级看门狗 ----
  // 周期计数看门狗在 finished 置位后就停止计数了, 挡不住"卡在同一个仿真时刻
  // 无限等待"(本例就踩过: 程序只发出 1 个字节, wait(uart_recv>=3) 永不满足)。
  // 所以必须有独立于周期计数的时间看门狗。
  initial begin : watchdog
    #(50ms);
    $display("[tb_soc] FAIL: global timeout at %0t, PC=0x%08x tohost=0x%08x uart_recv=%0d",
             $time, dbg_pc, tohost_word, uart_recv);
    $fatal(1);
  end

  // ---- 收尾判定 ----
  initial begin : finish_check
    wait (pass_seen);
    wait (uart_recv >= 3);
    #(BIT_TIME * 2);
    $display("=================================================");
    $display("  gpio_out      = 0x%08x  (expect 0x5a5a5a5a)", gpio_out);
    $display("  UART readback = 0x%02x 0x%02x 0x%02x  (expect 4f 4b 0a)",
             rb0[7:0], rb1[7:0], rb2[7:0]);
    $display("  UART loopback = %0d bytes, sequence ok = %0d", uart_recv, uart_ok);
    $display("  TX/RX counters= %0d / %0d", tx_bytes, rx_bytes);
    $display("  cycles        = %0d", cycles);
    if (gpio_out !== 32'h5A5A5A5A) begin
      $display("  RESULT : FAIL (gpio_out mismatch)");
      $fatal(1);
    end
    if (!uart_ok || uart_recv < 3) begin
      $display("  RESULT : FAIL (UART wire loopback mismatch)");
      $fatal(1);
    end
    if (rb0[7:0] !== 8'h4F || rb1[7:0] !== 8'h4B || rb2[7:0] !== 8'h0A) begin
      $display("  RESULT : FAIL (CPU-side UART readback mismatch)");
      $fatal(1);
    end
    $display("  RESULT : PASS");
    $finish;
  end

endmodule
