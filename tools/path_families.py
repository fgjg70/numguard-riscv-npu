#!/usr/bin/env python3
"""path_families.py - 按"路径族"统计 Vivado timing report, 而不是只看 top-1。

【为什么要这个工具】
本 session 连续三次"预测收益 >> 实测收益"(C-2 / C-12c / C-15), 共同根因是同一个:
**瓶颈是"族"而不是"某条路径"**。治好 top-1 的那条链, 不等于整族消失; 而如果 top-1
只是某族里的一条, 那么"修 top-1"的天花板就是 **Δ(top1, top2)**(PITFALLS R47)。
反过来, 如果 50 条最差路径**同源**(属于同一族), 那么一次结构改动可以同时治好 50 条 ——
那才是值得做的改动。这两种情况的处置完全相反, 所以**必须先看族分布**。

用法:
    python tools/path_families.py [report ...] [--top 50] [--by dest|source|pair] [--min-count 2]

默认 report = fpga/timing_paths.rpt (build.tcl 里 `report_timing -max_paths 50 -input_pins`)。
"""
import argparse
import glob
import os
import re
import sys

# 【GBK 控制台防护】本仓库在 Windows GBK 控制台下运行: print 里出现 =>/ℹ 这类非 GBK 字符会
# 抛 UnicodeEncodeError, 而且**往往发生在数据已经打印一半之后**(看起来像"跑崩了",
# 其实是编码)。fpga_workflow.py 早已这么做, 这里照抄。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_text(p):
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def norm_family(name: str) -> str:
    """把一条路径端点名归一成"族名"。

    目标是让"同一条链的不同 bit / 不同副本"落到同一个族:
      u_core/u_imem/rdata_reg_3/ADDRARDADDR[4]        -> u_core/u_imem/rdata_reg/ADDRARDADDR
      u_core/u_muldiv/prod_ss_reg__2/PCIN[0]          -> u_core/u_muldiv/prod_ss_reg/PCIN
      u_core/u_ex_mem/ctrl_reg[wb_src][1]_replica_4/C -> u_core/u_ex_mem/ctrl_reg[wb_src]/C
      u_core/u_id_ex/imm_reg[4]/CE                    -> u_core/u_id_ex/imm_reg/CE
    """
    if not name:
        return "?"
    s = re.sub(r"_replica(_\d+)?", "", name)          # 去掉 Vivado 的负载复制副本后缀
    s = re.sub(r"__\d+", "", s)                        # prod_ss_reg__2 -> prod_ss_reg
    parts = s.split("/")
    out = []
    for p in parts:
        # 去掉端点 bit / 位域下标:  foo_reg[3] -> foo_reg ;  ctrl[wb_src][1] -> ctrl[wb_src]
        p = re.sub(r"\[(\d+)\]$", "", p)
        # rdata_reg_3 -> rdata_reg (Vivado 对多位寄存器会加下标)
        p = re.sub(r"_(\d+)$", "", p)
        out.append(p)
    return "/".join(out)


def parse_paths(txt: str):
    """从 `report_timing` 文本里取出每条路径的 headline 字段。"""
    paths = []
    for blk in re.split(r"(?m)^\s*Slack \((?:MET|VIOLATED)\)", txt)[1:]:
        head = blk[:3000]
        def g(pat):
            m = re.search(pat, head, re.M)
            return m.group(1) if m else None
        # 注意真实格式是 `Slack (VIOLATED) :        -0.077ns` —— 冒号前**有空格**,
        # 切分后块首是 ` :  -0.077ns ...`。第一版漏了这个冒号, 于是所有路径都被跳过、
        # 报"没有解析到路径块"(自己踩的, 记在这里)。
        slack = g(r"^\s*:?\s*(-?[\d.]+)\s*ns")
        src = g(r"^\s*Source:\s*(\S+)")
        dst = g(r"^\s*Destination:\s*(\S+)")
        dp = g(r"^\s*Data Path Delay:\s*(-?[\d.]+)\s*ns")
        if slack is None or dst is None:
            continue
        lg = g(r"Data Path Delay:.*?logic\s*([\d.]+)\s*ns")
        rt = g(r"Data Path Delay:.*?route\s*([\d.]+)\s*ns")
        paths.append({
            "slack": float(slack),
            "source": src or "?",
            "dest": dst,
            "data_path": float(dp) if dp else None,
            "logic": float(lg) if lg else None,
            "route": float(rt) if rt else None,
        })
    return paths


def main() -> int:
    ap = argparse.ArgumentParser(
        description="按路径族统计 timing report (看族, 不要只看 top-1)")
    ap.add_argument("report", nargs="*",
                    help="timing report 路径 (默认 fpga/timing_paths.rpt; 支持 glob)")
    ap.add_argument("--top", type=int, default=50, help="只看最差的前 N 条 (默认 50)")
    ap.add_argument("--by", choices=["dest", "source", "pair"], default="dest",
                    help="按哪个端点归类 (默认 dest = 目的族)")
    ap.add_argument("--min-count", type=int, default=2, help="只打印 >= 该条数的族")
    a = ap.parse_args()

    files = a.report or [os.path.join(REPO, "fpga", "timing_paths.rpt")]
    expanded = []
    for f in files:
        g = glob.glob(f) if any(c in f for c in "*?[") else ([f] if os.path.isfile(f) else [])
        expanded += g
    if not expanded:
        print("  找不到 report; 先跑 `python tools\\fpga_workflow.py synth`")
        return 1

    for path in expanded:
        paths = parse_paths(read_text(path))
        if not paths:
            print(f"  {path}: 没有解析到路径块 (是否用了 -input_pins / report_timing?)")
            continue
        paths.sort(key=lambda p: p["slack"])
        sel = paths[:a.top]
        print("=" * 78)
        print(f"  {os.path.relpath(path, REPO)}   共 {len(paths)} 条, 取最差 {len(sel)} 条")
        print("=" * 78)
        worst = sel[0]
        print(f"  top-1 slack={worst['slack']:+.3f}  data_path={worst['data_path']:.3f}ns")
        if len(sel) > 1:
            d = sel[1]["slack"] - worst["slack"]
            print(f"  Δ(top1,top2) = {d:.3f} ns"
                  + ("   => 单修 top-1 的天花板只有这么大 (R47: 这是墙, 别改逻辑)"
                     if abs(d) < 0.05 else
                     "   => top-2 与 top-1 分属不同族, 单修 top-1 有意义"))
        print()

        def key(p):
            if a.by == "dest":
                return norm_family(p["dest"])
            if a.by == "source":
                return norm_family(p["source"])
            return norm_family(p["source"]) + "  ->  " + norm_family(p["dest"])

        groups = {}
        for p in sel:
            groups.setdefault(key(p), []).append(p)
        ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), min(x["slack"] for x in kv[1])))
        print(f"  按 {a.by} 归类 (只列 >= {a.min_count} 条的族):")
        print(f"    {'条数':>4}  {'worst slack':>11}  {'族'}")
        for k, v in ranked:
            if len(v) < a.min_count:
                continue
            print(f"    {len(v):>4}  {min(x['slack'] for x in v):>+11.3f}  {k}")
        single = sum(1 for k, v in ranked if len(v) == 1)
        big = max(len(v) for _, v in ranked) if ranked else 0
        print()
        print(f"  小结: 最大族 {big} 条 / 单条族 {single} 个 / 共 {len(ranked)} 个族")
        if big >= 0.6 * len(sel):
            print("  => **单一大族**: 一次结构改动可同时治多条 => 值得做结构修改")
        else:
            print("  => **分散**: 没有单一大族 => 结构改动收益有限, 应转物理/约束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
