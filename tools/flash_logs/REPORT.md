> **范围声明（[跨厂商对比]）**：本文记录的是 **Artix-7 达芬奇 Pro 板**的 QSPI Flash 烧写验证（2026-10-17），属于参照线证据。
> **不要**把它当成紫光同创 PG2L100H 的 Flash 状态：**Pango 侧 Flash 当前尚未写入**（仅 JTAG RAM 下载，见 `fpga_pg2l100h/vision/BOARD_VALIDATION.md`）。

# QSPI Flash 固化 — 报告 (2026-10-09)

**结论：成功。** 达芬奇 Pro 板载 QSPI Flash 已写入我们的 bitstream，并**逐字节读回校验通过**。
Flash 中当前是 **SPIx4 启动镜像**（按正点原子官方原语生成）。

---

## 0. 一句话根因

原以为的"Vivado 2018.3 没有 n25q128 条目 / part 选错"**全是误判**。真正的两个根因是：

1. **SPI flash accessor 设计没有进 FPGA。** hw_server 的 `XicomFlash` 服务由一个小 accessor
   设计提供，纯 Tcl 流程里 `program_hw_cfgmem` **不会**自己加载它 →
   hw_server 日志 `XicomFlash override` → `Invalid context` →
   `[Labtools 27-3347] Failure to set flash parameters`。
   修法：先用 `program_hw_devices` 把
   `<Vivado>/data/xicom/cfgmem/bitfile/spi_xc7a100t_pullnone.bit` 下进去。
   证据：之后设备报 **"programmed with a design that has 1 SPI core(s)"**。
2. **`PROGRAM.CFG_PROGRAM` 必须为 1。** 为 0 时工具只擦除，然后发**一次** 4 ms 就失败的
   `programVerify`，**什么都没写**；更坑的是 `VERIFY=0`+`CFG_PROGRAM=0` 时它照样打印
   `INFO: [Labtoolstcl 44-377] Flash programming completed successfully`（静默空操作）。

---

## 1. 芯片身份（板上实测，非推断）

```
Mfg ID : 20   Memory Type : ba   Memory Capacity : 18   Device ID 1 : 0   Device ID 2 : 0
```
= JEDEC `20 BA 18` = **Micron N25Q128 / MT25QL128（128 Mbit, 3.3 V）**，与原理图 BOM 一致。

hw_server **自己会识别芯片**并和所选 part 比对：

```
[Labtools 27-3291] Flash Programming Unsuccessful. Part selected mt25qu128, but part mt25ql128 detected.
```

⇒ 正确 part = **`mt25ql128-spi-x1_x2_x4`**。
`n25q128-3.3v-spi-x1_x2_x4` 只是它的 **`MEM_DEVICE_ALIAS` 属性**，**不是**可检索的 part 名
（`get_cfgmem_parts n25q128-3.3v-spi-x1_x2_x4` → **0 个对象**，这就是当初"搜不到"的原因）。

厂商文档独立印证：《达芬奇Pro之FPGA开发指南 V2.2》§5.3.13（书页 p.245）
"…此时我们选择 **mt25ql128-spi-x1_x2_x4**，然后点击 OK 即可。"

---

## 2. 完整对照表（part × 结果）

`get_cfgmem_parts -of [current_hw_device]` → **145** 项；其中 `MEM_TYPE=spi & MEM_DENSITY=128 &
xc7a100t ∈ COMPATIBLE_DEVICES` 的 **只有 9 个**（从
`data/xicom/xicom_cfgmem_part_table.csv` 解析，共 511 行）。

| part 名 | 三态 | 原始报错关键行 |
|---|---|---|
| `mt25ql128-spi-x1_x2_x4` | ✅ **SUCCESS**（CFG_PROGRAM=1） | `INFO: [Labtoolstcl 44-377] Flash programming completed successfully` |
| `mt25ql128-spi-x1_x2_x4`（CFG_PROGRAM=0，早期） | ❌ 27-3347 | `Flash Programming Unsuccessful: Byte 42 does not match (FF != 00)` |
| `mt25qu128-spi-x1_x2_x4` | ❌ 27-3291（part 不匹配） | `Part selected mt25qu128, but part mt25ql128 detected.` |
| `s25fl128sxxxxxx0-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected s25fl128sxxxxxx0, but part mt25ql128 detected.` |
| `s25fl128sxxxxxx1-spi-x1_x2_x4` | ❌ 27-3291 | 同上 |
| `s25fl128l-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected s25fl128l, but part mt25ql128 detected.` |
| `mx25l12845g-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected mx25l12845g, but part mt25ql128 detected.` |
| `mx25u12835f-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected mx25u12835f, but part mt25ql128 detected.` |
| `is25lp128-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected is25lp128, but part mt25ql128 detected.` |
| `is25wp128-spi-x1_x2_x4` | ❌ 27-3291 | `Part selected is25wp128, but part mt25ql128 detected.` |
| `mt25ql128-spi-x1_x2_x4_x8` | ❌ **44-655**（器件层不支持） | `[Labtoolstcl 44-655] Cfgmem part mt25ql128-spi-x1_x2_x4_x8 is not supported for device xc7a100t.` |
| `w25q128fw-qspi-x1-single` | ❌ **44-655** | `[Labtoolstcl 44-655] Cfgmem part w25q128fw-qspi-x1-single is not supported for device xc7a100t.` |
| `w25q128fv-qspi-x1-single` | ❌ **44-655** | 同上 |

