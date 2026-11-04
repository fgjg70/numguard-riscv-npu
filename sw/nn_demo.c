/* ============================================================================
 * nn_demo.c — 应用场景: 小型神经网络的推理核心 (GEMM 为主)
 * ----------------------------------------------------------------------------
 * 【为什么选它】赛题要求"应用场景构建"与"AI 模型性能"两项。一个能自证的
 * 应用场景需要满足三点: (a) 计算密集且可量化 (b) 结果可逐元素校验
 * (c) 有明确的"软件基线 vs 加速后"对比。**矩阵乘 (GEMM) 是神经网络推理里
 * 占比绝对主导的算子**, 所以本程序就是: 用 GEMM 搭一个两层 MLP 的前向传播,
 * 并把耗时按算子拆开测出来。
 *
 * 【本程序是"加速前基线"】纯软件实现, 跑在现有 RV32IM 核上 (无 M 扩展之外的
 * 加速)。它的作用是给出参照周期数 —— 之后加 GEMM 加速单元时, 加速比才有
 * 可比的基准。
 *
 * 尺寸选 16x16: dmem 的 .data/.bss 只有 8KB (见 link.ld 的 ASSERT),
 * 三块 16x16 的 int32 就是 3*1024 = 3072 字节, 留足余量。
 * GEMM 的 MAC 数 = 16^3 = 4096 次, 足以让循环体主导耗时。
 *
 * 输出 (UART):
 *   "ACCEL: gemm_cycles=<n> checksum=<hex> ok=<0|1>" 之类的行 —— 由 tb 比对
 * ==========================================================================*/

#include <stddef.h>

typedef unsigned int   u32;
typedef signed int     s32;
typedef unsigned long long u64;

#define MMIO_CYCLE_LO  (*(volatile u32 *)0xA0000008u)
#define MMIO_CYCLE_HI  (*(volatile u32 *)0xA000000Cu)

/* ---- GEMM 加速单元 MMIO (见 rv32_pkg.sv 的 MMIO_GEMM_*) ---- */
#define G_CTRL    (*(volatile u32 *)0xA0000040u)
#define G_STATUS  (*(volatile u32 *)0xA0000044u)
#define G_M       (*(volatile u32 *)0xA0000048u)
#define G_K       (*(volatile u32 *)0xA000004Cu)
#define G_N       (*(volatile u32 *)0xA0000050u)
#define G_CYCLES  (*(volatile u32 *)0xA0000054u)
#define G_DBGA    (*(volatile u32 *)0xA0000058u)
#define G_DBGV    (*(volatile u32 *)0xA000005Cu)
#define G_DBGW    (*(volatile u32 *)0xA0000060u)
#define G_DBGK    (*(volatile u32 *)0xA0000064u)
#define G_ARENA   ((volatile u32 *)0xA0000400u)
/* 缓冲窗口内的字偏移: A=0..63, B=64..127, C=128..383 */
#define G_A_W(w)  G_ARENA[(w)]
#define G_B_W(w)  G_ARENA[64u + (w)]
#define G_C_W(w)  G_ARENA[128u + (w)]

extern void uart_send_char(char c);
extern void report_done(void);
extern void report_fail(void);     /* W-01: 自检失败 -> tohost=2 -> tb 变红 */

/* ---- 期望值 (W-01) --------------------------------------------------------
 * 256 个字全为 1 时 checksum() 的 FNV-1a 结果。**独立复算过** (不读本程序的
 * 输出): 用 Python 逐字复刻 checksum() 的 `h ^= 1; h *= 16777619` 迭代 256 次
 * 得 0x78d762c5, 与运行时打印一致。写错这个常量, 回归必须变红。 */
#define PROBE_ALL_ONES_FNV 0x78d762c5u

/* ---- 被测尺寸 (改这里即可调规模) ---- */
#define N 16

