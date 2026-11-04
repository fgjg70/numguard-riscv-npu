> **范围/口径声明（工具文档）**：本文描述构建/仿真/上板工具，不是赛题交付结论。工具产出的数字需按根 [`COMPLIANCE.md`](https://github.com/fgjg70/FPGA/blob/main/COMPLIANCE.md) 的证据分级标注（实测/仿真/综合/派生）后方可对外引用。

# tools — 工作流工具集

本目录里的工具解决的是**工程纪律问题**，不是功能问题。
`skills/fpga-riscv-a100t/PITFALLS.md` 记录了 **51 条**（**R1~R51**）踩过的坑，其中最高频的一条是
**R1「改了 A 忘了同步 B」——全部静默失败**（不报错、结果不变或结果错）。
`fpga_workflow.py` 存在的唯一目的，就是把这些"靠人记"的纪律变成"机器每次都查"。

---

## 快速开始

```bat
rem 环境自检 + 漂移检测 (改完代码收尾时跑, 约 2 秒)
cmd /c tools\doctor.bat

rem ModelSim 增量回归 (只跑受影响的测试)
python tools\fpga_workflow.py verify

rem 三项指标 + 绝对吞吐
python tools\fpga_workflow.py report
```

---

## 1. `fpga_workflow.py` — 统一入口

### `doctor` — 环境自检

检查 6 个工具路径是否存在，并打印会话环境的关键变量。
**为什么需要**：DSH 会话的 `process.env` 可能被剥离，此时 Vivado 报
`32-bit platform is not supported`、`vsim` 报 `Unable to checkout a license`。
工具内置 `clean_env()`，显式补齐 `PROCESSOR_ARCHITECTURE=AMD64` /
`LM_LICENSE_FILE` / `PATH`，所以**即使会话环境是空的也能跑**。

### `check` — R1 漂移检测 + 综合警告门（核心）

**7 项检查**（外加一节非编号的 `[HIL]` 硬件在环健康度），全部来自 PITFALLS 里踩过的真坑：

| # | 检查项 | 对应的坑 |
|---|---|---|
| 1 | 三处 RTL 文件清单是否同步（`build.tcl` / `build_ila.tcl` / `run_coremark.py` / `CORE_SV_ORDER`） | R1：新增 RTL 忘了加进清单 → 静默用旧产物 |
| 2 | 是否存在"没有任何地方例化"的 RTL 模块 | 在研未接线的模块（曾白名单放过 `csr_file.sv`） |
| 3 | `sw/` 源文件是否比 `sim/prog/*.hex` 镜像新（**内容哈希**判据） | R1：改了软件忘了重建镜像 |
| 4 | bitstream 是否比 RTL / 镜像新（改了软件必须重新综合） | R1：imem/dmem 是综合时 `$readmemh` 进去的 |
| 5 | 内存布局同步（含 `tb_sw.sv` 的 tohost 地址与 `link.ld` 一致） | R1：改 `link.ld` 地址要同步 5 类文件 |
| 6 | `.bat` / `.tcl` 是否纯 ASCII | R2：cmd 用 OEM 代码页会吃掉行首字符 |
| 7 | **综合警告门**：Vivado 警告的新增必须分诊；`[Timing 38-1]` 出现即 FAIL | M-06 / R43 / X-08 |

另有 `[HIL] 硬件在环健康度`（非编号）：报告最后一次上板数据的年龄与 bitstream 新鲜度，
超 1 小时未上板 → `[warn]`。
> ✅ 它**已改用采集清单见证**（与 `hw` 同源），输出形如
> `最后一次上板数据: 2026-10-11 15:22:41  (N 分钟前)   [来源: 采集清单]`；
> 清单缺失时告警而不是拿 CSV 的 mtime 冒充新鲜度。
> 背景（X-02）：`results/ila_done.csv` 被 git 跟踪，**2026-10-11 10:51 实测到"内容未变、
> mtime 变现在"导致的假新鲜度**（`check` 曾因此报"5 分钟前"）—— 所以**mtime 永远不能当见证**。
> `[FAIL]` = 必须修，退出码 1；`[warn]` = 建议修，不阻塞；`[info]` = 已知在研项，**预期如此**。

`csr_file.sv` **曾经**被 `KNOWN_UNWIRED` 白名单放过（P1 之前 `core.sv` 还没例化它），
那个白名单**现已清空** —— 它已经在三处 RTL 清单与 `CORE_SV_ORDER` 里，`check` 会强制要求。
（`[info]` 这一级仍然保留：以后在研未接线的模块先挂白名单，别让它变成每次必红的噪音。）

同一份白名单思路用在 RTL 编译顺序上：`CORE_SV_ORDER` 显式列举顺序，
`rv32_pkg.sv` 必须第一个编译（其余模块 `import` 它）。
> 这不是格式问题而是语义问题 —— 首次跑回归时按字母序排序，`rv32_pkg` 排到了后面，
> vlog 一次性报了 60 个 `Could not find the package (rv32_pkg)`。
> `unregistered_core_sv()` 会拦住"新加了 .sv 但忘了登记"的情况。

#### 第 7 项详解：综合警告门（M-06）—— 拿 Vivado 自己的警告当真 lint

**为什么必须是 Vivado**：本机没有 verilator / slang / svlint（`Get-Command` 全部 NOT FOUND），
而 ModelSim 的 `vlog` 对全核 20+ 个文件**只报 3 条**无关警告（全是 `Defaulting port kind to var`）。
本项目真正吃过亏的警告**只在 Vivado 日志里**，而且当时都被当成"标准提示"放过：

| 警告 | 真身 |
|---|---|
| `[Synth 8-3331] design mmio_subsys has unconnected port wstrb[3..0]`（报了 4 次）| **X-08**：MMIO 写忽略 wstrb，端口声明了却没人读 |
| `[Timing 38-1] DLL output pin(s) used on clock modifying cell ... without a feedback net` | **R43**：MMCM 漏接 `.CLKFBIN` ⇒ 永不锁定 ⇒ 整颗 CPU 被按在复位里（两轮才定位）|
| 36 × `RAMB36 async control check: ... has an input control pin ...` | BRAM 异步控制引脚警告 |
| 31 × `[Synth 8-3332] Sequential element (prod_ss_reg[*]) is unused and will be removed ... muldiv` | 死状态 |
| 20 × `design btb has unconnected port pred_pc[*] / upd_pc[*]` | BTB 只用低位，高位天然未连 |

**机制**：

- 来源：`fpga/build.log`（评分版）与 `fpga/ila_build.log`（ILA 版）；
- 警告键**归一化**（去掉行号 / 绝对路径 / 位下标 / 十六进制常量）—— 否则每次综合都"看起来是新的"，
  门会天天响然后被人忽略，**那比没有门更糟**；
- 基线存在 `.fpga-cache/synth-warn-baseline.json`；
- **新增**的警告类 → `[FAIL]`（尚无基线时降级为 `[warn]`）；**变多**只列报告；**消失**也列；
- **R43 判据机器化**：任一日志出现 `Timing 38-1 ... without a feedback net` → **FAIL**
  （这就是 `top_a100t.sv` 里那句"修复判据：重新综合后该警告必须消失"的自动化）。

分诊完一批确认无害的警告后，把当前状态固化成基线：

```bat
python tools\fpga_workflow.py check --update-baseline
```

**首个基线**（2026-10-11 固化）：`fpga/build.log` 曾是 **48 类 / 274 条**。此后 RTL 泳道清掉了一批
警告，**当前 build.log 是 23 类 / 245 条**，其中 **9 类不在旧基线里** ⇒ `[7]` 判 `[FAIL]`
（**这是需要分诊、然后重新固化基线的状态**，不是文档问题）：
```bat
python tools\fpga_workflow.py check --update-baseline
```
首个基线里**已被证实有真东西**的是 `[Synth 8-3331] design mmio_subsys has unconnected port wstrb[3..0]`
（= **X-08**，MMIO 写忽略 wstrb，Vivado 报了 4 次没人看）；
`Timing 38-1` 反馈环警告**已消失**（R43 修复生效，且现在被门盯住 —— 出现即 `[FAIL]`）。

### 其他常用一行命令（干净克隆 / 交付前）

```bat
python tools\build_sw.py --all              REM 建全部 12 个回归镜像 (干净克隆第一步)
python tools\build_sw.py --all --with-hw    REM 再多一个上板镜像 cm_hw.hex (含 HW_CLK_HZ)
python tools\check_equiv.py                 REM LSU 字节通道等价性: 675 万组穷举, ~5 s
```

### `verify` — ModelSim 增量回归

跑 **12 个**测试（矩阵 = `fpga_workflow.py` 的 `test_matrix()`），每个测试的缓存键 =
**参与编译的所有文件的内容哈希** + plusargs + 编译器版本。
内容没变就跳过（首次约 30 秒，之后未受影响时 < 1 秒）。

| 测试 | 程序 | 内容 |
|---|---|---|
| `tb_units` | — | 单元检查（ALU/立即数/控制单元/CSR/regfile/BTB/AMO…）**数量以运行时打印为准** |
| `tb_core_basic` | `basic.hex` | 系统级自校验（含非对齐访存）|
| `tb_core_trap` | `trap.hex` | Zicsr + 异常/中断 + MRET（带 `+illegal_trace` 诊断探针）|
| `tb_soc_timer_irq` | `timer_irq.hex` | P2 定时器中断（mtime/mtimecmp → mip.MTIP）|
| `tb_core_m_ext` | `m_ext.hex` | RV32M 边界 |
| `tb_core_amo` | `amo.hex` | RV32A 原子指令（AMO 读-改-写 + min/max 符号语义）|
| `tb_core_amo_diag` | `amo_diag.hex` | RV32A 单步诊断（tohost 直接上报失败环节）|
| `tb_soc` | `mmio.hex` | 外设 + UART 环回 |
| **`tb_sw_lu_bench`** | `lu_bench.hex` | **load-use 代价标定**：距离 1 = **2 拍** / 距离 2 = **1 拍** + checksum（`fcdb48e` 新增）|
| `tb_sw_hello` | `hello.hex` | 裸机 hello |
| `tb_sw_coremark` | `cm_rv32im.hex` | CoreMark 仿真 |
| `tb_sw_nn_demo` | `nn_demo.hex` | 应用场景：神经网络 GEMM 核心 |

当前基线：**PASS 12**，全量约 25 秒，未受影响时 < 3 秒。

**判定依据是 testbench 自己打印的 `RESULT : PASS`，不是 `vsim` 的退出码。**
> ModelSim 用 `quit -f` 结束时，无论测试成败都返回 0。
> 早期版本用 `"PASS" in output` 判定，把"编译失败"和"超时"都误判成了通过 ——
> 4 个测试里 2 个假通过。现在用白名单（必须显式出现 `RESULT : PASS`）+ 崩溃/超时否决。

**XFAIL 语义**（当某个测试记录的是"还没做"的状态时用 `expect_fail_ok` 标记）：
- `XFAIL` = 如预期地失败，不算回归失败
- `XPASS` = 它开始通过了 → **说明那个待办做完了，该把标记去掉**，退出码 1 提醒你

XFAIL 测试**永远重跑、永不缓存** —— 缓存命中会把"从失败翻转成通过"这个重要信号掩盖掉。
> P1（异常/中断接线）就是用这个机制收尾的：`tb_core_trap` 由 XFAIL 翻成 XPASS，
> 提示标记已过时，去掉后全套变成 7 PASS。P8 时 `tb_core_amo` / `tb_core_amo_diag`
> 也是同一路径（XFAIL → PASS），**当前 `expect_fail_ok` 已全部摘掉，没有 XFAIL 项**。

选项：`--only tb_units tb_soc`（只跑指定）、`--force`（忽略缓存）、`-v`（通过时也打印摘要）。
日志落在 `.fpga-cache/logs/<test>.log`。

### `synth` — Vivado 综合 → 四项指标（测量半环）

```bat
python tools\fpga_workflow.py synth --prog cm_hw --tag my-experiment --record
```

一条命令走完 **synth → opt → place → phys_opt → route → bitstream → 解析报告 → 算四项指标**：

- **全局构建锁**：用 `O_EXCL` 原子创建锁文件 + pid 陈旧锁回收，保证同一时刻只有一个
  Vivado。两个实例重叠会让 IP 生成器初始化失败，而且**谎报** `pkgIndex.tcl` 缺失 ——
  报错指向完全无关的地方，极难定位。
- **参数化程序镜像**：把 `build.tcl` 的 `set PROG <name>` 临时改成要测的程序，
  跑完**自动还原**。缺 `.dmem.hex` 会**报错退出**而不是静默继续（否则 `.data/.rodata`
  全 0，bitstream 照样生成 —— 正是本工程反复踩的静默失败）。
- **内容哈希缓存**：RTL / 镜像 / `build.tcl` 任一变化才重建，否则复用上次结果。
- **报告归档**：`timing.rpt`/`util.rpt` 每次构建都被覆盖，所以自动归档到
  `.fpga-cache/reports/<时间戳>-<prog>/`。**本项目出现过"旧报告被覆盖后无法复核历史数字"**。

四项指标一次算出（赛题记三项，但必须同时看绝对吞吐）：

```
CoreMark/MHz  |  fmax (由 WNS 推)  |  CoreMark/LUTs  |  绝对吞吐 = CoreMark/MHz x fmax
```

`--record` 会按 `results/coremark.csv` 的表头顺序追加一行。

### `report` — 三项指标 + 绝对吞吐

从 `results/coremark.csv` 读历史，重算并打印：
**CoreMark/MHz、LUT、fmax、CoreMark/LUTs、绝对吞吐（CoreMark/MHz × fmax）**，
以及"最新保留配置 vs 基线"的四项变化率。

> 对应 SKILL.md 第 10.2 节的铁律：**赛题记三项指标，改一项必须同时看三项 + 绝对吞吐。**
> 反例就是 RAS 实验：CoreMark/MHz +1.0%，但 fmax −4.6%、CoreMark/LUTs −4.4% → 净亏回退。
> 手算这个容易只看一项就下结论，所以做成一条命令。

---

## 2. `doctor.bat` — 零摩擦包装

对应"上板构建一律走 `.bat`"的既有约定。为什么需要它：

1. **绕开 R2**：`cmd` 里敲带引号/中文的命令本身就是踩坑来源，`.bat` 纯 ASCII 一次写好
2. **绕开环境剥离**：`cd /d "%~dp0.."` 到仓库根，Python 自解析路径
3. **一条命令**：`doctor` + `check` 串起来跑，收尾时只跑这一个

---

## 3. 设计约定

- **所有路径用正斜杠**：Vivado/Tcl 只吃 `/`，Windows 两种都认，所以统一 `/` 避免转换
- **不 glob `fpga/`**：该目录有大量 Vivado 生成物（`.runs` / `.cache` / `.Xil`），
  所以 SoC 侧文件在 `SOC_SV` 里显式列举
- **解析而不是重写清单**：工具**读**三处清单来对账，不自动改写它们。
  自动改写会把"漂移"变成"被掩盖的漂移"，失去检查意义
- **严重度必须准确**：报警不准 = 没人看报警。`KNOWN_UNWIRED` 白名单（配合 `[info]` 级）
  就是为这个存在的 —— 在研未接线的模块先挂白名单，别让它变成每次必红的噪音。
  它现在是**空的**（`csr_file.sv` 已接线），但机制保留

---

## 4. 还没做的（按价值排序的建议）

> ⚠️ 本清单是**工具刚落地时**写的，其中第 1/2/4 条**已经实现**（`metrics` 的解析、
> `hw` 的读回、`synth` 的自动指标）—— 保留原文是为了记录当时的缺口。
> **唯一要特别当心的是第 2 条**：fmax 的自动解析**曾经**因为 MMCM 而静默算错
> （`CODE-REVIEW-PLAN.md` **M-01/X-01**），**修好之前不要相信任何工具给出的 fmax**。

1. **`verify` 接上 Vivado 时序**：把 `report` 与 `verify` 合并成"一次改动 → 三项指标全出"
2. **fmax 自动测量**：现在 `fmax` 要从 `fpga/timing.rpt` 手工读，可以并入 `verify --synth`
3. **`.hex` 依赖图**：第 3 项检查现在是"sw/ 下任一文件比镜像新"的粗粒度判断，
   实际可以按 `#include` 建精确依赖
4. **上板闭环自动化**：`ila_cap_done.bat` 的读回结果可以直接解析进 `results/coremark.csv`
5. **R3 期望值独立验算**：`tb_units.sv` 里的期望值目前靠人算对（曾把
   `0xFFFFFFEC / 3` 写成 1431655762，实际 1431655758）。可以让 Python 独立算一遍再比对

---

## 5. 其他工具（不是 `fpga_workflow.py` 的子命令）

这些脚本**没有**统一入口，但都在用；新会话最容易漏掉它们（所以在此登记）。

| 文件 | 作用 |
|---|---|
| `flash_boot.ps1` | **QSPI 固化一条命令**：accessor 预载 + 建像 + 烧写 + 16 MB 全片读回逐字节比对（推荐入口）|
| `flash_run.ps1` / `flash_sweep.ps1` | 单次 cfgmem 实验 / 9+2 个 part 逐一试烧（`-Part` `-Action`）|
| `flash_program.tcl` / `flash_mkimage.tcl` | 底层烧写流程 / `write_cfgmem` 建 `.mcs`/`.bin` |
| `flash_probe.tcl` / `flash_readback.tcl` / `flash_partcheck.tcl` | 只读诊断：枚举 part 库 / 全片读回 / 对比器件过滤查找 |
| `path_hotspot.py` | 从 `fpga/timing.rpt` 出 top-N 关键路径 + 扇出榜（物理优化前先测量，见 PITFALLS R47）|
| `pdf_text.py` / `pdf_mine.py` | 厂商 PDF 取文（ToUnicode-aware；`flash_logs/guide_text/` 就是它的产物）|
| `hier_util.tcl` | 按层次报告资源（普通 `report_utilization` 看不出问题）|
| `amo_vectors.py` | RV32A 黄金向量生成器（108 条，独立 Python 参考实现）|

> QSPI 流程的完整报告（根因 / part 对照表 / 失败日志）见 `tools/flash_logs/REPORT.md`；
> 坑见 PITFALLS **R44/R45/R46**。顶层文档入口见 `HANDOFF.md` §7 与 `CODE-MAP.md` §3。
