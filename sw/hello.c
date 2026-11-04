/* hello.c — 软件链路冒烟测试: 验证 crt0 / .data 搬运 / .bss 清零 / 日志 / 计数器 */
#include <stddef.h>

typedef unsigned int u32;
typedef unsigned long long u64;

#define MMIO_CYCLE_LO  (*(volatile u32 *)0xA0000008u)
#define MMIO_CYCLE_HI  (*(volatile u32 *)0xA000000Cu)

extern void uart_send_char(char c);
extern void report_done(void);
extern void report_fail(void);       /* W-01: 自检失败 -> tohost=2 -> tb 变红 */

/* 有初值的全局变量 -> 验证 crt0 的 .data 搬运 (初值在 imem 镜像里) */
static const char msg[] = "hello from rv32i!\n";   /* .rodata -> 必须放 DMEM */
static volatile u32 magic = 0xC0FFEE42u;  /* volatile 强制进 .data, 验证 crt0 搬运 */
volatile u32 table[4] = { 0x11111111u, 0x22222222u, 0x33333333u, 0x44444444u };
static u32 counter;                  /* .bss -> 必须为 0 */

/* ---- 期望值 (W-01) --------------------------------------------------------
 * 这些常量就是本程序的判据 —— 任何一个写错, 自检必须失败、tohost 必须变 2。
 * 独立验算: 0+1+...+999 = 999*1000/2 = 499500 (不是 499999 之类的近似值)。 */
#define EXPECT_SUM 499500u
static const u32  table_expect[4] = { 0x11111111u, 0x22222222u, 0x33333333u, 0x44444444u };
static const char msg_expect[]    = "hello from rv32i!\n";

static void puts_(const char *s) { while (*s) uart_send_char(*s++); }

static void putu(u32 v) {
    char b[12]; int i = 11; b[11] = 0;
    if (v == 0) { uart_send_char('0'); return; }
    while (v) { b[--i] = (char)('0' + (v % 10u)); v /= 10u; }
    puts_(&b[i]);
}

static void puthex(u32 v) {
    puts_("0x");
    for (int i = 28; i >= 0; i -= 4) {
        u32 d = (v >> i) & 0xFu;
        uart_send_char((char)(d < 10 ? '0' + d : 'a' + d - 10));
    }
}

static u64 read_cycles(void) {
    u32 lo, hi, hi2;
    do { hi = MMIO_CYCLE_HI; lo = MMIO_CYCLE_LO; hi2 = MMIO_CYCLE_HI; } while (hi != hi2);
    return ((u64)hi << 32) | (u64)lo;
}

int main(void) {
    u64 t0 = read_cycles();
    u32 ok = 1u;                                   /* W-01: 自检总线 */

    puts_(msg);                                    /* .rodata 字符串 */
    puts_("magic  = "); puthex(magic); uart_send_char('\n');   /* .data 初值 */
    puts_("bss    = "); putu(counter); uart_send_char('\n');   /* 应为 0 */

    if (magic != (u32)0xC0FFEE42u)  ok = 0u;       /* .data 初值被搬对了吗 */
    if (counter != 0u)              ok = 0u;       /* .bss 被清零了吗 */

    for (u32 i = 0; i < 1000u; i++) counter += i;
    puts_("sum    = "); putu(counter); uart_send_char('\n');   /* 0+..+999 = 499500 */
    if (counter != EXPECT_SUM)      ok = 0u;

    /* .data 数组: 验证搬运算的是初值而不是零 */
    puts_("table  = ");
    for (int i = 0; i < 4; i++) { puthex(table[i]); uart_send_char(' '); }
    uart_send_char('\n');
    for (int i = 0; i < 4; i++)
        if (table[i] != table_expect[i]) ok = 0u;

    /* .rodata 字符串: 逐字节比对 (含结尾 NUL) */
    for (unsigned i = 0; i < sizeof(msg_expect); i++)
        if ((unsigned char)msg[i] != (unsigned char)msg_expect[i]) ok = 0u;

    u64 t1 = read_cycles();
    puts_("cycles = "); putu((u32)(t1 - t0)); uart_send_char('\n');
    /* MMIO 自由运行计数器必须真的在走 (0 或倒流都说明 MMIO 通路坏了) */
    if (t1 <= t0)                   ok = 0u;

    /* ---- 结论必须编码进 tohost, 不能只打印 (W-01) ------------------------ */
    puts_("selfcheck_ok = "); putu(ok); uart_send_char('\n');
    if (ok) report_done();
    else    report_fail();
    return 0;
}
