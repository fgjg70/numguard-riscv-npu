#!/usr/bin/env python3
"""amo_vectors.py — RV32A 原子指令的**黄金向量生成器**

【为什么需要它】
A 扩展最凶的坑是 **min/max 有两套符号语义** (amomin/max 有符号,
amominu/maxu 无符号), 而这个错误**只在边界值上暴露** —— 用普通的小正数
测试会全绿。本人写 ALU 时就真把 amo_max 的三元方向写反过 (取成了 min)。

所以: 用**独立的参考实现**(本文件里的 Python 计算) 生成一组覆盖边界的
向量, 再由 testbench / 软件比对。**参考实现必须独立于被测实现** ——
本项目已经两次栽在"自己和自己比"上 (checksum_ref、gemm 的 hw_unpack),
所以这里刻意用 Python 重写一遍语义, 而不是复用 Verilog 的写法。

【输出】
  sim/prog/amo_vectors.hex  — 供 $readmemh 读入的向量表
  每条记录 4 个字: OP, OLD, RS2, EXPECT_NEW   (rd 一律等于 OLD, 单独校验)

【用法】
  python tools/amo_vectors.py                # 写到默认路径
  python tools/amo_vectors.py --check-only   # 只打印, 不写文件
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

M32 = 0xFFFFFFFF


def s32(v):
    """把 32 位无符号解释成有符号"""
    v &= M32
    return v - (1 << 32) if v & 0x80000000 else v


# ---------------------------------------------------------------------------
# 黄金参考语义 —— funct5 -> (名字, 新值计算)
# 注意: 这里**不复用** Verilog 的写法, 是照着 RISC-V 规范独立实现的
# ---------------------------------------------------------------------------
AMO_OPS = [
    (0b00001, "amoswap.w", lambda old, rs2: rs2),
    (0b00000, "amoadd.w",  lambda old, rs2: (old + rs2) & M32),
    (0b00100, "amoxor.w",  lambda old, rs2: old ^ rs2),
    (0b01100, "amoand.w",  lambda old, rs2: old & rs2),
    (0b01000, "amoor.w",   lambda old, rs2: old | rs2),
    # ---- 有符号比较 (amomin/max) ----
    (0b10000, "amomin.w",  lambda old, rs2: old if s32(old) < s32(rs2) else rs2),
    (0b10100, "amomax.w",  lambda old, rs2: rs2 if s32(old) < s32(rs2) else old),
    # ---- 无符号比较 (amominu/maxu) ----
    (0b11000, "amominu.w", lambda old, rs2: old if (old & M32) < (rs2 & M32) else rs2),
    (0b11100, "amomaxu.w", lambda old, rs2: rs2 if (old & M32) < (rs2 & M32) else old),
]

# ---------------------------------------------------------------------------
# 操作数对 —— 刻意挑**能让两套符号语义分道扬镳**的组合
# ---------------------------------------------------------------------------
PAIRS = [
    (0x00000000, 0x00000000, "全 0"),
    (0x00000001, 0x00000002, "普通小正数 (两套语义一致)"),
    (0xFFFFFFFF, 0x00000001, "-1 vs 1      -> 有符号 max=1; 无符号 max=-1"),
    (0x00000001, 0xFFFFFFFF, "1 vs -1      -> 上一条的反向"),
    (0x80000000, 0x7FFFFFFF, "最小负数 vs 最大正数 -> 两套语义完全相反"),
    (0x7FFFFFFF, 0x80000000, "上一条的反向"),
    (0x80000000, 0x80000000, "相等 (最小值)"),
    (0xFFFFFFFF, 0xFFFFFFFF, "相等 (-1)"),
    (0x80000000, 0x00000000, "负数 vs 0"),
    (0xFFFFFFFE, 0x80000001, "-2 vs 最小负数+1"),
    (0x80000001, 0xFFFFFFFE, "上一条的反向"),
    (0x0000FFFF, 0xFFFF0000, "无符号高位差异明显"),
]


def build():
    rows = []
    for funct5, name, fn in AMO_OPS:
        for old, rs2, why in PAIRS:
            rows.append((funct5, old & M32, rs2 & M32, fn(old & M32, rs2 & M32), name, why))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "sim", "prog", "amo_vectors.hex"))
    ap.add_argument("--check-only", action="store_true")
    a = ap.parse_args()

    rows = build()
    print("RV32A 黄金向量: %d 个算符 x %d 组操作数 = %d 条"
          % (len(AMO_OPS), len(PAIRS), len(rows)))
    print()

    # 把"两套语义不同"的那些单独标出来 —— 这些才是真正有判别力的用例:
    # 如果测试里只有小正数, min 与 minu 结果相同, **写反了也测不出来**。
    print("=== 有判别力的用例 (有符号/无符号结果不同) ===")
    ndiff = 0
    for old, rs2, why in PAIRS:
        mn  = old if s32(old) < s32(rs2) else rs2
        mnu = old if (old & M32) < (rs2 & M32) else rs2
        if mn != mnu:
            ndiff += 1
            print("  old=0x%08X rs2=0x%08X : amomin.w=0x%08X  amominu.w=0x%08X  <- %s"
                  % (old, rs2, mn, mnu, why))
    print("  共 %d/%d 组能区分有符号/无符号" % (ndiff, len(PAIRS)))
    print()

    if a.check_only:
        print("(--check-only: 不写文件)")
        return 0

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="ascii", newline="\n") as f:
        for funct5, old, rs2, exp, name, why in rows:
            # 4 个字一条: funct5, old, rs2, expect_new
            f.write("%08x\n" % funct5)
            f.write("%08x\n" % old)
            f.write("%08x\n" % rs2)
            f.write("%08x\n" % exp)
    print("已写出 %s  (%d 行 = %d 条 x 4 字)" % (a.out, len(rows) * 4, len(rows)))
    print()
    print("消费方式 (testbench/软件): 每条记录 4 个字 ——")
    print("   funct5 -> 组 AMO 编码 (instr[31:27])")
    print("   old    -> 内存里的旧值 (也是 rd 应当读到的值)")
    print("   rs2    -> 源操作数")
    print("   expect -> 应当写回内存的**新值**")
    return 0


if __name__ == "__main__":
    sys.exit(main())
