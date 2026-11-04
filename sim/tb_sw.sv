`timescale 1ns/1ps

// ============================================================================
// tb_sw.sv — 通用裸机程序测试台 (core + mmio_subsys)
// ----------------------------------------------------------------------------
// 用途: 跑任意 C/汇编程序, 程序结束(写 tohost=1)后把 dmem 里的日志缓冲区
//       当字符串 dump 出来。这样即使没有串口也能拿到程序输出。
//
// 约定 (见 sw/link.ld):
//   tohost    = 0x90002000  写 1 = 正常结束
//   log 缓冲区 = 0x90003000 .. 0x90003FFF  NUL 结尾
//
// plusarg:
//   +imem=<file>        程序 hex
//   +max_cycles=<N>     超时周期数 (默认 2_000_000; CoreMark 需要调大)
//   +dump=<file>        VCD 波形 (可选)
//   ---- 语义断言 (都不给 = 只观测, 不断言) ----
//   +lu_bubbles=B       微基准里"距离1 (消费者紧接 load)"每险恰好 B 拍
//                       (旧语义 1; C-2 起 2)。见 dump_log 断言块。
//   +expect_hazards=N   微基准每阶段的冒险数 (lu_bench.S = 8*128 = 1024)
//   +expect_d2_cost=N   微基准"距离2 (中间隔一条)"的总代价 (旧语义 0; C-2 = 1024)
//   +expect_lu_stall=N  断言全局 load-use 停顿拍数 == N
//   +expect_lu_events=N 断言 load-use 冒险事件数 == N
//   +expect_flush=N     断言 if_id_flush 拍数 == N
//   +expect_retire=N    断言真退休指令数 == N
//   +dump_words=N       把日志区前 N 个 32 位字按 hex+十进制打出来 (微基准自量的数)
//
// 【统计口径 (2026-10-11 修正, 两处)】
//   1) retire 分母: 旧版数 `mem_wb_ctrl != '0` 的**每一拍**, 而 hold_mem
//      (= lsu_stall | ex_busy) 会冻结 MEM/WB、同一条指令被重复计数 ->
//      CoreMark 基线自印 CPI=1.10 虚低。现排除冻结拍: 真退休 402720
//      (hold_mem 14415 拍里有 71 拍 WB 段是气泡), 与 fetch 402723 差 3 拍,
//      真 CPI = 1.1409 / IPC = 0.8765。
//   2) 重定向代价: 旧版打印 `n_redirect * 2` (把假设当实测)。现直接数 if_id_flush
//      拍数 n_flush。这次测量**推翻过一次假设、又验证过一次假设**, 两个数都要留:
//        · 修正当时 (C-11 之前, redirect 是单拍组合信号): 11530 拍/11530 次 = **1.00 拍/次**
//          -> 旧式按 2 拍打印, 虚报 23060 拍 = 全程 5.0%。这就是当初必须改成实测的原因。
//        · 现在 (C-11 的 2 拍冲刷窗口 `redir_flush = br_redir_eff || br_redir_r`,
//          core.sv:913): 8020 次 -> 16040 拍 = **2.00 拍/次**。
//      【归属别挂错 (rtl-pred 2026-10-11 实测澄清)】2.00 拍/次 是 **C-11** 带来的,
//      与 C-1 预测器无关: 同一份 core 只把预测器换回旧 64 项组合查表, 仍是
//      11530 次 x 2.00 拍 = 23060; 换成 C-1 后是 8020 次 x 2.00 拍 = 16040。
//      C-1 只改**次数**(11530->8020); C-11 才改**每次几拍**(1->2)。所以这条计数
//      在两次改动之间的含义会变 —— 引用它时必须写清是哪一版 RTL、以及 n_redirect。
//   另加 MMIO_MARK->MMIO_RESULT 测量窗口计数 (窗口内 cycles/fetch/stalls/事件)。
//   【别把"窗口 load-use 停顿"当"改 2 拍的增量"】C-2 的 load_use_mem 还会把
//   距离 2 的消费者纳入停顿 (旧语义免费): 基线窗口现值 28578, 真增量 36784
//   (= 28578 + 8213 个新增距离2事件)。增量只能靠改前/改后两条日志相减。
// ============================================================================

