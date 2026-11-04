# 技术总览（docs/TECHNICAL-OVERVIEW.md）

> 范围：交付主体为紫光同创 PG2L100H 线；Artix-7 线作为跨厂商对比。数字均带级别与证据路径（见 [`COMPLIANCE.md`](../COMPLIANCE.md)）。

## 1. 系统构成

```
紫光同创 PG2L100H (Logos2, -6, FBG676)
├─ CPU 域 95 MHz    : RV32IM 五级流水线 + **双发射(2-wide)** + Zicsr + M-mode；IMEM/DMEM 片上
├─ 视觉/NPU 域 50 MHz: OV7670 DVP 采集 → 帧缓冲 → 自研 INT8 NPU 推理 → LCD(AN430 4.3")
├─ 跨时钟桥         : 采集/推理(50MHz) ↔ CPU(125MHz)
└─ 应用             : NUMGUARD 料箱数字标签防错核验（+ 手势/COCO80 实验模式）

Artix-7 XC7A100T 参照线（[跨厂商对比]，Vivado）
├─ CPU 150 MHz      : RV32IM 五级流水 + BTB/PHT + 非对齐访存拆分 + Zicsr
├─ 自研 INT8 NPU    : 112 op 全硬件 YOLO-Fastest v1.1（128 MAC/cycle 特化引擎）
└─ 软件/测试        : ModelSim 回归 PASS 12（`feat/ai-accel` 分支含 NN 4 项共 16）；112-op 双模 E2E 位精确 PASS
```

## 2. CPU 微架构（两条线同源、实现不同）

- 五级流水 IF/ID/EX/MEM/WB（IF/ID 由 IMEM 输出寄存器承担）；前递 + load-use 停顿 + 重定向。
- 分支预测：512 项两级流水 BTB + 8192 项 2 位饱和计数器 PHT（Artix 线实测；Pango 线为 Verilog-2005 移植实现）。
- 异常/中断：完整 M-mode（mstatus/mie/mip/mtvec/mepc/mcause/mtval/mscratch）。
- 访存：非对齐跨字 2 拍自动拆分；dmem 字节写使能。
- 扩展实验（未上 Pango 板）：RV32A 真预约（LR/SC）、RV32C 25 条压缩指令 —— `feat/isa-ac` 分支。
- 证据：[`README-ARTIX.md`](../README-ARTIX.md)（Artix 线完整说明）、`results/p5-ai-accelerator.md` 等。

## 3. 自研 INT8 NPU

### 3.1 交付主体侧（PG2L100H，50 MHz 域）
- 用途：NUMGUARD 数字标签核验的定点检测加速（micro-YOLO 规模网络）。
- 性能：**纯计算 1,769,238 周期 @50 MHz = 35.38 ms**（B/C 级：来自验收文档；端到端延迟需另测）。
- 证据：[`fpga_pg2l100h/vision/competition/ACCEPTANCE.md`](../fpga_pg2l100h/vision/competition/ACCEPTANCE.md)；功能边界（手势 35%、COCO80 未部署）同文档。

### 3.2 对比线（Artix-7，[跨厂商对比]）
- YOLO-Fastest v1.1 int8 PTQ，112 个 op **全部硬件执行**（conv/dwconv/pool/add/copy/upsample）；
- 特化引擎：128 MAC/cycle（16 OC lane × 8 px）、PACKED3/ADD_R、W/Q 大 BRAM 存储（W 320KB + Q 48KB + A/B 128KB，整机 132/135 tiles）；
- 端到端：**112-op 双模 E2E 位精确 PASS**（`hw_bad=0 / auto_bad=0`），NPU **1,191,020 周期**；
- 软件基线：**473,254,313 周期**（K=112 探针实测）⇒ **加速比 401.9×**（NPU 纯计算口径）/ 45.2×（含 CPU 侧开销）；
- 位流：100 MHz（sha `537af245…`），最高已验证 122.22 MHz；**从未上板**（B 级仿真/综合证据）。
- 证据：`feat/ai-accel` 分支的 `results/DELIVERY`/`STAGE-REVIEW` 等文档与 `results/ai-accel/`。

## 4. 视频通路与时钟

- OV7670 DVP 8/10-bit → 采集（50 MHz 域）→ 帧缓冲/快照 → LCD RGB 并口（AN430 4.3"）；Pango 侧为 125/50 MHz 双时钟域，复位等两 PLL 锁定。
- 关键 PDS 经验：CPU PLL 在 PFD=50 MHz 时必须 `BANDWIDTH=HIGH`（否则不锁 → 白屏），见 `fpga_pg2l100h/vision/CLOCK_SPLIT.md`、`BOARD_VALIDATION.md`。
- Artix 线：MMCM 21/7 → `sys_clk_core` 150 MHz；AI 档 100/122.22 MHz 由 `--nn-freq-mhz` 统一配置。

## 5. 软件与工具

- 裸机 C：CoreMark（`-O2 -mbranch-cost=1 -fira-loop-pressure` 实测配方；配方统一修复 R54 见 `results/NEXT-BURN.md` §0，`tools/cm_recipe.py` 属本地待推送分支）、NN demo、YOLO 双模驱动；
- 统一工作流：`tools/fpga_workflow.py`（doctor/check/verify/synth/report/metrics/profile/hw）；
- Pango：`fpga_pg2l100h/vision/build.py`（PDS 构建/下载）；Artix：Vivado 2018.3 + `fpga/build.tcl`。
- 复现步骤见 [`REPRODUCE.md`](REPRODUCE.md)；证据登记见 [`EVIDENCE.md`](EVIDENCE.md)。

## 6. 已验证结论速查

| 结论 | 级别 | 证据 |
|---|---|---|
| PG2L100H CPU **95 MHz 双发射** WNS **+0.493**（当前烧录版，位流 `51f5eda2…`）/ 上一版单发射 125 MHz +0.609 | C | `fpga_pg2l100h/vision/BOARD_VALIDATION.md` |
| 摄像头→LCD 预览 | A（目视） | 同文档 2026-11-03 记录 |
| NPU 纯计算 1,769,238 周期 | B/C | `ACCEPTANCE.md` |
| Artix 150 MHz WNS +0.007 | C | `results/coremark.csv` 行 `legal150-*` |
| Artix CoreMark 324.19 | A | `results/uart_flash_boot_final.txt`（[跨厂商对比]） |
| AI 线 112-op E2E PASS / 401.9× | B | `feat/ai-accel` 分支 `results/ai-accel/`（[跨厂商对比]） |
