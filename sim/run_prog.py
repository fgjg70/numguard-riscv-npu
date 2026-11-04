#!/usr/bin/env python3
"""run_prog.py — 迭代期单程序仿真器 (不写回归矩阵, 不改 verify-state.json)

用途: 新微基准/程序在"登记进回归矩阵"之前先跑起来看数。它复用
tools/fpga_workflow.py 的编译链、仿真锁与 clean_env, 所以与 `verify` 同口径
(同一份 RTL 清单、同一套 ModelSim 环境), 但**不碰** verify 的缓存状态文件 ——
避免迭代期的临时测试污染别人的回归状态。

用法:
    python sim/run_prog.py --imem sim/prog/lu_bench.hex ^
        --plus +lu_bubbles=1 +expect_lu_events=1024 +dump_words=2
    python sim/run_prog.py --imem sim/prog/cm_rv32im.hex --dmem sim/prog/cm_rv32im.dmem.hex ^
        --max-cycles 5000000

退出码: 0 = testbench 打印 RESULT : PASS, 1 = 其它。
"""

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))

import fpga_workflow as W  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--imem", required=True, help="程序 hex (仓库相对路径)")
    ap.add_argument("--dmem", default=None, help="数据镜像 hex (可选)")
    ap.add_argument("--top", default="tb_sw")
    ap.add_argument("--tb", default="sim/tb_sw.sv")
    ap.add_argument("--plus", nargs="*", default=[], help="额外的 +plusarg")
    ap.add_argument("--max-cycles", type=int, default=200000)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    for p in [a.imem] + ([a.dmem] if a.dmem else []):
        if not os.path.exists(W.abspath(p)):
            print(f"[run_prog] 缺文件: {p} (先 python tools/asm.py ... 或 tools/build_sw.py)")
            return 1

    # 【R1 兜底】.hex 比同名 .S 旧 => 跑的是旧镜像 (asm.py 汇编失败时不写文件,
    # 静默沿用上一次的 hex)。tools/build_sw.py --all 只覆盖它登记的镜像,
    # 微基准不在其中, 所以这里自己判一次。
    for p in [a.imem] + ([a.dmem] if a.dmem else []):
        src = os.path.splitext(W.abspath(p))[0] + ".S"
        if os.path.exists(src) and os.path.getmtime(src) > os.path.getmtime(W.abspath(p)):
            print(f"[run_prog] [FAIL] {p} 比 {os.path.basename(src)} 旧 —— 先重新汇编:")
            print(f"           python tools/asm.py {os.path.relpath(src, REPO).replace(os.sep, '/')} "
                  f"-o {os.path.relpath(W.abspath(p), REPO).replace(os.sep, '/')}")
            return 1

    with W.BuildLock(timeout=1800, path=W.SIM_LOCK_FILE, label="单程序仿真"):
        rc, out, workdir = W.compile_sim(W.canonical_rtl(), a.tb, ())
        if rc != 0:
            print(out)
            print("[run_prog] 编译失败 (vlog)")
            return 1
        vsim = os.path.join(W.MSIM, "vsim.exe")
        cmd = [vsim, "-c", "-do", "run -all; quit -f", "work." + a.top,
               "+imem=" + W.abspath(a.imem)]
        if a.dmem:
            cmd.append("+dmem=" + W.abspath(a.dmem))
        cmd += list(a.plus)
        cmd.append(f"+max_cycles={a.max_cycles}")
        rc, out2, dt = W.run(cmd, cwd=workdir, timeout=a.timeout)

    print(out2)
    ok = "RESULT : PASS" in out2
    print(f"[run_prog] {'RESULT : PASS' if ok else 'FAIL'}  ({dt:.1f}s, rc={rc})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
