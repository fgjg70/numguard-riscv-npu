#!/usr/bin/env python3
"""elf2hex.py - RISC-V ELF -> imem / dmem 两个 $readmemh 镜像

用法:
    python tools/elf2hex.py <file.elf> -o <imem.hex> [--dmem <dmem.hex>] [--objcopy <path>]

为什么是两个镜像 (哈佛结构):
  本核 load/store 只经 LSU 访问 dmem 窗口, 无法用 lw 从 imem 读初值。
  所以 .text 进 imem 镜像, .data/.rodata 的初值进 dmem 镜像 (按 VMA 平铺)。
  crt0 不再需要搬运 .data, 只需清 .bss。
"""

import argparse, os, struct, subprocess, sys


def find_objcopy(explicit):
    if explicit:
        return explicit
    for d in os.environ.get('PATH', '').split(os.pathsep):
        if not d:
            continue
        for suffix in ('.exe', ''):
            p = os.path.join(d, 'riscv-none-elf-objcopy' + suffix)
            if os.path.exists(p):
                return p
    return 'riscv-none-elf-objcopy'


def dump(objcopy, elf, section, outbin):
    cmd = [objcopy, '-O', 'binary']
    if section:
        cmd += ['--only-section=' + section]
    cmd += [elf, outbin]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return None, r.stderr
    if not os.path.exists(outbin):
        return b'', ''
    with open(outbin, 'rb') as f:
        data = f.read()
    os.remove(outbin)
    return data, ''


def write_hex(data, path, base_addr, limit_words, label):
    if len(data) % 4:
        data += b'\x00' * (4 - len(data) % 4)
    words = len(data) // 4
    with open(path, 'w') as f:
        for i in range(0, len(data), 4):
            f.write('%08x\n' % struct.unpack('<I', data[i:i + 4])[0])
    pct = 100.0 * words / limit_words
    print('  %-5s %-28s %6d bytes  %5d words  (%5.1f%% of %d)' %
          (label, os.path.basename(path), len(data), words, pct, limit_words))
    return words


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('elf')
    ap.add_argument('-o', '--output', required=True, help='imem hex')
    ap.add_argument('--dmem', default=None, help='dmem hex (不给则用 <output>.dmem.hex)')
    ap.add_argument('--objcopy', default=None)
    ap.add_argument('--imem-words', type=int, default=4096)
    ap.add_argument('--dmem-words', type=int, default=4096)
    args = ap.parse_args()

    objcopy = find_objcopy(args.objcopy)
    dmem_out = args.dmem or (os.path.splitext(args.output)[0] + '.dmem.hex')

    text, err = dump(objcopy, args.elf, '.text', args.output + '.text.bin')
    if text is None:
        print('objcopy(.text) failed: %s' % err, file=sys.stderr)
        return 1
    data, err = dump(objcopy, args.elf, '.data', args.output + '.data.bin')
    if data is None:
        print('objcopy(.data) failed: %s' % err, file=sys.stderr)
        return 1

    print('=== images ===')
    nw = write_hex(text, args.output, 0x80000000, args.imem_words, 'imem')
    dw = write_hex(data, dmem_out, 0x90000000, args.dmem_words, 'dmem')

    rc = 0
    if nw > args.imem_words:
        print('  !! imem overflow', file=sys.stderr); rc = 2
    if dw > args.dmem_words:
        print('  !! dmem overflow', file=sys.stderr); rc = 2
    return rc


if __name__ == '__main__':
    sys.exit(main())
