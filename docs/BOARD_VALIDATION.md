> **范围/口径声明（交付主体）**：本文属于**紫光同创 PG2L100H** 交付主体线。文中"实测"指上板实测并应附证据路径；"未达成/未部署"项见根 [`COMPLIANCE.md`](https://github.com/fgjg70/FPGA/blob/main/COMPLIANCE.md) §4 边界清单。注意：**Pango 侧 Flash 当前尚未写入**（JTAG RAM 下载），详见 [`fpga_pg2l100h/vision/BOARD_VALIDATION.md`](https://github.com/fgjg70/FPGA/blob/main/fpga_pg2l100h/vision/BOARD_VALIDATION.md)。

# PG2L100H实际下载与待完成联调

## 【当前版本】2026-11-04 11:41 —— 95 MHz 双发射（`dual_j11`）

| 项 | 值 |
|---|---|
| 位流 | `build/dual_j11/generate_bitstream/top_pg2l100h_vision.sbit`，3,791,112 B |
| SHA256 | `51f5eda2d5e2d178380072dd186902d875836bb031a1c0b041208704fe30cf4c` |
| CPU | **双发射（2-wide in-order）@ 95 MHz**；PLL: 50→/2(PFD 25)→×38(VCO 950)→/10 |
| 其他域 | NPU/采集/MMIO 50 MHz、视频 27 MHz、LCD 9 MHz、相机 MCLK 12.5 MHz |
| 时序 | **All Constraints Met**；CPU 域 WNS **+0.493 ns**；hold WHS +0.350 ns |
| 资源 | LUT 9980(15%) / FF 6573(5%) / APM 13(6%) / DRM 99(64%) |
| 功耗 | ≈1.02 W |
| 上板证据 | 🔲 **未入库**：`build_summary.json` 的 `hardware_tested` 仍为 `false`，无下载日志/配置读回/照片 |

> 完整说明见 [`CURRENT-BUILD.md`](CURRENT-BUILD.md)。**下文均为历史记录**：
> 上一版为 125 MHz 单发射（位流 `f179a403…`），其数字与本版不可混用。

---

## 2026-11-03 13:30：发现FPGA未配置，重新下载125/50 MHz版

用户再次反馈白屏、SW2及其他按键无反应。13:28:00扫描确认器件PG2L100H、ID0x00602899，但DONE为0；配置状态0x00180c80，done和done_i均为0，gwen、grsn、gouten也为0。当时没有运行已完成配置的用户逻辑，按SW2逻辑复位不能重新装入工程。之前的JTAG下载均仅写FPGA RAM；断电会丢失，配置清除或重新上电也需重新下载。本轮没有证据区分此前配置被清除的具体原因。未配置状态的pll_lock虽为1，但不能据此认定CPU时钟或应用在运行。

核对本地源码、暂存项目、模型与固件哈希通过，并确认位流为已修正HIGH带宽的125/50 MHz版，SHA256为 `f179a403c71df8d6c4075faacc28616d2268e9121c35fcd45d82d7eb1162fef2`。13:30:00已重新下载到FPGA RAM，13:30:02和13:30:52两次读回均为0x081ffca0，DONE、done_i及PLL锁定均为1，五项配置错误均为0。CPU、指令存储和数据存储仍配置125 MHz，NPU、采集及MMIO仍为50 MHz，未更改RTL或重新生成位流。

用户随后确认“摄像头显示在屏幕上了”，本次位流的摄像头到LCD预览通路已得到实物目视确认。CPU固件执行、LED心跳、实际按键、识别精度和图像方向仍未单独确认；预览硬件可独立于CPU运行。JTAG客户端及本轮服务器均已退出，未写Flash。证据见 `validation/hardware_20261103/cpu125_white_screen_followup/`。调试时保持供电；断电后需重新下载，SW2只能复位已加载的逻辑。

## 2026-11-03：修正125 MHz PLL带宽并重新烧录

用户反馈125 MHz版白屏，SW2及其他按键无反应；修复前JTAG实读状态0x0817fca0，PLL锁定标志为0。CPU PLL使用50 MHz输入、IDIV=1，PFD为50 MHz，却选择OPTIMIZED带宽，违反PDS附带UG041001 V1.4第33页的带宽要求。系统复位等待视频PLL与CPU PLL均锁定，因此PLL未锁定会同时阻止LCD、摄像头、按键和CPU启动。

将CPU PLL的BANDWIDTH修正为HIGH，CPU、指令存储和数据存储仍为125 MHz，NPU、MMIO和摄像头采集仍为50 MHz。01:05:28完成原生综合、布局布线及新位流生成，所有约束通过，CPU建立WNS为+0.609 ns，50 MHz域为+7.052 ns。严格Verilog-2005编译和Pango PLL模型板级仿真均通过；新增检查可拒绝旧带宽配置，并检查SW2复位、两个PLL重新锁定和复位释放。

01:07:47已将修正版写入FPGA RAM，器件ID0x00602899，DONE及done_i均为1，五项配置错误均为0；01:07:49和01:08:11两次状态均为0x081ffca0，PLL锁定标志已变为1。位流3791112字节，SHA256为 `f179a403c71df8d6c4075faacc28616d2268e9121c35fcd45d82d7eb1162fef2`。本轮JTAG客户端与服务器均已退出。

当前已验证配置完成及PLL锁定，尚未取得修复后LCD画面、物理按键响应或应用UART的实物确认，也未用示波器测量125 MHz。下一步观察LCD是否显示CAM WAIT、CAM ERR或实时画面，以及SW2复位和SW5彩条切换是否响应。证据、修复前后状态、位流及本轮两项回归见 `validation/hardware_20261103/cpu125_white_screen/`；机器可读状态见 `validation/hardware_summary.json`。

## 历史三模式125/50 MHz版本：配置完成，但PLL未锁定

2026-11-02本轮将SW4长按统一为HANDWRITE→GESTURE→COCO80三模式循环。CPU、IMEM和DMEM维持125 MHz，NPU、MMIO和摄像头保持50 MHz。七项定向检查通过，包括实际生产固件九类/十类头文件、严格Verilog、状态机、工具、LCD和两套RV32整机；新增去抖按键完整循环、忙时取消和未部署模式互锁。原生布局布线和详细时序均通过，CPU建立WNS为+0.553 ns，50 MHz域为+7.006 ns；映射LUT4469、FF3706、APM10、DRM64。操作见 `MODES.md`，时钟计时区别见 `CLOCK_SPLIT.md`。

2026-11-02 23:45:46按用户要求再次将同一份125/50 MHz位流写入FPGA RAM；此前一次成功下载为23:09:23。器件ID确认0x00602899，cfg_program成功、DONE及done_i均为1，超时、读回CRC、认证、CRC和ID错误位均为0。位流为 `build/j11/generate_bitstream/top_pg2l100h_vision.sbit`，3791112字节，SHA256为 `0b3b59514c567ab5c313c3f858d7659f19aaf005ab8c7cebcca95e7adaf78baa`，22:46:59生成。Flash未写入，断电后RAM配置丢失。本轮交互式配置客户端成功连接USB Cable II，下载后已释放本轮JTAG服务器。

本次配置状态连续两次读为0x0817fca0，其中pll_lock位19仍为0，over_temp位27为1；实际温度及其标志解释未测定。DONE和配置错误检查证明文件已配置，不能据此认定125 MHz用户时钟正常运行。尚待按SW2逻辑复位，并观察LCD、心跳LED或读取应用UART确认启动；未根据聚合状态位判断具体哪个PLL。本次未修改RTL或重新构建位流。

当前构建权重仍是原数字模型和旧九类手势；北方十类尚待实拍训练，COCO80只完成主机前向和资源核查。三模式中的COCO80设计为保持预览并显示NO MODEL，硬件停止NPU，固件不读取旧结果或产生识别事件；板上行为尚待观察。80类移植边界见 `model/COCO80_FEASIBILITY.md`。本次成功配置的文件、命令和PDS日志在 `validation/hardware_20261102/program_cpu125_20261102_234420/`，前次成功记录保留在 `validation/hardware_20261102/program_cpu125_20261102_230549/`。三模式构建、七项检查和此前两次失败扫描冻结在 `validation/hardware_20261102/three_modes_20261102_225013/`。首次时钟拆分版本15项完整回归及下载失败记录保留在 `validation/hardware_20261102/cpu125_npu50_20261102_220034/`，不能将其位流哈希或15项结果写成本轮执行结果。

## 历史成功下载：50 MHz基线

日期：2026-11-02，Asia/Shanghai。已使用本机PDS 2022.2-SP6.1完整版本完成实际综合、布局布线、时序分析和位流生成，并经PANGO USB CABLE II下载到扫描确认的PG2L100H。应用层的摄像头、LCD、数字/手势识别验收尚未完成。

20:54:59已下载当前北方手势一至十接口版本，保留MVFP=37的180度旋转设置。用户此前实拍确认DVP版已有真实画面，ID76、VR73、C1 E0、完整帧非零，但方向倒置；旋转后的正立画面仍待确认。本次完整13项回归、原生PDS构建、时序和源码哈希通过，另补充实际固件九类/十类解码和训练数据评估测试。当前证据目录为 `validation/hardware_20261102/northern10_interfaces_20261102_204814/`，此前相机实拍观察记录保存在 `validation/hardware_20261102/dvp18_20261102_194149/`。

手型以用户参考图为准，十为双手食指交叉；采集、训练导出、固件和LCD已支持十类，但当前下载仍包含原九类权重，不能据此声称已能识别新标准或“十”。缓存中标为十的22组原图全部是握拳，不采用；一的部分旧样本也与伸食指要求不符。标准、数据审核及采集流程见 `model/GESTURE_STANDARD.md`。

此前FIFO位流的实拍为CAM ERR、ID00、VR00、ST00、RX30、F0000、T0000、C0 E1，首次SCCB写入即失败。最新两张实物照片确认蓝色摄像头是18针DVP，而此前错误地按资料中的22针AL422模块适配。旧映射把实物SCL作为VSYNC计数、未提供MCLK、把PWDN作为高电平FIFO控制，符合触碰后V增加且无图像的现象。当前已重映射并更换采集器；这确认了接口适配错误，尚不能排除接触不良或模块本身问题。新约束假定18针逐号相连，J7-1为3.3V、J7-2为GND，旧22针接线表停用。

## 已取得的实测证据

| 项目 | 本次结果 | 证据 |
|---|---|---|
| 芯片识别 | PG2L100H，ID 0x00602899 | 本轮证据目录/jtag_scan.log |
| 目标构建 | Logos2 / PG2L100H / -6 / FBG676，PDS构建成功 | build/j11/build_summary.json |
| 时序 | All Constraints Met，clk50建立WNS为7.912 ns | build/j11/place_route/top_pg2l100h_vision_timing_summary_after_hold_fix.txt |
| 映射资源 | LUT 4261/66600，FF 3460/133200，APM 13/240，DRM 65/155 | build/j11/device_map/run.log |
| 下载 | Program成功，DONE=1，done_i=1 | 本轮证据目录/jtag_program.log |
| 配置错误位 | id_err、crc_err、aut_err、rbcrc_err、timeout均为0 | 本轮证据目录/jtag_program.log |
| 配置状态 | 0x081ffca0，over_temp位为1 | 本轮证据目录/jtag_program.log |
| 应用串口 | 仍只有FTDI COM12/COM13，没有CH340 | 本轮证据目录/serial_inventory.json |
| 源码回归 | 当前完整13项全部通过，另补九类/十类生产固件与训练数据评估测试 | 本轮证据目录/simulation_summary.json、hand_firmware_summary.json、training_data_supplement.json |

CPU、NPU和采集的设计与约束为50 MHz，视频27 MHz、LCD像素9 MHz，相机MCLK 12.5 MHz、SCCB 50 kHz。PWDN固定低，MCLK启动1 ms后释放RESET，再等待50 ms配置。尚未通过示波器测量相机MCLK/PCLK及数据相位；原生时序通过与实际外部采样验证分开记录。映射资源来自当前DVP18工程。

下载文件：build/j11/generate_bitstream/top_pg2l100h_vision.sbit，3791112字节，SHA256：

```text
20c02300464a9122a146ae9dd9fc9c55ee8fcc55bc0d7a47c6d2f0f2e312f9a3
```

本次使用cfg_program作JTAG逻辑配置，未执行Flash、eFuse或密钥写入命令。此RAM配置断电后丢失；断电后需重新下载才能验证本版应用。本轮证据目录保存实际命令program_ram.tcl、下载日志、完整位流副本和构建/仿真摘要。

## 本轮修正

当前活动采集器为 `camera_dvp_capture.v`，以50 MHz同步慢速DVP输入，高低字节合成RGB565，仅接收320×240完整帧并抽样为160×120；空同步脉冲、短行、奇数字节或总数不符会被拒绝，显示确认前禁止覆盖。P/L/V为原始边沿计数，F为合格帧，B为拒绝帧，区分干扰与完整图像。

复位同步器的GTP_DFF_P/P异步端例外保留，同步器级间及同步释放路径继续计时。DVP输入只对首级同步路径作异步处理，显示数据包最大延迟20 ns。PDS合法重写元数据后的artifact哈希按实际输出更新。本次完整13项回归、原生构建及build.py --check通过；固件十类分支还通过主机MMIO夹具执行实际生产解码代码验证，但尚无十类训练模型的整机或实物识别结果。

配置进行中显示黄色CAM WAIT，配置失败显示红色CAM ERR，配置完成但尚无整帧时显示NO FRAME。128位摄像头诊断随272位完整包在视频帧边界交接。当前保持SCCB应答与PID/版本检查，不以强制成功绕过实际故障；数字、手势模型和50 MHz主频保持原配置。

## 当前需要接线配合的部分

电脑仍只枚举到同一个FTDI下载器的COM12/COM13，未识别板载CH340。本轮没有打开下载器串口，JTAG烧录成功后已释放自己启动的服务器。新位流应用状态尚不能从当前USB连接读取。

首先确认当前旋转版LCD人像已正立，保持ID76、VR73、C1 E0且F有变化。此前用户照片已确认真实预览，完整识别精度和长时间稳定性仍未验证。串口联调另接J10 CH340N的USB接口，J9的1-2、3-4接通，115200、8N1、无流控。确认周期状态行NUMGUARD S=...后再执行：

1. 输入?，核对配置成功、图像有效、推理计数NN增长和NPU周期；记录所有失败状态。
2. 输入g、l，分别等待MODE HAND 1-9和MODE LABEL 0-9，核对切换后旧框清除；SW4长按1秒也需实物操作验证。
3. 用tools/capture_numeric.py分别采集标签和手势输入，校验4096字节、补零、CRC32及手势0/127轮廓。新手势数据保存到camera_hand_northern10，依照参考图采集一至十、独立人物/会话与负样本；十的标注框要同时包含双手。当前九类固件也能提供十手型的原始输入快照，此时不表示模型能识别十。没有有效相机帧时保留故障，不填写识别结果。
4. 目视检查J11 AN430的实时画面、红框和英文标签，保存实际照片/连续录像。软件预览图不能代替此步骤。
5. 按competition/ACCEPTANCE.md采集独立人物和标签样本，统计成功、误分类、拒识及无事件。现有手势公开图验证仍只有52/148正确接受，尚未取得板上识别精度。

另外，本轮下载后的配置状态显示over_temp位为1。尚未取得实际温度或该位在当前未使用ADC配置下的解释，不能据此填写温度数值或长时间稳定运行通过。需要核实芯片温度和散热，再做长时间运行验收；原始位值保留在hardware_summary.json和JTAG日志中。

机器可读汇总为validation/hardware_summary.json：本轮125/50 MHz版本programming_success=false、application_verified=false、overall_acceptance_complete=false；上一次50 MHz成功配置单列在last_successful_programming。以下基线数据不能作为新版本已烧录或应用验收的证据。
