#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mk_cm_hw.py -- 重新构建上板跑分镜像 sim/prog/cm_hw.hex。

【为什么需要它】tools/fpga_workflow.py 里印的"配方"缺了 FLAGS_STR，照抄会编译失败；
而 FLAGS_STR 的引号在 PowerShell 里会被吃掉。这里用 subprocess 的**列表**
形式传参（与 tools/run_coremark.py 完全相同的写法），一次成功。

【为什么要重调 ITERATIONS】CoreMark 的有效成绩要求运行 >= 10 秒。
本核变快后会跌破 10 秒（实测去掉那个冗余 IF/ID 寄存器把它从 10.16 s 拉到 9.91 s；
注意本核**始终是 5 级**，见 core.sv 头部口径说明），
所以**每次性能提升都要重算 ITERATIONS**：

    ITERATIONS_new = ceil(ITERATIONS_old * 10.0 / 实测秒数 * 1.05)   # 留 5% 余量

【2026-10-09 系统时钟 50 -> 70 MHz（MMCM 原语）后的取整】
R35 的公式按">=10 s"给下限；但**时钟变快不影响拍数**，只压缩墙钟时间：
    ticks(3000) = 536,943,384 / 1300 * 3000 = 1,239,100,117 拍（外推值，以实测为准）
    t(70 MHz)   = 1,239,100,117 / 70e6     = 17.70 s   （50 MHz 下同一镜像要 24.8 s）
所以 3000 迭代与频率无关地满足 >= 10 s，并且给未来继续提速留了很大余量。
**上界**：上板 tick 是 31 位计数器（见 mmio_subsys.sv），上限 2^31-1 = 2,147,483,647 拍
    => ITERATIONS_max = 1300 * 2,147,483,647 / 536,943,384 = 5,199 次
    => 3000 次用掉 57.7% 量程，安全。

用法:
    python tools/mk_cm_hw.py 3000
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PY = sys.executable

CM_SRC = ["core_main.c", "core_list_join.c", "core_matrix.c",
          "core_state.c", "core_util.c", "core_portme.c", "ee_printf.c"]


def read_clk_hz() -> int:
    """从 `fpga/top_a100t.sv` 的 `.CLK_HZ(<N>)` 读真实系统时钟 —— **单一事实来源**。

    【为什么必须读而不是写 70_000_000】(W-02)
    `sw/coremark/core_portme.h` 的 CLOCKS_PER_SEC 曾经停在 50 MHz (MMCM 之前的值),
    于是硬件侧"必须跑满 10 秒才算有效成绩"的判定**宽了 1.4 倍**
    (1300 迭代 @70 MHz 真实 7.67 s 会被算成 10.74 s 并判合法)。
    现在上板/评分构建靠 `-D HW_CLK_HZ=<真实频率>` 打开时长自证; 如果这里的频率与
    `top_a100t.sv` 的 MMCM 配置不一致, 自证就变成自欺。所以两处必须同源。
    """
    p = os.path.join(REPO, "fpga", "top_a100t.sv")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            m = re.search(r"\.CLK_HZ\s*\(\s*([\d_]+)\s*\)", f.read())
        if m:
            return int(m.group(1).replace("_", ""))
    except OSError:
        pass
    print("  [warn] 读不到 top_a100t.sv 的 .CLK_HZ(...) —— 退回 70 MHz")
    return 70_000_000


