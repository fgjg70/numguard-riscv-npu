#!/usr/bin/env python3
"""build_sw.py - RISC-V rv32i bare-metal build: compile + link + imem hex

Usage:
    python tools/build_sw.py sw/hello.c sw/portme.c -o sim/prog/hello.hex
    python tools/build_sw.py sw/coremark/*.c -I sw/coremark -o sim/prog/coremark.hex

    # 【W-07】一条命令构建**全部 12 项回归**需要的镜像 (全新克隆可复现 PASS 12)
    python tools/build_sw.py --all
    python tools/build_sw.py --all --with-hw     # 额外构建上板跑分镜像 cm_hw.hex
    python tools/build_sw.py --list              # 只列出 --all 会构建什么

    # 【W-02】上板/评分镜像必须告诉程序真实主频 (默认 50 MHz = 仿真时钟)
    python tools/build_sw.py sw/coremark/*.c -I sw/coremark -D ITERATIONS=3000 \
        --clk-hz auto -o sim/prog/cm_hw.hex      # auto = 从 fpga/top_a100t.sv 的 .CLK_HZ() 读

Notes:
  - sw/crt0.S and sw/libc_min.c are ALWAYS linked in (mandatory startup + minimal libc).
    Forgetting crt0.S makes --gc-sections drop the whole program (silent 0-byte image).
  - -nostdlib avoids newlib bloat, but -lgcc is required: RV32I has no HW multiply/divide,
    so gcc emits __mulsi3/__udivsi3/... calls.
  - `--all` **先删旧 .hex 再构建**, 任何一步失败即非零退出 (PITFALLS R1/W-06:
    绝不能汇编/编译失败却把上一次的旧镜像拿去跑测试).
"""

import argparse, glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_GCC_DIR = r'D:\software\tools\riscv-none-elf\xpack-riscv-none-elf-gcc-15.2.0-1\bin'
DEFAULT_LD = os.path.join(REPO, 'sw', 'link.ld')
ALWAYS = [os.path.join(REPO, 'sw', 'crt0.S'), os.path.join(REPO, 'sw', 'libc_min.c')]

SW = os.path.join(REPO, 'sw')
PROG = os.path.join(REPO, 'sim', 'prog')

# CoreMark 的源文件列表 (与 tools/run_coremark.py、tools/mk_cm_hw.py 保持一致)
CM_SRC = ['core_main.c', 'core_list_join.c', 'core_matrix.c', 'core_state.c',
          'core_util.c', 'core_portme.c', 'ee_printf.c']

# 回归矩阵 (tools/fpga_workflow.py: test_matrix()) 里用到的**汇编**镜像。
# amo_diag2 不在 12 项里, 但它是 amo_diag 的备用诊断支, 一起建齐免得下次找不到。
# lu_bench: load-use 代价标定微基准 (验证泳道 2026-10-11 新增)。必须登记在这里,
#   否则 `build_sw.py --all` 不会"删了重建"它 —— 改了 .S 忘了汇编就会**静默跑旧镜像**
#   (这就是 PITFALLS R1 的经典形态: 一处改了, 另一处没跟上)。
ASM_IMAGES = ['basic', 'trap', 'timer_irq', 'm_ext', 'amo', 'amo_diag', 'amo_diag2', 'mmio',
              'lu_bench']


def find_tool(name, gcc_dir):
    for d in [gcc_dir] + os.environ.get('PATH', '').split(os.pathsep):
        if not d:
            continue
        for suffix in ('.exe', ''):
            p = os.path.join(d, name + suffix)
            if os.path.exists(p):
                return p
    return name


def detect_hw_clk_hz():
    """从 fpga/top_a100t.sv 的顶层参数 .CLK_HZ(...) 读真实主频 (W-02 的单一来源)。

    为什么不让调用方手填: 手填就会和 RTL/XDC 漂移 —— 70 MHz 版曾被按 50 MHz
    算时间 (偏差 1.4 倍), 而 XDC 里是 create_generated_clock sys_clk_70。
    读不到就明确报错, 绝不退回一个"看起来也对"的默认值。
    """
    p = os.path.join(REPO, 'fpga', 'top_a100t.sv')
    if not os.path.exists(p):
        return None
    txt = open(p, encoding='utf-8', errors='replace').read()
    m = re.search(r'\.CLK_HZ\s*\(\s*([0-9_]+)\s*\)', txt)
    if not m:
        return None
    return int(m.group(1).replace('_', ''))


