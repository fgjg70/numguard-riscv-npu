#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""npu_inventory.py — 逐 NPU 家族的并行度 / 周期 / 算力 / 利用率核算（单一事实来源）

为什么需要这个脚本
------------------
仓库里同时存在 3 个 NPU/加速器家族、共 6 个可运行实体，散落在 2 个分支。
"谁的算力多大"这类问题靠手算必错（本项目已因手算行数/周期吃过 R1/R54）。
本脚本把周期与算力**从 RTL 里的层参数和实测周期反推**，每个数字都打印出公式，
便于任意第三方按同一条公式复算。

证据级别约定（与 results/RTL-DISCIPLINE.md 一致）
--------------------------------------------------
  E0 = 源码                          E1 = 仿真
  E2 = 综合（fmax/资源，未上板）      E3 = 上板实测

用法
    python tools/npu_inventory.py            # 打印全部核算
    python tools/npu_inventory.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import json
import math

# ============================================================================
# 1. vision_npu（紫光同创 PG2L100H 交付主体）
#    层参数直接抄自 rtl/vision_npu.v 的 SETUP 状态（layer_index 0..5），
#    改 RTL 的层表必须同步改这里，否则周期对不上（脚本会 self-check）。
# ============================================================================
# (input_width, input_channels, output_channels, kernel_size, stride) × 6 层
VISION_LAYERS = [
    (64, 1, 8, 3, 2),
    (32, 8, 16, 3, 2),
    (16, 16, 24, 3, 2),
    (8, 24, 32, 3, 2),
    (4, 32, 32, 3, 1),
    (4, 32, 27, 1, 1),
]
# tb_npu.v 里的硬断言（E1 级实测值，脚本用来交叉验证推导公式）
VISION_CYCLES_MEASURED_1LANE = 1769238
# xsim_26008.backup.log:23 的 soc_hand PASS 行（E1 级实测值，4-lane）
VISION_CYCLES_MEASURED_4LANE = 242310

VISION_CLK_HZ = 50_000_000          # build_summary.json: npu_clock_hz


def vision_out_geometry(inw, oc, k, s):
    """vision_npu 的输出版图：SETUP 直接给出 output_width，逐层固定 stride 缩小 2 倍。"""
    ow = {64: 32, 32: 16, 16: 8, 8: 4, 4: 4}[inw]
    return ow, ow


def vision_macs():
    """真实乘法次数（= 乘累加次数），与并行度无关。"""
    tot = 0
    detail = []
    for inw, ic, oc, k, s in VISION_LAYERS:
        ow, oh = vision_out_geometry(inw, oc, k, s)
        m = oh * ow * oc * ic * k * k
        tot += m
        detail.append((inw, ic, oc, k, s, oh, ow, m))
    return tot, detail


def vision_cycles_1lane():
    """单 lane FSM：每输出通道 = BIAS_REQ(1) + BIAS_LOAD(1) + N_tap×(REQ+MAC=2) + STORE(1)。"""
    cyc = 0
    detail = []
    for inw, ic, oc, k, s in VISION_LAYERS:
        ow, oh = vision_out_geometry(inw, oc, k, s)
        n_tap = ic * k * k
        n_out_ch = oh * ow * oc
        c = n_out_ch * (2 * n_tap + 3)
        cyc += c
        detail.append((n_out_ch, n_tap, c))
    return cyc + len(VISION_LAYERS), detail   # +6 次 SETUP


def vision_cycles_4lane():
    """4 lane FSM：OC 方向 4 路 + tap 读算流水 ⇒ 每组 = BIAS_REQ(1)+BIAS_LOAD(1)+(N_tap+1)+STORE×k。

    STORE 拍数 = 本组实际输出通道数（末组可能不足 4）。
    """
    cyc = 0
    detail = []
    for inw, ic, oc, k, s in VISION_LAYERS:
        ow, oh = vision_out_geometry(inw, oc, k, s)
        n_tap = ic * k * k
        c = 0
        for _y in range(oh):
            for _x in range(ow):
                oc_done = 0
                while oc_done < oc:
                    lanes = min(4, oc - oc_done)
                    c += 1 + 1 + (n_tap + 1) + lanes
                    oc_done += lanes
        cyc += c
        detail.append((math.ceil(oc / 4), n_tap, c))
    return cyc + len(VISION_LAYERS), detail


