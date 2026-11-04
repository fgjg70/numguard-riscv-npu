# 当前烧录版本（CURRENT-BUILD）

> **本文是"板子上现在跑的是什么"的单一事实来源。** 其它文档若与本文冲突，以本文为准。
> 最后更新：2026-11-04（依据 `build/dual_j11/` 的全部构建产物逐项核对，未手抄任何数字）

---

## 1. 身份

| 项 | 值 |
|---|---|
| 构建目录 | `fpga_pg2l100h/vision/build/dual_j11/`（仓库内） |
| 位流文件 | `build/dual_j11/generate_bitstream/top_pg2l100h_vision.sbit` |
| **位流 SHA256** | **`51f5eda2d5e2d178380072dd186902d875836bb031a1c0b041208704fe30cf4c`** |
| 位流大小 | **3,791,112 B** |
| 生成时间 | **2026-11-04 11:41**（`bitstream_console.log`：`Current time: Wed Nov 4 11:41:00 2026`） |
| 工具链 | PDS **2022.2-SP6.1**（build 138931） |
| 构建机 | 队友机（`C:/Users/chenq/...`），产物已入库 |
| 器件 | **紫光同创 Logos2 / PG2L100H / FBG676 / 速度档 -6 / 工业级** |
| CPU 变体 | **`cpu_variant: dual`** = **双发射**（见 §3） |
| 构建标记 | `timing_constraints_met: true`；`hardware_tested: **false**`（构建脚本未记录下载，板载状态由队友口头确认） |

> ⚠️ **与上一版的关系**：上一版是 **125 MHz 单发射**（位流 `f179a403…`，`build/j11/` 目录已不在仓库）。
> 本文这一版是 **95 MHz 双发射**，两者位流、频率、微架构都不同，**不可混用数字**。

---

## 2. 时钟

| 时钟域 | 频率 | 说明 |
|---|---|---|
| **CPU / IMEM / DMEM** | **95 MHz** | 时序报告里的域名 **`cpu95`** |
| NPU / 摄像头采集 / MMIO / UART / 按键 | 50 MHz | |
| 视频扫描 | 27 MHz | |
| LCD 像素 | 9 MHz | |
| 相机 MCLK | 12.5 MHz | |

**95 MHz 怎么来的**（`rtl/vision_cpu_clock.v` 的 `GTP_GPLL` 参数）：

```
CLKIN 50 MHz
  → STATIC_RATIOI(2)   ⇒ PFD = 25 MHz
  → STATIC_RATIOM(1)   ⇒ ×1
  → STATIC_RATIOF(38.0)⇒ VCO = 25 × 38 = 950 MHz
  → STATIC_RATIO0(10.0)⇒ CLKOUT0 = 950 / 10 = 95.000 MHz  （精确整除）
```

> ⚠️ 该模块的**输出端口仍叫 `clk_125m`**（`top_pg2l100h_vision.v:34,116,178`），是 125 MHz 版遗留的**过期命名** ——
> 名字与实际 95 MHz 不符，属于本工程明令避免的"标签与事实不一致"，建议后续改名（本文先如实记录）。

---

## 3. CPU 微架构：真正的双发射

**不是**"双核"，是**单核 2-wide 顺序双发射**：一个周期最多取/译/执行两条指令。

| 机制 | 实现（`build/dual_j11/rtl/core.v`） |
|---|---|
| 双取指 | **两套 IMEM**：`u_imem`（`pc`）+ `u_imem1`（`slot1_next_address` = `pc+4`）⇒ 每拍取 2 条 |
| 双译码 | `u_ctrl` + `u_ctrl1`（`control_unit`）、`u_imm1`（`imm_gen`） |
| 双执行槽 | 第二条走独立流水寄存器 `u_id_ex1` → `slot1_ex_*`（`slot1_ex_valid/instr/pc/rs1_data/...`） |
| 顺序提交 | 第二槽只在第一槽"可配对"时才发射，且统一受 `redir_kill`/`pipe_stall` 控制 |

**发射条件**（`core.v:300`，原样摘录）：

```verilog
assign slot1_issue = boot_done && primary_pairable && slot1_simple && !if_pred_taken &&
                     !pair_raw && !secondary_load_use && !(primary_memory && slot1_memory) &&
                     pc[14:2] != 13'h1fff;
```

