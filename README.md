# NUMGUARD · RISC-V + NPU 数字标签防错验证系统

> 紫光同创杯「基于 RISC-V 处理器的软硬件系统设计」参赛作品
> 交付版本：**95 MHz 双发射 CPU + 4-MAC NPU**（2026-11-04 上板验证版）

---

## 一、系统概览

| 项 | 规格 |
|---|---|
| **FPGA** | 紫光同创 **PG2L100H**（Logos2，FBG676，-6） |
| **CPU** | **双发射 RV32IMC @ 95 MHz**（2-wide in-order，双 IMEM、双译码器、第二执行槽） |
| **NPU** | **4-MAC int8 CNN @ 50 MHz**（固定 6 层：数字识别 + 手势识别双模型） |
| **视频链路** | 摄像头 DVP 采集（12.5 MHz MCLK）→ 帧缓冲 → LCD 驱动（像素时钟 9 MHz） |
| **工具链** | PDS 2022.2-SP6.1 |

## 二、应用：NUMGUARD（数字标签防错验证）

面向工业产线的**标签数字防错**：摄像头拍摄标签 → NPU 识别数字 → CPU 校验 → LCD 显示结果。
三种工作模式（长按 SW4 切换）：
- **HANDWRITE** — 手写数字识别（已部署）
- **GESTURE** — 手势识别（实验性，52/148 独立样本通过）
- **COCO80** — COCO 80 类目标检测（**未部署**，显示 `NO MODEL`）

## 三、交付版本的关键数据

| 项 | 数值 |
|---|---|
| 位流文件 | `top_pg2l100h_vision.sbit`（3,791,112 B） |
| 位流 sha256 | `51f5eda2d5e2d178…`（完整值见 `bitstream/build_summary.json`） |
| **时序** | **All Constraints Met**（cpu95 WNS **+0.493 ns**，14,484 端点） |
| **LUT** | **9,980 / 66,600（15%）** |
| **FF** | **6,573 / 133,200（5%）** |
| **APM（DSP）** | 13 / 240（6%） |
| **DRM（BRAM）** | 99 / 155（**64%**，主要用于视频帧缓冲） |
| **功耗** | 1.021 W |
| 验证状态 | 13 项注册测试（见 `docs/BOARD_VALIDATION.md`） |

## 四、目录结构

```
submission/
├── rtl/           板上验证的 RTL（39 个 Verilog 文件，取自 dual_j11 构建快照）
│   └── spec/      PDS 约束文件（.fdc）
├── bitstream/     位流 + 构建摘要
├── docs/          架构 / 合规 / 证据 / 当前版本说明
├── sw/            固件源码（C）与汇编测试程序
├── sim/           仿真测试平台
├── tools/         Python 工具链（汇编器 / 构建 / 报告 / 分析）
├── evidence/      时序 / 资源 / 多种子报告原始数据
└── README.md      本文件
```

## 五、重要说明（诚实声明）

1. **本目录的 `rtl/` 取自 `build/dual_j11/rtl/`（板上验证快照）**，与仓库主 RTL 树（`fpga_pg2l100h/vision/rtl/`）**不一致** —— 主树仍是单发射 + 1-MAC NPU 的旧版，**无法重建本目录的位流**。
2. **本版本不含 DDR3** —— 顶层无 DDR3 端口、`vision_soc.v` 未例化任何 DDR3 模块；
   RTL 里的 `vision_ddr3_*.v` 属遗留文件（未被引用）。全部存储走片上 BRAM（DRM 99/155，64%）。
3. **NPU 是 4-MAC 固定 6 层 CNN**，不是 128-MAC 通用引擎（后者在 `feat/ai-accel` 分支，未上板）。
4. **COCO80 模式未部署**（模型标注 `NO MODEL`）。
5. 精度/性能证据分级见 `docs/COMPLIANCE.md`（A=上板实测 / B=仿真 / C=综合 / D=派生 / E=未测）。

## 六、构建与验证

见 `docs/CURRENT-BUILD.md`（构建步骤与版本身份）与 `docs/BOARD_VALIDATION.md`（板级验证清单）。

## 七、开源许可

MIT License（见 `LICENSE`）。第三方组件（RVCoreP 参考实现等）遵循其原始许可。