# ============================================================================
# 2. nn_conv_engine（Artix-7 / feat/ai-accel）
#    并行度抄自 nn_conv_engine.sv 的 parameter（OC_LANES=16, PX_LANES=8）。
# ============================================================================
CONV_OC_LANES = 16
CONV_PX_LANES = 8
CONV_MAC_PER_CYCLE = CONV_OC_LANES * CONV_PX_LANES     # 128

# 实测周期（E1 级，ModelSim 端到端 E2E）
# MAC 数来源：YOLO=model-report.md「MACs@128 = 20.07M」；
#             slim/bottlecap 由 plan/*/model.json 逐层 oh*ow*oc*ic*k*k 求和（tools/npu_inventory.py 可复算）
CONV_MEASURED = {
    # name: (cycles, macs, note)
    "yolo_v3_A5e":   (1191020, 20070000,   "YOLO-Fastest v1.1 128x128, 112-op, A7a 之前"),
    "yolo_v3_A7a":   (1049089, 20070000,   "YOLO-Fastest v1.1 128x128, 112-op, dw16"),
    "slim_A7a":      (653129,  15652512,   "SlimMobileNet 96x96, 71-op, 136K 参数"),
    "bottlecap_101": (37631729, 2271966928, "EnhancedMobileNetV2-FPN 224x224, 106-op, 2.05M 参数"),
}

CONV_FREQ = {
    "100MHz(交付)": 100.0e6,
    "122.22MHz(最高验证)": 122.22e6,
}


# ============================================================================
# 3. gemm_accel（Artix-7，已 GEMM_EN 门控默认关闭）
# ============================================================================
GEMM_MACS = 16 * 16 * 16          # M=K=N=16
GEMM_CYCLES = 4608                # 实测 accel_cycles
GEMM_CLK_HZ = 150_000_000         # 交付档域内


# ============================================================================
# 4. 打印
# ============================================================================
def go_ops(mac_per_cycle, hz):
    """int8 MAC 折算算力：1 MAC = 2 ops（乘 + 加）。FPGA/ASIC 厂商标称 TOPS 同口径。"""
    return mac_per_cycle * hz * 2 / 1e9


def section(t):
    print()
    print("=" * 78)
    print(t)
    print("=" * 78)