def resolve_clk_hz(spec):
    if spec is None:
        return None
    if str(spec).strip().lower() == 'auto':
        hz = detect_hw_clk_hz()
        if not hz:
            raise SystemExit('[ERROR] --clk-hz auto: 没能从 fpga/top_a100t.sv 读到 .CLK_HZ(...)')
        return hz
    return int(spec)


def build_image(sources, output, includes=(), defines=(), march='rv32i', opt='-Os',
                ld=None, gcc_dir=None, no_gc=False):
    """编译 + 链接 + elf2hex 一个镜像。返回 0 = 成功。"""
    gcc_dir = gcc_dir or DEFAULT_GCC_DIR
    ld = ld or DEFAULT_LD
    gcc = find_tool('riscv-none-elf-gcc', gcc_dir)
    objcopy = find_tool('riscv-none-elf-objcopy', gcc_dir)
    tool_gcc_dir = os.path.dirname(gcc)

    output = os.path.join(REPO, output) if not os.path.isabs(output) else output
    elf = os.path.splitext(output)[0] + '.elf'
    mapf = os.path.splitext(output)[0] + '.map'

    srcs = list(ALWAYS)
    for s in sources:
        hits = glob.glob(s if os.path.isabs(s) else os.path.join(REPO, s))
        srcs.extend(hits if hits else [s])
    for s in srcs:
        if not os.path.exists(s):
            print('MISSING SOURCE: %s' % s, file=sys.stderr)
            return 1

    cmd = [gcc, '-march=' + march, '-mabi=ilp32', opt,
           '-ffreestanding', '-fno-builtin', '-fno-strict-aliasing',
           '-nostdlib', '-nostartfiles',
           '-Wl,-Map=' + mapf,
           '-Wl,--no-warn-rwx-segments',
           '-T', ld, '-o', elf]
    if not no_gc:
        cmd += ['-Wl,--gc-sections', '-ffunction-sections', '-fdata-sections']
    cmd += ['-I' + i for i in includes]
    cmd += ['-D' + d for d in defines]
    cmd += srcs + ['-lgcc']

    print('[cc] %d sources -> %s' % (len(srcs), os.path.basename(elf)))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        print('BUILD FAILED', file=sys.stderr)
        return 1
    if r.stderr.strip():
        print(r.stderr.strip())

    r2 = subprocess.run([sys.executable, os.path.join(HERE, 'elf2hex.py'),
                         elf, '-o', output, '--objcopy', objcopy],
                        capture_output=True, text=True)
    print(r2.stdout.strip())
    if r2.returncode != 0:
        print(r2.stderr, file=sys.stderr)
        return r2.returncode

    size = find_tool('riscv-none-elf-size', tool_gcc_dir)
    rs = subprocess.run([size, '-A', elf], capture_output=True, text=True)
    if rs.returncode == 0:
        for line in rs.stdout.splitlines():
            if line.strip() and not line.startswith('.'):
                print('    ' + line.strip())
            elif line.startswith('.'):
                parts = line.split()
                if len(parts) >= 2:
                    print('    %-14s %6s' % (parts[0], parts[1]))

    rel = os.path.relpath(output, REPO) if output.startswith(REPO) else output
    print('[OK] %s' % rel)
    return 0


def asm_image(name):
    """汇编一个 sim/prog/<name>.S -> .hex (asm.py 失败时非零退出)。"""
    src = os.path.join(PROG, name + '.S')
    out = os.path.join(PROG, name + '.hex')
    lst = os.path.join(PROG, name + '.lst')
    if not os.path.exists(src):
        print('MISSING SOURCE: %s' % src, file=sys.stderr)
        return 1
    print('[asm] %s -> %s' % (os.path.relpath(src, REPO), os.path.relpath(out, REPO)))
    r = subprocess.run([sys.executable, os.path.join(HERE, 'asm.py'), src, '-o', out],
                       capture_output=True, text=True)
    with open(lst, 'w', encoding='utf-8') as f:      # 与旧 run.bat 的 `> prog\x.lst` 等价
        f.write(r.stdout)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        print('ASSEMBLE FAILED: %s' % name, file=sys.stderr)
        return 1
    print('[OK] %s' % os.path.relpath(out, REPO))
    return 0


