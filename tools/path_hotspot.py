#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""path_hotspot.py - 从 Vivado timing report 里挖出"下一个该优化的抓手"

【为什么需要它】
本项目两次独立实测都指向同一件事: fmax 的抓手不是"名字看起来该优化"的地方, 而是
timing report 里 **fo 很大 + 该跳延迟很大** 的网:
    ref5 参照核 `ex_b[3]` 一根网驱动 115 个负载、单跳 3.0 ns
    -> 在 RTL 上对该寄存器加 `(* max_fanout = 16 *)` 触发复制 -> 137.25 -> 145.75 MHz (+8.50)
手工翻 timing.rpt 数 fo 又慢又容易看漏, 所以做成工具。

【用法】
    python tools/path_hotspot.py                            # 默认 fpga/timing.rpt
    python tools/path_hotspot.py ref5/runs/e1-base-a/timing.rpt --top 10
    python tools/path_hotspot.py "ref5/runs/*/timing.rpt"   # 多份 -> 只打对照表(复现性)
    python tools/path_hotspot.py fpga/timing.rpt --json     # 机器可读
    python tools/path_hotspot.py fpga/timing.rpt --period 10 --min-fo 0

    路径参数可以是: 文件 / 目录(自动找 timing.rpt) / 含通配符的模式 / 逗号分隔多个

【输出】
  1. 总结: WNS / fmax = 1000/(period-WNS) / TNS / failing endpoints / WHS
           + Top-N 的 logic-route 平均占比 (判断"逻辑受限"还是"布线受限")
  2. Top-N 最差 setup 路径: slack / Source -> Destination / logic-route 分解 /
           Logic Levels / **每一跳的元件类型与延迟** (报告里有的话)
  3. 每条路径上的**高扇出网** (fo 降序) + **全局 top 高扇出网** (跨路径去重)
           + **"复制候选"短名单** (fo >= 阈值, 按该跳延迟降序)

【退出码】
  0 = 解析成功 (哪怕报告没有 per-hop 明细)
  1 = 文件不存在 / 格式不符 (既没有 Design Timing Summary 也没有任何路径块)

【容错】
  - `net (fo=41)` 与 `net (fo=41, routed)` 都认
  - 没有 per-hop 明细的报告也能正常出总结
  - 周期从报告里解析 (Period(ns) / Clock Summary / Requirement / period=),
    解析不到才退回 20 ns 并显式告警; `--period` 可强制覆盖
  - 明细表按"分隔线分段"取数据段, 不依赖固定行号