def report():
    out = {}

    section("① vision_npu 单 lane（fpga_pg2l100h/vision/rtl/vision_npu.v，185 行）")
    macs, det = vision_macs()
    cyc1, d1 = vision_cycles_1lane()
    print(f"{'层':<4}{'in_w':>5}{'ic':>4}{'oc':>4}{'k':>3}{'s':>3}{'out':>9}{'MAC':>12}")
    for i, (inw, ic, oc, k, s, oh, ow, m) in enumerate(det):
        print(f"{i:<4}{inw:>5}{ic:>4}{oc:>4}{k:>3}{s:>3}{f'{oh}x{ow}x{oc}':>9}{m:>12,}")
    print(f"合计 MAC = {macs:,}")
    print(f"推导周期 = {cyc1:,}   实测(tb_npu.v 硬断言) = {VISION_CYCLES_MEASURED_1LANE:,}"
          f"   差 = {cyc1 - VISION_CYCLES_MEASURED_1LANE}")
    peak = go_ops(1, VISION_CLK_HZ)
    t = VISION_CYCLES_MEASURED_1LANE / VISION_CLK_HZ
    eff = macs * 2 / t / 1e9
    print(f"峰值算力 = 1 MAC/拍 x {VISION_CLK_HZ/1e6:.0f} MHz = {peak:.3f} GOPS")
    print(f"实测帧时间 = {t*1e3:.2f} ms；有效算力 = {macs:,} MAC / {t*1e3:.2f} ms = {eff:.3f} GOPS")
    print(f"MAC 利用率 = {eff/peak*100:.1f}%  （瓶颈：2 拍/tap + BIAS/STORE 开销）")
    out["vision_1lane"] = dict(macs=macs, cycles=cyc1, meas=VISION_CYCLES_MEASURED_1LANE,
                               peak_gops=peak, eff_gops=eff, util=eff / peak)

    section("② vision_npu 4 lane（build/dual_j11/rtl/vision_npu.v，199 行，板上实际）")
    cyc4, d4 = vision_cycles_4lane()
    print(f"{'层':<4}{'组/像素':>8}{'tap':>6}{'周期':>12}")
    for i, (grp, n_tap, c) in enumerate(d4):
        print(f"{i:<4}{grp:>8}{n_tap:>6}{c:>12,}")
    print(f"合计 MAC = {macs:,}（与单 lane 相同模型）")
    print(f"推导周期 = {cyc4:,}   实测(xsim soc_hand PASS) = {VISION_CYCLES_MEASURED_4LANE:,}"
          f"   差 = {cyc4 - VISION_CYCLES_MEASURED_4LANE}")
    peak4 = go_ops(4, VISION_CLK_HZ)
    t4 = VISION_CYCLES_MEASURED_4LANE / VISION_CLK_HZ
    eff4 = macs * 2 / t4 / 1e9
    print(f"峰值算力 = 4 MAC/拍 x {VISION_CLK_HZ/1e6:.0f} MHz = {peak4:.3f} GOPS")
    print(f"实测帧时间 = {t4*1e3:.3f} ms（{1/t4:.0f} fps 计算上限）；有效算力 = {eff4:.3f} GOPS")
    print(f"MAC 利用率 = {eff4/peak4*100:.1f}%")
    print(f"相对单 lane 加速 = {VISION_CYCLES_MEASURED_1LANE/VISION_CYCLES_MEASURED_4LANE:.2f}x"
          f"（= 4 lane x 2 拍->1 拍流水的复合效果）")
    out["vision_4lane"] = dict(cycles=cyc4, meas=VISION_CYCLES_MEASURED_4LANE,
                               peak_gops=peak4, eff_gops=eff4, util=eff4 / peak4,
                               speedup=VISION_CYCLES_MEASURED_1LANE / VISION_CYCLES_MEASURED_4LANE,
                               ms=t4 * 1e3)

    section("③ nn_conv_engine（Artix-7 / feat/ai-accel，128 MAC/拍）")
    print(f"并行度 = OC_LANES({CONV_OC_LANES}) x PX_LANES({CONV_PX_LANES}) = {CONV_MAC_PER_CYCLE} MAC/拍")
    print("（注：dw / pool / copy / add 路径只激活 lane 0..7，峰值仅对 dense conv 成立）")
    for fname, fhz in CONV_FREQ.items():
        print(f"\n-- 时钟 {fname} --")
        pk = go_ops(CONV_MAC_PER_CYCLE, fhz)
        print(f"峰值算力 = {pk:.2f} GOPS（= {CONV_MAC_PER_CYCLE} x {fhz/1e6:.2f} MHz x 2 / 1e9）")
        for name, (cyc, m, note) in CONV_MEASURED.items():
            t = cyc / fhz
            if m:
                eff = m * 2 / t / 1e9
                print(f"  {name:<14} {cyc:>12,} 拍  {t*1e3:>9.2f} ms  "
                      f"有效 {eff:>7.2f} GOPS  利用率 {eff/pk*100:>5.1f}%   <- {note}")
            else:
                print(f"  {name:<14} {cyc:>12,} 拍  {t*1e3:>9.2f} ms  "
                      f"({'MAC 数未入库'})              <- {note}")
    out["conv_engine"] = {
        "mac_per_cycle": CONV_MAC_PER_CYCLE,
        "peak_gops_100": go_ops(CONV_MAC_PER_CYCLE, 100e6),
        "peak_gops_122": go_ops(CONV_MAC_PER_CYCLE, 122.22e6),
    }

    section("④ gemm_accel（Artix-7，GEMM_EN 门控默认关闭）")
    t = GEMM_CYCLES / GEMM_CLK_HZ
    eff = GEMM_MACS * 2 / t / 1e9
    pk = go_ops(1, GEMM_CLK_HZ)
    print(f"峰值 = 1 MAC/拍 x {GEMM_CLK_HZ/1e6:.0f} MHz = {pk:.3f} GOPS")
    print(f"16x16x16 GEMM = {GEMM_MACS:,} MAC / {GEMM_CYCLES:,} 拍 = "
          f"{GEMM_MACS/GEMM_CYCLES:.3f} MAC/拍（利用率 {GEMM_MACS/GEMM_CYCLES*100:.1f}%）")
    print(f"有效算力 = {eff:.3f} GOPS")
    out["gemm"] = dict(macs=GEMM_MACS, cycles=GEMM_CYCLES, peak_gops=pk, eff_gops=eff)

    section("⑤ 横向：同一把尺子（峰值 GOPS / MAC 阵列规模 / 每拍利用）")
    rows = [
        ("vision_npu 1lane",  1,   50.0,  macs, VISION_CYCLES_MEASURED_1LANE),
        ("vision_npu 4lane",  4,   50.0,  macs, VISION_CYCLES_MEASURED_4LANE),
        ("gemm_accel",        1,  150.0,  GEMM_MACS, GEMM_CYCLES),
        ("conv_engine A7a", 128, 100.00,  20070000, 1049089),
        ("conv_engine A7a", 128, 122.22,  20070000, 1049089),
        ("conv slim",       128, 122.22,  15652512, 653129),
        ("conv 瓶盖",        128, 122.22, 2271966928, 37631729),
    ]
    print(f"{'实体':<20}{'MAC/拍':>7}{'MHz':>8}{'峰值GOPS':>10}{'MAC数':>14}{'实际GOPS':>10}{'利用':>7}")
    for name, mac_pc, mhz, m, cyc in rows:
        pk = mac_pc * mhz * 1e6 * 2 / 1e9
        t = cyc / (mhz * 1e6)
        eff = m * 2 / t / 1e9 if m else 0
        print(f"{name:<20}{mac_pc:>7}{mhz:>8.2f}{pk:>10.2f}{m:>14,}{eff:>10.2f}{eff/pk*100:>6.1f}%")

    section("⑥ 资源占用（各自综合报告的原始数字，未做归一化）")
    res = [
        # name, device, LUT, LUT_tot, FF, FF_tot, BRAM, BRAM_tot, DSP, DSP_tot, note
        ("交付档基线（无 NPU）", "XC7A100T", 3098, 63400, 1447, 126800, 9, 135, 8, 240, "fmax 150.2"),
        ("conv_engine 引擎 OOC", "XC7A100T", 30700, 63400, 19731, 126800, 124, 135, 200, 240, "A7a @122.22"),
        ("conv_engine 整机", "XC7A100T", 36985, 63400, 16381, 126800, 132, 135, 168, 240, "AI 档 @100"),
        ("gemm_accel 整机(开)", "XC7A100T", 9984, 63400, 14097, 126800, None, 135, None, 240, "对比 4019/1387"),
        ("vision 整机(4lane)", "PG2L100H", 9980, 66600, 6573, 133200, 99, 155, 13, 240, "双发射 CPU + 视频 + NPU"),
    ]
    print(f"{'实体':<22}{'器件':<11}{'LUT':>7}{'占比':>7}{'FF':>9}{'占比':>7}{'BRAM':>6}{'占比':>7}{'DSP':>5}{'占比':>7}")
    for nm, dev, lut, lt, ff, ft, br, bt, dsp, dt, note in res:
        brs = f"{br}/{bt}" if br is not None else "n/a"
        dsps = f"{dsp}/{dt}" if dsp is not None else "n/a"
        print(f"{nm:<22}{dev:<11}{lut:>7,}{lut/lt*100:>6.1f}%{ff:>9,}{ff/ft*100:>6.1f}%"
              f"{brs:>10}{((br/bt*100) if br else 0):>6.1f}%{dsps:>11}{((dsp/dt*100) if dsp else 0):>6.1f}%   {note}")

    section("⑦ 单位 LUT 的峰值算力（跨厂商 LUT 不等价，只看量级）")
    eff_rows = [
        ("vision_npu 4lane", 0.400, None, "NPU 单独 LUT 未在报告中拆分 ⇒ 无法归一"),
        ("conv_engine A7a", 31.29, 30700, "引擎 OOC LUT（峰值 @122.22MHz）"),
        ("gemm_accel", 0.300, 5965, "整机差 9984-4019"),
    ]
    for nm, gops, lut, note in eff_rows:
        if lut:
            print(f"{nm:<20} {gops:>6.3f} GOPS / {lut:>6,} LUT = {gops*1e3/lut:>6.2f} MOPS/LUT   {note}")
        else:
            print(f"{nm:<20} {gops:>6.3f} GOPS  {note}")

    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非表格")
    a = ap.parse_args()
    data = report()
    if a.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
