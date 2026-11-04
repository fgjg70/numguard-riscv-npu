#!/usr/bin/env python3
"""sweep_clk.py -- 时钟小阶梯 (安全版, 替代被正则改坏 RTL 的 PowerShell 版)。

【为什么用 Python 而不是 PowerShell 的 -replace】
我用 PowerShell 的 `Get-Content -Raw` + `-replace` + WriteAllText 改 RTL 时,
`.CLK_HZ` 那一行与下一行被**合并**了(原因未完全定位, 嫌疑是 -Raw 与 CRLF 的交互),
结果 Vivado 报 `Synth 8-2715 syntax error near .`, 白跑两个点。
⇒ 教训: **不要用 shell 正则对 RTL 做文本手术**; 这里改用 Python, 并且:
  ① 显式 `newline=""` 原样保留换行;
  ② 每处替换**断言恰好命中 1 次** (静默的 0 次替换 = 配置根本没生效,
     而 2 次替换会改到别的地方 —— 两种都必须立刻报错);
  ③ 每次替换后**自证 RTL/XDC/CLK_HZ 三处同源**。

目标: 在"支配族已正余量、只剩 2 条 id_ex_fwd_*_reg/D 离群"的树上,
找出**确实过门且余量足够上板**的那一档 (131.25 差 0.077 / 129.6875 差 0.024)。
"""
import io
import os
import re
import subprocess
import sys

REPO = r"D:\USER\Desktop\FPGA\FPGA"
TOP = os.path.join(REPO, "fpga", "top_a100t.sv")
XDC = os.path.join(REPO, "fpga", "daVinciPro.xdc")


def sub1(text, pat, rep, what):
    new, n = re.subn(pat, rep, text)
    if n != 1:
        raise SystemExit(f"[BLOCK] {what}: 期望恰好替换 1 处, 实际 {n} 处")
    return new


def set_clk(mult, num, den, hz, freq):
    t = io.open(TOP, encoding="utf-8", newline="").read()
    t = sub1(t, r"\.CLKFBOUT_MULT_F\s*\([0-9.]+\)", f".CLKFBOUT_MULT_F    ({mult})", "CLKFBOUT_MULT_F")
    t = sub1(t, r"\.CLKOUT0_DIVIDE_F\s*\([0-9.]+\)", ".CLKOUT0_DIVIDE_F   (8.0)", "CLKOUT0_DIVIDE_F")
    t = sub1(t, r"\.CLK_HZ\s*\([0-9_]+\)", f".CLK_HZ  ({hz})", "CLK_HZ")
    io.open(TOP, "w", encoding="utf-8", newline="").write(t)
    x = io.open(XDC, encoding="utf-8", newline="").read()
    # ⚠ 必须锚到**行首缩进**的那一行(真正的 create_generated_clock),
    #   否则会同时命中上面注释里的 `#   XDC : -multiply_by ...` (实测命中 2 处,
    #   被 sub1 的"恰好 1 处"断言挡住 —— 这就是那道断言存在的意义)。
    x = sub1(x, r"(?m)^\s+-multiply_by\s+[0-9]+\s+-divide_by\s+[0-9]+",
             f"  -multiply_by {num} -divide_by {den}", "XDC ratio")
    io.open(XDC, "w", encoding="utf-8", newline="").write(x)
    a = 50.0 * float(mult) / 8.0
    b = 50.0 * float(num) / float(den)
    if abs(a - b) > 1e-9 or abs(a - freq) > 1e-6:
        raise SystemExit(f"[BLOCK] 三处不同源: RTL={a} XDC={b} 目标={freq}")
    print(f"  [clk] MULT_F={mult} -> VCO {50*float(mult):.4f}, /8 = {a:.4f} MHz "
          f"(XDC {num}/{den})  周期 {1000.0/a:.4f} ns  OK")


PTS = [
    # (tag, MULT_F, XDC num, XDC den, CLK_HZ literal, 目标 MHz)
    ("clk128", "20.5", "41", "16", "128_125_000", 128.125),
    ("clk125", "20.0", "5", "2", "125_000_000", 125.000),
    # ILA 工程流的上限 ≈121 MHz(它比评分流多一个调试核, 实测差 0.434ns),
    # 为了让**上板验证覆盖交付配置**, 交付档必须落在 ILA 流能过门的位置。
    ("clk118", "19.0", "19", "8", "118_750_000", 118.750),
    ("clk117", "18.75", "75", "32", "117_187_500", 117.1875),
]

print("=== 时钟小阶梯 (目标: 确实过门且余量足够上板的档) ===")
print("    参考: C-12a @131.25 = -0.077 ; @129.6875 = -0.024 (支配族已 +0.019, 仅 2 条离群)")


def _apply_only(tag_want):
    """`python sweep_clk.py apply clk128` —— 只应用某一档, 不跑综合。
    给"选定档位后重建镜像/上板"用, 避免手写文件编辑(手写审计成本高且易错)。"""
    for tag, mult, num, den, hz, freq in PTS:
        if tag == tag_want:
            set_clk(mult, num, den, hz, freq)
            print(f"  [apply] {tag} = {freq} MHz 已写入 RTL + XDC")
            return
    raise SystemExit(f"[BLOCK] 未知档位 {tag_want}; 可用: {[p[0] for p in PTS]}")


if len(sys.argv) > 1:
    if sys.argv[1] == "apply":
        if len(sys.argv) < 3:
            raise SystemExit("[BLOCK] 用法: sweep_clk.py apply <tag>")
        _apply_only(sys.argv[2])
        sys.exit(0)
    raise SystemExit("[BLOCK] 用法: sweep_clk.py [apply <tag>]")

for tag, mult, num, den, hz, freq in PTS:
    set_clk(mult, num, den, hz, freq)
    out = subprocess.run([sys.executable, os.path.join(REPO, "tools", "fpga_workflow.py"),
                          "synth", "--force", "--tag", tag, "--record", "--status", "measure"],
                         cwd=REPO, capture_output=True, text=True, errors="replace")
    txt = (out.stdout or "") + (out.stderr or "")
    m_met = re.search(r"BUILD_TIMING_CONSTRAINTS_MET=(\d)", txt)
    m_wns = re.search(r"BUILD_TIMING_WNS=(-?[\d.]+)", txt)
    if not m_wns:
        err = [l for l in txt.splitlines() if "ERROR" in l][:1]
        print(f"  {tag:<8} [无数据 rc={out.returncode}] {err}")
        continue
    met = subprocess.run([sys.executable, os.path.join(REPO, "tools", "fpga_workflow.py"), "metrics"],
                         cwd=REPO, capture_output=True, text=True, errors="replace").stdout
    fm = re.search(r"fmax_mhz\s+([\d.]+)", met)
    ab = re.search(r"absolute\s+([\d.]+)", met)
    print(f"  {tag:<8} target {freq:>8.4f} MHz  MET={m_met.group(1) if m_met else '?'}  "
          f"WNS={m_wns.group(1):>7}  fmax={fm.group(1) if fm else '?':>6}  "
          f"吞吐={ab.group(1) if ab else '?':>6}")
print("CLK_SWEEP_DONE")