/* 三块矩阵: 都是 .bss (初值由 init_matrices 填), 避免依赖 dmem 镜像里的初值 */
static s32 A[N][N];
static s32 B[N][N];
static s32 C[N][N];

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

/* ---- 用确定性公式填矩阵, 保证每次跑的数一样, 可比对 ---- */
static void init_matrices(void) {
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) {
            A[i][j] = (s32)((i * 7 + j * 3) & 0x1F) - 16;   /* -16..15, 有正有负 */
            B[i][j] = (s32)((i * 5 + j * 11) & 0x0F) - 8;   /*  -8..7 */
        }
    }
}

/* ---- 软件 GEMM 基线: 标准三重循环 (i,k,j 顺序, 内层连续访存) ---- */
static void gemm_sw(void) {
    for (int i = 0; i < N; i++) {
        for (int j = 0; j < N; j++) C[i][j] = 0;
        for (int k = 0; k < N; k++) {
            s32 a = A[i][k];
            for (int j = 0; j < N; j++) C[i][j] += a * B[k][j];
        }
    }
}

/* ---- 校验和 A: 对"软件 GEMM 写出的 C 矩阵"求 FNV-1a ---- */
static u32 checksum(void) {
    u32 h = 2166136261u;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            h ^= (u32)C[i][j];
            h *= 16777619u;
        }
    return h;
}

/* ---- 校验和 B: **自己独立算一遍** (long long 累加, 不读 C 矩阵) ----
 * 【为什么必须独立算】第一版这里错写成"再对 C 求一次校验和", 于是 A 与 B
 * 恒等, match 永远是 1 —— 一个**永远为真、什么也证明不了**的检查。
 * 参考实现必须有自己的累加器与自己的乘法路径, 这样两者相等才说明
 * "C 矩阵的内容 == 逐个元素独立计算的结果"。
 * 用 long long 累加同时排除"中间溢出把错误掩盖掉"的可能。 */
static u32 checksum_ref(void) {
    u32 h = 2166136261u;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            long long acc = 0;
            for (int k = 0; k < N; k++) acc += (long long)A[i][k] * (long long)B[k][j];
            h ^= (u32)acc;
            h *= 16777619u;
        }
    return h;
}

/* ---- 逐元素比对: 返回不匹配的元素个数 (比"校验和相等"更强) ---- */
static u32 count_mismatch(void) {
    u32 bad = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            long long acc = 0;
            for (int k = 0; k < N; k++) acc += (long long)A[i][k] * (long long)B[k][j];
            if ((long long)C[i][j] != acc) bad++;
        }
    return bad;
}

/* ---- 加速路径 -------------------------------------------------------------
 * 把 A/B 打包成 int8 推进加速单元的本地缓冲, 启动, 轮询完成, 把 C 读回来。
 *
 * 【为什么软件要自己打包】加速单元的缓冲按 int8 存放 (每字 4 个), 所以软件
 * 要把 s32 截成 8 位再拼字。这也正是"本核 dmem 单读口"带来的代价 ——
 * 数据搬运必须由 CPU 做, 而且这部分开销要如实算进加速比 (下面分开统计)。
 * --------------------------------------------------------------------------*/
static void gemm_hw_pack(void) {
    for (int w = 0; w < 64; w++) {
        u32 pa = 0, pb = 0;
        for (int b = 0; b < 4; b++) {
            int idx = w * 4 + b;          /* A/B 都是 16x16 行主序 -> 线性下标 */
            pa |= ((u32)(A[idx / N][idx % N] & 0xFF)) << (8 * b);
            pb |= ((u32)(B[idx / N][idx % N] & 0xFF)) << (8 * b);
        }
        G_A_W(w) = pa;
        G_B_W(w) = pb;
    }
}

/* 加速单元是否在场: 往 A 窗口写已知值再读回。
 * 【为什么必须先探活】若加速单元没被综合进去 (默认 GEMM_EN 关), 整个
 * MMIO_GEMM_* 窗口都读回 0, 而 gemm_hw_run() 的轮询会**永远等不到 done** ——
 * 死循环卡住。软件不能假设外设一定存在。 */
