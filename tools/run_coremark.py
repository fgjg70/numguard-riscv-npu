#!/usr/bin/env python3
"""run_coremark.py - 一键: 构建 CoreMark -> 仿真 -> 解析指标 -> 追加 CSV

用法:
    python tools/run_coremark.py --tag baseline-v2 [--iterations 1] [--record]

要点:
  - 自动设置 LM_LICENSE_FILE / PATH (本会话的进程环境被剥离, 不设则 vsim 拿不到 license)
  - 指标来自程序打印的 "Total ticks" / "Iterations", CoreMark/MHz = iters*1e6/ticks
  - 资源与 fmax 从 fpga/util.rpt、fpga/timing.rpt 读取 (若存在)
"""

import argparse, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

# 【不许再有一份 fmax 解析副本】2026-10-11 风格审查抓出: 本文件曾是 fpga_workflow.py
# 那个 M-01 bug 的**未修副本**(按 `^sys_clk` 取 WNS + 硬编码 `1000/(20-wns)`)。
# 直接复用已修好的实现, 让"同一个口径"只有一个定义。
sys.path.insert(0, HERE)
from fpga_workflow import parse_worst_setup_path  # noqa: E402
MSIM = r'D:\software\tools\Modelsim\win64'
LIC = os.path.join(MSIM, 'LICENSE.TXT')
GCC_DIR = r'D:\software\tools\riscv-none-elf\xpack-riscv-none-elf-gcc-15.2.0-1\bin'
PY = sys.executable

RTL = ['rv32_pkg.sv','alu.sv','branch_unit.sv','control_unit.sv','imm_gen.sv','pc.sv','regfile.sv',
       'id_ex_reg.sv','ex_mem_reg.sv','mem_wb_reg.sv','hazard_unit.sv',
       'load_store_unit.sv','imem.sv','dmem.sv','btb.sv','bht.sv','ras.sv','gemm_accel.sv','muldiv.sv','csr_file.sv','core.sv']
# 【R1 纪律】这份列表必须与 fpga/build.tcl 的 read_verilog、tools/fpga_workflow.py 的
# CORE_SV_ORDER 保持同步 —— 漏一个模块的后果是 "module X not found" (硬失败) 或更糟:
# 静默跑另一份设计。C-1 引入 bht.sv (两级流水 gshare 的 PHT) 时这里就漏了,
# 由 `python tools/fpga_workflow.py check` 的 drift-check 抓出来 (已补)。
# rv32_pkg.sv 必须排第一 (其余模块 import 它, 顺序是语义不是格式)。
CM_SRC = ['core_main.c','core_list_join.c','core_matrix.c','core_state.c','core_util.c','core_portme.c','ee_printf.c']


def env():
    e = dict(os.environ)
    e['LM_LICENSE_FILE'] = LIC
    e['MGLS_LICENSE_FILE'] = LIC
    e['PATH'] = MSIM + os.pathsep + e.get('PATH', '')
    return e


