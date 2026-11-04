#!/usr/bin/env python3
"""asm.py — RV32I 迷你汇编器 (教学用)

用法:
    python tools/asm.py sim/prog/basic.S -o sim/prog/basic.hex

支持:
  - .text / .data 段 (.text -> 0x80000000, .data -> 0x90000000, 见 rv32_pkg 地址映射)
  - .org <addr>   直接指定后续地址
  - .word <imm>   原始数据字
  - 标签: name:    (两遍扫描, 分支/跳转自动算相对偏移)
  - 伪指令: li rd, imm32 / mv rd, rs / nop / j label / ret
  - 指令: lui auipc jal jalr beq bne blt bge bltu bgeu
          lb lh lw lbu lhu sb sh sw
          addi slti sltiu xori ori andi slli srli srai
          add sub sll slt sltu xor srl sra or and
  - 操作数: 十进制 / 0x 十六进制 / 负数 / 标签
  - 寄存器: x0-x31 或 ABI 名 (zero ra sp gp tp t0-t6 s0-s11 a0-a7)

输出:
  - hex 文件 (每行一个 32 位字, 供 $readmemh; 只含 .text, 从 .text 起点开始)
  - 汇编清单 + 标签表 (打印到 stdout, 便于对照波形调试)
"""

import sys
import re

TEXT_BASE = 0x80000000
DATA_BASE = 0x90000000

ABI = {
    'zero': 0, 'ra': 1, 'sp': 2, 'gp': 3, 'tp': 4,
    't0': 5, 't1': 6, 't2': 7,
    's0': 8, 'fp': 8, 's1': 9,
    'a0': 10, 'a1': 11, 'a2': 12, 'a3': 13, 'a4': 14, 'a5': 15, 'a6': 16, 'a7': 17,
    's2': 18, 's3': 19, 's4': 20, 's5': 21, 's6': 22, 's7': 23,
    's8': 24, 's9': 25, 's10': 26, 's11': 27,
    't3': 28, 't4': 29, 't5': 30, 't6': 31,
}

# ---- Zicsr: CSR 名字 -> 地址 (只列本核实现的) ----
CSR_NAME = {
    'mstatus': 0x300, 'misa': 0x301, 'mie': 0x304, 'mtvec': 0x305,
    'mscratch': 0x340, 'mepc': 0x341, 'mcause': 0x342, 'mtval': 0x343,
    'mip': 0x344,
    'mvendorid': 0xF11, 'marchid': 0xF12, 'mimpid': 0xF13, 'mhartid': 0xF14,
}

R_OPS = {
    'add': (0x00, 0), 'sub': (0x20, 0), 'sll': (0x00, 1), 'slt': (0x00, 2),
    'sltu': (0x00, 3), 'xor': (0x00, 4), 'srl': (0x00, 5), 'sra': (0x20, 5),
    'or': (0x00, 6), 'and': (0x00, 7),
    # ---- RV32M: funct7 = 0x01 ----
    'mul': (0x01, 0), 'mulh': (0x01, 1), 'mulhsu': (0x01, 2), 'mulhu': (0x01, 3),
    'div': (0x01, 4), 'divu': (0x01, 5), 'rem': (0x01, 6), 'remu': (0x01, 7),
}
I_ARITH = {
    'addi': 0, 'slti': 2, 'sltiu': 3, 'xori': 4, 'ori': 6, 'andi': 7,
    'slli': 1, 'srli': 5, 'srai': 5,
}
LOAD = {'lb': 0, 'lh': 1, 'lw': 2, 'lbu': 4, 'lhu': 5}
STORE = {'sb': 0, 'sh': 1, 'sw': 2}
BRANCH = {'beq': 0, 'bne': 1, 'blt': 4, 'bge': 5, 'bltu': 6, 'bgeu': 7}


def reg(tok):
    t = tok.strip().lower()
    if t.startswith('x') and t[1:].isdigit():
        n = int(t[1:])
        if 0 <= n <= 31:
            return n
    if t in ABI:
        return ABI[t]
    raise ValueError('bad register: %s' % tok)


def value(tok, labels, pc=0, relative=False):
    """解析立即数: 十进制/十六进制/负数/标签"""
    t = tok.strip()
    if t in labels:
        v = labels[t] - pc if relative else labels[t]
        return v
    v = int(t, 0)
    return v


def csr(tok, labels):
    """CSR 操作数: 支持助记名 (mstatus/mtvec/...) 也支持数字"""
    t = tok.strip()
    if t in CSR_NAME:
        return CSR_NAME[t]
    return value(t, labels) & 0xFFF


def r_type(f7, rs2, rs1, f3, rd):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0b0110011