**两态语义不同（不是一回事）**：
* `44-655` = 该 part 的 `COMPATIBLE_DEVICES` 里**根本没有 xc7a100t**。全部 `w25q128*` 只有
  `qspi-*` 变体，`COMPATIBLE_PARTS` 只有 Zynq（`xa7z*/xc7z*`）⇒ **在 Artix-7 上物理不可用**，
  与 SPI 模式/`-interface` 参数无关。
* `27-3291` = part 在表里但**型号对不上**，工具实测芯片是 `mt25ql128`。
* `27-3347` = 通用"Flash Programming Unsuccessful"外壳码，**内部细节才是关键**：
  `Failure to set flash parameters`（accessor 缺失，参数阶段）vs
  `Byte N does not match`（verity 阶段）。早期把两者混为一谈是误判来源。

---

## 3. 可复现的确切命令序列

一条命令（本脚本会自动做 accessor 预载 + 生成镜像 + 烧写 + **16 MB 全片读回逐字节比对**）：

```powershell
# SPIx4（当前 flash 中的内容；厂商推荐）
powershell -File D:\USER\Desktop\FPGA\FPGA\tools\flash_boot.ps1 `
  -Bit D:/USER/Desktop/FPGA/FPGA/fpga/cpu_a100t_spix4.bit -Iface SPIx4

# 回退到 SPIx1（已同样验证通过）
powershell -File D:\USER\Desktop\FPGA\FPGA\tools\flash_boot.ps1 -Iface SPIx1
```

实测输出（SPIx4，**全新自动启动的 hw_server**，即普通用户环境）：

```
[1/4] accessor = ...\tools\flash_logs\accessor\spi_xc7a100t_pullup.bit (601662 bytes)
[2/4] image = D:\...\fpga\cpu_a100t_spix4.bin (3825788 bytes, SPIx4)
PART=mt25ql128-spi-x1_x2_x4 ACTION=program VERDICT=PROGRAM_OK
      Mfg ID : 20   Memory Type : ba   Memory Capacity : 18
      Readback Operation successful.
