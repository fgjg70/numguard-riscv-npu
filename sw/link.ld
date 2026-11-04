/* ============================================================================
 * link.ld — RV32I 哈佛结构裸机链接脚本
 * ----------------------------------------------------------------------------
 * 本核是 *哈佛结构*: imem (0x8000_0000) 与 dmem (0x9000_0000) 是两个独立地址
 * 空间, 而 load/store **只能到达 dmem** —— 读写 0x8000_xxxx 会落到 MMIO 总线读到 0。
 *
 * 【.data 的初值从哪来 —— 这条注释曾被写反过, 别再改回去】
 * 早期设计想用 "LMA 在 IMEM / VMA 在 DMEM" 的经典做法, 由 crt0 在启动时把初值
 * 从 imem 搬到 dmem。**在本核上这是错的**, 因为那条搬运用的 `lw` 读 imem 读不到,
 * 结果会把 .data 整段搬成 0 (真实踩过的故障)。
 *
 * 正解 (当前实现): .data/.rodata 的 VMA 直接放在 DMEM, **初值由 dmem 自己的
 * 初始化镜像装入** (仿真 +dmem=<file>; 综合 DMEM_INIT_FILE 宏)。
 * 此时 __data_lma == __data_vma, crt0 的搬运循环自然空转, crt0 实际只需清 .bss。
 *
 * 内存布局 (dmem 16KB = 0x9000_0000..0x9000_3FFF):
 *   0x9000_0000 .. 0x9000_1FFF   .data / .bss        (8KB, 有 ASSERT 守卫)
 *   0x9000_2000 .. 0x9000_2003   tohost              (固定约定, 见 rv32_pkg)
 *   0x9000_2004 .. 0x9000_2BFF   栈 (向下生长, 顶 0x9000_2C00)
 *   0x9000_3000 .. 0x9000_3FFF   日志缓冲区 (printf 重定向目标, 供 tb / ILA 读取)
 *
 * 布局调整记录: 原先 .data/.bss 只有 4KB, CoreMark 正好用满 (2064+2032=4096);
 * 上板验证需要再放几个全局变量, 12 字节就溢出, 于是把 tohost 从 0x1000 上移到
 * 0x2000, .data/.bss 扩容到 8KB。三处 tb 与本文件的 ASSERT 必须同步改。
 * ========================================================================== */

OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{
  IMEM (rx) : ORIGIN = 0x80000000, LENGTH = 16K
  DMEM (rw) : ORIGIN = 0x90000000, LENGTH = 16K
}

PROVIDE(__stack_top = 0x90002C00);
PROVIDE(__log_base  = 0x90003000);
PROVIDE(__log_end   = 0x90004000);
PROVIDE(__tohost    = 0x90002000);

SECTIONS
{
  .text : {
    KEEP(*(.text.init))
    *(.text .text.*)
    . = ALIGN(4);
  } > IMEM

  /* 【哈佛结构的坑 —— 改过两次, 记牢】
   * 本核 load/store 只经 LSU 访问 dmem 窗口 (0x9000_0000 起), 读 0x8000_xxxx
   * 会落到 MMIO 总线并读到 0。所以:
   *   1) .rodata 不能留在 IMEM —— 否则字符串字面量全变空串;
   *   2) .data 初值也 *不能* 靠 crt0 从 IMEM 搬运 —— 那条 lw 同样读不到,
   *      会把 .data 整段搬成 0。
   * 正解: .data/.rodata 的 VMA 就放在 DMEM, 初值由 dmem 的初始化镜像直接装入
   * (仿真: +dmem=<file>; 综合: DMEM_INIT_FILE 宏), crt0 只需清 .bss。
   * 此时 __data_lma == __data_vma, crt0 的搬运循环自然空转。 */
  .data : {
    __data_vma = .;
    *(.srodata .srodata.*)
    *(.rodata .rodata.*)
    *(.sdata .sdata.*)
    *(.data .data.*)
    . = ALIGN(4);
    __data_end = .;
  } > DMEM
  __data_lma = ADDR(.data);

  .bss (NOLOAD) : {
    __bss_start = .;
    *(.sbss .sbss.*)
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(4);
    __bss_end = .;
  } > DMEM

  /* .data + .bss 不得侵入 tohost (0x90002000) */
  ASSERT(__bss_end <= 0x90002000, "ERROR: .data+.bss overflow past 0x90002000")
  /* dmem 初始化镜像从 0x9000_0000 开始平铺, .data 必须紧贴起点 (elf2hex 依赖此约定) */
  ASSERT(ADDR(.data) == 0x90000000, "ERROR: .data must start at 0x90000000 for the dmem image")
  /* 代码镜像不得超出 imem */
  ASSERT(SIZEOF(.text) <= 0x4000, "ERROR: .text overflow past imem")

  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.riscv.attributes) }
}