def read_ticks_per_iter():
    """从最近一次 CoreMark 仿真日志读"每迭代周期数" —— expect_tick 的唯一来源。

    CoreMark 的 `Total ticks` 是**周期数**, 与主频无关, 所以它随流水线语义变化:
      C-2 之前 413,033 拍/迭代; C-2+C-11 之后 460,311 拍/迭代 (+11.4%)。
    硬编码任何一个值都会让上板的 tick 偏差判据失真(低则假失败, 高则放过回归)。
    返回 None 表示读不到 —— 调用方必须 BLOCK, 不许退回常数。
    """
    log = os.path.join(REPO, ".fpga-cache", "logs", "tb_sw_coremark.log")
    # 【2026-10-16 加固】优先读**默认配置**的 canonical 副本。
    # 为什么: `verify` 会按顺序跑 默认/--a-ext/--imem_comb_read/--ras 并覆盖同名日志,
    # 最后留下的可能是 RAS 配置的数(实测 461,712 vs 默认 463,366, 差 0.36%),
    # 而这里算出的 expect_tick 是上板 tick 判据(门限 0.5%)的**唯一**基准 —— 用错配置
    # 会把大半余量静默吃掉。fpga_workflow.cmd_verify 现在会另存 `.default.log`。
    log_def = os.path.join(REPO, ".fpga-cache", "logs", "tb_sw_coremark.default.log")
    used = None
    for cand in (log_def, log):
        try:
            with open(cand, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        if re.search(r"Total ticks\s*:\s*\d+", txt):
            used = cand
            break
    if used is None:
        return None
    if used != log_def:
        print("  [warn] 每迭代拍数取自 %s (默认配置副本缺失) —— 若最近跑过宏配置, "
              "这个值可能不是默认配置的" % os.path.basename(used))
    else:
        print("  [tpi] 每迭代拍数取自默认配置副本 %s" % os.path.basename(used))
    m = re.search(r"Total ticks\s*:\s*(\d+)", txt)
    it = re.search(r"Iterations\s*:\s*(\d+)", txt)
    if not m or not it:
        return None
    n = int(it.group(1))
    if n <= 0:
        return None
    return int(m.group(1)) // n


def main():
    if len(sys.argv) > 1:
        iters = int(sys.argv[1])
    else:
        iters = 3000
    opt = "-Os"
    march = "rv32im"
    clk_hz = read_clk_hz()
    out = os.path.join(REPO, "sim", "prog", "cm_hw.hex")

    cmd = [PY, os.path.join(HERE, "build_sw.py")]
    # 与 run_coremark.py 的 CM_SRC **完全一致** (少一个 core_portme.c 就会
    # 报 undefined reference to seedN_volatile)
    for f in CM_SRC:
        p = os.path.join(REPO, "sw", "coremark", f)
        if os.path.exists(p):
            cmd.append(p)
        else:
            print("  [BLOCK] 缺源文件 %s" % p)
            return 1
    cmd += ["--include=" + os.path.join(REPO, "sw", "coremark"),
            "-D", "ITERATIONS=%d" % iters,
            # 【W-02 配套, 必须传】定义 HW_CLK_HZ => core_portme.h 把它当作
            # CLOCKS_PER_SEC 并打开 CM_MIN_SECS_CHECK(>=10 s 自证)。不传 = 硬件侧
            # 的时长门静默失效, 而打印出来的秒数还会按 50 MHz 算 (少报 1.4 倍)。
            "-D", "HW_CLK_HZ=%d" % clk_hz,
            # 必须是**一个** argv 元素, 且内含双引号 —— 这是 shell 里做不到的部分
            "-D", 'FLAGS_STR="%s -march=%s -mabi=ilp32"' % (opt, march),
            "--march=" + march, "--opt=" + opt, "-o", out]

    print("[mk_cm_hw] ITERATIONS=%d opt=%s march=%s" % (iters, opt, march))
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        print("BUILD FAILED")
        return 1
    for line in (r.stdout or "").splitlines():
        if "[cc]" in line or "imem" in line or "dmem" in line:
            print("  " + line)
    for f in ("cm_hw.hex", "cm_hw.dmem.hex"):
        p = os.path.join(REPO, "sim", "prog", f)
        print("  %-18s %s" % (f, "OK" if os.path.exists(p) else "MISSING"))

    # ------------------------------------------------------------------
    # 【X-09 单一来源】把"这个 ITERATIONS 对应的预期 tick / 等待秒数 / 时钟"
    # 写成一份小 JSON, 供 build.tcl / build_ila.tcl / ila_cap_done.tcl / cmd_hw 读取。
    # 在此之前这三个数散落在三处**手工同步**的魔数里:
    #   ila_cap_done.tcl 的 `after 22000`、fpga_workflow.py 的 `--expect-tick`、
    #   build_ila.tcl 注释里的 "17.70 s" —— R35 要求每次提速后重算 ITERATIONS,
    #   而三处只要漏改一处, 抓取就会落在程序跑完之前 (读到 score_result=0)。
    # 该文件被 .gitignore 忽略 (sim/prog/*.build.json), 是**构建产物**不是源码。
    # ------------------------------------------------------------------
    clk_hz = clk_hz                     # 与编译期 -D HW_CLK_HZ 同源 (见 read_clk_hz)
    # ------------------------------------------------------------------
    # 【expect_tick 必须从**当前**仿真日志推导, 不许硬编码】
    # 原来这里写的是 `536_943_384 / 1300`(= 413,033 拍/迭代 的旧基线)。
    # C-2(load-use 1→2 拍) + C-11(寄存 redirect) 把每迭代周期数抬到 **460,311**,
    # 于是那个硬编码值比真值低 11% ⇒ `hw` 的 tick 偏差判据(≤0.5%)会**报假失败**;
    # 反过来若它偏高, 就会**放过**一次真实的语义回归。两者都不可接受。
    # 这与 R35("提速后要重算 ITERATIONS")是同一族的另一半:
    #   **改流水线语义之后, 必须重算 expect_tick**(它是周期数, 不是时间)。
    # 唯一的来源是 CoreMark 自己的 `Total ticks` 与 `Iterations`。
    # ------------------------------------------------------------------
    tpi = read_ticks_per_iter()
    if not tpi:
        print("  [BLOCK] 读不到 CoreMark 的每迭代周期数 (需要 .fpga-cache/logs/tb_sw_coremark.log)")
        print("          先跑一次: python tools\\fpga_workflow.py verify --only tb_sw_coremark")
        print("          **不许回退成硬编码常数** —— 那会让上板判据失真。")
        return 1
    expect_tick = tpi * iters
    print("  [tick] 每迭代 %d 拍 (取自**默认配置** CoreMark 日志的 Total ticks/Iterations) x %d 迭代"
          % (tpi, iters))
    # 必须 > 实际运行时间: int(运行秒数) + 5, 与 build-repo 的契约一致
    wait_seconds = int(expect_tick / clk_hz) + 5

    # ------------------------------------------------------------------
    # 【赛题硬要求: 基准必须跑 >= 10 秒】——提频会**缩短**运行时间
    # 2026-10-11 提频到 128.125 MHz 时实测 10.61 s, 只剩 0.61 s 余量: 再往上
    # (约 >135 MHz) 就会违反赛题规则, 而"跑得更快"看起来完全像个好成绩 ——
    # 这类"指标变好但规则被违反"的形态正是本工程最需要拦的。
    # 需要的迭代数 = ceil(10 * clk_hz / 每迭代拍数)。
    # 这是 R35("提速后要重算 ITERATIONS")的**成品闸门**: 以前只靠人记得重算。
    # ------------------------------------------------------------------
    run_secs = expect_tick / clk_hz
    need_iters = -(-10 * clk_hz // tpi)          # ceil, 整数运算避免浮点边界
    if run_secs < 10.0:
        print("  [BLOCK] 这个镜像在 %.4f MHz 下只跑 **%.2f s** < 赛题要求的 10 s"
              % (clk_hz / 1e6, run_secs))
        print("          ITERATIONS 至少要 **%d** (当前 %d)。改大后重跑本脚本。"
              % (need_iters, iters))
        print("          注意: 加大迭代会同步抬高 expect_tick, 也要重新核对 31 位上限。")
        return 1
    # 当前迭代数下, 仍满足 >=10s 的**最高频率** (Hz): f_max = iters * tpi / 10
    max_f = iters * tpi / 10.0
    print("  [time] %d 迭代 @ %.4f MHz = **%.2f s** (赛题要求 >= 10 s, 余量 %.2f s)"
          % (iters, clk_hz / 1e6, run_secs, run_secs - 10.0))
    print("         => 当前 ITERATIONS=%d 下最高可提到 **%.1f MHz**; 再高就需要更多迭代"
          " (%.1f MHz 需要 %d)" % (iters, max_f / 1e6, clk_hz / 1e6, need_iters))

    # ------------------------------------------------------------------
    # 【31 位 tick 上限】上板 tick 是 mmio_subsys 里的 31 位计数器
    # (score_result 的 bit31 被软件借去当"结果有效"标志), 上限 2^31-1 = 2,147,483,647 拍。
    # 按实测基准外推: 1300 迭代 = 536,943,384 拍 => ITERATIONS_max = 5199。
    # 3000 迭代用掉 57.7%, 安全; 但**将来继续提速要扩容 ITERATIONS 时**, 一旦越过
    # 5,199 就会在硬件上回绕成一个小数字 —— 而它看起来完全像一个"变快了"的好成绩。
    # 这里硬拦住, 不让那个坑有机会出现 (R35 的配套闸门)。
    # ------------------------------------------------------------------
    TICK_MAX = 2 ** 31 - 1
    # 【安全余量】不许贴着上限取值: 实测硬件 tick 与仿真外推有 **0.151% 偏差**
    # (105MHz 档: 硬件 1,359,770,617 vs 预期 1,361,826,000)。方向虽有利, 但
    # "把 0.016% 的余量当安全"是赌 —— 一旦某次改动让硬件 tick 偏高就回绕,
    # 而回绕后看起来像个"变快了"的好成绩(正是本闸门要防的)。故留 0.5% 余量。
    TICK_SAFETY = 1.005
    # ⚠ 余量必须进**判据**, 不能只写在提示里 —— 我第一版就是只改了消息文本,
    #   结果 4730 仍被放过(等于余量没生效)。这类"闸门看着在、其实没拦"的形态
    #   比没有闸门更危险, 所以下面用 eff_max 做比较。
    eff_max = int(TICK_MAX / TICK_SAFETY)
    if expect_tick > eff_max:
        iters_max = int(TICK_MAX / (tpi * TICK_SAFETY))
        print("  [BLOCK] ITERATIONS=%d 的预期 tick %d 超过**含 %.1f%% 安全余量**的有效上限 %d"
              % (iters, expect_tick, (TICK_SAFETY - 1) * 100, eff_max))
        print("          按实测基准外推, ITERATIONS 最大只能到 **%d**。" % iters_max)
        print("          越过它 tick 会在硬件上回绕, 而结果看起来像'变快了' —— 必须同时")
        print("          扩宽 mmio_subsys 的计数器与 score_result 的打包方式。")
        return 1
    print("  [tick] 预期 %d / 上限 %d (占用 %.1f%%)"
          % (expect_tick, TICK_MAX, 100.0 * expect_tick / TICK_MAX))
    build_json = {
        "prog": "cm_hw", "iterations": iters,
        "expect_tick": expect_tick, "wait_seconds": wait_seconds,
        "clk_hz": clk_hz, "generated_by": "tools/mk_cm_hw.py",
    }
    jp = os.path.join(REPO, "sim", "prog", "cm_hw.build.json")
    with open(jp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(build_json, f, indent=2, ensure_ascii=False)
    print("  %-18s %s" % ("cm_hw.build.json", "OK"))
    print("  [build.json] ITERATIONS=%d expect_tick=%d wait_seconds=%ds clk=%dHz"
          % (iters, expect_tick, wait_seconds, clk_hz))
    return 0


if __name__ == "__main__":
    sys.exit(main())
