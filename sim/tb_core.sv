`timescale 1ns/1ps

// ============================================================================
// tb_core.sv — 五级流水线系统级自校验测试平台
// ----------------------------------------------------------------------------
// 机制:
//   - 用 IMEM_INIT 参数把汇编器产出的 hex 装入指令存储器 (相对路径基于
//     运行目录, run_core.bat 会先 cd 到 sim/);
//   - 监视 DMEM 的 tohost 地址 (DMEM_BASE + 0x1000):
//       写 1       -> 全部测试通过, 结束仿真 PASS
//       写 N+1     -> 测试 N 失败, 结束仿真 FAIL
//       0 (初值)   -> 尚未写入, 继续运行
//   - 监视 core 的 dbg_illegal 粘滞标志: 任何非法指令都判失败;
//   - 超时保护: 超过 max_cycles 未结束则报超时失败 (附当前 PC/指令)。
//     周期上限由 +max_cycles=<N> 覆盖 (默认 20000); 回归矩阵给的是 200000。
// 运行: sim\run_core.bat   (先跑 tools/asm.py 生成 hex)
// ============================================================================

module tb_core;
  import rv32_pkg::*;

  localparam time TCLK_HALF  = 5ns;      // 100 MHz 等效
  // 【W-11】超时周期数必须可由 +max_cycles=<N> 覆盖: 回归矩阵给 tb_core 传的是
  // 200000, 而这里原先是写死的 20000 —— 矩阵的超时配置是**死配置**, 五个
  // tb_core* 项实际都在 20000 拍超时。写更长的新程序会得到假的 "timeout"。
  // (写法照抄 sim/tb_sw.sv:104 的 $value$plusargs。)
  int unsigned max_cycles = 20000;

  // tohost 约定地址 = DMEM_BASE + 0x2000 (见 sw/link.ld)
  localparam logic [31:0] TOHOST_ADDR     = DMEM_BASE + 32'h2000;
  localparam int unsigned TOHOST_WORD_IDX = (TOHOST_ADDR - DMEM_BASE) / 4;

  logic clk = 1'b0;
  logic rst = 1'b1;
  always #(TCLK_HALF) clk = ~clk;

  int unsigned cycles   = 0;
  bit          finished = 1'b0;

  logic [31:0] dbg_pc, dbg_instr, dbg_rdata;
  logic        dbg_illegal;

  logic [31:0] dbg_id_pc;
  logic [31:0] dbg_mem_addr, dbg_mem_wdata, ext_rdata;
  logic        dbg_mem_we, ext_we, ext_re;
  logic [31:0] ext_addr, ext_wdata;
  logic [3:0]  ext_wstrb;

  core u_dut (
    .clk            (clk),
    .rst            (rst),
    .dbg_pc         (dbg_pc),
    .dbg_id_pc      (dbg_id_pc),
    .dbg_instr      (dbg_instr),
    .dbg_illegal    (dbg_illegal),
    .dbg_raddr      (5'd0),
    .dbg_rdata      (dbg_rdata),
    .dbg_mem_we     (dbg_mem_we),
    .dbg_mem_addr   (dbg_mem_addr),
    .dbg_mem_wdata  (dbg_mem_wdata),
    // 本测试平台不接外设: MMIO 读恒 0 (程序也不访问 MMIO 区)
    .ext_we         (ext_we),
    .ext_re         (ext_re),
    .ext_addr       (ext_addr),
    .ext_wdata      (ext_wdata),
    .ext_wstrb      (ext_wstrb),
    .ext_rdata      (ext_rdata),
    // 本平台不接外设: 定时器中断恒低 (P2 的定时器测试在 tb_soc/tb_sw 里做)
    .irq_timer      (1'b0)
  );

  assign ext_rdata = 32'h0;

  // tohost 观察 (直接看 DMEM 内的字; 程序不做其它解释)
  wire [31:0] tohost_word = u_dut.u_dmem.mem[TOHOST_WORD_IDX];

  // ==========================================================================
  // 陷阱诊断探针 (加 +illegal_trace 才启用, 平时零开销)
  // --------------------------------------------------------------------------
  // 为什么需要它: dbg_illegal 是**粘滞**标志 (只置位不清零), 所以它一旦置位就无法
  // 从值本身判断"是哪一拍、哪条指令"触发的。之前只靠 PC/instr 猜, 猜错过两次
  // (报 0x8000005c, 又报 0x80000058, 真正的非法指令在 0x80000054)。
  // 这里直接打印锁存那一拍的完整状态, 一次看清。
  // ==========================================================================
  `ifndef SYNTHESIS
  // 逐拍 pre-edge 轨迹: 在 negedge 采样, 避开 tb 的 always_ff, 这样即使 $fatal
  // 终止仿真, 最后几拍的状态也已经打印出来了。
  always @(negedge clk) begin
    if (!rst && $test$plusargs("illegal_trace") && cycles >= 40) begin
      $display("[pre] cyc=%0d if_pc=0x%08x if_instr=0x%08x | ex_pc=0x%08x ex_illegal=%b",
               cycles, u_dut.if_pc, u_dut.if_instr, u_dut.id_ex_pc, u_dut.id_ex_ctrl.illegal);
      $display("      id_illegal=%b exc_req=%b trap_req=%b trap_enter=%b trap_redir=%b irq_take=%b irq_pend=%b",
               u_dut.id_ctrl.illegal, u_dut.exc_req, u_dut.trap_req, u_dut.trap_enter,
               u_dut.trap_redir, u_dut.irq_take, u_dut.irq_pending);
      $display("      pipe_stall=%b redirect=%b redirect_raw=%b actual_taken=%b pred_ok=%b ex_taken=%b pc_src=%b",
               u_dut.pipe_stall, u_dut.redirect, u_dut.redirect_raw, u_dut.actual_taken,
               u_dut.pred_ok, u_dut.ex_taken, u_dut.pc_src);
      $display("      dmem_we=%b mem_read=%b mem_write=%b reg_write=%b pc_sel=%0d dbg_illegal=%b",
               u_dut.dmem_we, u_dut.id_ex_ctrl.mem_read, u_dut.id_ex_ctrl.mem_write,
               u_dut.id_ex_ctrl.reg_write, u_dut.id_ex_ctrl.pc_sel, dbg_illegal);
    end
  end
  `endif

  `ifndef SYNTHESIS
  initial begin
    bit trace_on;
    trace_on = $test$plusargs("illegal_trace");
    if (trace_on) begin
      $display("[trace] cycle | if_pc      if_instr    (ID) | ex_pc      | ill stll bubl trap_enter redirect exc_req");
      forever begin
        @(posedge clk);
        if (!rst) begin
          $display("[trace] %5d | 0x%08x 0x%08x | 0x%08x |  %b   %b    %b      %b        %b      %b",
                   cycles,
                   u_dut.if_pc, u_dut.if_instr,
                   u_dut.id_ex_pc,
                   u_dut.id_ctrl.illegal, u_dut.pipe_stall, u_dut.id_ex_bubble,
                   u_dut.trap_enter, u_dut.redirect, u_dut.exc_req);
        end
      end
    end
  end
  `endif

  initial begin
    begin
      string dump_file;
      // +dump=wave.vcd 可转储波形 (用 GTKWave 查看), 不加则不产生文件
      if ($value$plusargs("dump=%s", dump_file)) begin
        $dumpfile(dump_file);
        $dumpvars(0, tb_core);
      end
    end

    if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 20000;
    repeat (4) @(posedge clk);
    rst = 1'b0;
    $display("[tb_core] 复位释放, PC=0x%08x, max_cycles=%0d", PC_RESET, max_cycles);
  end

  always_ff @(posedge clk) begin
    if (!rst && !finished) begin
      cycles <= cycles + 1;

      if (cycles > max_cycles) begin
        finished = 1'b1;
        $display("[tb_core] FAIL: 超时 (%0d 周期), 最后 PC=0x%08x instr=0x%08x",
                 max_cycles, dbg_pc, dbg_instr);
        $fatal(1);
      end

      if (dbg_illegal) begin
        finished = 1'b1;
        // 必须用 dbg_id_pc 而不是 dbg_pc: dbg_illegal 是在 ID 段锁存的, 而 dbg_pc
        // 是 IF 段"正在取"的地址, 比那条非法指令早 2 条 (8 字节)。用 dbg_pc 会把
        // 诊断指向无关的地方 (实测: 报了 PC=0x8000005c, 真正的非法指令在 0x80000054)。
        $display("[tb_core] FAIL: 遇到非法指令 (id_pc=0x%08x instr=0x%08x)",
                 dbg_id_pc, dbg_instr);
        $fatal(1);
      end

      if (tohost_word == 32'd1) begin
        finished = 1'b1;
        $display("=================================================");
        $display("  全部 6 组测试通过 (算术/冒险/访存/非对齐/分支/移位)");
        $display("  执行周期: %0d", cycles);
        $display("  RESULT : PASS");
        $finish;
      end
      else if (tohost_word != 32'd0) begin
        finished = 1'b1;
        $display("[tb_core] FAIL: 测试 %0d 失败 (tohost=%0d)",
                 tohost_word - 1, tohost_word);
        $display("[tb_core] 退出时 PC=0x%08x, 周期数 %0d", dbg_pc, cycles);
        $fatal(1);
      end
    end
  end

endmodule
