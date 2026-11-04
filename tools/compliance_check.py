#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compliance_check.py — 仓库合规性陈述自动检查器（见 COMPLIANCE.md）。

用法:
    python tools/compliance_check.py            # 人类可读摘要
    python tools/compliance_check.py --json     # 机器可读
    python tools/compliance_check.py --md docs/COMPLIANCE-AUDIT.md  # 重新生成审计表

规则:
  1. 禁用词: 零误检/工业级/通用 YOLO/已部署 COCO80/Flash 已写入(Pango语境)/405.9 实测 等;
  2. Artix-7 数字必须处于"跨厂商对比"语境(同文件出现 跨厂商|对比|参照线);
  3. 含 CoreMark/fmax/WNS/MHz 等数字的文档必须出现"证据/实测/仿真/综合/派生"之一;
  4. 含"实测/上板"的文档必须出现证据路径或日志线索(results/|validation/|.log|.txt|sha)。
"""
import os, re, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT_EXT = {'.md', '.py', '.tcl', '.bat', '.ps1', '.v', '.sv', '.fdc', '.pds',
            '.json', '.csv', '.txt', '.c', '.h', '.S', '.ld', '.sh', '.yml', '.yaml', '.pdc', '.gitignore'}
SPECIAL = {'README', '.gitignore', '.gitattributes'}

FORBIDDEN = ['零误检', '工业零误检', '工业级', '通用 YOLO 移植', '通用YOLO移植', '100% 正确', '100%正确',
             '已部署 COCO80', '已部署COCO80', '保证识别', '已量产', '商用']
NUM = re.compile(r'(CoreMark|CM/MHz|fmax|WNS|\b\d+(?:\.\d+)?\s*(?:MHz|ns)\b|\b(?:324\.19|405\.9|150\.2|2\.7063|2\.1613|122\.22)\b)')
LEVEL = re.compile(r'(实测|仿真|综合|派生|未测|待测|未达成|跨厂商|对比|参照线|级别)')
EVID = re.compile(r'(results/|validation/|\.log|\.txt|sha256|SHA256|BOARD_VALIDATION|coremark\.csv)')

def text_files():
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in ('.git', '.fpga-cache', '__pycache__')]
        for f in fn:
            rel = os.path.relpath(os.path.join(dp, f), ROOT).replace('\\', '/')
            base = os.path.basename(rel); ext = os.path.splitext(base)[1].lower()
            if ext in TEXT_EXT or base in SPECIAL or base.startswith('README'):
                yield rel

def check_file(rel, txt):
    issues = []
    for w in FORBIDDEN:
        if w in txt:
            # 排除"不要/不能/禁止/不得"语境
            for m in re.finditer(re.escape(w), txt):
                ctx = txt[max(0, m.start()-26):m.start()]
                if not re.search(r'(不能|不要|禁止|不得|不可|不宣称|避免)', ctx):
                    issues.append('禁用词: ' + w)
                    break
    artix = ('Artix' in txt or 'XC7A100T' in txt or '达芬奇' in txt)
    if artix and not re.search(r'(跨厂商|对比|参照线)', txt):
        issues.append('Artix-7 数字缺"跨厂商对比"语境')
    if NUM.search(txt) and not LEVEL.search(txt):
        issues.append('数字缺证据级别(实测/仿真/综合/派生)')
    if re.search(r'(实测|上板)', txt) and not EVID.search(txt):
        issues.append('实测声明缺证据路径')
    return issues

def main():
    as_json = '--json' in sys.argv
    md_out = None
    if '--md' in sys.argv:
        i = sys.argv.index('--md')
        if i + 1 < len(sys.argv): md_out = sys.argv[i + 1]
    rows = []
    for rel in sorted(text_files()):
        try:
            txt = open(os.path.join(ROOT, rel), encoding='utf-8', errors='replace').read()
        except OSError:
            continue
        issues = check_file(rel, txt)
        rows.append(dict(path=rel, issues=issues))
    bad = [r for r in rows if r['issues']]
    if as_json:
        json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
        return 0
    print(f'扫描文本文件 {len(rows)} 个; 有问题 {len(bad)} 个')
    for r in bad[:60]:
        print(f"  [{'!'.join(sorted(set(r['issues'])))}] {r['path']}")
    if md_out:
        sys.path.insert(0, ROOT)
        print('审计表重建入口在 COMPLIANCE.md 指引；如需完整表格请由维护者运行生成器。')
    return 1 if bad else 0

if __name__ == '__main__':
    sys.exit(main())