def regression_images(include_hw=False, clk_hz=None):
    """--all 构建的镜像清单。每一项都是 dict(kind=..., ...)。"""
    imgs = [dict(kind='asm', name=n) for n in ASM_IMAGES]
    imgs.append(dict(kind='cc', name='hello',
                     sources=['sw/hello.c', 'sw/portme.c'], includes=[SW],
                     march='rv32i', out='sim/prog/hello.hex'))
    # 【nn_demo 必须用 -O0】不是保守, 是实测: 同一份源码在 -Os/-O2 下
    # gemm_sw 那种循环形状被 GCC 15 编错 (256 个元素错 255 个, 见
    # results/p5-compiler-bug.md)。-O0 的结果与主机端独立计算完全一致,
    # 所以应用场景镜像按 -O0 构建 —— 换工具链版本/编译选项前先跑 sw/opt_probe.c。
    imgs.append(dict(kind='cc', name='nn_demo',
                     sources=['sw/nn_demo.c', 'sw/portme.c'], includes=[SW],
                     march='rv32im', opt='-O0', out='sim/prog/nn_demo.hex'))
    cm_srcs = ['sw/coremark/' + f for f in CM_SRC]
    cm_defs = ['FLAGS_STR="-Os -march=rv32im -mabi=ilp32"']
    imgs.append(dict(kind='cc', name='cm_rv32im', sources=cm_srcs,
                     includes=[os.path.join(SW, 'coremark')], march='rv32im',
                     defines=['ITERATIONS=1'] + cm_defs,
                     out='sim/prog/cm_rv32im.hex'))
    # 上板跑分镜像: ITERATIONS 按 R35 给足 >=10 s (70 MHz 下 3000 次约 17.7 s),
    # 并传入真实主频 (HW_CLK_HZ) 让 >=10 s 判定与时间口径都是真的。
    if include_hw:
        hw_defs = ['ITERATIONS=3000'] + cm_defs
        if clk_hz:
            hw_defs.append('HW_CLK_HZ=%d' % clk_hz)
        imgs.append(dict(kind='cc', name='cm_hw', sources=cm_srcs,
                         includes=[os.path.join(SW, 'coremark')], march='rv32im',
                         defines=hw_defs, out='sim/prog/cm_hw.hex'))
    return imgs


def remove_stale(out_rel):
    """构建前删掉旧产物 —— 这是 W-06/R1 的根治办法。

    旧 run.bat 用 `if not exist prog\\basic.hex` 当守卫: 汇编失败时 asm.py
    **不写文件**, 守卫看到旧文件还在, 于是静默测试上一次的程序。先删掉,
    任何失败都只会得到"缺镜像"这个响亮的结果。
    """
    base = os.path.join(REPO, os.path.splitext(out_rel)[0])
    for ext in ('.hex', '.dmem.hex'):
        p = base + ext
        if os.path.exists(p):
            os.remove(p)