逐项含义：
- `primary_pairable`（L246）：`!id_ctrl[31] && id_ctrl[30:28]==0 && id_ctrl[44:43]<2` ⇒ 第一条必须是**简单指令**（非访存/非分支那类）
- `slot1_simple`（L245）：`slot1_allowed(slot1_instr)` ⇒ 第二条限定在**允许配对**的指令集合
- `!if_pred_taken`：分支预测 taken 时不配对
- `!pair_raw`（L299）：两条之间**不允许 RAW 相关**
- `!secondary_load_use`（L296-298）：第二条不能撞上 load-use
- `!(primary_memory && slot1_memory)`：**同拍不允许两条访存**
- `pc[14:2] != 13'h1fff`：避开 IMEM 回卷边界

---

## 4. 面积与功耗（PDS 报告，同一份布局布线结果）

| 资源 | 用量 | 总量 | 占比 | 来源 |
|---|---|---|---|---|
| **LUT** | **9,980** | 66,600 | **15%** | `device_map/run.log` 的 `Device Utilization Summary` |
| **FF** | **6,573** | 133,200 | **5%** | 同上 |
| **APM**（乘法器） | **13** | 240 | **6%** | 同上 |
| **DRM**（BRAM） | **99** | 155 | **64%** | 同上 |
| 器件总利用率 | — | — | **15%** | `place_route/run.log` |
| 功耗 | **≈1.02 W** | — | — | `multiseed_summary.csv` 的 `Power` 列 / `report_power/` |

> DRM 用到 64% 主要是 64K×16 帧缓冲（16 组 × 2 DRM36K）占的；NPU 权重/Q 另占一部分。

---

## 5. 时序（`place_route/top_pg2l100h_vision_timing_summary_after_hold_fix.txt`）

**`Design Summary : All Constraints Met.`**

| Launch → Capture | WNS (ns) | 失败端点 | 端点总数 |
|---|---|---|---|
| clk50 → clk50 | +7.688 | 0 | 9,611 |
| cpu95 → clk50 | +18.235 | 0 | 68 |
| clk27 → clk27 | +16.635 | 0 | 2,619 |
| clk50 → clk27 | +17.764 | 0 | 377 |
| clk27 → lcd_pixel | +22.661 | 0 | 27 |
| **cpu95 → cpu95** | **+0.493** | **0** | **14,484** |
| clk50 → cpu95 | +9.270 | 0 | 32 |

- **关键路径在 CPU 域：WNS +0.493 ns**（周期 10.526 ns）⇒ 该域 **fmax ≈ 99.7 MHz**，即当前 95 MHz 还有 **≈5%** 余量。
- Hold（WHS）：clk50 +0.342 / clk27 +0.347 / cpu95 **+0.350** / lcd +1.166 —— 全部为正，**无 hold 违例**。
- `multiseed_summary.csv`（单种子 single）：Setup(Slow) **0.436** / Setup(Fast) 4.207 / Hold(Slow) 0.342 / Hold(Fast) 0.250，通过率 100%。

---

## 6. 功能构成（这一版里有什么）

| 子系统 | 内容 |
|---|---|
| CPU | 上述双发射 RV32IM（含 Zicsr、M 扩展、精确异常/中断；双 BRAM IMEM/DMEM） |
| **自研 INT8 NPU** | 50 MHz 单引擎；数字 0~9 与手势两套模型 ROM 复用；MODE_CPU 逐 op / MODE_AUTO 自主执行 |
| 视觉 | OV7670 **18 针 DVP**（`camera_dvp_capture.v`）→ 帧缓冲 → **AN430 4.3" LCD**（320×240 放大 160×120 预览） |
| 应用 | **NUMGUARD**：工单期望值 + 独立识别 + 连续三次一致才确认；空场重置、移走校验、OK/BAD 计数、LCD 顶部状态栏、串口审计事件 |
| 交互 | SW2 复位 / SW4 短按加期望值、长按 1 秒切换 HANDWRITE→GESTURE→COCO80 / SW5 彩条切换 |
| 模式边界 | COCO80 只做模式控制与 `NO MODEL` 提示（**未部署模型**）；手势为**实验分支** |

`build_summary.json` 里的 13 项验证测试：`board, camera, cdc, guard, lcd, npu, snapshot, snapshot_hand, soc, soc_hand, tools, training_data, verilog`。

---

## 7. 明确没有证据的部分（不做宣称）

1. **上板实测未记录进仓库** —— `build_summary.json` 的 `hardware_tested` 仍为 `false`；没有本次的下载日志/配置状态读回/照片入库。
2. **应用级未逐项确认**：CPU 固件执行、按键响应、应用 UART 输出、识别精度、三次确认延迟 —— 均无本次证据。
3. **无 CoreMark 数据**：这一版（95 MHz 双发射）**没有跑过 CoreMark**，不得引用任何 CoreMark 数字。
4. 手势模型独立样本验证仅 52/148 通过，**不适合宣称数量确认**。
