# 交付包盘点清单

> 盘点时间：2026-11-04 · 版本：**95 MHz 双发射 CPU + 4-MAC NPU**（`dual_j11` 上板版）
> 用途：新开源仓库的第一版内容

---

## 一、可以上交的内容（本目录已收录）

| 类别 | 内容 | 文件数 | 大小 | 来源 |
|---|---|---|---|---|
| **RTL** | 板上验证的完整 RTL（CPU + NPU + SoC + 摄像头 + LCD + 顶层（**无 DDR3**）） | **39** | 180 KB | `build/dual_j11/rtl/` ✅ |
| **约束** | PDS 约束（`.fdc`） | 3 | 21 KB | `pg2l100h_camera_lcd.fdc` / `riscv.fdc` / `mes50hp.fdc` |
| **位流** | `top_pg2l100h_vision.sbit` + `build_summary.json` | 2 | 3.7 MB | `build/dual_j11/` |
| **文档** | README / COMPLIANCE / TECHNICAL-OVERVIEW / CURRENT-BUILD / BOARD_VALIDATION | 5 | 40 KB | 仓库根 + `docs/` + `fpga_pg2l100h/vision/` |
| **固件** | C 源码 + 汇编测试程序 | 18 | 173 KB | `sw/` |
| **仿真** | 测试平台（`tb_*.sv`） | 7 | 101 KB | `sim/` |
| **工具链** | Python 汇编器 / 构建 / 报告 / 分析 | 32 | 389 KB | `tools/`（不含数据/权重） |
| **证据** | 时序报告 / 资源报告 / 多种子汇总 | 3 | 29 KB | `build/dual_j11/` |
| **合计** | | **109** | **4.4 MB** | |

---

## 二、明确**不**收录的内容（及原因）

| 不收录 | 原因 |
|---|---|
| `results/`（1 MB，83 个文档） | AI 线（128-MAC / YOLO / 瓶盖）的研究记录，**不属于本次交付版本** |
| `fpga_pg2l100h/vision/rtl/`（主 RTL 树） | **是单发射 + 1-MAC 旧版**，与板上不一致（见下方"重要"） |
| `.fpga-cache/` / 各种 `build/` 中间产物 | 体积大、可重建 |
| `tools/yolo/data/`（6.5 MB 模型/权重/图片） | 第三方训练产物，可由脚本重新下载 |
| `ebf_pg2l100h_demo/`（厂商示例工程） | 与作品无关 |
| `refs/RVCoreP/`（参考实现） | 第三方代码，仅作阅读参考 |
| 各类 `*.log` / `*.jou` / `*.wdb` | 仿真运行产物 |

---

## 三、⚠️ 交付前必须知道的三件事

1. **`rtl/` 取自 `build/dual_j11/rtl/`（板上快照）**
   主 RTL 树 `fpga_pg2l100h/vision/rtl/` 里的 `cpu/core.v`（17.6 KB 单发射）和
   `vision_npu.v`（185 行，1-MAC）**与本目录的 `core.v`（32.6 KB 双发射）和
   `vision_npu.v`（199 行，4-MAC）不一致**。本目录才是能重建位流的那一份。

2. **NPU 是 4-MAC 固定 6 层 CNN**
   不是 128-MAC 通用引擎（那个在 `feat/ai-accel` 分支，未上板）。

3. **COCO80 模式未部署**
   NUMGUARD 的 COCO80 模式显示 `NO MODEL`。

---

## 四、可与本包配套但未收录（按需补）

| 内容 | 在哪 | 说明 |
|---|---|---|
| 128-MAC 通用 NPU 引擎（SV + 转 Verilog） | `feat/ai-accel` + `fpga_pg2l100h/vision/rtl/nn/` | 完整子系统 4 套仿真通过，未上板 |
| 瓶盖检测模型（106/71 op） | `results/bottlecap/` | 精度可行，盘古板装不下 |
| YOLO-Fastest COCO-80（112 op） | `results/ai-accel/` + `sim/prog/yolo*` | C128 全片上可行，C320 需 DDR3 |
| 设计图 | `docs/figures/` | 设计流程图 + 硬件架构图 |

---

## 五、建议的新仓库结构

```
（当前 submission/ 就是建议的根目录）
├── README.md           ✅ 已写
├── LICENSE             ✅ MIT
├── INVENTORY.md        ✅ 本文件
├── rtl/                ✅ 39 文件
│   └── spec/           ✅ 3 约束
├── bitstream/          ✅ 位流 + 摘要
├── docs/               ✅ 5 文档
├── sw/                 ✅ 18 文件
├── sim/                ✅ 7 TB
├── tools/              ✅ 32 文件
└── evidence/           ✅ 3 报告
```
