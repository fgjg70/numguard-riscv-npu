# 基于紫光同创 PG2L100H 的 RISC-V CPU + 自研 INT8 NPU 视觉核验系统

> **赛道**：全国大学生嵌入式芯片与系统设计竞赛 · FPGA 创新设计赛道 · **紫光同创杯 · 赛题一「基于 RISC-V 处理器的软硬件系统设计」**
> **交付主体器件**：紫光同创 **PG2L100H**（Logos2 / -6 / FBG676，PDS 2022.2-SP6.1）
> **提交截止**：2026-11-04 18:00
> 本仓库同时保留一条 Artix-7 XC7A100T 分支作为**跨厂商对比**（规则允许仅作对比；交付主体必须是紫光同创器件）。

## 0. 合规与证据（先读）

- **交付主体 = 紫光同创 PG2L100H**；Artix-7 XC7A100T 线仅作**跨厂商对比**（赛题规则 3.4）。
- **数字必须带级别与证据**：A 实测（上板） / B 实测（仿真） / C 综合 / D 派生 / E 未测——见 [`COMPLIANCE.md`](https://github.com/fgjg70/FPGA/blob/main/COMPLIANCE.md)。
- **证据登记**：[`docs/EVIDENCE.md`](docs/EVIDENCE.md)（每个对外数字对应一行 + 状态）。
- **提交清单**：[`docs/SUBMISSION-CHECKLIST.md`](docs/SUBMISSION-CHECKLIST.md)（截止 2026-11-04 18:00）。
- **逐文件合规审计**：[`docs/COMPLIANCE-AUDIT.md`](docs/COMPLIANCE-AUDIT.md)（覆盖全库 836 文件）。
- **当前烧录版本（单一事实来源）**：[`fpga_pg2l100h/vision/CURRENT-BUILD.md`](fpga_pg2l100h/vision/CURRENT-BUILD.md) —— **95 MHz 双发射**，位流 `51f5eda2…`，CPU 域 WNS **+0.493 ns**。
- 上一版为 125 MHz 单发射（位流 `f179a403…`，`build/j11/` 已不在仓库）；**两版数字不可混用**。
- **当前最重要缺口**：① Pango **Flash 未写入**（断电需重新下载）；② Pango CoreMark 原始日志未入库；③ 设计报告（Word/PDF）与演示视频未产出；④ 摄像头预览已目视确认，但 CPU 固件/按键/UART 未逐项确认。

## 1. 交付主体与关键指标（PG2L100H）

| 项 | 实测值 | 来源 |
|---|---|---|
| CPU / IMEM / DMEM | **95 MHz 双发射**（2-wide in-order），建立 WNS **+0.493 ns**（All Constraints Met） | `fpga_pg2l100h/vision/CURRENT-BUILD.md` §3/§5 |
| NPU / 摄像头采集 / MMIO | 50 MHz，WNS +7.052 ns | PDS 时序报告 |
| 资源 | LUT **9980**/66600（15%）、FF **6573**/133200（5%）、APM **13**/240（6%）、DRM **99**/155（64%） | `build/dual_j11/device_map/run.log` |
| 配置 | 芯片 ID `0x00602899`、DONE=1、5 项配置错误位全 0、PLL 锁定 0→1 | `fpga_pg2l100h/vision/BOARD_VALIDATION.md` |
| 位流（当前烧录） | 3,791,112 B，SHA256 **`51f5eda2d5e2d178380072dd186902d875836bb031a1c0b041208704fe30cf4c`**（`build/dual_j11/`） | `CURRENT-BUILD.md` §1 |
| 视觉通路 | OV7670 → DVP 采集 → 帧缓冲 → LCD（AN430 4.3"），**摄像头画面已目视确认上屏** | 同上 |
| 自研 INT8 NPU | **4-lane** 定点检测/核验加速；**纯计算 242,310 周期 @50 MHz = 4.85 ms**（板上实际版本） | 周期推导 = 实测（`xsim ... PASS cycles=242310`）；单 lane 旧版为 1,769,238 拍 = 35.38 ms，见 [`results/NPU-INVENTORY.md`](results/NPU-INVENTORY.md) |
| 应用 | **NUMGUARD** 料箱数字标签防错核验（数字 0~9 + 手势实验分支） | `fpga_pg2l100h/vision/competition/DEMO.md` |

> ⚠️ **当前状态（务必先读）**：位流通过 **JTAG 下载到 FPGA RAM**，**尚未写入 Flash**——断电/重新上电后需重新下载；`SW2` 只能复位已加载逻辑。上电自启动（Flash 烧写）是决赛演示前必须完成的一项。

## 2. 仓库地图

| 目录 / 文件 | 内容 | 状态 |
|---|---|---|
| `fpga_pg2l100h/` | **PG2L100H 交付工程**（vision 主工程 + camera_lcd_debug 分阶段调试工程） | 🎯 交付主体 |
| `fpga_pg2l100h/vision/` | RISC-V CPU + 自研 INT8 NPU + 视频通路 + NUMGUARD 应用 + 模型/向量/验收文档 | 🎯 交付主体 |
| `archive/` | 历史归档（`legacy/` 早期单周期 Verilog、`pds/` 旧 PDS 实验工程） | 🗄 归档 |
| `ebf_pg2l100h_demo/` | 底板厂商示例工程（HDMI/ETH 参考、原理图）——**被 Pango 工程以相对路径引用（模板/引脚来源）** | 📎 第三方/依赖 |
| `fpga_mes50hp/` | 早期 MES50HP(PGL50H) 开发工程——**被 Pango camera_lcd_debug 以相对路径引用** | 📎 依赖 |
| `fpga_pds_1_0_9/` | 原 PGL22G 工程迁移基线（兼容留档） | 📎 兼容基线 |
| `results/` | 迭代记录、设计报告素材、成绩 CSV、证据清单 | 📊 证据 |
| `tools/` | 构建/仿真/上板/仓库工作流脚本（`fpga_workflow.py` 等） | 🔧 工具 |
| `sim/`、`sw/`、`fpga/` | Artix-7 线的仿真、裸机 C、Vivado 工程（对比用） | 🆚 对比线 |
| 根目录 `*.sv` | Artix-7 交付档的 RV32IM 核 RTL（**零厂商原语**，可移植性证据） | 🆚 对比线 |
| `skills/`、`README-ARTIX.md` 等 | 开发踩坑记录 / 历史交付说明 / 交接文档 | 📚 开发文档 |

> `fpga_mes50hp/` 与 `ebf_pg2l100h_demo/` 保持在仓库根目录是**有意的**：`fpga_pg2l100h/camera_lcd_debug/{build.py,project_file.py}` 用相对路径引用它们，移动会破坏构建。见 [`THIRD_PARTY.md`](THIRD_PARTY.md)。

## 3. 快速开始

### 3.1 交付主体（PG2L100H）
1. 构建/下载说明：`fpga_pg2l100h/README.md`、`fpga_pg2l100h/vision/BOARD_VALIDATION.md`
2. 时钟域说明：`fpga_pg2l100h/vision/CLOCK_SPLIT.md`（当前烧录版：CPU/IMEM/DMEM **95 MHz 双发射**；NPU/采集/MMIO 50 MHz。上一版单发射 125 MHz）
3. 三模式（HANDWRITE / GESTURE / COCO80）：`fpga_pg2l100h/vision/MODES.md`
4. 验收与演示脚本：`fpga_pg2l100h/vision/competition/{ACCEPTANCE.md,DEMO.md}`

### 3.2 Artix-7 对比线
见 [`README-ARTIX.md`](README-ARTIX.md)；仿真回归：`python tools/fpga_workflow.py verify`（**PASS 12**；`feat/ai-accel` 分支另含 4 项 NN 回归共 16）。

### 3.3 自研 INT8 NPU 对比材料（feat/ai-accel 分支）
`feat/ai-accel` 分支（远端已推送）包含：YOLO-Fastest v1.1 int8 **112 op 全硬件 E2E 位精确 PASS**、NPU **1,049,089 周期**（A7a：dw 16-lane；此前 1,191,020）、CPU 软件基线 **473,254,313 周期** ⇒ **加速比 401.9×（按 1,177,320 口径）/ 451.1×（按 A7a 1,049,089）**、XC7A100T **100 MHz 位流**（sha `537af245…`）与最高已验证 **122.22 MHz**；另有瓶盖 slim 模型 E2E（653,129 拍，F1 0.9976）。

> **三个 NPU 家族的完整盘点（算力/利用率/资源/精度/证据级别）见 [`results/NPU-INVENTORY.md`](results/NPU-INVENTORY.md)** —— 板上 4-lane 为 **0.40 GOPS**，`feat/ai-accel` 的 128-MAC 引擎为 **31.29 GOPS**，两者差 78 倍且不可互换。

## 4. 提交物状态（2026-11-03）

| 赛题硬性提交物 | 状态 |
|---|---|
| 完整工程源码 + 仿真/构建脚本 | ✅ 本仓库（Pango 主体 + Artix 对比线） |
| CoreMark 跑分结果 + PPA 指标 | ⚠️ Pango 侧实测原始日志待入库；Artix 侧见 `README-ARTIX.md` |
| 设计报告（Word + PDF，含扩展部分） | 🔲 制作中（本仓库 md 为素材） |
| 演示视频（3 分钟） | 🔲 制作中（脚本见 `fpga_pg2l100h/vision/competition/DEMO.md`） |

## 5. 口径与边界声明（答辩会被问）

- **交付主体只能是紫光同创器件**；Artix-7 的数字（如 CoreMark 324.19 实测、405.9 派生）**仅作跨厂商对比**，不是本次交付成绩。
- **未达成/未部署**：无 OS/RTOS、无 MicroPython/Arduino 移植、无自研 IDE；手势识别当前正确率约 35%，不作主线宣称；COCO80 仅主机前向/资源核查，未在板上部署；无 I-Cache、无板上中断演示。
- **NPU 周期口径**：板上跑的是 **4-lane** 版，**242,310 拍纯计算 = 4.85 ms @50 MHz**（相对单 lane 快 7.30×，其中 4× 来自 OC 并行、1.83× 来自 tap 流水）；`rtl/vision_npu.v`（主 RTL）仍是**单 lane 旧版 1,769,238 拍 = 35.38 ms**，重建板上位流必须用 `build/dual_j11/rtl/` 快照。端到端延迟需另测（采集→NPU→CPU→LCD→业务判定）。全量 NPU 盘见 [`results/NPU-INVENTORY.md`](results/NPU-INVENTORY.md)。
- **Flash 未写入**：当前为 JTAG RAM 下载；现场演示前需完成 Flash 自启动或固化下载流程（含备机/兜底视频）。

## 6. 技术文档入口

- [`docs/TECHNICAL-OVERVIEW.md`](docs/TECHNICAL-OVERVIEW.md) — 系统架构/CPU 微架构/NPU/视频通路/时钟域
- [`docs/REPRODUCE.md`](docs/REPRODUCE.md) — 两条线的构建/仿真/上板复现步骤
- [`docs/EVIDENCE.md`](docs/EVIDENCE.md) — 证据登记表（含待补清单）
- [`docs/SUBMISSION-CHECKLIST.md`](docs/SUBMISSION-CHECKLIST.md) — 提交检查清单
- [`COMPLIANCE.md`](https://github.com/fgjg70/FPGA/blob/main/COMPLIANCE.md) — 合规规范（证据分级/禁用词/边界清单）
- [`docs/README.md`](docs/README.md) — 全部文档索引

## 7. 仓库整理记录

- 2026-11-03：删除重复快照 `releases/cpu50mhz/`（384 文件/9.2MB，保留于 commit `f07836d`）；删除 `_pds_probe/` 临时工程与编辑器/工具临时文件；`legacy/`→`archive/legacy/`、`fpga_pds/`→`archive/pds/`；新增本 README、`README-ARTIX.md`、`docs/README.md`、`THIRD_PARTY.md`、`archive/README.md`；`.gitignore` 增补临时文件规则。**RTL、构建脚本、Pango 工程路径均未改动。**
- 分支约定：`main` = 交付/总览；`feat/ai-accel` = 自研 NPU 对比材料；`feat/isa-ac` = RV32A/C 扩展实验；`tag deliv150-o2ira` = Artix 150 MHz 交付档。