module tb_sw;
  import rv32_pkg::*;

  localparam time TCLK_HALF = 10ns;      // 50 MHz

  // tohost 约定地址 = DMEM_BASE + 0x2000 (见 sw/link.ld)
  localparam logic [31:0] TOHOST_ADDR = DMEM_BASE + 32'h2000;
  localparam int unsigned TOHOST_IDX  = (TOHOST_ADDR - DMEM_BASE) / 4;
  localparam logic [31:0] LOG_ADDR    = 32'h90003000;
  localparam int unsigned LOG_IDX     = (LOG_ADDR - DMEM_BASE) / 4;
  localparam int unsigned LOG_MAX     = 4096;

  logic clk = 1'b0;
  logic rst = 1'b1;
  always #(TCLK_HALF) clk = ~clk;

  int unsigned cycles = 0;
  int unsigned max_cycles = 2000000;
  bit finished = 1'b0;
  bit pass_seen = 1'b0;

  logic [31:0] dbg_pc, dbg_id_pc, dbg_instr, dbg_rdata, dbg_mem_addr, dbg_mem_wdata;
  logic        dbg_illegal, dbg_mem_we;
  logic        ext_we, ext_re, uart_txd, uart_rxd;
  logic [31:0] ext_addr, ext_wdata, ext_rdata;
  logic [3:0]  ext_wstrb;
  logic [31:0] gpio_out, tx_bytes, rx_bytes;
  // 【声明必须在例化之前】具名端口连接 `.irq_timer (irq_timer)` 里若 irq_timer
  // 尚未声明, 仿真器会在此处**隐式声明**一条 net; 之后那句 `logic irq_timer;`
  // 就变成重复声明 -> vlog-2388 'already declared in this scope'。
  // (实测踩过: 把声明写在例化后面, tb_sw/tb_soc 全部编译失败)
  logic        irq_timer;

  core u_dut (
    .clk (clk), .rst (rst),
    .dbg_pc (dbg_pc), .dbg_id_pc (dbg_id_pc), .dbg_instr (dbg_instr),
    .dbg_illegal (dbg_illegal),
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
    .gpio_out (gpio_out), .gpio_in (32'h0),
    .uart_txd (uart_txd), .uart_rxd (uart_rxd),
    .tx_byte_count (tx_bytes), .rx_byte_count (rx_bytes),
    .irq_timer (irq_timer)
  );

  wire [31:0] tohost_word = u_dut.u_dmem.mem[TOHOST_IDX];

  // ---------------------------------------------------------------------------
  // 【测量窗口计数】MMIO_MARK(0xA0000020) -> MMIO_RESULT(0xA0000024)
  // 软件侧 start_time()/stop_time() 的 ticks 就是这个区间 (core_portme.c)。
  // 为什么必须有: CoreMark 的 "Total ticks = 413070" **只是测量区间**内的周期,
  // 而 TB 的全局 n_stall_lu 含启动/初始化。要精确预言 C-2 把 load-use 从
  // 1 拍改成 2 拍后 ticks 涨多少, 必须数"窗口内的 load-use 停顿拍数"。
  // (声明必须在这里: 下面 MMIO 监视器与启动 initial 都要用。)
  // ---------------------------------------------------------------------------
  logic        in_window = 1'b0;
  int unsigned n_win_cycles = 0;
  int unsigned n_win_fetch = 0;
  int unsigned n_win_stall = 0;
  int unsigned n_win_stall_lu = 0;
  int unsigned n_win_lu_events = 0;
  // load_use 与 lsu_stall/ex_busy **同拍**的拍数。这类拍已经被别的理由冻住了,
  // 多插的气泡**不增加周期** —— 所以"停顿拍数"与"多花的周期数"不是一回事。
  // 基线 (1 拍语义) 实测全局 n_stall == load-use+muldiv+split (差 0, overlap=0);
  // C-2 起 overlap = 990 (CoreMark), 所以窗口周期差 ≠ 窗口停顿差。要看真代价
  // 只能用"窗口 cycles 前后对比", 不能用停顿数直接推。
  int unsigned n_win_lu_overlap = 0;

  // ---------------------------------------------------------------------------
  // 【语义断言】默认全部关闭 (值为 0 = 不断言), 只由 plusarg 显式打开。
  // 这样 tb_sw 对 CoreMark / hello / nn_demo 仍是"只观测", 而微基准 lu_bench.S
  // 用 +lu_bubbles / +expect_hazards / +expect_d2_cost 把语义钉死。
  // ---------------------------------------------------------------------------
  int exp_lu_stall = 0;    // +expect_lu_stall=N : 断言 load-use 停顿拍数恰好 N
  int exp_lu_events = 0;   // +expect_lu_events=N: 断言 load-use 冒险事件数恰好 N
  int exp_flush = 0;       // +expect_flush=N    : 断言冲刷拍数恰好 N
  int exp_retire = 0;      // +expect_retire=N   : 断言真退休指令数恰好 N
  // +lu_bubbles=B : 微基准"距离1 (消费者紧接 load)"每险恰好 B 拍。**不是**
  // n_stall_lu == B*n_lu_events —— C-2 起 n_stall_lu 还含"距离2"那一类, 两者
  // 不再是同一个数 (实测 C-2: 3072 拍 vs 2048 事件)。见 dump_log 的断言块。
  int lu_bubbles = 0;
  // 微基准把"程序自己量的数"以原始 32 位字写在日志区开头 (0x90003000), 字符串
  // dump 会在第一个 0 字节停住; 这个开关让 TB 把前 N 个字按 hex+十进制打出来。
  int dump_words = 0;      // +dump_words=N      : 打印日志区前 N 个 32 位字
  // 微基准 lu_bench.S 的代价断言参数 (见 dump_log 里的断言块):
  //   +lu_bubbles=B      距离1 (消费者紧接 load) 每险恰好 B 拍
  //   +expect_hazards=N  每阶段冒险数 (lu_bench.S = K*I = 8*128 = 1024)
  //   +expect_d2_cost=N  距离2 (中间隔一条) 的总代价 (旧语义 0; C-2 起 = 冒险数)
  int expect_hazards = 1024;
  int expect_d2_cost = 0;

  // ---- 上板跑分上报路径监视 ----
  // 软件在测量区间起点写 MMIO_MARK, 结束时把最终 tick 数写进 MMIO_RESULT。
  // 上板时这两个信号喂给 ILA (串口不通, 只能走 JTAG 读回), 这里先在仿真里
  // 确认软件确实写了、值也对 —— 免得白跑一次 10 秒的上板实验。
  localparam logic [31:0] MMIO_MARK_A   = 32'hA000_0020;
  localparam logic [31:0] MMIO_RESULT_A = 32'hA000_0024;
  logic        saw_mark, saw_result;
  logic [31:0] hw_result;
  always @(posedge clk) begin
    if (rst) begin
      saw_mark   <= 1'b0;
      saw_result <= 1'b0;
      hw_result  <= 32'd0;
      in_window  <= 1'b0;
    end
    else begin
      if (ext_we && (ext_addr == MMIO_MARK_A)) begin
        saw_mark  <= 1'b1;
        in_window <= 1'b1;      // 测量区间起点 (软件 start_time)
      end
      if (ext_we && (ext_addr == MMIO_RESULT_A)) begin
        saw_result <= 1'b1;
        hw_result  <= ext_wdata;
        in_window  <= 1'b0;     // 测量区间终点 (软件 stop_time)
      end
    end
  end

  // ---- 启动 ----
  initial begin : boot
    begin
      string f;
      if ($value$plusargs("dump=%s", f)) begin $dumpfile(f); $dumpvars(0, tb_sw); end
    end
    if (!$value$plusargs("max_cycles=%d", max_cycles)) max_cycles = 2000000;
    // 语义断言开关 (不给 plusarg 就全部为 0 = 不断言, 保持 tb_sw 的"只观测"属性)
    void'($value$plusargs("expect_lu_stall=%d",  exp_lu_stall));
    void'($value$plusargs("expect_lu_events=%d", exp_lu_events));
    void'($value$plusargs("expect_flush=%d",     exp_flush));
    void'($value$plusargs("expect_retire=%d",    exp_retire));
    void'($value$plusargs("lu_bubbles=%d",       lu_bubbles));
    void'($value$plusargs("dump_words=%d",       dump_words));
    void'($value$plusargs("expect_hazards=%d",   expect_hazards));
    void'($value$plusargs("expect_d2_cost=%d",   expect_d2_cost));
    if (exp_lu_stall || exp_lu_events || exp_flush || exp_retire || lu_bubbles)
      $display("[tb_sw] 语义断言已开启: lu_stall=%0d lu_events=%0d flush=%0d retire=%0d bubbles=%0d",
               exp_lu_stall, exp_lu_events, exp_flush, exp_retire, lu_bubbles);
    repeat (4) @(posedge clk);
    rst = 1'b0;
    $display("[tb_sw] reset released, max_cycles=%0d", max_cycles);
  end

  // ---- 主循环 ----
  always_ff @(posedge clk) begin
    if (!rst && !finished) begin
      cycles <= cycles + 1;

      if (cycles > max_cycles) begin
        finished = 1'b1;
        $display("[tb_sw] FAIL: timeout (%0d cycles), id_pc=0x%08x instr=0x%08x",
                 max_cycles, dbg_id_pc, dbg_instr);
        $fatal(1);
      end

      if (dbg_illegal) begin
        finished = 1'b1;
        $display("[tb_sw] FAIL: illegal instruction id_pc=0x%08x instr=0x%08x",
                 dbg_id_pc, dbg_instr);
        $fatal(1);
      end

      if (tohost_word == 32'd1) begin
        finished  = 1'b1;
        pass_seen = 1'b1;
      end
      else if (tohost_word != 32'd0) begin
        finished = 1'b1;
        $display("[tb_sw] FAIL: tohost=0x%08x (not 1), id_pc=0x%08x",
                 tohost_word, dbg_id_pc);
        $fatal(1);
      end
    end
  end

  // ---- 性能剖析 (层次化引用 core 内部信号; 纯仿真手段, 不改 RTL) ----
  // 目的: 在优化之前先量清楚周期花在哪 —— 分支惩罚、load-use 停顿、拆分访存。
  int unsigned n_fetch = 0;      // IF/ID 前进的周期数 ≈ 取回(发射)指令数
  int unsigned n_redirect = 0;   // 重定向次数 = 预测错误数 (redirect 是"预测错", 不是"分支 taken")
  int unsigned n_flush = 0;      // if_id_flush 拍数 = **实测**的重定向代价
                                 // (C-11 前 1.00 拍/次, C-11 起 2.00 拍/次; 见文件头口径注释)
  int unsigned n_stall = 0;      // pipe_stall 周期数
  int unsigned n_stall_lsu = 0;  // 其中拆分访存造成的
  // ---------------------------------------------------------------------------
  // 【真退休计数 — 2026-10-11 口径修正】
  // 旧口径 `if (mem_wb_ctrl != '0) n_wb++` 是**每拍都数**: 而 hazard_unit 的
  //   hold_mem = lsu_stall || ex_busy
  // 会让 EX/MEM 与 MEM/WB 一起冻结, 同一条指令在 WB 段被**重复观测**。
  // 实测 (CoreMark 基线): n_wb=417064, 而 hold_mem 拍数 = muldiv busy 14415
  //   (split-mem=0) => 真退休 = 402649, 与独立计数的 fetch=402723 只差 74 拍
  //   (0.018%)。旧口径让自印 CPI = 459460/417064 = 1.102 (虚低 3.5%)。
  //
  // 修正: 只在"WB 段真的前进"的拍上计一次 (排除 hold_mem 冻结拍)。
  // n_wb_raw 保留旧口径, 只用于对账 (两者之差必须 == hold_mem 拍数)。
  // ---------------------------------------------------------------------------
  int unsigned n_wb = 0;         // **真退休指令数** (排除 hold_mem 冻结拍)
  int unsigned n_wb_raw = 0;     // 旧口径: mem_wb_ctrl != '0 的拍数 (含重复计数)
  int unsigned n_both = 0;       // pipe_stall 与 if_id_flush 同拍的拍数 (对账用)
  int unsigned n_hold_mem = 0;   // hold_mem 拍数 (对账: n_wb_raw - n_wb 必须等于它)
  // load-use **冒险事件数** (load_use 上升沿)。1 拍气泡语义下它 == n_stall_lu;
  // 2 拍气泡语义下 n_stall_lu == bubbles * n_lu_events。用这两个数的关系来自证
  // "每条 load-use 恰好插了 N 拍气泡", 而不是只看总数拍脑袋。
  int unsigned n_lu_events = 0;
  logic        lu_d = 1'b0;
  // 重定向分类: 决定分支预测该怎么做
  int unsigned n_bcond = 0;      // 条件分支 *总数* (不论跳不跳)
  int unsigned n_br_taken = 0;   // 条件分支 taken (BEQ/BNE/BLT/...)
  int unsigned n_jal = 0;        // JAL
  int unsigned n_jalr = 0;       // JALR (函数返回/间接跳转 —— 有 BTB 也难预测)
  // 停顿构成 (决定下一步该优化什么)
  int unsigned n_stall_lu = 0;   // load-use 停顿
  int unsigned n_stall_ls = 0;   // 拆分访存停顿
  int unsigned n_stall_ex = 0;   // 多周期执行单元忙 (乘除法)
  // 【为什么要数 load】用周期换频率的改动必须先把账算平: 例如"让 load 在 MEM
  //   多停一拍"会让**每一次 load** 都多花 1 拍, 代价 = n_load / cycles。
  //   没有这个数就只能凭感觉估。
  int unsigned n_load = 0;       // load 在 MEM 且流水线前进的周期数 ≈ load 条数
  // 预测器精度
  int unsigned n_ctrl = 0;       // 控制流指令数 (每条做一次预测)
  int unsigned n_mis  = 0;       // 预测错误数
  // 【按类型分】决定下一步该优化哪一类预测器:
  //   条件分支错多 -> 方向预测器 (2 位 Smith / 更深的表)
  //   JALR(返回) 错多 -> 需要 RAS (返回地址栈)
  int unsigned n_mis_br = 0, n_mis_jal = 0, n_mis_jalr = 0;
  // 【条件分支误判再细分】决定该修 BTB 组织还是方向预测器:
  //   BTB 没命中      -> 预测"不跳", 若实际 taken 则误判 => 该扩表 / 改组织
  //   BTB 命中但方向错 -> 表命中了, 2 位计数器的方向判断错了 => 该改方向预测器
  int unsigned n_align_err = 0;    // 取指对齐自检失败次数 (必须为 0)
  int unsigned n_ras_push = 0, n_ras_pop = 0, n_ras_nonempty = 0;  // RAS 自证
  int unsigned n_pred_is_ret = 0, n_ras_diff = 0;   // 预测被判为返回的次数 / 与 BTB 目标不同的次数
  int unsigned n_mis_br_nohit = 0, n_mis_br_dir = 0;
  int unsigned n_br_hit = 0, n_br_nohit = 0;      // 条件分支的 BTB 命中情况

  // 复现 btb.sv 的"有没有表项"判据 (纯观测用)。
  // ---------------------------------------------------------------------------
  // 【为什么改成从实例参数推导】旧版把几何**硬编码**成 IDX_BITS=6/PC_TOP=14
  //   (idx=pc[7:2], tag=pc[13:8])。C-1 把 BTB 从 64 项改成 512 项 (IDX_BITS=9,
  //   tag=pc[13:11]) 之后这段切片整片失真, 日志里打出
  //       "条件分支总数 70449 | BTB 有表项 1036 (1%) | 无表项 69413 (98%)"
  //   —— 真实命中率是 96%+, 于是"无表项占条件分支误判 %"这条分析结论**整个反过来**
  //   (把方向预测器的问题误判成容量/冲突问题, 下一步就会去扩表)。
  //   这是 PITFALLS R1 的经典形态: 改了 A (BTB 几何) 忘了同步 B (TB 复现判据),
  //   而且**不报错、只是数变荒谬**。
  //
  // 修法有两层:
  //   1) 索引/tag 的位宽与切片**全部从 u_btb 实例的参数推导** (几何再改也不会错);
  //   2) 加运行时自证 n_btb_rep_bad: 把本判据与 BTB 自己的 q_hit 逐拍比对
  //      —— 推导错了会**大声失败**, 而不是静默给出荒谬统计。
  //   btb.sv 原文: pidx = p2_pc[IDX_BITS+1:2], ptag = p2_pc[PC_TOP-1:IDX_BITS+2],
  //                q_hit = q_vld && (q_tag == q_tagexp)  (打一拍后输出)
  // ---------------------------------------------------------------------------
  localparam int unsigned BTB_IDX_BITS = u_dut.u_btb.IDX_BITS;
  localparam int unsigned BTB_PC_TOP   = u_dut.u_btb.PC_TOP;
  localparam int unsigned BTB_TAG_LSB  = BTB_IDX_BITS + 2;
  localparam int unsigned BTB_TAG_W    = BTB_PC_TOP - BTB_TAG_LSB;
  localparam int unsigned BTB_N        = 1 << BTB_IDX_BITS;
  function automatic logic btb_has_entry(input logic [31:0] pc);
    logic [BTB_IDX_BITS-1:0] idx;
    logic [BTB_TAG_W-1:0]    tg;
    begin
      idx = pc[BTB_IDX_BITS+1:2];
      tg  = pc[BTB_PC_TOP-1:BTB_TAG_LSB];
      btb_has_entry = u_dut.u_btb.vld[idx] && (u_dut.u_btb.tag[idx] == tg);
    end
  endfunction

  // ---- 自证: 本判据必须与 BTB 自己的 q_hit 逐拍一致 ----
  // 时序: BTB 在 T 拍用 p2_pc = pc_next(T) 组合查表、在 T+1 拍输出 q_hit。
  //      所以这里也在 T 拍用 pc_next 算一份、打一拍, T+1 拍与 pred_hit(=q_hit)
  //      比对 —— 两边读的是同一个 posedge 前的同一个组合值, 必须逐位相等。
  logic        rep_hit_d = 1'b0;
  int unsigned n_btb_rep_bad = 0;   // 必须 0; 非 0 说明复现判据与 btb.sv 不同源
  always @(posedge clk) begin
    if (!rst) begin
      if (rep_hit_d !== u_dut.pred_hit) n_btb_rep_bad++;
      rep_hit_d <= btb_has_entry(u_dut.pc_next);
    end
  end

  always @(posedge clk) begin
    if (!rst) begin
      // ----------------------------------------------------------------------
      // 【取指对齐自检】if_pc 处的指令字必须真的等于 if_instr。
      // 为什么必须有这条: 一旦取指存储器改成寄存读, rdata 会比 addr 晚一拍;
      // 若 PC 与指令没有做对应的相位对齐, 就变成"pc_N 配 instr(pc_{N-1})",
      // 症状是程序行为莫名错乱(先是 timeout, 之后是某个测试组失败), 而
      // RTL 语法/编译全部正常 —— 属于极难定位的静默错位。
      // 这条检查把"错位"变成一行直接报错。
      //
      // 两个坑 (都实测踩过, 见注释避免后人重踩):
      //  1) 索引位宽必须是 imem 真实的 AW 位, 不是随便切 16 位。
      //     16KB = 4096 字 -> AW=12 -> 用 if_pc[13:2]。
      //  2) **flush 那一拍 if_id 里留的是上一拍的旧值, 而且 pc 被清成 0**。
      //     所以必须跳过 flush 的拍, 否则会在 pc=0 上报一堆假错位。
      //     用 if_pc==0 作废值判据 (PC_RESET=0x8000_0000, 合法 PC 不会是 0)。
      // ----------------------------------------------------------------------
      //  3) 前几拍要跳过: imem 的 initial 先把整块填成 NOP, $readmemh 在同一次
      //     initial 里随后才覆盖。复位期间 if_id 先锁到的是"填 NOP 阶段"的内容,
      //     于是 cycle 0 会报一次假错位。用 cycles 门限跳过启动窗口。
      if (cycles > 4 && !u_dut.if_id_flush && u_dut.if_pc != 32'd0) begin
        logic [31:0] exp_instr;
        exp_instr = u_dut.u_imem.mem[u_dut.if_pc[13:2]];
        if (u_dut.if_instr !== exp_instr) begin
          n_align_err++;
          if (n_align_err <= 5)
            $display("[ALIGN] cycle %0d: if_pc=0x%08x if_instr=0x%08x 期望 0x%08x  <-- 取指错位!",
                     cycles, u_dut.if_pc, u_dut.if_instr, exp_instr);
        end
      end

      if (!u_dut.if_id_flush && !u_dut.pipe_stall) n_fetch++;
      if (u_dut.redirect)    n_redirect++;
      if (u_dut.if_id_flush) n_flush++;            // 实测的重定向代价 (拍)
      if (u_dut.pipe_stall)  n_stall++;
      if (u_dut.pipe_stall && u_dut.if_id_flush) n_both++;
      if (u_dut.lsu_stall)   n_stall_lsu++;
      // 【真退休】只数 WB 段真正前进的拍 (排除 hold_mem 冻结拍, 见声明处注释)
      if (u_dut.mem_wb_ctrl != '0) begin
        n_wb_raw++;
        if (!u_dut.u_hazard.hold_mem) n_wb++;
      end
      if (u_dut.u_hazard.hold_mem) n_hold_mem++;

      // ---- 测量窗口 (MMIO_MARK -> MMIO_RESULT) ----
      if (in_window) begin
        n_win_cycles++;
        if (!u_dut.if_id_flush && !u_dut.pipe_stall) n_win_fetch++;
        if (u_dut.pipe_stall) n_win_stall++;
        if (u_dut.u_hazard.load_use) n_win_stall_lu++;
        if (u_dut.u_hazard.load_use && !lu_d) n_win_lu_events++;
        if (u_dut.u_hazard.load_use && (u_dut.lsu_stall || u_dut.md_busy)) n_win_lu_overlap++;
      end

      // 停顿构成 (pipe_stall = load_use || lsu_stall || ex_busy)
      if (u_dut.u_hazard.load_use) n_stall_lu++;   // load_use 在 hazard_unit 内部
      // load-use **事件** (上升沿): 2 拍气泡语义下 load_use 会连续两拍为 1,
      // n_stall_lu = 2 * n_lu_events; 1 拍语义下两者相等。断言就建立在二者关系上。
      if (u_dut.u_hazard.load_use && !lu_d) n_lu_events++;
      lu_d <= u_dut.u_hazard.load_use;
      if (u_dut.lsu_stall) n_stall_ls++;
      if (u_dut.md_busy)   n_stall_ex++;
      // load 在 MEM 段且本拍流水线前进 -> 一次 load "消耗" 一个 MEM 周期
      if (u_dut.ex_mem_ctrl.mem_read && !u_dut.pipe_stall) n_load++;

      // RAS 自证 (仅在 RAS_EN 打开时存在)
`ifdef RAS_EN
      if (u_dut.ex_is_call)      n_ras_push++;
      if (u_dut.ex_is_ret)       n_ras_pop++;
      if (!u_dut.u_ras.empty)    n_ras_nonempty++;
      // 预测时"这条 PC 被识别为返回"以及"RAS 目标与 BTB 目标不同"的次数。
      // 若 pred_is_ret 一直是 0, 说明 is_ret 标记没起作用 (而非 RAS 无用) ——
      // 这个区分非常关键: 实测就先踩过"只把 is_ret 写进 gshare 分支、
      // 默认的 Smith 分支没写", 导致 RAS 压栈弹栈都正常却完全不改变预测。
      if (u_dut.pred_is_ret) begin
        n_pred_is_ret++;
        if (u_dut.ras_top !== u_dut.btb_pred_target) n_ras_diff++;
      end
`endif

      // ======================================================================
      // 【C-15 之后: EX 段统计必须排除"错路影子"】PITFALLS 盲区 6 (口径必须
      //   对应计时区间里的**真指令**)。
      // C-15 把误判纠正改成: 检出拍不再冲 ID/EX, 错路指令以 X0 形态进 EX **占
      //   一拍**, 下一拍用寄存器 br_redir_r 把它连同 ID/EX、EX/MEM 一起冲掉;
      //   这一拍 `u_dut.redir_kill`(= br_redir_r) 为 1, 且 **id_ex_ctrl 里留的
      //   仍是那条错路指令自己的控制字** (core.sv:748 只把 alu_op 换成 ALU_ADD,
      //   pc_sel 原样保留)。所以:
      //     · 若错路指令本身是分支/JAL/JALR (误判后紧跟的错路码里很常见),
      //       `pc_sel != 0` -> 被计进 ctrl-flow / 条件分支总数 **分母**;
      //     · 它从未被"预测"过 (它的预测随 PC 一起被丢弃), 计入分母就是虚高。
      //   实测 (CoreMark, C-15 前后): ctrl-flow 99616 -> 101245 (+1629, 其中
      //   条件分支 70449 -> 71848), 而 mispredict 恒为 8020 —— 分子对、分母错,
      //   于是 accuracy 被"抬高"、条件分支 taken%/BTB 命中率被稀释。
      //   注意这**不是**放宽断言: n_mis/n_mis_* 走 redirect_raw, 而 C-15 已把
      //   redirect_raw 门控成 `!pred_ok && !redir_kill`, 本来就是对的; 这里只是
      //   让分母与"真指令"对齐。同理 n_stall_lu 那组是 RTL 侧把 X0 从 hazard
      //   视角屏蔽后才是 3072 (不是 TB 放宽)。
      //   若哪天 C-15 被回退, `u_dut.redir_kill` 不存在 -> 编译期报错 (大声),
      //   不会静默算错。
      // ======================================================================
      // 预测器精度: 每条控制流指令做一次预测 (停顿期间不重复计)
      if (u_dut.id_ex_ctrl.pc_sel != 2'd0 && !u_dut.redir_kill) begin
        if (!u_dut.pipe_stall) n_ctrl++;
        if (u_dut.redirect_raw) n_mis++;
      end
      // 按类型统计预测错误 (pc_sel: 1=BR 2=J 3=JR)
      if (u_dut.redirect_raw && !u_dut.pipe_stall && !u_dut.redir_kill) begin
        if      (u_dut.id_ex_ctrl.pc_sel == 2'd1) n_mis_br++;
        else if (u_dut.id_ex_ctrl.pc_sel == 2'd2) n_mis_jal++;
        else if (u_dut.id_ex_ctrl.pc_sel == 2'd3) n_mis_jalr++;
      end
      // 【条件分支误判再细分】纯观测 (层次化读 BTB 阵列, 不改 RTL)。
      // 目的: 分清两种完全不同的病因 ——
      //   (a) BTB 没表项 -> 只能预测"不跳"; 若实际 taken 就是"容量/冲突"问题
      //   (b) BTB 有表项但方向计数器判反 -> 是"方向预测器"问题
      // 修法完全不同, 必须先分清。
      // 做法: 用与 btb.sv 完全相同的 index/tag 切片, 在 EX 段 PC 上复现它的
      // 组合读公式 (btb.sv: pidx=pc[IDX+1:2], ptag=pc[PC_TOP-1:IDX+2])。
      // !redir_kill: 错路影子不是"条件分支"的样本 (见上面那段口径说明)。
      if (u_dut.id_ex_ctrl.pc_sel == 2'd1 && !u_dut.redir_kill) begin
        if (btb_has_entry(u_dut.id_ex_pc)) n_br_hit++;
        else                         n_br_nohit++;
        if (u_dut.redirect_raw && !u_dut.pipe_stall) begin
          if (btb_has_entry(u_dut.id_ex_pc)) n_mis_br_dir++;
          else                         n_mis_br_nohit++;
        end
      end

      // pc_sel_e: PC_NEXT=0 PC_BR=1 PC_J=2 PC_JR=3
      if (u_dut.id_ex_ctrl.pc_sel == 2'd1 && !u_dut.redir_kill) begin
        n_bcond++;
        if (u_dut.redirect) n_br_taken++;
      end
      if (u_dut.redirect && !u_dut.redir_kill) begin
        if (u_dut.id_ex_ctrl.pc_sel == 2'd2) n_jal++;
        if (u_dut.id_ex_ctrl.pc_sel == 2'd3) n_jalr++;
      end
    end
  end

  // ---- PC 轨迹 (调试用, +trace 开启; 每 2000 周期采一次) ----
  bit trace_on = 1'b0;
  initial if ($test$plusargs("trace")) trace_on = 1'b1;

  // 逐拍窗口: +trace 从第 20000 拍起打印 60 拍, 带关键内部信号
  always @(posedge clk) begin
    if (!rst && trace_on && cycles >= 2140 && cycles < 2200)
      $display("[trace] c=%0d pc=%08x pT=%b pTgt=%08x mis=%b stall=%b idpc=%08x sel=%0d aT=%b aTgt=%08x exT=%b",
               cycles, dbg_pc, u_dut.pred_taken, u_dut.pred_target,
               u_dut.redirect_raw, u_dut.pipe_stall,
               u_dut.id_ex_pc, u_dut.id_ex_ctrl.pc_sel,
               u_dut.actual_taken, u_dut.actual_target, u_dut.ex_taken);
  end

  // ---- x5 (t0) 写入追踪: 只在值落在 __umodsi3 区域时打印 ----
  always @(posedge clk) begin
    if (!rst && trace_on && u_dut.wb_reg_write && (u_dut.wb_rd == 5'd5)
        && (u_dut.wb_data >= 32'h8000_03a0) && (u_dut.wb_data <= 32'h8000_03d0))
      $display("[x5] c=%0d  at_pc=0x%08x   x5 <- 0x%08x",
               cycles, u_dut.mem_wb_pc_plus4 - 32'd4, u_dut.wb_data);
  end

  // ---- 日志 dump ----
  initial begin : dump_log
    wait (pass_seen);
    #(TCLK_HALF * 4);
    $display("=================================================");
    $display("  cycles = %0d", cycles);
    $write("  --- program output ---\n");
    for (int i = 0; i < LOG_MAX; i++) begin
      logic [7:0] b;
      b = u_dut.u_dmem.mem[LOG_IDX + i/4] >> (8 * (i % 4));
      if (b == 8'h00) break;
      $write("%c", b);
    end
    $display("  --- end of output ---");
    if (dump_words != 0) begin
      $display("  --- log words (微基准自量的原始数) ---");
      for (int i = 0; i < dump_words; i++)
        $display("  log[%0d] = 0x%08x  (%0d)", i,
                 u_dut.u_dmem.mem[LOG_IDX + i], u_dut.u_dmem.mem[LOG_IDX + i]);
    end
    $display("  --- profile ---");
    $display("  cycles     = %0d", cycles);
    $display("  fetch      = %0d   (IF/ID 前进的拍数 = 发射指令数)", n_fetch);
    // 【口径修正】旧行是 `retire(wb) = mem_wb_ctrl != '0 的拍数`, 被 hold_mem 冻结拍
    // 重复计数(实测虚高 14415)。现在两行都打, 差额必须 == hold_mem 拍数。
    $display("  retire(wb) = %0d   <- 真退休 (已排除 hold_mem 冻结拍)", n_wb);
    $display("  retire(raw)= %0d   <- 旧口径 (含冻结重复计数; 差 %0d = hold_mem 拍数 %0d)",
             n_wb_raw, n_wb_raw - n_wb, n_hold_mem);
    // 【口径修正】旧行把"每次重定向 2 拍"当常数乘上去 (假设不是实测)。
    // 现在直接数 if_id_flush 拍数, 代价 = n_flush / n_redirect。
    // 【归属别挂错】这个 2 来自 **C-11** 的两拍冲刷窗口 (core.sv:913), 不是 C-1
    // 预测器; C-1 只改**次数** (11530 -> 8020)。实测坐实: 同一份 core 换回旧
    // 64 项组合查表仍是 2.00 拍/次 (rtl-pred 2026-10-11)。
    $display("  redirects  = %0d   实测冲刷拍数 = %0d  -> 每次 %0d.%02d 拍  (旧行按固定 2 拍算 = %0d 周期)",
             n_redirect, n_flush,
             n_redirect == 0 ? 0 : n_flush / n_redirect,
             n_redirect == 0 ? 0 : ((n_flush * 100) / n_redirect) % 100,
             n_redirect * 2);
    $display("  stalls     = %0d   (其中拆分访存 %0d) = %0d%%",
             n_stall, n_stall_lsu, (n_stall * 100) / (cycles == 0 ? 1 : cycles));
    // ---- 周期对账: cycles 应当 = fetch + flush + stall - 同拍重叠 + 残差 ----
    begin : acct
      int signed accounted, residual;
      accounted = int'(n_fetch) + int'(n_flush) + int'(n_stall) - int'(n_both);
      residual  = int'(cycles) - accounted;
      $display("  --- cycle accounting (自证口径无遗漏) ---");
      $display("  cycles(%0d) = fetch(%0d) + flush(%0d) + stall(%0d) - both(%0d) + 残差(%0d)",
               cycles, n_fetch, n_flush, n_stall, n_both, residual);
      $display("  残差应为 0 (只允许收尾竞态的 ±2); 非 0 说明还有一类周期没被数到");
      $display("  stall 构成和 = load-use(%0d) + split-mem(%0d) + muldiv(%0d) = %0d  (与 pipe_stall 拍数差 %0d)",
               n_stall_lu, n_stall_ls, n_stall_ex, n_stall_lu + n_stall_ls + n_stall_ex,
               (n_stall >= n_stall_lu + n_stall_ls + n_stall_ex)
                 ? n_stall - (n_stall_lu + n_stall_ls + n_stall_ex)
                 : (n_stall_lu + n_stall_ls + n_stall_ex) - n_stall);
      // retire 与 fetch 的独立互证: 两条独立计数路径 (WB 段 vs IF/ID 段) 之差
      // 只允许是收尾竞态。差 > 几十拍就说明 retire 口径还有问题。
      $display("  互证: fetch - retire = %0d  (应为 0±3; 大偏差 = 口径还有洞)",
               (n_fetch >= n_wb) ? int'(n_fetch - n_wb) : -int'(n_wb - n_fetch));
    end
    $display("  --- hardware score reporting ---");
    $display("  MMIO_MARK written   = %0d", saw_mark);
    // 【口径修正】旧行把打包值当 ticks 直接打印 (显示 2147896718, 无法判读)。
    // core_portme.c: MMIO_RESULT = {bit31 = CRC 全对, [30:0] = ticks}。
    $display("  MMIO_RESULT written = %0d   raw=0x%08x   CRC-valid=%0d   ticks=%0d",
             saw_result, hw_result, hw_result[31], hw_result[30:0]);
    $display("  --- measurement window (MMIO_MARK -> MMIO_RESULT = CoreMark ticks 区间) ---");
    $display("  window cycles   = %0d   (软件自报 ticks = %0d, 差 %0d)",
             n_win_cycles, hw_result[30:0],
             (n_win_cycles > hw_result[30:0]) ? n_win_cycles - hw_result[30:0]
                                              : hw_result[30:0] - n_win_cycles);
    $display("  window fetch    = %0d", n_win_fetch);
    $display("  window stalls   = %0d", n_win_stall);
    // 【口径】这一行是**当前实现在本窗口内的实测值**, 不是"改成 2 拍后的增量"。
    // 曾经把它当增量报过一次, 结果低估 22%: C-2 的 load_use_mem 还会把"距离 2
    // 的消费者"纳入停顿 (旧语义完全免费), 所以增量 = 新覆盖的冒险数 x 1 拍,
    // **必须用改前/改后两条日志的 window cycles 相减**, 不能拿现值当增量。
    $display("  window load-use = %0d 拍 / %0d 冒险事件  (当前实现实测; 平均 %0d.%02d 拍/事件)",
             n_win_stall_lu, n_win_lu_events,
             (n_win_lu_events == 0) ? 0 : n_win_stall_lu / n_win_lu_events,
             (n_win_lu_events == 0) ? 0 : ((n_win_stall_lu * 100) / n_win_lu_events) % 100);
    $display("  window lu overlap (与 lsu/ex 同拍) = %0d   <- 这类拍不额外增加周期, 故代价要看 window cycles 差值",
             n_win_lu_overlap);
    $display("  --- stall breakdown ---");
    $display("  load-use   = %0d  (%0d%%)   冒险事件 = %0d  -> 平均 %0d.%02d 拍/事件",
             n_stall_lu, (n_stall_lu * 100) / (cycles == 0 ? 1 : cycles), n_lu_events,
             n_lu_events == 0 ? 0 : n_stall_lu / n_lu_events,
             n_lu_events == 0 ? 0 : ((n_stall_lu * 100) / n_lu_events) % 100);
    $display("  split-mem  = %0d", n_stall_ls);
    $display("  muldiv busy= %0d", n_stall_ex);
    // 【代价估算用】每让一次 load 多占 1 个 MEM 周期, 时间就多 n_load/cycles
    $display("  load(MEM)  = %0d  (%0d%% of cycles)  <- 每次 load 多停 1 拍的代价就是这个百分比",
             n_load, (n_load * 100) / (cycles == 0 ? 1 : cycles));
    $display("  --- 取指对齐自检 (寄存读改动的守门人) ---");
    if (n_align_err != 0)
      $display("  *** 失败 %0d 次: if_pc 与 if_instr 不同源 (取指错位!) ***", n_align_err);
    else
      $display("  OK (0 次错位)");
`ifdef RAS_EN
    // RAS 自证: 必须能区分"RAS 没用"和"RAS 没生效"。
    // 实测踩过: is_ret 只写进了 gshare 分支, 默认的 Smith 分支没写 ->
    // 压栈/弹栈计数都正常, 但 pred_is_ret 恒为 0, 对误判毫无影响。
    $display("  --- RAS (返回地址栈) ---");
    $display("  push(call) = %0d   pop(ret) = %0d   栈顶非空拍数 = %0d",
             n_ras_push, n_ras_pop, n_ras_nonempty);
    $display("  预测判为返回 = %0d   其中 RAS 目标 != BTB 目标 = %0d",
             n_pred_is_ret, n_ras_diff);
    begin : btb_ret_count
      int unsigned n_ret_entries;
      n_ret_entries = 0;
      for (int bi = 0; bi < int'(BTB_N); bi++)
        if (u_dut.u_btb.vld[bi] && u_dut.u_btb.is_ret[bi]) n_ret_entries++;
      $display("  BTB 中 is_ret=1 的表项 = %0d (为 0 则标记没写进表)", n_ret_entries);
    end
`endif
    $display("  --- predictor accuracy ---");
    // BTB 复现判据自证: 几何/切片与 btb 实例不同源时, 下面的"BTB 有表项/无表项"
    // 统计会整片失真 (实测 C-1 改 512 项时打出 1% 命中率, 真实 96%+)。
    if (n_btb_rep_bad != 0)
      $display("  *** 失败 %0d 次: btb_has_entry 与 btb 自身 q_hit 不一致 -> 本 TB 复现的索引/tag 切片已过期, 下面的 BTB 命中统计不可信 ***", n_btb_rep_bad);
    else
      $display("  BTB 复现判据自证: OK (0 次不一致; 几何由 u_btb 实例参数推导, IDX=%0d TAG=%0d N=%0d)",
               BTB_IDX_BITS, BTB_TAG_W, BTB_N);
    $display("  ctrl-flow  = %0d   mispredict = %0d   accuracy = %0d%%",
             n_ctrl, n_mis, n_ctrl == 0 ? 0 : 100 - (n_mis * 100) / n_ctrl);
    $display("  --- mispredict by type (决定下一个优化方向) ---");
    $display("  cond branch = %0d  (%0d%% of mispredicts)", n_mis_br,
             n_mis == 0 ? 0 : (n_mis_br * 100) / n_mis);
    $display("  JAL         = %0d  (%0d%%)", n_mis_jal,
             n_mis == 0 ? 0 : (n_mis_jal * 100) / n_mis);
    $display("  JALR        = %0d  (%0d%%)  <- 若这项大, RAS 才有价值", n_mis_jalr,
             n_mis == 0 ? 0 : (n_mis_jalr * 100) / n_mis);
    $display("  --- 条件分支: BTB 冲突 vs 方向预测 (决定改表组织还是改方向预测器) ---");
    $display("  条件分支总数 = %0d   BTB 有表项 = %0d (%0d%%)   无表项 = %0d (%0d%%)",
             n_br_hit + n_br_nohit, n_br_hit,
             (n_br_hit + n_br_nohit) == 0 ? 0 : (n_br_hit * 100) / (n_br_hit + n_br_nohit),
             n_br_nohit,
             (n_br_hit + n_br_nohit) == 0 ? 0 : (n_br_nohit * 100) / (n_br_hit + n_br_nohit));
    $display("  误判细分: 无表项(容量/冲突) = %0d   有表项但方向错 = %0d",
             n_mis_br_nohit, n_mis_br_dir);
    $display("           无表项占条件分支误判 %0d%% (这项大 => 该扩容/改组织)",
             n_mis_br == 0 ? 0 : (n_mis_br_nohit * 100) / n_mis_br);
    $display("  --- redirect breakdown ---");
    $display("  cond branch total = %0d  (taken %0d = %0d%%)",
             n_bcond, n_br_taken, n_bcond == 0 ? 0 : (n_br_taken * 100) / n_bcond);
    $display("  JAL  = %0d", n_jal);
    $display("  JALR = %0d   <- 函数返回, 简单 BTB 难以预测, 需要 RAS", n_jalr);
    // 【口径修正】旧行 CPI = cycles / (mem_wb_ctrl!=0 的拍数) 分母虚高 -> CPI 虚低
    // (CoreMark 基线自印 1.10, 真值 1.141)。现在分母是真退休数, 并同时给 IPC。
    if (n_wb != 0) begin
      $display("  CPI        = %0d.%03d   (cycles/retire, 真退休分母)",
               cycles / n_wb, ((cycles * 1000) / n_wb) % 1000);
      $display("  IPC        = %0d.%03d   (retire/cycles)",
               (n_wb * 1000) / (cycles == 0 ? 1 : cycles) / 1000,
               ((n_wb * 1000) / (cycles == 0 ? 1 : cycles)) % 1000);
    end

    // ========================================================================
    // 【语义断言】只在显式给 plusarg 时生效 (+lu_bubbles / +expect_*)。
    // 微基准 lu_bench.S 有三个**指令数逐字相同**的阶段, 只差消费者相对 load 的
    // 位置: 距离 3 (无冒险) / 距离 2 / 距离 1。它把三个阶段自量的周期数写在
    // log[0]=cycA / log[1]=cycB / log[2]=cycC。于是:
    //     距离1 代价 = cycC - cycA = lu_bubbles * expect_hazards
    //     距离2 代价 = cycB - cycA = expect_d2_cost
    // 【为什么要分距离量 (2026-10-11 C-2 实测教训)】C-2 把"load 在 MEM 且 ID
    //   依赖它"也算停顿, 于是距离 2 的消费者**从免费变成停 1 拍**。只看"距离 1
    //   事件 × 1 拍"会漏掉这一整块 (CoreMark 上漏了 8213 次), 把代价低估 22%。
    // 同时用 TB 侧的 n_stall_lu 独立复核 (两条证据来源不同, 都对才算数)。
    // 数据正确性由程序自己的 checksum 保证: 被扣住的消费者读完 load 后累加,
    // 与 3*128*36 不符就写 tohost!=1 -> tb_sw 的 tohost 判据直接 FAIL。
    // ========================================================================
    if (lu_bubbles != 0) begin
      int signed cycA, cycB, cycC, d1, d2;
      cycA = int'(u_dut.u_dmem.mem[LOG_IDX + 0]);
      cycB = int'(u_dut.u_dmem.mem[LOG_IDX + 1]);
      cycC = int'(u_dut.u_dmem.mem[LOG_IDX + 2]);
      d1 = cycC - cycA;
      d2 = cycB - cycA;
      $display("  [断言] 微基准自量: cycA=%0d cycB=%0d cycC=%0d  |  距离1 代价=%0d (期望 %0d)  距离2 代价=%0d (期望 %0d)",
               cycA, cycB, cycC, d1, lu_bubbles * expect_hazards, d2, expect_d2_cost);
      if (d1 != lu_bubbles * expect_hazards)
        $fatal(1, "[tb_sw] FAIL: 距离1 load-use 代价 %0d != 期望 %0d (= %0d 冒险 x %0d 拍)",
               d1, lu_bubbles * expect_hazards, expect_hazards, lu_bubbles);
      if (d2 != expect_d2_cost)
        $fatal(1, "[tb_sw] FAIL: 距离2 load-use 代价 %0d != 期望 %0d",
               d2, expect_d2_cost);
      if (n_stall_lu != lu_bubbles * expect_hazards + expect_d2_cost)
        $fatal(1, "[tb_sw] FAIL: TB 侧 load-use 停顿 %0d != 期望 %0d (= 距离1 %0d + 距离2 %0d)",
               n_stall_lu, lu_bubbles * expect_hazards + expect_d2_cost,
               lu_bubbles * expect_hazards, expect_d2_cost);
      $display("  [断言] 距离1 = %0d 拍/冒险, 距离2 = %0d 拍/冒险; TB 侧 n_stall_lu = %0d 全部符合  OK",
               lu_bubbles, (expect_hazards == 0) ? 0 : expect_d2_cost / expect_hazards, n_stall_lu);
    end
    if (exp_lu_events != 0 && n_lu_events != exp_lu_events)
      $fatal(1, "[tb_sw] FAIL: load-use 事件数 期望 %0d 实测 %0d", exp_lu_events, n_lu_events);
    if (exp_lu_stall != 0 && n_stall_lu != exp_lu_stall)
      $fatal(1, "[tb_sw] FAIL: load-use 停顿拍数 期望 %0d 实测 %0d", exp_lu_stall, n_stall_lu);
    if (exp_flush != 0 && n_flush != exp_flush)
      $fatal(1, "[tb_sw] FAIL: if_id_flush 拍数 期望 %0d 实测 %0d", exp_flush, n_flush);
    if (exp_retire != 0 && n_wb != exp_retire)
      $fatal(1, "[tb_sw] FAIL: 真退休指令数 期望 %0d 实测 %0d", exp_retire, n_wb);
    if (exp_lu_stall || exp_lu_events || exp_flush || exp_retire)
      $display("  [断言] expect_* 全部满足 (lu_stall=%0d lu_events=%0d flush=%0d retire=%0d)",
               n_stall_lu, n_lu_events, n_flush, n_wb);
    // 【W-03】取指对齐自检必须**判失败**, 不能只打印红字然后照旧 PASS。
    // 这条检查是 "IMEM 寄存读" 那次改动 (R30) 的唯一安全网: 静默错位的症状是
    // 程序行为莫名错乱, 而本 tb 之前无论 n_align_err 是多少都打印 RESULT : PASS。
    if (n_align_err != 0)
      $fatal(1, "[tb_sw] FAIL: 取指错位 %0d 次 (if_pc 与 if_instr 不同源)", n_align_err);
    // 【同族防线 (R1)】BTB 几何改了但 TB 复现判据没同步 -> 统计整片失真却不报错。
    // 与取指对齐自检同一纪律: 必须判失败, 不能只打印红字。
    if (n_btb_rep_bad != 0)
      $fatal(1, "[tb_sw] FAIL: btb_has_entry 与 btb 的 q_hit 不一致 %0d 次 (TB 复现的 BTB 索引/tag 切片与 btb.sv 实例不同源)", n_btb_rep_bad);
    $display("  RESULT : PASS");
    $finish;
  end

  // ---- 时间级看门狗 ----
  initial begin : watchdog
    #(500ms);
    $display("[tb_sw] FAIL: global timeout at %0t, tohost=0x%08x cycles=%0d",
             $time, tohost_word, cycles);
    $fatal(1);
  end

endmodule
