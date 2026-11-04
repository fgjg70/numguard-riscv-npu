/* ============================================================================
 * opt_probe.c — 隔离"哪个构造在 -Os/-O2 下被 miscompile"
 * ----------------------------------------------------------------------------
 * 背景: sw/nn_demo.c 在 -O0 下完全正确 (checksum 与主机端计算一致),
 *       但在 -Os/-O2 下矩阵内容就是错的。说明是**编译器/代码生成**问题,
 *       不是核的功能问题 (CoreMark 在 -Os 下 CRC 全对, 说明核本身没问题)。
 *
 * 本探针把候选构造拆开, 每个都算一个 FNV 校验和并打印期望值 (主机可算),
 * 逐项对照即可定位是哪一种表达式被编错。
 * ==========================================================================*/

#include <stddef.h>

typedef unsigned int u32;
typedef signed int   s32;

extern void uart_send_char(char c);
extern void report_done(void);

#define N 16

/* 主机端可复算的期望值 (见 tools/ 里同公式) */
static s32 t1[N][N];      /* 构造 1: 带按位与 + 减法的索引表达式 */
static s32 t2[N][N];      /* 构造 2: 纯线性索引表达式, 无按位与 */
static s32 t3[N][N];      /* 构造 3: 常量表赋值 */
static u32 t4[N];         /* 构造 4: 一维, 乘法链 */

static void puts_(const char *s) { while (*s) uart_send_char(*s++); }
static void puthex(u32 v) {
    puts_("0x");
    for (int i = 28; i >= 0; i -= 4) {
        u32 d = (v >> i) & 0xFu;
        uart_send_char((char)(d < 10 ? '0' + d : 'a' + d - 10));
    }
}
static void putu(u32 v) {
    char b[12]; int i = 11; b[11] = 0;
    if (!v) { uart_send_char('0'); return; }
    while (v) { b[--i] = (char)('0' + (v % 10u)); v /= 10u; }
    puts_(&b[i]);
}
static u32 fnv(const s32 *p, int n) {
    u32 h = 2166136261u;
    for (int i = 0; i < n; i++) { h ^= (u32)p[i]; h *= 16777619u; }
    return h;
}

/* 构造 5: 完整复刻 nn_demo 的 gemm_sw 循环形状 (i / k / j 顺序, 内层连续取 B) */
static s32 A5[N][N], B5[N][N], C5[N][N];

int main(void) {
    /* ---- 先打印关键对象的地址, 判断是否有重叠/越界 ---- */
    puts_("addr: A5="); puthex((u32)(size_t)&A5[0][0]);
    puts_(" C5=");      puthex((u32)(size_t)&C5[0][0]);
    puts_(" t1=");      puthex((u32)(size_t)&t1[0][0]);
    puts_("\n");

    /* 构造 5: 逐字节复刻 gemm_sw —— 与 nn_demo 唯一的差别是这里只跑一次 */
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            A5[i][j] = (s32)((i * 7 + j * 3) & 0x1F) - 16;
            B5[i][j] = (s32)((i * 5 + j * 11) & 0x0F) - 8;
        }
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) C5[i][j] = 0;
        for (int k = 0; k < N; k++) {
            s32 a = A5[i][k];
            for (int j = 0; j < N; j++) C5[i][j] += a * B5[k][j];
        }
    }
    puts_("  t5("); putu((u32)C5[0][0]); puts_(","); putu((u32)C5[0][1]);
    puts_(","); putu((u32)C5[N-1][N-1]);
    puts_(") fnv="); puthex(fnv(&C5[0][0], N * N)); puts_("\n");

    /* 构造 1: (i*7 + j*3) & 0x1F, 再减 16  —— nn_demo 用的就是这个 */
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            t1[i][j] = (s32)((i * 7 + j * 3) & 0x1F) - 16;

    /* 构造 2: 去掉按位与, 用取模 —— 同语义的另一种写法 */
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            t2[i][j] = (s32)((i * 7 + j * 3) % 32) - 16;

    /* 构造 3: 直接常量, 排除一切算术 */
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++)
            t3[i][j] = (s32)(i * N + j) - 32;

    /* 构造 4: 一维乘法链 */
    for (int i = 0; i < N; i++) t4[i] = (u32)(i * 11 + 5) * 7u;

    puts_("opt_probe N="); putu((u32)N); puts_("\n");
    puts_("  t1("); putu((u32)t1[0][0]); puts_(","); putu((u32)t1[N-1][N-1]);
    puts_(") fnv="); puthex(fnv(&t1[0][0], N * N)); puts_("\n");
    puts_("  t2("); putu((u32)t2[0][0]); puts_(","); putu((u32)t2[N-1][N-1]);
    puts_(") fnv="); puthex(fnv(&t2[0][0], N * N)); puts_("\n");
    puts_("  t3("); putu((u32)t3[0][0]); puts_(","); putu((u32)t3[N-1][N-1]);
    puts_(") fnv="); puthex(fnv(&t3[0][0], N * N)); puts_("\n");
    puts_("  t4("); putu(t4[0]); puts_(","); putu(t4[N-1]);
    puts_(") fnv="); puthex(fnv((const s32 *)t4, N)); puts_("\n");

    report_done();
    return 0;
}
