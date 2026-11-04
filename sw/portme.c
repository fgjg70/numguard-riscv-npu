/* ============================================================================
 * portme.c — CoreMark / 裸机程序的本平台适配层
 * ----------------------------------------------------------------------------
 * 日志输出: 直接写进 dmem 的日志缓冲区 (0x9000_3000 .. 0x9000_3FFF),
 *           由仿真测试台 / 硬件 ILA 读取。这样 *不需要* 串口驱动就能拿到
 *           程序输出 —— 本机 FT2232H 走 D2XX 驱动, 没有 COM 口。
 * 计时:     读 MMIO 的 64 位自由运行周期计数器 (rv32_pkg: MMIO_CYCLE_LO/HI)。
 *           这是测出 CoreMark/MHz 的前提。
 * ========================================================================== */

typedef unsigned int   u32;
typedef unsigned long long u64;

#define MMIO_CYCLE_LO  (*(volatile u32 *)0xA0000008u)
#define MMIO_CYCLE_HI  (*(volatile u32 *)0xA000000Cu)
#define MMIO_GPIO_OUT  (*(volatile u32 *)0xA0000000u)

#define LOG_BASE   ((volatile char *)0x90003000u)
#define LOG_LIMIT  0x1000u
#define TOHOST     (*(volatile u32 *)0x90002000u)

static u32 log_pos = 0;

/* ---- 日志输出 ---- */
void uart_send_char(char c) {
    if (log_pos < LOG_LIMIT - 1u) LOG_BASE[log_pos++] = c;
    LOG_BASE[log_pos] = 0;          /* 保持 NUL 结尾, 便于 tb 直接当字符串读 */
}

void log_flush_marker(void) { LOG_BASE[log_pos] = 0; }

/* ---- 周期计数 (64 位, 分两次读) ---- */
static u64 read_cycles(void) {
    u32 lo, hi, hi2;
    do {
        hi  = MMIO_CYCLE_HI;
        lo  = MMIO_CYCLE_LO;
        hi2 = MMIO_CYCLE_HI;
    } while (hi != hi2);            /* 防止读 lo 时进位 */
    return ((u64)hi << 32) | (u64)lo;
}

/* ---- CoreMark 需要的接口 ---- */
typedef u64 CORE_TICKS;

static u64 t_start, t_stop;

void start_time(void) { t_start = read_cycles(); }
void stop_time(void)  { t_stop  = read_cycles(); }
CORE_TICKS get_time(void) { return t_stop - t_start; }

/* ============================================================================
 * 结论上报 (W-01) —— tohost 是本平台唯一能被 tb **自动判定**的通道。
 * ----------------------------------------------------------------------------
 * 约定 (见 sim/tb_sw.sv:129-138):
 *   写 1      -> [tb_sw] 判 PASS, 并把日志缓冲区 dump 出来
 *   写非 0/1  -> [tb_sw] $fatal -> 回归变红
 *
 * 【为什么必须由程序自己写】以前不管程序算出什么都无条件打印
 * "RESULT : PASS", 于是算错也是绿灯 (早期 11 项里有 3 项的结论只靠人眼看日志；当前矩阵 12 项)。
 * 所以每个程序都必须把"算得对不对"编码进 tohost:
 *   全部自检通过 -> report_done()  (tohost = 1)
 *   任何自检失败 -> report_fail()  (tohost = 2, 并**自旋**)
 *
 * report_fail() 必须自旋: main 返回后 crt0.S 会无条件再写一次 tohost=1,
 * 若不挡住, 失败标志会被这一笔覆盖掉 (而覆盖发生在几个周期之后, 靠时序
 * 侥幸让 tb 看到 2 是不允许的 —— 判定必须与写法无关地确定)。
 * ========================================================================== */
#define TOHOST_OK   1u
#define TOHOST_FAIL 2u

void report_done(void) { TOHOST = TOHOST_OK; }

void report_fail(void) {
    TOHOST = TOHOST_FAIL;
    for (;;) { }          /* 见上方: 绝不让 crt0 再把 tohost 覆盖成 1 */
}

void _exit(int code) {
    (void)code;
    report_done();
    for (;;) { }
}