def build_all(args):
    clk_hz = resolve_clk_hz(args.clk_hz) if args.clk_hz else detect_hw_clk_hz()
    imgs = regression_images(include_hw=args.with_hw, clk_hz=clk_hz)
    if args.list:
        for im in imgs:
            if im['kind'] == 'asm':
                print('  %-22s <- (assembler) sim/prog/%s.S' % (im['name'], im['name']))
            else:
                print('  %-22s <- %s  [%s %s]' % (im['name'], ' '.join(im['sources']),
                                                  im['march'], im.get('opt', '-Os')))
        if args.with_hw and not clk_hz:
            print('  [warn] 未能读到 fpga/top_a100t.sv 的 .CLK_HZ() -> cm_hw 不带 HW_CLK_HZ')
        return 0

    if args.with_hw:
        print('[build_sw --all] 上板镜像 cm_hw: ITERATIONS=3000 HW_CLK_HZ=%s' %
              (clk_hz if clk_hz else '(未读到, 退回 50 MHz 默认 —— W-02 会失真)'))
    failed = []
    for im in imgs:
        out = im['out'] if im['kind'] == 'cc' else 'sim/prog/%s.hex' % im['name']
        remove_stale(out)
        if im['kind'] == 'asm':
            rc = asm_image(im['name'])
        else:
            rc = build_image(im['sources'], out, includes=im.get('includes', ()),
                             defines=im.get('defines', ()), march=im.get('march', 'rv32i'),
                             opt=im.get('opt', '-Os'))
        if rc != 0:
            failed.append(im['name'])
        print('-' * 60)

    if failed:
        print('[FAIL] %d 个镜像构建失败: %s' % (len(failed), ', '.join(failed)),
              file=sys.stderr)
        return 1

    record_sw_hash()
    print('[OK] 全部 %d 个回归镜像构建完成 (sim/prog/)' % len(imgs))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sources', nargs='*')
    ap.add_argument('-o', '--output')
    ap.add_argument('-I', '--include', action='append', default=[])
    ap.add_argument('-D', '--define', action='append', default=[])
    ap.add_argument('--ld', default=DEFAULT_LD)
    ap.add_argument('--gcc-dir', default=DEFAULT_GCC_DIR)
    ap.add_argument('-O', '--opt', default='-Os')
    ap.add_argument('--march', default='rv32i',
                    help='ISA 字符串, 如 rv32i / rv32im。注意: 这个才是真正生效的, '
                         '-D FLAGS_STR 只影响 CoreMark 打印出来的字符串')
    ap.add_argument('--no-gc', action='store_true', help='disable --gc-sections')
    ap.add_argument('--all', action='store_true',
                    help='[W-07] 构建全部回归镜像 (不用再手抄散文配方)')
    ap.add_argument('--with-hw', action='store_true',
                    help='配合 --all: 额外构建上板跑分镜像 cm_hw.hex')
    ap.add_argument('--list', action='store_true',
                    help='配合 --all: 只列出会构建哪些镜像')
    ap.add_argument('--clk-hz', default=None,
                    help='[W-02] 真实主频, 传给 -DHW_CLK_HZ=<N> (上板/评分构建必须传); '
                         '写 auto 表示从 fpga/top_a100t.sv 的 .CLK_HZ() 读')
    args = ap.parse_args()

    if args.all:
        return build_all(args)

    if not args.output:
        ap.error('需要 -o/--output (或用 --all 构建全部回归镜像)')

    defines = list(args.define)
    hz = resolve_clk_hz(args.clk_hz)
    if hz:
        defines.append('HW_CLK_HZ=%d' % hz)
        print('[clk] CLOCKS_PER_SEC=%d (HW_CLK_HZ, 上板/评分构建: >=10s 判定生效)' % hz)
    elif args.clk_hz == 'auto':
        print('[warn] HW_CLK_HZ 未生效')

    rc = build_image(args.sources, args.output, includes=args.include, defines=defines,
                     march=args.march, opt=args.opt, ld=args.ld,
                     gcc_dir=args.gcc_dir, no_gc=args.no_gc)
    if rc != 0:
        return rc
    record_sw_hash()
    return 0


def record_sw_hash(group=None):
    """把当前 sw/ 源码的内容哈希记进 .fpga-cache/sw-build.json。

    为什么需要: tools/fpga_workflow.py check 用**内容哈希**判断
    "镜像是否需要重建"(不用 mtime)。构建成功后记录一次, 下次 check 才能对上。
    不记录的话 check 会退化成弱判据 (只喊"无基线可比")。
    """
    try:
        sys.path.insert(0, HERE)
        import fpga_workflow as fw
        import json, os as _os
        h = fw._sw_source_hash()
        p = _os.path.join(fw.CACHE_DIR, 'sw-build.json')
        _os.makedirs(fw.CACHE_DIR, exist_ok=True)
        try:
            cur = json.loads(open(p, encoding='utf-8').read())
        except Exception:
            cur = {}
        # 没指定 group 就把已知的两组都刷新 (都是同一份 sw/ 源码)
        for g in (([group] if group else ['hello', 'coremark'])):
            cur[g] = h
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(cur, f, indent=1)
    except Exception as e:
        # 记录失败不该让构建失败 —— 它只影响 check 的判据强度
        print('[warn] 未能记录 sw 源码哈希 (%s); check 会退化为弱判据' % e,
              file=sys.stderr)


if __name__ == '__main__':
    sys.exit(main())
