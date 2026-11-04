#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trace_diff.py -- 逐拍执行轨迹比对 (诊断 RTL 改动的"第一处分歧")

【为什么需要它】
本工程三次改动 (实验 D / E5 / J) 都是"理论等价、实测失败", 而每次卡住的
地方都一样: **不知道从哪一拍开始跑偏**。只看最终 PASS/FAIL 只能知道"坏了",
看不到"哪一拍、哪个信号先不一致"。

tb_core.sv 在 `+illegal_trace` 下会逐拍打印:
    [trace] cycle | if_pc if_instr | ex_pc | ill stll bubl trap_enter redirect exc_req
本工具把两个日志里的这些行对齐, 报出**第一处分歧**以及分歧字段。

【用法】
    # 1) 改 RTL 之前, 先存一份基线轨迹
    python tools/fpga_workflow.py verify --force --only tb_core_trap
    copy .fpga-cache\\logs\\tb_core_trap.log .fpga-cache\\traces\\baseline_trap.log

    # 2) 改完 RTL 再跑一次, 然后比对
    python tools/trace_diff.py .fpga-cache/traces/baseline_trap.log .fpga-cache/logs/tb_core_trap.log

    # 也可以在任意两个日志之间比对 (自动只取 [trace] 行)
    python tools/trace_diff.py a.log b.log --context 3

退出码: 0 = 轨迹逐拍一致; 1 = 存在分歧; 2 = 用法/读取错误。
"""

import argparse
import os
import sys

TRACE_TAG = "[trace]"

# tb_core.sv 打印的字段顺序 (用于给出可读的字段名)
FIELD_NAMES = [
    "if_pc", "if_instr", "ex_pc",
    "illegal", "stall", "bubble", "trap_enter", "redirect", "exc_req",
]


def parse_trace(path):
    """读一个日志/轨迹文件, 返回 {cycle: (token, ...)}。"""
    if not os.path.isfile(path):
        raise IOError("trace file not found: %s" % path)
    rows = {}
    header_seen = False
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            i = line.find(TRACE_TAG)
            if i < 0:
                continue
            body = line[i + len(TRACE_TAG):].replace("|", " ")
            toks = body.split()
            if not toks:
                continue
            if not toks[0].isdigit():
                # 表头行: [trace] cycle | if_pc ...
                header_seen = True
                continue
            rows[int(toks[0])] = tuple(toks[1:])
    if not rows:
        raise IOError(
            "no [trace] rows in %s -- run the sim with +illegal_trace "
            "(tb_core / tb_sw do this via the 'verify --trace' helper)" % path)
    return rows, header_seen


def cmp_rows(a, b, cycle):
    """返回 (字段名, 基线值, 新值) 列表; 长度不同时只报共同部分。"""
    out = []
    n = min(len(a), len(b))
    for k in range(n):
        if a[k] != b[k]:
            name = FIELD_NAMES[k] if k < len(FIELD_NAMES) else "field%d" % k
            out.append((name, a[k], b[k]))
    if len(a) != len(b):
        out.append(("signed_int", "%d tokens" % len(a), "%d tokens" % len(b)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="逐拍执行轨迹比对 (找 RTL 改动的第一处分歧)")
    ap.add_argument("baseline", help="基线轨迹文件 (改动前)")
    ap.add_argument("candidate", help="待检轨迹文件 (改动后)")
    ap.add_argument("--context", type=int, default=2,
                    help="分歧处上下各多打印几拍 (默认 2)")
    ap.add_argument("--max-report", type=int, default=5,
                    help="最多列几处分歧 (默认 5, 0 = 全部)")
    args = ap.parse_args(argv)

    try:
        base, _ = parse_trace(args.baseline)
        cand, _ = parse_trace(args.candidate)
    except IOError as e:
        print("ERROR: %s" % e)
        return 2

    cycles = sorted(set(base) | set(cand))
    diffs = []
    for c in cycles:
        if c not in base:
            diffs.append((c, [("(missing in baseline)", "-", " ".join(cand[c]))]))
        elif c not in cand:
            diffs.append((c, [("(missing in candidate)", " ".join(base[c]), "-")]))
        else:
            d = cmp_rows(base[c], cand[c], c)
            if d:
                diffs.append((c, d))

    print("=" * 74)
    print("  逐拍轨迹比对")
    print("=" * 74)
    print("  基线   : %s  (%d 拍, cycle %d..%d)"
          % (args.baseline, len(base),
             min(base) if base else -1, max(base) if base else -1))
    print("  待检   : %s  (%d 拍, cycle %d..%d)"
          % (args.candidate, len(cand),
             min(cand) if cand else -1, max(cand) if cand else -1))

    if not diffs:
        print("\n  => 逐拍完全一致 (改动的执行行为与被测程序无关/等价)")
        return 0

    first = diffs[0][0]
    print("  分歧   : %d 拍不一致,  **第一处 = cycle %d**" % (len(diffs), first))
    print("")
    shown = diffs if args.max_report == 0 else diffs[:args.max_report]
    for c, items in shown:
        print("  ---- cycle %d ----" % c)
        for name, bv, cv in items:
            print("       %-12s baseline=%-12s candidate=%s" % (name, bv, cv))
        for k in range(1, args.context + 1):
            for cc, lbl in ((c - k, "前"), (c + k, "后")):
                if cc in base and cc in cand:
                    same = "same" if base[cc] == cand[cc] else "DIFF"
                    print("       [%s %d] %s  base=%s  cand=%s"
                          % (lbl, cc, same, " ".join(base[cc]),
                             " ".join(cand[cc])))
        print("")
    if len(diffs) > len(shown):
        print("  ... 还有 %d 处分歧 (用 --max-report 0 全看)" % (len(diffs) - len(shown)))

    print("  ")
    print("  => 改动的执行流从 cycle %d 起就不同了: 先看那一拍的 if_pc/ex_pc 与" % first)
    print("     各个控制位, 再去 RTL 里找是哪条通路在那之前改变了取指/重定向。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