def i_type(imm12, rs1, f3, rd, opc):
    return ((imm12 & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | opc


def s_type(imm12, rs2, rs1, f3):
    return (((imm12 >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) \
           | ((imm12 & 0x1F) << 7) | 0b0100011


def b_type(imm13, rs2, rs1, f3):
    return (((imm13 >> 12) & 1) << 31) | (((imm13 >> 5) & 0x3F) << 25) | (rs2 << 20) \
           | (rs1 << 15) | (f3 << 12) | (((imm13 >> 1) & 0xF) << 8) \
           | (((imm13 >> 11) & 1) << 7) | 0b1100011


def u_type(imm20, rd, opc):
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | opc


def j_type(imm21, rd):
    return (((imm21 >> 20) & 1) << 31) | (((imm21 >> 1) & 0x3FF) << 21) \
           | (((imm21 >> 11) & 1) << 20) | (((imm21 >> 12) & 0xFF) << 12) \
           | (rd << 7) | 0b1101111


def parse_mem_operand(tok, labels):
    m = re.match(r'^(.+?)\(\s*([A-Za-z0-9_]+)\s*\)$', tok.strip())
    if not m:
        raise ValueError('bad memory operand: %s' % tok)
    return value(m.group(1), labels), reg(m.group(2))


def li_split(v):
    """li 伪指令的 lui/addi 拆分, 返回 (hi20, lo12)"""
    hi = (v + 0x800) >> 12
    lo = v - (hi << 12)
    return hi & 0xFFFFF, lo & 0xFFF


def instruction_size(mn, ops, labels):
    """该行占用的字节数 (li 可能展开为两条)"""
    if mn == 'li':
        t = ops[1].strip()
        try:
            v = int(t, 0)
        except ValueError:
            # 符号操作数 (如 li t0, handler): 第一遍标签还没算出来。
            # **两遍都必须给同一个答案**, 否则地址布局会错位 —— 所以一律按
            # 8 字节预留。imem 地址 0x8000_xxxx 的 li 本来就是 8 字节, 不浪费。
            return 8
        hi, lo = li_split(v)
        return 8 if (lo != 0 or hi == 0) else 4
    return 4


def encode(mn, ops, pc, labels):
    """编码一行, 返回指令字列表"""
    if mn == 'nop':
        return [i_type(0, 0, 0, 0, 0b0010011)]
    if mn == 'mv':
        return [i_type(0, reg(ops[1]), 0, reg(ops[0]), 0b0010011)]
    if mn == 'li':
        rd = reg(ops[0])
        hi, lo = li_split(value(ops[1], labels))
        out = [u_type(hi, rd, 0b0110111)]
        if lo != 0 or hi == 0:
            out.append(i_type(lo, rd, 0, rd, 0b0010011))
        return out
    if mn == 'j':
        return [j_type(value(ops[0], labels, pc, True) & 0x1FFFFF, 0)]
    if mn == 'ret':
        return [i_type(0, 1, 0, 0, 0b1100111)]
    if mn in R_OPS:
        f7, f3 = R_OPS[mn]
        return [r_type(f7, reg(ops[2]), reg(ops[1]), f3, reg(ops[0]))]
    if mn in I_ARITH:
        f3 = I_ARITH[mn]
        rd, rs1 = reg(ops[0]), reg(ops[1])
        v = value(ops[2], labels)
        if mn in ('slli', 'srli', 'srai'):
            sh = v & 0x1F
            if mn == 'srai':
                sh |= 0x400
            return [i_type(sh, rs1, f3, rd, 0b0010011)]
        return [i_type(v & 0xFFF, rs1, f3, rd, 0b0010011)]
    if mn in LOAD:
        rd = reg(ops[0])
        off, rs1 = parse_mem_operand(ops[1], labels)
        return [i_type(off & 0xFFF, rs1, LOAD[mn], rd, 0b0000011)]
    if mn in STORE:
        rs2 = reg(ops[0])
        off, rs1 = parse_mem_operand(ops[1], labels)
        return [s_type(off & 0xFFF, rs2, rs1, STORE[mn])]
    if mn in BRANCH:
        rs1, rs2 = reg(ops[0]), reg(ops[1])
        off = value(ops[2], labels, pc, True)
        return [b_type(off & 0x1FFF, rs2, rs1, BRANCH[mn])]
    if mn == 'lui':
        return [u_type(value(ops[1], labels) & 0xFFFFF, reg(ops[0]), 0b0110111)]
    if mn == 'auipc':
        return [u_type(value(ops[1], labels) & 0xFFFFF, reg(ops[0]), 0b0010111)]
    if mn == 'jal':
        if len(ops) == 1:
            rd, target = 1, ops[0]
        else:
            rd, target = reg(ops[0]), ops[1]
        return [j_type(value(target, labels, pc, True) & 0x1FFFFF, rd)]
    if mn == 'jalr':
        if len(ops) == 1:
            rd, off, rs1 = 1, 0, reg(ops[0])
        elif len(ops) == 2 and '(' in ops[1]:
            rd = reg(ops[0])
            off, rs1 = parse_mem_operand(ops[1], labels)
        else:
            rd, rs1 = reg(ops[0]), reg(ops[1])
            off = value(ops[2], labels)
        return [i_type(off & 0xFFF, rs1, 0, rd, 0b1100111)]
    # ---- Zicsr (opcode 1110011 = SYSTEM) ----
    if mn in ('csrrw', 'csrrs', 'csrrc'):
        f3 = {'csrrw': 1, 'csrrs': 2, 'csrrc': 3}[mn]
        return [i_type(csr(ops[1], labels), reg(ops[2]), f3, reg(ops[0]), 0b1110011)]
    if mn in ('csrrwi', 'csrrsi', 'csrrci'):
        f3 = {'csrrwi': 5, 'csrrsi': 6, 'csrrci': 7}[mn]
        return [i_type(csr(ops[1], labels), value(ops[2], labels) & 0x1F, f3, reg(ops[0]), 0b1110011)]
    # 伪指令
    if mn == 'csrr':
        return [i_type(csr(ops[1], labels), 0, 2, reg(ops[0]), 0b1110011)]
    if mn == 'csrw':
        return [i_type(csr(ops[0], labels), reg(ops[1]), 1, 0, 0b1110011)]
    if mn == 'csrs':
        return [i_type(csr(ops[0], labels), reg(ops[1]), 2, 0, 0b1110011)]
    if mn == 'csrc':
        return [i_type(csr(ops[0], labels), reg(ops[1]), 3, 0, 0b1110011)]
    if mn == 'csrsi':
        return [i_type(csr(ops[0], labels), value(ops[1], labels) & 0x1F, 6, 0, 0b1110011)]
    if mn == 'csrci':
        return [i_type(csr(ops[0], labels), value(ops[1], labels) & 0x1F, 7, 0, 0b1110011)]
    # ---- 陷阱指令 (SYSTEM 的固定编码) ----
    if mn == 'ecall':
        return [0x00000073]
    if mn == 'ebreak':
        return [0x00100073]
    if mn == 'mret':
        return [0x30200073]
    if mn == 'fence':
        return [0x0FF0000F]          # 本核顺序执行, FENCE 可退化为 NOP
    raise ValueError('unknown mnemonic: %s' % mn)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src_path = sys.argv[1]
    out_path = 'prog.hex'
    if '-o' in sys.argv:
        out_path = sys.argv[sys.argv.index('-o') + 1]

    lines = open(src_path, encoding='utf-8').read().splitlines()

    # 第一遍: 计算每条指令的地址与标签 (li 展开会占 8 字节, 必须一并统计)
    pc = TEXT_BASE
    seg = 'text'
    labels = {}
    items = []                          # (地址, 源码行, mn, ops, 大小)
    for raw in lines:
        line = raw.split('#')[0].strip()
        if not line:
            continue
        if line.startswith('.text'):
            seg, pc = 'text', TEXT_BASE
            continue
        if line.startswith('.data'):
            seg, pc = 'data', DATA_BASE
            continue
        if line.startswith('.org'):
            pc = int(line.split()[1], 0)
            continue
        if line.startswith('.word'):
            if seg == 'text':
                items.append((pc, raw, '.word', [line.split()[1]], 4))
                pc += 4
            continue
        if line.endswith(':'):
            labels[line[:-1].strip()] = pc
            continue
        if ':' in line:
            lab, rest = line.split(':', 1)
            labels[lab.strip()] = pc
            line = rest.strip()
            if not line:
                continue
        mn = line.split()[0].lower()
        ops = [o.strip() for o in line[len(mn):].split(',') if o.strip()]
        size = instruction_size(mn, ops, labels)
        items.append((pc, raw, mn, ops, size))
        pc += size

    # 第二遍: 编码
    words = []
    listing = []
    for addr, raw, mn, ops, size in items:
        if mn == '.word':
            enc = [int(ops[0], 0) & 0xFFFFFFFF]
        else:
            enc = encode(mn, ops, addr, labels)
        exp = size // 4
        if len(enc) != exp:
            print('WARN: %s 展开 %d 条, 预期 %d 条 (%s)' % (mn, len(enc), exp, raw))
        for i, e in enumerate(enc):
            words.append(e & 0xFFFFFFFF)
            listing.append((addr + 4 * i, e & 0xFFFFFFFF, raw))

    with open(out_path, 'w', encoding='ascii') as f:
        for w in words:
            f.write('%08x\n' % w)

    print('=== 汇编清单 (%d 条指令, 输出 %s) ===' % (len(words), out_path))
    for addr, e, raw in listing:
        print('%08x: %08x    %s' % (addr, e, raw))
    print('=== 标签 ===')
    for k, v in labels.items():
        print('%-12s = 0x%08x' % (k, v))
    return 0


if __name__ == '__main__':
    sys.exit(main())
