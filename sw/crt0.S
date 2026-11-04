# =============================================================================
# crt0.S — 启动代码 (RV32I, 哈佛结构)
# -----------------------------------------------------------------------------
# 1) 建栈
# 2) 把 .data 从 IMEM 的 LMA 搬到 DMEM 的 VMA  (镜像只装在 imem 里)
# 3) 清零 .bss (dmem 上电内容未知)
# 4) 调 main; 返回后写 tohost = 1 通知结束, 然后自旋
#
# C 里若要用 exit(), 见 sw/portme.c 的 _exit 实现 (同样写 tohost)。
# =============================================================================

    .section .text.init, "ax", @progbits
    .globl _start
    .type  _start, @function
_start:
    la    sp, __stack_top          # 栈顶

    # ---- 搬运 .data: (a0)=源 LMA, (a1)=目标 VMA, (a2)=结束 VMA ----
    la    a0, __data_lma
    la    a1, __data_vma
    la    a2, __data_end
1:  bgeu  a1, a2, 2f
    lw    t0, 0(a0)
    sw    t0, 0(a1)
    addi  a0, a0, 4
    addi  a1, a1, 4
    j     1b

    # ---- 清零 .bss ----
2:  la    a0, __bss_start
    la    a1, __bss_end
3:  bgeu  a0, a1, 4f
    sw    zero, 0(a0)
    addi  a0, a0, 4
    j     3b

    # ---- 进 main ----
4:  call  main

    # main 返回: 写 tohost = 1 (tb 约定: 1 = 正常结束)
    li    t0, 0x90002000
    li    t1, 1
    sw    t1, 0(t0)
5:  j     5b
    .size _start, . - _start