"""

import argparse
import glob
import json
import os
import re
import sys
import textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DEFAULT_REPORT = os.path.join(REPO, "fpga", "timing.rpt")
FALLBACK_PERIOD = 20.0

# ---------------------------------------------------------------- 正则
RE_SLACK = re.compile(r'^Slack\s+\((MET|VIOLATED)\)\s*:\s*(-?\d+(?:\.\d+)?)ns')
RE_SRC = re.compile(r'^\s*Source:\s*(\S+)')
RE_DST = re.compile(r'^\s*Destination:\s*(\S+)')
RE_REQ = re.compile(r'^\s*Requirement:\s*(-?\d+\.\d+)ns')
RE_GROUP = re.compile(r'^\s*Path Group:\s*(\S+)')
RE_PTYPE = re.compile(r'^\s*Path Type:\s*(\S+)')
RE_DPD = re.compile(
    r'^\s*Data Path Delay:\s*(-?\d+\.\d+)ns\s*'
    r'\(logic\s+(-?\d+\.\d+)ns\s*\((-?\d+\.\d+)%\)\s*'
    r'route\s+(-?\d+\.\d+)ns\s*\((-?\d+\.\d+)%\)\)')
RE_LL = re.compile(r'^\s*Logic Levels:\s*(\d+)\s*(?:\((.*?)\))?\s*$')
RE_PERIOD_DECL = re.compile(r'^\s*Period\(ns\):\s*(-?\d+\.\d+)')
RE_PERIOD_INLINE = re.compile(r'period=\s*(-?\d+\.\d+)ns')

# `net (fo=41)` / `net (fo=41, routed)` / `net (fo=41, routed, ...)`
RE_NET = re.compile(
    r'^\s*net\s+\(fo=(\d+)\s*(?:,\s*([^)]*?))?\)\s+'
    r'(-?\d+\.\d+)\s+(-?\d+\.\d+)\s+(\S.*?)\s*$')

# 表格里的分隔线: 长横线 + 空白 + 短横线 (与表头那条长横线区分开)
RE_SEP = re.compile(r'^\s*-{20,}\s+-{5,}\s*$')

# 带 (延迟, 累计) 的元件行
RE_ELEM = re.compile(
    r'^\s*(?:(?P<loc>[A-Za-z][\w./\[\]]*)\s+)?'
    r'(?:(?P<dtype>[A-Za-z][\w.]*)\s*(?:\((?P<prop>[^)]*)\))?\s+)?'
    r'(?P<incr>-?\d+\.\d+)\s+(?P<path>-?\d+\.\d+)\s*(?P<edge>[rfb])?\s{2,}'
    r'(?P<res>\S.*?)\s*$')

# 不带延迟的元件行 (端点, 或延迟在下一行)
RE_CELL = re.compile(
    r'^\s*(?P<loc>[A-Za-z][\w./\[\]]*)\s+(?P<dtype>[A-Za-z][\w.]*)\s+'
    r'(?P<edge>[rfb])\s{2,}(?P<res>\S.*?)\s*$')

# 纯粹的"元件头"行: `RAMB36_X2Y10  RAMB36E1 (Prop_...)`, 延迟在下一行
# (要求整行没有独立的小数, 免得误吃别的行)
RE_CELL_HEAD = re.compile(
    r'^\s*(?P<loc>[A-Za-z][\w./\[\]]*)\s+(?P<dtype>[A-Za-z][\w.]*)\s*'
    r'(?:\((?P<prop>[^)]*)\))?\s*$')
RE_FLOAT = re.compile(r'\d+\.\d+')

# 时钟树网络 (不是复制候选, 单独标出来)
RE_CLKNET = re.compile(r'BUFG|IBUF|(^|[/_])clk([_/]|$)|clock', re.I)

REPORT_KINDS = ('setup', 'hold')


# ---------------------------------------------------------------- 解析
def read_lines(path):
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        return fh.read().split('\n')


def parse_design_summary(lines):
    """从 `| Design Timing Summary` 表里取 WNS/TNS/failing/WHS 等 12 个数。"""
    start = None
    for i, ln in enumerate(lines):
        if 'Design Timing Summary' in ln:
            start = i
            break
    if start is None:
        return None
    for i in range(start, min(start + 25, len(lines))):
        if 'WNS(ns)' in lines[i] and 'TNS' in lines[i]:
            for j in range(i + 1, min(i + 5, len(lines))):
                toks = lines[j].split()
                if len(toks) >= 12 and all(
                        re.fullmatch(r'-?\d+(?:\.\d+)?', t) for t in toks[:12]):
                    vals = [float(t) for t in toks[:12]]
                    return {
                        'wns': vals[0],
                        'tns': vals[1],
                        'tns_failing_endpoints': int(vals[2]),
                        'tns_total_endpoints': int(vals[3]),
                        'whs': vals[4],
                        'ths': vals[5],
                        'ths_failing_endpoints': int(vals[6]),
                        'ths_total_endpoints': int(vals[7]),
                        'wpws': vals[8],
                        'tpws': vals[9],
                        'tpws_failing_endpoints': int(vals[10]),
                        'tpws_total_endpoints': int(vals[11]),
                    }
    return None


def parse_period(lines):
    """返回 (period_ns, 来源说明)。解析不到返回 (None, None)。"""
    # 1) 约束段里的 `Period(ns):  20.000`
    for ln in lines:
        m = RE_PERIOD_DECL.match(ln)
        if m:
            return float(m.group(1)), 'constraint block "Period(ns):"'
    # 2) Clock Summary 表: 行首时钟名 + {rise fall} + period + freq
    seen_hdr = False
    for ln in lines:
        if 'Clock Summary' in ln:
            seen_hdr = True
            continue
        if not seen_hdr:
            continue
        m = re.match(r'^\s*(\S+)\s+\{[^}]*\}\s+(-?\d+\.\d+)\s+(-?\d+\.\d+)\s*$', ln)
        if m:
            return float(m.group(2)), 'Clock Summary table (clock "%s")' % m.group(1)
        if 'Intra Clock Table' in ln:
            break
    # 3) 最差 setup 路径的 Requirement (= 单周期时钟的周期)
    for ln in lines:
        m = RE_REQ.match(ln)
        if m:
            return float(m.group(1)), 'Requirement of the worst setup path'
    # 4) 路径里的 `period=20.000ns`
    for ln in lines:
        m = RE_PERIOD_INLINE.search(ln)
        if m:
            return float(m.group(1)), 'inline "period=...ns" in a path header'
    return None, None


def split_blocks(lines):
    """把报告切成 path block 列表 (按 `Slack (...)` 行切)。"""
    blocks = []
    section = None
    cur = None
    for ln in lines:
        if ln.startswith('Max Delay Paths'):
            section = 'setup'
            continue
        if ln.startswith('Min Delay Paths'):
            section = 'hold'
            continue
        m = RE_SLACK.match(ln)
        if m:
            if cur is not None:
                blocks.append(cur)
            cur = {'kind': section or 'setup',
                   'status': m.group(1),
                   'slack': float(m.group(2)),
                   'lines': []}
            continue
        if cur is not None:
            cur['lines'].append(ln)
    if cur is not None:
        blocks.append(cur)
    return blocks


def split_table_segments(body):
    """按分隔线把明细表切成段。返回 (时钟段, 数据段)。

    正常报告的段序是 [时钟路径, 数据路径, 尾部]。
    分隔线缺失时退化成"整段当数据段"。
    """
    start = None
    for i, ln in enumerate(body):
        if 'Netlist Resource(s)' in ln:
            start = i + 1
            break
    if start is None:
        return [], []
    segs = [[]]
    for ln in body[start:]:
        if RE_SEP.match(ln):
            segs.append([])
            continue
        segs[-1].append(ln)
    segs = [s for s in segs if any(x.strip() for x in s)]
    if len(segs) >= 2:
        return segs[0], segs[1]
    if segs:
        return [], segs[0]
    return [], []


def _pick_data_segment(body):
    """取明细表里的**数据路径**段 (第 2 段), 拿不到就退回整表。"""
    clock_seg, data_seg = split_table_segments(body)
    if data_seg:
        return data_seg
    return clock_seg


def parse_hops(data_seg):
    """把数据段解析成 hop 序列 (cell / net 交替)。

    nets 里的每根网还带上它的**驱动元件** (driver_type/driver_name) --
    因为"加 max_fanout 属性"的对象是那个驱动器 (寄存器或 LUT), 不是网本身。
    """
    hops = []
    nets = []
    pending = None
    last_cell = None
    for ln in data_seg:
        m = RE_NET.match(ln)
        if m:
            net = {'kind': 'net',
                   'fo': int(m.group(1)),
                   'flags': (m.group(2) or '').strip(),
                   'incr': float(m.group(3)),
                   'path': float(m.group(4)),
                   'name': m.group(5).strip(),
                   'driver_type': (last_cell or {}).get('type', '') or '',
                   'driver_name': (last_cell or {}).get('name', '') or ''}
            hops.append(net)
            if net['fo'] > 0:
                nets.append(net)
            pending = None
            continue
        m = RE_ELEM.match(ln)
        if m:
            dtype = m.group('dtype') or (pending['type'] if pending else '')
            cell = {'kind': 'cell',
                    'type': dtype,
                    'prop': m.group('prop') or (pending['prop'] if pending else ''),
                    'loc': m.group('loc') or (pending['loc'] if pending else ''),
                    'incr': float(m.group('incr')),
                    'path': float(m.group('path')),
                    'name': m.group('res').strip()}
            hops.append(cell)
            last_cell = cell
            pending = None
            continue
        m = RE_CELL.match(ln)
        if m:
            cell = {'kind': 'cell',
                    'type': m.group('dtype'),
                    'prop': '',
                    'loc': m.group('loc') or '',
                    'incr': None,
                    'path': None,
                    'name': m.group('res').strip()}
            hops.append(cell)
            last_cell = cell
            pending = {'type': m.group('dtype'),
                       'prop': '',
                       'loc': m.group('loc') or ''}
            continue
        if not RE_FLOAT.search(ln):
            m = RE_CELL_HEAD.match(ln)
            if m:
                # 只记类型, 不产生 hop —— 真正的延迟在下一行
                pending = {'type': m.group('dtype'),
                           'prop': m.group('prop') or '',
                           'loc': m.group('loc') or ''}
    return hops, nets


def parse_block(block):
    """把一个 path block 解析成结构化 dict。"""
    out = {'kind': block['kind'], 'status': block['status'], 'slack': block['slack']}
    for ln in block['lines']:
        if 'source' not in out:
            m = RE_SRC.match(ln)
            if m:
                out['source'] = m.group(1)
                continue
        if 'destination' not in out:
            m = RE_DST.match(ln)
            if m:
                out['destination'] = m.group(1)
                continue
        if 'data_path_delay' not in out:
            m = RE_DPD.match(ln)
            if m:
                out['data_path_delay'] = float(m.group(1))
                out['logic_ns'] = float(m.group(2))
                out['logic_pct'] = float(m.group(3))
                out['route_ns'] = float(m.group(4))
                out['route_pct'] = float(m.group(5))
                continue
        if 'logic_levels' not in out:
            m = RE_LL.match(ln)
            if m:
                out['logic_levels'] = int(m.group(1))
                out['logic_levels_hist'] = (m.group(2) or '').strip()
                continue
        if 'path_group' not in out:
            m = RE_GROUP.match(ln)
            if m:
                out['path_group'] = m.group(1).strip('*')
                continue
        if 'path_type' not in out:
            m = RE_PTYPE.match(ln)
            if m:
                out['path_type'] = m.group(1)
                continue
    data_seg = _pick_data_segment(block['lines'])
    hops, nets = parse_hops(data_seg)
    out['hops'] = hops
    out['nets'] = nets
    out['is_async'] = str(out.get('path_group', '')).startswith('async')
    out.setdefault('source', '?')
    out.setdefault('destination', '?')
    out.setdefault('path_group', '?')
    out.setdefault('path_type', '?')
    out.setdefault('logic_levels', None)
    out.setdefault('logic_levels_hist', '')
    out.setdefault('data_path_delay', None)
    return out


def period_from_worst_setup(raw_blocks):
    """从**最差 setup 路径块**里取它的 `Requirement:` 作为时钟周期。

    【为什么不能直接用 parse_period() 的第 1 条】
    报告里 `Period(ns): 20.000` 的**第一次出现**(timing.rpt:200) 属于 `sys_clk` 的
    **脉宽(pulse width)明细块**, 而设计真正跑在 MMCM 出来的 `sys_clk_70`(14.286ns)。
    脉宽余量一混进来, fmax 就被算成 1000/(20.000-0.888) = **52.32 MHz**
    (真值 74.64) —— 而且这个错值看起来完全"正常"。
    路径块里的 `Requirement` 与它的 `slack` **同源同一条路径**, 是唯一不会串台的口径。
    (见 CODE-REVIEW-PLAN X-05; 与 fpga_workflow.parse_worst_setup_path 同一原理。)
    """
    setup = [b for b in raw_blocks if b.get('kind') == 'setup']
    if not setup:
        return None, None
    worst = min(setup, key=lambda b: b['slack'])
    grp = '?'
    for ln in worst['lines']:
        m = RE_GROUP.match(ln)
        if m:
            grp = m.group(1).strip('*')
            break
    for ln in worst['lines']:
        m = RE_REQ.match(ln)
        if m:
            return float(m.group(1)), 'worst setup path Requirement (clock "%s")' % grp
    return None, None


def analyze(path, period_override):
    lines = read_lines(path)
    res = {'report': os.path.abspath(path), 'warnings': []}

    summary = parse_design_summary(lines)
    raw_blocks = split_blocks(lines)
    blocks = [parse_block(b) for b in raw_blocks]

    if summary is None and not blocks:
        res['ok'] = False
        res['error'] = ('既找不到 "Design Timing Summary" 表, '
                        '也找不到任何 "Slack (MET/VIOLATED)" 路径块 -- 不是 Vivado timing report?')
        return res

    # 【口径顺序】最差 setup 路径的 Requirement 优先; 它保证 period 与 wns 同源。
    period, period_src = period_from_worst_setup(raw_blocks)
    if period_override is not None:
        period, period_src = period_override, '--period (命令行覆盖)'
    elif period is None:
        period, period_src = parse_period(lines)
        if period is None:
            period, period_src = FALLBACK_PERIOD, 'DEFAULT %g ns (报告里没解析到, 请用 --period)' % FALLBACK_PERIOD
            res['warnings'].append('没能从报告里解析出时钟周期, 已退回默认 %g ns' % FALLBACK_PERIOD)
        else:
            res['warnings'].append(
                '报告里没有 setup 路径明细 -> 周期取自次级来源(%s); '
                '若设计跑在生成时钟上, 这个值可能不是核心时钟, 建议显式 --period' % period_src)

    if summary is None:
        res['warnings'].append('没找到 Design Timing Summary 表, 只用路径块里的 slack')

    wns = summary['wns'] if summary else (min(b['slack'] for b in blocks) if blocks else None)

    res['ok'] = True
    res['period_ns'] = period
    res['period_source'] = period_src
    res['summary'] = summary
    res['wns'] = wns
    if wns is None:
        res['fmax_mhz'] = None
    elif period - wns <= 0:
        res['fmax_mhz'] = None
        res['warnings'].append(
            'period - WNS <= 0 (%.3f - %.3f): fmax 不可估, 设计在当前周期下已无法收敛'
            % (period, wns))
    else:
        res['fmax_mhz'] = 1000.0 / (period - wns)

    setup = sorted([b for b in blocks if b['kind'] == 'setup'],
                   key=lambda b: b['slack'])
    hold = sorted([b for b in blocks if b['kind'] == 'hold'],
                  key=lambda b: b['slack'])
    res['all_setup_paths'] = setup
    res['all_hold_paths'] = hold
    if not setup and not hold:
        res['warnings'].append('报告里没有任何 per-hop 路径明细, 只有总结表')
    return res


# ---------------------------------------------------------------- 汇总
def global_fanout(paths, min_fo):
    """跨路径汇总高扇出网: 去重(取最大 fo), 记录出现次数与该网最大单跳延迟。"""
    agg = {}
    for p in paths:
        for n in p['nets']:
            if n['fo'] < min_fo:
                continue
            key = n['name']
            cur = agg.get(key)
            if cur is None:
                agg[key] = {'name': key,
                            'fo': n['fo'],
                            'route_ns_max': n['incr'],
                            'route_ns_sum': n['incr'],
                            'hits': 1,
                            'driver_type': n.get('driver_type', ''),
                            'driver_name': n.get('driver_name', ''),
                            'is_clock': bool(RE_CLKNET.search(key))}
            else:
                cur['fo'] = max(cur['fo'], n['fo'])
                if n['incr'] >= cur['route_ns_max']:
                    cur['route_ns_max'] = n['incr']
                    cur['driver_type'] = n.get('driver_type', '')
                    cur['driver_name'] = n.get('driver_name', '')
                cur['route_ns_sum'] += n['incr']
                cur['hits'] += 1
    return sorted(agg.values(), key=lambda d: (-d['fo'], -d['route_ns_max']))


def path_fanout(path, min_fo):
    return sorted([n for n in path['nets'] if n['fo'] >= min_fo],
                  key=lambda n: (-n['fo'], -n['incr']))


def avg(seq):
    seq = [x for x in seq if x is not None]
    return (sum(seq) / len(seq)) if seq else None


# ---------------------------------------------------------------- 打印
def fmt(v, spec='%.3f', dash='-'):
    return dash if v is None else (spec % v)


def print_path(p, idx, min_fo, show_hops=True):
    print('')
    print('  [%d] slack %s ns  (%s)  %s delay %s ns' % (
        idx, fmt(p['slack']), p['status'],
        p['kind'].upper(), fmt(p['data_path_delay'])))
    print('      Source      : %s' % p['source'])
    print('      Destination : %s' % p['destination'])
    print('      Path Group  : %s   /  %s' % (p.get('path_group', '?'),
                                              p.get('path_type', '?')))
    if p.get('is_async'):
        print('      [!] 这是 async_default 组 (Recovery/Removal) 的路径 —— '
              '复位/异步控制, 不是普通 setup 路径;')
        print('          它上榜通常意味着**复位/使能网扇出极大**, 要另作处理, '
              '别混进功能时序的账里。')
    if p.get('logic_ns') is not None:
        print('      Delay split : logic %.3f ns (%.1f%%)  |  route %.3f ns (%.1f%%)'
              % (p['logic_ns'], p['logic_pct'], p['route_ns'], p['route_pct']))
        if p['route_pct'] >= 50.0:
            print('      => 布线主导 (route >= 50%): "砍逻辑"的收益会被布线吃掉')
        else:
            print('      => 逻辑主导 (logic > 50%): 逻辑级数/逻辑深度才是抓手')
    if p.get('logic_levels') is not None:
        hist = ('  (%s)' % p['logic_levels_hist']) if p['logic_levels_hist'] else ''
        print('      Logic Levels: %d%s' % (p['logic_levels'], hist))

    if show_hops and p['hops']:
        print('')
        print('      hop  cell-type     logic    net-fo   route   net / resource')
        print('      ---  -----------  -------  -------  ------  %s'
              % ('-' * 52))
        n = 0
        for i, h in enumerate(p['hops']):
            if h['kind'] == 'net':
                prev = p['hops'][i - 1] if i > 0 else None
                if prev is not None and prev['kind'] == 'cell':
                    continue            # 已经并进上一行, 不重复打
                print('      %3s  %-11s  %7s  %7s  %6s  %s'
                      % ('-', '(net)', '-', 'fo=%d' % h['fo'],
                         '%.3f' % h['incr'], h['name']))
                continue
            n += 1
            nxt = p['hops'][i + 1] if i + 1 < len(p['hops']) else None
            if nxt is not None and nxt['kind'] == 'net':
                fo = 'fo=%d' % nxt['fo']
                route = '%.3f' % nxt['incr']
                name = nxt['name']
            else:
                fo = route = '-'
                name = h['name']
            print('      %3d  %-11s  %7s  %7s  %6s  %s'
                  % (n, (h['type'] or '?')[:11], fmt(h['incr']), fo, route, name))

    fo = path_fanout(p, min_fo)
    print('')
    if not fo:
        print('      High-fanout nets (fo >= %d):  (无, 或报告没有 per-hop 明细)' % min_fo)
        return
    print('      High-fanout nets on this path (fo desc, fo >= %d):' % min_fo)
    for n in fo:
        tag = '  [clk-tree]' if RE_CLKNET.search(n['name']) else ''
        drv = ('   <- 驱动: %s' % n['driver_type']) if n.get('driver_type') else ''
        print('        fo=%-5d route %7.3f ns   %s%s%s'
              % (n['fo'], n['incr'], n['name'], tag, drv))


def print_report(res, args, multi=False):
    print('=' * 100)
    print('path_hotspot  %s' % res['report'])
    print('=' * 100)

    if not res.get('ok'):
        print('  [FAIL] %s' % res.get('error', 'parse failed'))
        return

    s = res.get('summary') or {}
    print('  period = %s ns   (%s)' % (fmt(res['period_ns']), res['period_source']))
    print('  WNS    = %s ns        fmax = %s MHz   (fmax = 1000/(period-WNS))'
          % (fmt(res.get('wns')), fmt(res.get('fmax_mhz'), '%.2f')))
    if res.get('summary'):
        print('  TNS    = %s ns        failing endpoints = %s / %s'
              % (fmt(s.get('tns')), s.get('tns_failing_endpoints', '-'),
                 s.get('tns_total_endpoints', '-')))
        print('  WHS    = %s ns        hold failing = %s'
              % (fmt(s.get('whs')), s.get('ths_failing_endpoints', '-')))
    else:
        print('  TNS/WHS: (n/a -- 这份报告是 report_timing 输出, 不含 '
              'Design Timing Summary 表)')

    setup = res['all_setup_paths']
    hold = res['all_hold_paths']
    top = setup[:args.top]

    if top:
        lp = avg([p.get('logic_pct') for p in top])
        rp = avg([p.get('route_pct') for p in top])
        print('')
        print('  Top-%d setup paths: logic %s%% / route %s%% (平均)  ->  %s'
              % (len(top), fmt(lp, '%.1f'), fmt(rp, '%.1f'),
                 ('布线受限' if (rp or 0) >= 50 else '逻辑受限')))
        print('  路径块总数: max-delay %d (其中 async_default/异步 %d) / hold %d'
              % (len(setup), sum(1 for p in setup if p.get('is_async')), len(hold)))
        if len(setup) <= 1:
            print('  [i] 这份报告只含 %d 条 setup 路径 (report_timing_summary 默认 -max_paths 1)。'
                  % len(setup))
            print('      想看 top-N / 高扇出网清单, 请用全展开的报告:')
            print('        report_timing -delay_type max -max_paths 25 -file timing_paths.rpt')

    if multi:
        return

    for w in res['warnings']:
        print('  [warn] %s' % w)

    if not setup and not hold:
        print('')
        print('  [warn] 报告里没有路径明细, 只有总结表')
        return

    print('')
    print('  ' + '=' * 96)
    print('  Top-%d 最差 MAX-DELAY 路径 (setup + async_default; 按 slack 升序)' % len(top))
    print('  ' + '=' * 96)
    for i, p in enumerate(top, 1):
        print_path(p, i, args.min_fo, show_hops=not args.no_hops)

    if hold:
        print('')
        print('  ' + '=' * 96)
        print('  最差 HOLD 路径 (setup 之外的第二类风险)')
        print('  ' + '=' * 96)
        print_path(hold[0], 1, args.min_fo, show_hops=False)

    if not args.no_hops:
        gf = global_fanout(setup, args.min_fo)
        print('')
        print('  ' + '=' * 96)
        print('  全局 Top-%d 高扇出网 (跨 %d 条 setup 路径汇总/去重, fo 降序, fo >= %d)'
              % (args.top_fanout, len(top), args.min_fo))
        print('  ' + '=' * 96)
        if not gf:
            print('    (无: 报告没有 per-hop 明细, 或没有 fo >= %d 的网)' % args.min_fo)
        for n in gf[:args.top_fanout]:
            tag = '  [clk-tree]' if n['is_clock'] else ''
            drv = ('  驱动 %s' % n['driver_type']) if n.get('driver_type') else ''
            print('    fo=%-5d 该网最大单跳 %7.3f ns  出现 %2d 条路径  %s%s%s'
                  % (n['fo'], n['route_ns_max'], n['hits'], n['name'], drv, tag))

        cands = [n for n in gf
                 if n['fo'] >= args.fo_threshold and not n['is_clock']]
        cands.sort(key=lambda n: (-n['route_ns_max'], -n['fo']))
        print('')
        print('  ' + '=' * 96)
        print('  复制候选 (fo >= %d, 按该跳延迟降序) -- *这正是本项目实测的抓手*'
              % args.fo_threshold)
        print('  判据: fo 大 + 该跳延迟大 => 对该**驱动器(寄存器)**加 (* max_fanout=16 *) 让综合器复制')
        print('  ' + '=' * 96)
        if not cands:
            print('    (无候选)')
        for n in cands[:args.top_fanout]:
            drv = ('  <- 驱动: %s  %s' % (n['driver_type'], n['driver_name'])
                   if n.get('driver_type') else '')
            print('    fo=%-5d 单跳 %7.3f ns  %s%s'
                  % (n['fo'], n['route_ns_max'], n['name'], drv))
        if cands:
            print('')
            print('    * 驱动器是 FDCE/FDRE 等寄存器 -> 复制寄存器 (ref5 E5 就是这样 +8.50 MHz)')
            print('    * 驱动器是 LUT -> 只能复制 LUT 逻辑, 收益通常小得多, 先别动')
            print('    !! 加完属性必须回头看 FF 数有没有上升 (PITFALLS R37-4):')
            print('       没上升 = 属性被接受但没复制 = 纯布局扰动, 不是优化。')


# ---------------------------------------------------------------- JSON
def to_json(res, args):
    if not res.get('ok'):
        return {'ok': False, 'report': res['report'], 'error': res.get('error')}
    setup = res['all_setup_paths']
    top = setup[:args.top]
    keys = ('kind', 'status', 'slack', 'source', 'destination', 'data_path_delay',
            'logic_ns', 'logic_pct', 'route_ns', 'route_pct',
            'logic_levels', 'logic_levels_hist')
    slim = [{k: p.get(k) for k in keys} for p in top]
    for p, sp in zip(top, slim):
        sp['fanout_nets'] = [{'fo': n['fo'], 'incr_ns': n['incr'], 'name': n['name'],
                              'driver_type': n.get('driver_type', '')}
                             for n in path_fanout(p, args.min_fo)]
        sp['hops'] = [{'kind': h['kind'], 'type': h.get('type'),
                       'name': h['name'], 'incr_ns': h.get('incr'),
                       'fo': h.get('fo')} for h in p['hops']]
    return {
        'ok': True,
        'report': res['report'],
        'period_ns': res['period_ns'],
        'period_source': res['period_source'],
        'summary': res.get('summary'),
        'wns': res.get('wns'),
        'fmax_mhz': res.get('fmax_mhz'),
        'setup_path_count': len(setup),
        'hold_path_count': len(res['all_hold_paths']),
        'top_paths': slim,
        'global_fanout': global_fanout(setup, args.min_fo),
        'copy_candidates': [n for n in global_fanout(setup, args.fo_threshold)
                            if not n['is_clock']],
        'warnings': res['warnings'],
    }


# ---------------------------------------------------------------- 输入解析
def resolve_inputs(raw):
    """把用户给的参数展开成文件列表。"""
    out = []
    for item in raw:
        for piece in item.split(','):
            piece = piece.strip().strip('"').strip("'")
            if not piece:
                continue
            if os.path.isdir(piece):
                cand = os.path.join(piece, 'timing.rpt')
                out.append(cand if os.path.isfile(cand) else piece)
                continue
            if any(ch in piece for ch in '*?['):
                hits = sorted(glob.glob(piece))
                if hits:
                    out.extend(hits)
                else:
                    out.append(piece)
                continue
            out.append(piece)
    seen, uniq = set(), []
    for p in out:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            uniq.append(p)
    return uniq


# ---------------------------------------------------------------- main
def main(argv=None):
    try:
        sys.stdout.reconfigure(errors='replace')
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        description='从 Vivado timing report 里挖出高扇出网 / 最差路径 = 下一个优化抓手',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            例:
              python tools/path_hotspot.py
              python tools/path_hotspot.py ref5/runs/e1-base-a/timing.rpt --top 10
              python tools/path_hotspot.py "ref5/runs/*/timing.rpt"     # 多份 -> 对照表
              python tools/path_hotspot.py fpga/timing.rpt --json
            """))
    ap.add_argument('report', nargs='*', default=[DEFAULT_REPORT],
                    help='timing report 路径 (默认 fpga/timing.rpt); '
                         '支持目录 / 通配符 / 逗号分隔多个')
    ap.add_argument('--top', type=int, default=10, help='打印前 N 条最差 setup 路径 (默认 10)')
    ap.add_argument('--top-fanout', type=int, default=15,
                    help='高扇出网清单最多打印多少条 (默认 15)')
    ap.add_argument('--min-fo', type=int, default=8,
                    help='进入高扇出清单的最小 fo (默认 8; 0 = 全都列)')
    ap.add_argument('--fo-threshold', type=int, default=16,
                    help='"复制候选" 的 fo 门槛 (默认 16, 与实测用的 max_fanout=16 对齐)')
    ap.add_argument('--period', type=float, default=None,
                    help='强制指定时钟周期 ns (默认从报告解析, 再退回 20)')
    ap.add_argument('--json', action='store_true', help='输出机器可读 JSON')
    ap.add_argument('--no-hops', action='store_true',
                    help='只打总结 + 每条路径的 headline, 不打逐跳明细/扇出清单')
    args = ap.parse_args(argv)

    files = resolve_inputs(args.report)
    if not files:
        print('  [FAIL] 没有指定任何报告文件', file=sys.stderr)
        return 1

    missing = [f for f in files if not os.path.isfile(f)]
    if missing and len(missing) == len(files):
        for m in missing:
            print('  [FAIL] 文件不存在: %s' % m, file=sys.stderr)
        print('  提示: 默认位置是 fpga/timing.rpt; ref5 的逐次运行报告在 '
              'ref5/runs/<tag>/timing.rpt (该目录被 .gitignore, 可能已不在工作区)',
              file=sys.stderr)
        return 1

    results = []
    for f in files:
        if not os.path.isfile(f):
            print('  [warn] 跳过不存在的文件: %s' % f, file=sys.stderr)
            continue
        results.append(analyze(f, args.period))

    if args.json:
        payload = ([to_json(r, args) for r in results] if len(results) > 1
                   else to_json(results[0], args))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if all(r.get('ok') for r in results) else 1

    if len(results) > 1:
        print('=' * 100)
        print('path_hotspot -- 多份报告对照 (%d 份)  [复现性检查: 同配置应逐位同 WNS/路径]' % len(results))
        print('=' * 100)
        print('  %-52s %8s %9s %7s %7s' % ('报告', 'WNS(ns)', 'fmax(MHz)', 'setup', 'hold'))
        print('  %-52s %8s %9s %7s %7s' % ('-' * 52, '-' * 8, '-' * 9, '-' * 7, '-' * 7))
        for r in results:
            name = r['report']
            if len(name) > 52:
                name = '...' + name[-49:]
            print('  %-52s %8s %9s %7d %7d'
                  % (name, fmt(r.get('wns')), fmt(r.get('fmax_mhz'), '%.2f'),
                     len(r.get('all_setup_paths') or []),
                     len(r.get('all_hold_paths') or [])))
        ok = [r for r in results if r.get('ok')]
        if len(ok) > 1:
            fp = {}
            for r in ok:
                key = (round(r['wns'], 4) if r.get('wns') is not None else None,
                       r['all_setup_paths'][0]['source'] if r['all_setup_paths'] else '?')
                fp.setdefault(key, []).append(
                    '%s/%s' % (os.path.basename(os.path.dirname(r['report'])),
                               os.path.basename(r['report'])))
            print('')
            print('  分组 (WNS + 关键路径源点相同 = 逐位可复现):')
            for i, (k, v) in enumerate(fp.items(), 1):
                print('    group %d: WNS=%s  src=%s  x%d  %s'
                      % (i, k[0], k[1], len(v), ', '.join(v)))
        return 0

    r = results[0]
    print_report(r, args)
    return 0 if r.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