def run(cmd, cwd, timeout=1800, shell=False):
    # shell=True 用于仿真那条命令: 走 cmd.exe 解析 && 串联, 避免 Windows 对
    # 参数列表二次加引号 (曾把 "path" 变成 ""path"")。
    r = subprocess.run(cmd, cwd=cwd, env=env(), capture_output=True, text=True,
                       timeout=timeout, shell=shell)
    return r.returncode, r.stdout + r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tag', required=True)
    ap.add_argument('--iterations', type=int, default=1)
    ap.add_argument('--max-cycles', type=int, default=50000000)
    ap.add_argument('--record', action='store_true', help='追加到 results/coremark.csv')
    ap.add_argument('--opt', default='-Os')
    ap.add_argument('--extra-cflags', default='')
    ap.add_argument('--march', default='rv32i')
    args = ap.parse_args()

    hexfile = os.path.join(REPO, 'sim', 'prog', 'coremark.hex')

    # --- 1. 构建 ---
    build = [PY, os.path.join(HERE, 'build_sw.py')]
    build += [os.path.join(REPO, 'sw', 'coremark', f) for f in CM_SRC]
    build += ['--include=' + os.path.join(REPO, 'sw', 'coremark'),
              '-D', 'ITERATIONS=%d' % args.iterations,
              '-D', 'FLAGS_STR="%s -march=%s -mabi=ilp32"' % (args.opt, args.march),
              '--march=' + args.march,
              '--opt=' + args.opt, '-o', hexfile]
    if args.extra_cflags:
        build += args.extra_cflags.split()
    rc, out = run(build, REPO)
    if rc != 0:
        print(out[-3000:]); return 1
    m = re.search(r'imem\s+\S+\s+(\d+) words\s+\(\s*([\d.]+)%', out)
    if m:
        print('[build] imem %s words (%s%%)' % (m.group(1), m.group(2)))

    # --- 2. 仿真 ---
    work = tempfile.mkdtemp(prefix='cm_')
    src = ' '.join('"%s"' % os.path.join(REPO, f) for f in RTL)
    cmd = ('vlib work && vlog -sv -work work %s "%s" "%s" && vsim -c -do "run -all; quit -f" work.tb_sw '
           '+imem=%s +dmem=%s +max_cycles=%d') % (
        src, os.path.join(REPO, 'fpga', 'mmio_subsys.sv'), os.path.join(REPO, 'sim', 'tb_sw.sv'),
        hexfile.replace('\\', '/'), hexfile.replace('.hex', '.dmem.hex').replace('\\', '/'), args.max_cycles)
    rc, out = run(cmd, work, shell=True)
    print(out[-4000:] if rc != 0 else out[out.find('--- program output ---'):][:2500])

    # ---- 失败判据 (W-10): 必须在解析 ticks **之前**判 ----------------------
    # 【为什么】CoreMark 算错时打的是 "ERROR! list crc ...", 而 harness/本工具的
    # 崩溃判据原先只有 "** Error"/"Fatal" —— 于是 CRC 不匹配也是绿灯 + exit 0,
    # 还会被 --record 写进 results/coremark.csv。
    # 另外 (W-01) 三个程序现在都把结论编码进 tohost: 算错 -> tohost=2 -> tb_sw
    # 立即 $fatal, 日志段根本不会被 dump 出来。所以"解析不到 Total ticks"本身
    # 也已经是失败信号, 这里必须给出**可读的原因**, 而不是一句"未能解析"。
    bad = [l.strip() for l in re.findall(
        r'^.*(?:ERROR!|Cannot validate operation|Errors detected).*$', out, re.M)]
    tb_fail = [l.strip() for l in re.findall(r'^.*\[tb_sw\] FAIL:.*$', out, re.M)]
    tb_ok = ('RESULT : PASS' in out)
    if bad or tb_fail or not tb_ok:
        print('=' * 60)
        for line in (bad + tb_fail)[:6]:
            print('  %s' % line)
        if not tb_ok:
            print('  [FAIL] 仿真没有 "RESULT : PASS" —— 程序已把"算得对不对"'
                  '编码进 tohost (W-01), 说明它自判失败')
        print('  [FAIL] CoreMark 结果不可信 -> 不记录 (exit 1)')
        return 1

    ticks = re.search(r'Total ticks\s*:\s*(\d+)', out)
    iters = re.search(r'Iterations\s*:\s*(\d+)', out)
    if not ticks or not iters:
        print('!! 仿真 PASS 但未能解析 Total ticks / Iterations'); return 1
    tick = int(ticks.group(1)); it = int(iters.group(1))
    cmmhz = it * 1e6 / tick
    print('=' * 60)
    print('  ticks = %d   iterations = %d' % (tick, it))
    print('  CoreMark/MHz = %.4f' % cmmhz)
    print('  CRC/有效性: OK (无 ERROR! 行, 且有 RESULT : PASS)')

    # --- 3. 资源 / fmax ---
    lut = ff = bram = fmax = ''
    util = os.path.join(REPO, 'fpga', 'util.rpt')
    tim = os.path.join(REPO, 'fpga', 'timing.rpt')
    if os.path.exists(util):
        u = open(util).read()
        m1 = re.search(r'Slice LUTs\s*\|\s*(\d+)', u[u.rfind('Slice LUTs') - 200:])
        m2 = re.search(r'Slice Registers\s*\|\s*(\d+)', u[u.rfind('Slice Registers') - 200:])
        m3 = re.search(r'Block RAM Tile\s*\|\s*(\d+)', u[u.rfind('Block RAM Tile') - 200:])
        lut = m1.group(1) if m1 else ''
        ff = m2.group(1) if m2 else ''
        bram = m3.group(1) if m3 else ''
    if os.path.exists(tim) and lut:
        t = open(tim).read()
        # 【不许按时钟名 + 列对齐取 WNS】见 parse_worst_setup_path 的 docstring:
        # MMCM 之后 `sys_clk` 行只剩脉宽端点(WNS/TNS 列为**空**), 而
        # `^sys_clk\s+([-\d.]+)` 会跨过空列抓到 WPWS(7.000) ⇒ fmax 被算成
        # 1000/(20-7.000)=76.92, 且**对 RTL 时序变化完全不敏感**(实测曾恒等 30+ 次)。
        # 现在: 路径块里的字段是**带标签**的, 周期从报告的 Requirement 取, 不硬编码。
        w = parse_worst_setup_path(t)
        if w:
            fmax = '%.1f' % (1000.0 / (w["requirement_ns"] - w["slack_ns"]))
    print('  LUT=%s FF=%s BRAM=%s fmax=%s MHz' % (lut, ff, bram, fmax))

    if args.record:
        csv = os.path.join(REPO, 'results', 'coremark.csv')
        import datetime
        with open(csv, 'a') as f:
            f.write('%s,%s,%d,%d,%.4f,%s,%s,%s,%s,"%s"' % (
                datetime.date.today().isoformat(), args.tag, it, tick, cmmhz,
                lut, ff, bram, fmax, 'run_coremark.py'))
            f.write(chr(10))
        print('  -> appended to results/coremark.csv')
    return 0


if __name__ == '__main__':
    sys.exit(main())