readback=16777216 image=3825788
VERIFY=PASS  (first 3825788 bytes identical; 0 stray non-FF bytes after image)
```

底层 Tcl 等价序列（`tools/flash_program.tcl`，action=`program`）：

```tcl
open_hw; connect_hw_server; open_hw_target
set dev [lindex [get_hw_devices] 0]; current_hw_device $dev
set_property PROGRAM.FILE <spi_xc7a100t_pullup.bit> $dev
program_hw_devices $dev                      ;# ← 关键第 1 步：加载 accessor
refresh_hw_device -update_hw_probes false $dev
set_pobj [get_cfgmem_parts -of [current_hw_device] mt25ql128-spi-x1_x2_x4]
set_property PROGRAM.HW_CFGMEM "" $dev       ;# 复位上一次绑定
delete_hw_cfgmem [get_hw_cfgmems -quiet]
create_hw_cfgmem -hw_device $dev [lindex $set_pobj 0]
set cfg [get_property PROGRAM.HW_CFGMEM $dev]
set_property PROGRAM.FILES {cpu_a100t_spix4.bin} $cfg
set_property PROGRAM.ERASE        1 $cfg
set_property PROGRAM.BLANK_CHECK  1 $cfg
set_property PROGRAM.CFG_PROGRAM  1 $cfg     ;# ← 关键第 2 步
set_property PROGRAM.VERIFY       1 $cfg
program_hw_cfgmem $cfg
```
成功的 `programVerify` 是**连续一串**（每个 ~1.4 s），log 证据：
```
TCF 14:31:41.321: Command: C 160 XicomFlash programVerify ...
TCF 14:31:42.728: Command: C 161 XicomFlash programVerify ...
... (C 162..C 165)
```
> `PROGRAM.FILES` 只接受 `.mcs`/`.bin`；把 `.prm` 一起塞进去会被拒：
> `[Labtoolstcl 44-518] Invalid configuration file assigned. Valid file type extension .mcs or .bin`
> （GUI 里那个 .prm 输入框不走这个属性。）

### x4 镜像用的原语（厂商原话，已进 `fpga/build.tcl`，env 门控）
```
set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]
set_property CONFIG_MODE SPIx4 [current_design]
set_property BITSTREAM.CONFIG.CONFIGRATE 50 [current_design]
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property BITSTREAM.CONFIG.UNUSEDPIN PULLUP [current_design]
```
构建日志（6 条全部 `BUILD_SPIBOOT_SET`，并 `BUILD_SPIBOOT_VERIFY` 回读确认生效）：
```
BUILD_SPIBOOT_SET: BITSTREAM.CONFIG.SPI_BUSWIDTH=4
BUILD_SPIBOOT_SET: CONFIG_MODE=SPIx4
BUILD_SPIBOOT_SET: BITSTREAM.CONFIG.UNUSEDPIN=PULLUP
BUILD_BIT_NAME=cpu_a100t_spix4.bit
BUILD_OK
```

---

## 4. 上电自举验证步骤（**只剩这一步需要人做**）

1. 关掉 Vivado / 断开或让 JTAG 空闲（不要让 Hardware Manager 占着）。
2. **断电再上电**（拔插电源或关/开电源开关），模式拨码**不要动**。
3. 看板上 **DONE 绿灯**：上电后约 1 s 内常亮 ⇒ 从 QSPI 自举成功。
4. 想进一步确认"跑的是我们的 RISC-V 核"：本设计的观测点在 `ila_cap_done.bat` 的流程里
   （`program_hw_devices` + ILA）。**注意**：自举后 FPGA 里是我们自己的设计（不是 ILA 版），
   若要用 ILA 需重新 JTAG 下载 `cpu_a100t.bit`。
5. 若 DONE 不亮（x4 未自举）：跑
   `powershell -File tools\flash_boot.ps1 -Iface SPIx1` 回退到 x1 镜像再上电。

---

## 5. 板子安全性（有没有进过擦除/写入）

| 阶段 | 是否动过 flash |
|---|---|
| 早期所有 27-3347 尝试 | **没有**。全部停在 "set flash parameters"，发生在任何擦/写之前 |
| 首次 blank check（accessor 就位后） | 只读，报 `Byte 32 is not blank` |
| 早期 CFG_PROGRAM=0 的 program | **擦除执行了**，但**写入为 0 字节** |
| 修复后的 program | 擦除 + 写入 + verify，均成功 |

擦除确实发生过：修复前 flash 非空 → 擦后 16 MB 读回 **0 个非 FF 字节**。
⚠️ **代价**：板上原有的出厂镜像（第一个非 FF 字节恰在 offset 32，
符合任何 write_cfgmem 生成的 7 系列镜像头部）已被擦掉，现在是我们的镜像。

---

## 6. 改动清单

| 文件 | 说明 |
|---|---|
| `fpga/build.tcl:207-263` | 新增 QSPI 启动镜像原语块（env 门控：`SPI_BOOT_X4=1` / `SPI_BOOT=<n>` / `BIT_NAME=<f>`）；每条 `set_property` 包在 `catch` 里 + 回读打印。**不设 env 时行为与原来逐字节相同**，默认产物仍是 `cpu_a100t.bit` |
| `tools/flash_program.tcl` | 新增：accessor 预载 + 器件过滤的 cfgmem 绑定 + 8 种 action（probe/blankcheck/program/program_doc/...） |
| `tools/flash_run.ps1` | 单次实验 runner，完整 stdout/stderr 落盘 + 三态判定 |
| `tools/flash_probe.tcl` | 只读：枚举 part 表 / help / 属性 |
| `tools/flash_readback.tcl` | 只读：16 MB 全片读回 |
| `tools/flash_partcheck.tcl` | 只读：对比"器件过滤 vs 不过滤"查找，捕获 44-655 原文 |
| `tools/flash_mkimage.tcl` | `write_cfgmem` 生成 .mcs/.bin |
| `tools/flash_sweep.ps1` | 9+2 个 part 逐一试烧，出 CSV |
| `tools/flash_boot.ps1` | **一条命令端到端 + 自校验**（推荐入口） |
| `tools/pdf_text.py` | ToUnicode-aware PDF 取文（厂商指南，2405 页） |
| `tools/pdf_mine.py` | 早期失败版本，保留作对照 |
| 产物 | `fpga/cpu_a100t_spix1.{bit,mcs,bin,prm}`、`fpga/cpu_a100t_spix4.{bit,mcs,bin,prm}`、`fpga/x4_{timing,timing_paths,util}.rpt` |

**未 commit、未切分支；`cpu_a100t.bit` 与 `timing.rpt`/`util.rpt` 未被改动**（x4 构建后已还原并核对
`git status` 只显示 ` M fpga/build.tcl` + 新文件）。
`python tools/fpga_workflow.py check` → **exit=0**（6 条 warning，均为既有的"非 ASCII 注释"类别）。

---

## 7. 复现脚本、日志位置

* `tools/flash_logs/sweep_results.csv` — 上面那张对照表的机器可读版
* `tools/flash_logs/*__*.full.log` — 每次实验的完整 Vivado 输出（未被截断）
* `tools/flash_logs/hw_server.log` — hw_server 侧 TCF 命令流（`XicomFlash override/selectQSPI/erase/programVerify`），**这是最硬的证据**
* `tools/flash_logs/readback.bin` — 最近一次 16 MB 全片读回
* `tools/flash_logs/guide_text/guide_text.txt` — 厂商指南全文（2405 页）