static int gemm_hw_present(void) {
    G_A_W(0) = 0xDEADBEEFu;
    return G_A_W(0) == 0xDEADBEEFu;
}

static u32 gemm_hw_run(void) {
    G_M = N; G_K = N; G_N = N;
    G_CTRL = 1;                            /* bit0 = start */
    /* 轮询完成, 但**必须有超时** —— 外设没响应时不能把 CPU 卡死。
     * 这个上限远超正常耗时 (实测 4608 拍), 只用来兜住"外设不在/坏了"。 */
    u32 guard = 0;
    while (((G_STATUS & 2u) == 0u) && (guard < 2000000u)) guard++;
    return G_CYCLES;
}

static void gemm_hw_unpack(void) {
    for (int w = 0; w < (N * N); w++)
        C[w / N][w % N] = (s32)G_C_W(w);
}

int main(void) {
    u64 t0, t1;
    /* W-01: 自检结果 —— 必须一路传到结尾并编码进 tohost, 否则算错也是绿灯 */
    u32 probe_ok = 0u;      /* 最小读写链 + 校验和自检 */
    u32 sw_ok    = 0u;      /* 软件 GEMM 对独立参考实现 */

    /* ---- 先做一组自检, 把"是软件 bug 还是核的 bug"分清楚 ----
     * 64 位乘法会调 libgcc 的 __muldi3 (RV32 没有 64 位乘), 而 32 位乘走 M 扩展。
     * 两者的正确性必须分开验 —— 否则矩阵对不上时无从判断是谁的问题。 */
    {
        s32 a32 = 12345, b32 = -6789;
        s32 p32 = a32 * b32;                       /* -> mul (M 扩展) */
        long long a64 = 123456789LL, b64 = -987654321LL;
        long long p64 = a64 * b64;                 /* -> __muldi3 (libgcc) */
        puts_("selfcheck: mul32=");  puthex((u32)p32);
        puts_(" expect=");           puthex((u32)(12345 * -6789));
        puts_(" mul64_lo=");         puthex((u32)(unsigned long long)p64);
        puts_(" expect_lo=");        puthex((u32)(unsigned long long)(123456789LL * -987654321LL));
        puts_("\n");
    }

    puts_("nn_demo: GEMM ");
    putu((u32)N); puts_("x"); putu((u32)N); puts_(" (software baseline)\n");

    init_matrices();

    /* ---- 分步自证: 先确认"数组读写+校验和"这条链本身是通的 ----
     * 踩过的坑: 第一版跑出来 C 恒为全 0 (校验和正好等于"256 个 0 的 FNV"),
     * 但看不出是"没写"还是"读错地方"。所以这里先做一次最小闭环:
     * 用最简单的循环写一个已知值, 立即读回并算校验和, 与手算期望比对。
     * 这样后面 C 若再全 0, 就能确定问题在 gemm_sw 而不在读写链。 */
    {
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) C[i][j] = 1;
        u32 h1 = checksum();                       /* 256 个 1 的 FNV-1a */
        puts_("probe: all_ones_checksum="); puthex(h1);
        puts_(" expect=");                  puthex(PROBE_ALL_ONES_FNV);
        puts_(" probe_ok=");                putu(h1 == PROBE_ALL_ONES_FNV ? 1u : 0u);
        puts_("\n");
        probe_ok = (h1 == PROBE_ALL_ONES_FNV) ? 1u : 0u;   /* W-01: 判据 */
        for (int i = 0; i < N; i++) for (int j = 0; j < N; j++) C[i][j] = 0;
    }

    puts_("  A[0][0]="); putu((u32)A[0][0]); puts_(" B[0][0]="); putu((u32)B[0][0]);
    puts_(" A[1][2]="); putu((u32)A[1][2]); puts_("\n");

    t0 = read_cycles();
    gemm_sw();
    t1 = read_cycles();

    puts_("  after_gemm C[0][0]="); putu((u32)C[0][0]);
    puts_(" C[0][1]="); putu((u32)C[0][1]);
    puts_(" C[15][15]="); putu((u32)C[15][15]); puts_("\n");

    u32 cs  = checksum();
    u32 ref = checksum_ref();
    u32 bad = count_mismatch();

    /* 再打一次 C 的前几个元素: 若 checksum 之前正确、之后变 0, 说明是
     * 后面某个调用把 C 冲掉了 (栈溢出踩 .bss 是首要嫌疑)。 */
    puts_("  post_checksum C[0][0]="); putu((u32)C[0][0]);
    puts_(" C[0][1]="); putu((u32)C[0][1]); puts_("\n");

    puts_("  gemm_cycles="); putu((u32)(t1 - t0)); puts_("\n");
    puts_("  macs=");        putu((u32)(N * N * N)); puts_("\n");
    puts_("  cycles_per_mac=");
    /* 定点显示, 保留两位: 避免引入浮点 (本核无 F 扩展) */
    putu((u32)(t1 - t0) / (u32)(N * N * N)); puts_(".");
    putu((((u32)(t1 - t0) * 100u) / (u32)(N * N * N)) % 100u); puts_("\n");
    puts_("  checksum=");     puthex(cs);  puts_("\n");
    puts_("  checksum_ref="); puthex(ref); puts_("\n");
    puts_("  mismatch=");     putu(bad);   puts_("\n");
    puts_("  sw_match=");     putu((cs == ref && bad == 0u) ? 1u : 0u); puts_("\n");
    sw_ok = (cs == ref && bad == 0u) ? 1u : 0u;        /* W-01: 判据 */

    /* ======================================================================
     * 加速路径: 同一个问题, 走 GEMM 加速单元
     * 判据: 读回的 C 必须与**独立参考实现**逐元素一致 (不是与软件结果比,
     *       虽然两者应当相同 —— 用独立参考更硬)。
     * ====================================================================*/
    u64 h0, h1, h2;
    /* 【先探活, 再决定跑不跑】加速单元默认未综合进评分版 (GEMM_EN 关),
     * 此时整个 MMIO_GEMM_* 窗口读回 0。不在场就明确报告"未测试" ——
     * 而不是让轮询卡死、也不是报一堆看起来像功能失败的假失败。
     * (放在启动**之前**: 读 STATUS 会清 done 标志, 放后面会把完成标志吃掉。) */
    if (!gemm_hw_present()) {
        puts_("  --- accelerator ---\n");
        puts_("  not_present=1 (未综合进本 bitstream; 设 GEMM_EN=1 重新构建)\n");
        /* W-01: 加速单元不在场只说明"这条路径没测", 软件路径的自检仍然必须判 */
        puts_("  verdict_sw_ok="); putu(probe_ok && sw_ok); puts_("\n");
        if (probe_ok && sw_ok) report_done();
        else                   report_fail();
        return 0;
    }
    u32 rb = 0xDEADBEEFu;

    /* 【先把软件结果留下来再跑硬件】踩过的坑: gemm_hw_unpack() 会把 C 覆盖成
     * 硬件结果, 而 count_mismatch()/逐行直方图是拿 C 与"独立参考"比的 ——
     * 于是它们实际在**用硬件结果和硬件结果比**, 恒等于自己跟自己比,
     * 什么也证明不了。所以必须在 C 被覆盖前把软件基准存下来。 */
    static s32 C_sw[N][N];
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) C_sw[i][j] = C[i][j];
    u32 cs_sw = checksum();

    h0 = read_cycles();
    gemm_hw_pack();
    u32 accel_cycles = gemm_hw_run();
    gemm_hw_unpack();                 /* 此后 C = 硬件结果 */
    h2 = read_cycles();

    /* 硬件结果 vs 独立参考实现 (不经过 C_sw, 直接重算) */
    u32 bad_hw = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) {
            long long acc = 0;
            for (int k = 0; k < N; k++) acc += (long long)A[i][k] * (long long)B[k][j];
            if ((long long)C[i][j] != acc) bad_hw++;
        }
    /* 硬件结果 vs 软件结果 (两条独立实现互证) */
    u32 diff_sw = 0;
    for (int i = 0; i < N; i++)
        for (int j = 0; j < N; j++) if (C[i][j] != C_sw[i][j]) diff_sw++;

    u32 cs_hw = checksum();

    puts_("  --- accelerator ---\n");
    puts_("  probe alive="); putu(rb == 0xDEADBEEFu ? 1u : 0u); puts_("\n");
    /* 读回规模寄存器: 若 M/K/N 与写入不符, 说明寄存器通路有问题,
     * 后面的"结果不对"就只是它的下游现象。 */
    puts_("  regs M/K/N="); putu(G_M); puts_("/"); putu(G_K);
    puts_("/"); putu(G_N); puts_("\n");
    /* 把 A/B 缓冲里第 i 行对应的**第一个字**从硬件读回来。
     * 期望: A 的第 i 行 = 字 i*4。若第 0 行有值而其余行为 0, 说明"存"坏了;
     * 若都有值, 说明问题在 gemm 的 a_byte_idx 算法, 不在缓冲。 */
    puts_("  buf A row0..:");
    for (int i = 0; i < N; i++) { puts_(" "); puthex(G_A_W(i * 4)); }
    puts_("\n");
    puts_("  dbg A_off="); puthex(G_DBGA);
    puts_(" a_val/b_val="); puthex(G_DBGV); puts_("\n");
    puts_("  dbg wr_cnt/idx="); puthex(G_DBGW);
    puts_(" kn="); puthex(G_DBGK); puts_("\n");
    /* 直接按字读回 C 的若干**代表性**条目 (第 0 个字与最后一个字):
     * 写回计数已证明做了 256 次、最后下标 255, 所以只要这些字读回是 0,
     * 就说明问题在"c_buf 的读通路"而不是写通路。 */
    puts_("  C[0]=");   putu(G_C_W(0));
    puts_(" C[1]=");    putu(G_C_W(1));
    puts_(" C[255]=");  putu(G_C_W(255));
    puts_("\n");
    /* 【窗口测绘】在 C 区的代表性字地址上写已知值再读回 —— 一次划出
     * "arena 窗口的哪个范围是通的"。比继续猜位宽直接得多。 */
    {
        const u32 probes[6] = { 0u, 16u, 64u, 200u, 255u, 300u };
        puts_("  C win map:");
        for (int t = 0; t < 6; t++) {
            u32 w = probes[t];
            G_C_W(w) = 0xAAAA0000u + w;
            puts_(" "); putu(w); puts_("="); puthex(G_C_W(w));
        }
        puts_("\n");
    }
    puts_("  buf B row0..:");
    for (int i = 0; i < N; i++) { puts_(" "); puthex(G_B_W(i * 4)); }
    puts_("\n");
    puts_("  accel_cycles=");  putu(accel_cycles);        puts_("\n");
    puts_("  ramp_cycles=");   putu((u32)(h2 - h0));     puts_("\n");
    /* 逐点看前几个元素: 排错时"哪里开始不对"比"有多少不对"有用得多 */
    puts_("  dump C[0][0..5] sw/hw:");
    for (int j = 0; j < 6; j++) {
        long long acc = 0;
        for (int k = 0; k < N; k++) acc += (long long)A[0][k] * (long long)B[k][j];
        puts_(" "); putu((u32)acc); puts_("/"); putu((u32)C[0][j]);
    }
    puts_("\n");
    puts_("  dump C[1][0..3] sw/hw:");
    for (int j = 0; j < 4; j++) {
        long long acc = 0;
        for (int k = 0; k < N; k++) acc += (long long)A[1][k] * (long long)B[k][j];
        puts_(" "); putu((u32)acc); puts_("/"); putu((u32)C[1][j]);
    }
    puts_("\n");
    puts_("  checksum_hw=");   puthex(cs_hw);            puts_("\n");
    puts_("  mismatch_hw=");   putu(bad_hw);             puts_("\n");
    puts_("  diff_vs_sw=");    putu(diff_sw);            puts_("\n");
    /* 每行错几个 —— "错在哪里"比"错多少"有用得多:
     * 集中在某几行 -> 行索引推进有问题; 每行都错同样的列 -> 列索引/B 缓冲有问题。 */
    puts_("  hw_bad_per_row:");
    for (int i = 0; i < N; i++) {
        u32 bad = 0;
        for (int j = 0; j < N; j++) {
            long long acc = 0;
            for (int k = 0; k < N; k++) acc += (long long)A[i][k] * (long long)B[k][j];
            if ((long long)C[i][j] != acc) bad++;
        }
        puts_(" "); putu(bad);
    }
    puts_("\n");
    /* 逐点看第 0 行与第 8 行 (硬件在 8 行之后开始错) —— 直接看数值 */
    puts_("  row0 ref/hw:");
    for (int j = 0; j < 6; j++) {
        long long acc = 0;
        for (int k = 0; k < N; k++) acc += (long long)A[0][k] * (long long)B[k][j];
        puts_(" "); putu((u32)acc); puts_("/"); putu((u32)C[0][j]);
    }
    puts_("\n");
    puts_("  row8 ref/hw:");
    for (int j = 0; j < 6; j++) {
        long long acc = 0;
        for (int k = 0; k < N; k++) acc += (long long)A[8][k] * (long long)B[k][j];
        puts_(" "); putu((u32)acc); puts_("/"); putu((u32)C[8][j]);
    }
    puts_("\n");
    puts_("  hw_match=");      putu(bad_hw == 0u ? 1u : 0u); puts_("\n");

    /* 加速比: 软件 GEMM 周期 / 加速单元自报周期 (定点两位) */
    {
        u32 sw = (u32)(t1 - t0);
        puts_("  speedup_x100=");
        if (accel_cycles == 0u) { puts_("inf\n"); }
        else {
            putu(sw / accel_cycles); puts_(".");
            putu(((sw * 100u) / accel_cycles) % 100u); puts_("\n");
        }
        /* 含搬运在内的端到端对比 (更诚实的口径) */
        puts_("  e2e_speedup_x100=");
        if ((u32)(h2 - h0) == 0u) { puts_("inf\n"); }
        else {
            putu(sw / (u32)(h2 - h0)); puts_(".");
            putu(((sw * 100u) / (u32)(h2 - h0)) % 100u); puts_("\n");
        }
    }

    /* ---- 结论必须编码进 tohost, 不能只打印 (W-01) ------------------------
     * 硬件路径的判据是"逐元素 vs 独立参考"以及"vs 软件结果"两者都为 0;
     * 软件路径的判据在上面的 sw_match。任何一条不过 -> tohost=2 -> tb 变红。 */
    {
        u32 all_ok = probe_ok && sw_ok && (bad_hw == 0u) && (diff_sw == 0u);
        puts_("  verdict_probe="); putu(probe_ok);
        puts_(" sw=");             putu(sw_ok);
        puts_(" hw_vs_ref=");      putu(bad_hw == 0u ? 1u : 0u);
        puts_(" hw_vs_sw=");       putu(diff_sw == 0u ? 1u : 0u);
        puts_(" all_ok=");         putu(all_ok); puts_("\n");
        if (all_ok) report_done();
        else        report_fail();
    }
    return 0;
}
