#!/usr/bin/env python3
"""fpga_workflow.py - 本工程的统一工作流入口 (校验 / 回归 / 报告)

设计目标: 把 PITFALLS.md 里"反复踩、且静默失败"的坑变成一条命令就能查出来的东西。

  doctor   环境自检: 工具链是否就位, 会话 env 是否需要修复
  check    R1 漂移检测: RTL 清单三处是否同步 / bitstream 新鲜度 / 内存地址 / 编码
  verify   ModelSim 增量回归: 只跑受影响的测试 (内容指纹缓存)
  report   从 results/*.csv 与 fpga/*.rpt 汇总三项指标 + 绝对吞吐

用法:
    python tools/fpga_workflow.py check
    python tools/fpga_workflow.py verify                  # 增量, 跳过未受影响的
    python tools/fpga_workflow.py verify --only tb_units  # 单跑一个
    python tools/fpga_workflow.py verify --force          # 全量重跑
    python tools/fpga_workflow.py report

退出码: 0 = 全部通过, 1 = 有 FAIL/BLOCK, 2 = 用法错误
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

# --------------------------------------------------------------------------
# 控制台编码: Windows 默认代码页是 GBK, 而本工程的输出含大量中文与 => [i] [!] 之类的
# 符号。stdout 一旦被重定向到管道/文件, Python 按 locale(GBK) 编码写出, 遇到非 GBK
# 字符**直接抛 UnicodeEncodeError** —— 实测 `synth --record --status keep` 就因此崩溃,
# 而 **CSV 已经写下去了** => "数据成功但命令失败", 最难排查的一类状态。
# 这里把 stdout/stderr 统一钉成 UTF-8, 从根上消掉这一类 (tools/*.py 里的非 GBK 字符
# 也已普查清零, 两道保险)。
# --------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# --------------------------------------------------------------------------
# 路径常量 (全部用正斜杠, 因为 Vivado/Tcl 只吃正斜杠, 而 Windows 两种都认)
# --------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE).replace("\\", "/")

CACHE_DIR = os.path.join(REPO, ".fpga-cache")
STATE_FILE = os.path.join(CACHE_DIR, "verify-state.json")
LOG_DIR = os.path.join(CACHE_DIR, "logs")

# 工具链 (来自 SKILL.md 第 1 节, 已核实存在)
MSIM = "D:/software/tools/Modelsim/win64"
MSIM_LIC = MSIM + "/LICENSE.TXT"
GCC_BIN = "D:/software/tools/riscv-none-elf/xpack-riscv-none-elf-gcc-15.2.0-1/bin"
GCC = GCC_BIN + "/riscv-none-elf-gcc.exe"
VIVADO = "D:/software/Learn/Xilinx/Vivado/2018.3"
VIVADO_BAT = VIVADO + "/bin/vivado.bat"

PYTHON = sys.executable

# --------------------------------------------------------------------------
# 规范源文件集
# --------------------------------------------------------------------------
# CPU 核: 仓库根目录下的所有 .sv (纯行为级, 零厂商原语)
CORE_EXCLUDE: set[str] = set()
# SoC 侧: 显式列举, 不 glob —— fpga/ 里有大量 Vivado 生成物
SOC_SV = ["fpga/mmio_subsys.sv", "fpga/top_a100t.sv"]

# 已知"在研、尚未接线"的文件: 不被任何模块例化是**预期**的, 报为 info 而非 warn。
# 一旦 P1 (异常/中断 core 接线) 做完, 从这里删掉, 检查会自动开始强制要求。
KNOWN_UNWIRED: set[str] = set()      # csr_file.sv 已于 P1 接入 core, 不再是"在研未接线"

# 每份清单各自应有的集合。差异只在"该有的没有"时报错, 避免把
# "top_a100t 不在 ModelSim 回归里" 这种正常情况误报成问题。
BUILD_FILES = {
    "vivado-score": {
        "path": "fpga/build.tcl",
        "pattern": r"read_verilog\s+-sv\s+\[list\s+(.*?)\]",
        "expected": "rtl",
        "desc": "评分版综合 (非工程模式)",
    },
    "vivado-ila": {
        "path": "fpga/build_ila.tcl",
        "pattern": r"add_files\s+-norecurse\s+\[list\s+(.*?)\]",
        "expected": "rtl",
        "desc": "ILA 调试版综合 (工程模式)",
    },
    "modelsim-coremark": {
        "path": "tools/run_coremark.py",
        "pattern": r"^RTL\s*=\s*\[(.*?)\]",
        "expected": "core",
        "desc": "CoreMark 回归 (ModelSim) —— tb_sw 直接例化 mmio_subsys, 不要 top",
    },
}


# ==========================================================================
# 通用工具
# ==========================================================================
def posix(p: str) -> str:
    return p.replace("\\", "/")


def abspath(rel: str) -> str:
    return posix(os.path.join(REPO, rel))


def read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# CPU 核 RTL 的**编译顺序** —— rv32_pkg 必须第一个编译, 其余模块 import 它。
# 为什么显式列举而不 glob 后排序: 首次跑回归时按字母序把 rv32_pkg 排到了后面,
# vlog 报 60 个 "Could not find the package (rv32_pkg)" —— 顺序是语义, 不是格式。
CORE_SV_ORDER = [
    "rv32_pkg.sv",
    "alu.sv", "branch_unit.sv", "control_unit.sv", "imm_gen.sv", "pc.sv", "regfile.sv",
     "id_ex_reg.sv", "ex_mem_reg.sv", "mem_wb_reg.sv",
    "hazard_unit.sv", "load_store_unit.sv",
    "imem.sv", "dmem.sv", "btb.sv",
    # bht.sv: C-1 的两级流水 gshare 的 PHT (8192 项, 1 块 RAMB18)。
    # 【必须登记】仿真是 glob 有兜底, 但:
    #   ① 综合走 build.tcl:123 的**显式列表**, 漏了就是 "module bht not found";
    #   ② `check` 的 drift-check 用这张表, 漏了会报"未登记核文件"。
    # 两处必须同步 (R1: 一处改了另一处没跟上 = 静默失效)。
    "bht.sv",
    "ras.sv", "gemm_accel.sv", "muldiv.sv", "csr_file.sv",
    "core.sv",
]


def discover_core_sv() -> list[str]:
    """CPU 核 RTL, 按编译顺序返回 (rv32_pkg 在最前)。"""
    out = [f for f in CORE_SV_ORDER if os.path.isfile(os.path.join(REPO, f))]
    # 兜底: 仓库里新出现的、还没登记进 CORE_SV_ORDER 的核文件
    known = set(CORE_SV_ORDER)
    for name in sorted(os.listdir(REPO)):
        if (name.endswith(".sv") and name not in known
                and os.path.isfile(os.path.join(REPO, name))):
            out.append(name)
    return out


def unregistered_core_sv() -> list[str]:
    """仓库根目录存在、但没登记进 CORE_SV_ORDER 的 .sv (check 会报出来)。"""
    known = set(CORE_SV_ORDER)
    return sorted(
        n for n in os.listdir(REPO)
        if n.endswith(".sv") and n not in known and os.path.isfile(os.path.join(REPO, n))
    )


def canonical_rtl() -> list[str]:
    """本工程全部综合用 RTL 源文件。"""
    return discover_core_sv() + SOC_SV


MODULE_RE = re.compile(r"^\s*module\s+([A-Za-z_]\w*)", re.M)
INST_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?:#\s*\([^;]*?\)\s*)?([A-Za-z_]\w*)\s*\(", re.M)


def synth_inputs_for(args=None) -> list[str]:
    """本次综合**实际用到**的输入文件清单。

    【为什么需要它】`canonical_rtl()` 是"仓库里所有核 .sv"的全清单, 但有些模块
    被宏门控、根本不进网表 —— 例如 `gemm_accel.sv` 只在定义 `GEMM_EN` 时才被
    `mmio_subsys.sv` 例化。若拿它的 mtime 判"bitstream 是否过期", 就会**误报**
    stale (实测踩过: 明明两个构建都没开 GEMM_EN, check 却报 gemm_accel.sv 过期)。

    所以: 这里把门控模块过滤掉, 得到"真的会被综合"的那一份清单。
    check 判新鲜度、synth 记录清单, 两边都用它, 保证口径一致。
    """
    gemm_on = bool(getattr(args, "gemm", False))
    out = []
    for f in canonical_rtl():
        base = os.path.basename(f)
        if base == "gemm_accel.sv" and not gemm_on:
            continue          # GEMM_EN 关 -> 该模块不会被例化, 不进网表
        out.append(f)
    return out + ["fpga/top_a100t.sv", "fpga/mmio_subsys.sv", "fpga/daVinciPro.xdc"]


def declared_modules(rel: str) -> list[str]:
    try:
        return MODULE_RE.findall(read_text(abspath(rel)))
    except OSError:
        return []


def declared_in_any(module: str, files: list[str]) -> bool:
    return any(module in declared_modules(f) for f in files)


def all_sv_sources() -> list[str]:
    """所有会被编译的 SV 源文件 (RTL + tb), 用于判断"某模块是否真的被例化过"。"""
    out = list(canonical_rtl())
    simdir = os.path.join(REPO, "sim")
    if os.path.isdir(simdir):
        for n in sorted(os.listdir(simdir)):
            if n.endswith(".sv"):
                out.append("sim/" + n)
    return out


def instantiated_modules() -> set[str]:
    """扫描所有 SV 源, 收集被例化的模块名。

    注意端口列表可能跨行 (tb_sw.sv 就是 .clk (clk),\\n .rst (rst), ...),
    所以用 DOTALL 版的非贪婪匹配, 不能只看单行。
    """
    used: set[str] = set()
    for f in all_sv_sources():
        try:
            txt = read_text(abspath(f))
        except OSError:
            continue
        # 去掉 // 行注释, 免得把注释里的模块名当例化
        txt = re.sub(r"//[^\n]*", "", txt)
        for m in re.finditer(r"^\s*([A-Za-z_]\w*)\s*(?:#\s*\(.*?\)\s*)?([A-Za-z_]\w*)\s*\(",
                            txt, re.M | re.S):
            used.add(m.group(1))
    return used


def parse_tcl_list(path: str, pattern: str) -> list[str] | None:
    """从 .tcl / .py 里抽出文件清单, 返回相对仓库根的 posix 路径。"""
    try:
        text = read_text(abspath(path))
    except OSError:
        return None
    if path.endswith(".py"):
        # Python: 抽 '单引号字符串'
        m = re.search(pattern, text, re.M | re.S)
        if not m:
            return None
        items = re.findall(r"['\"]([^'\"]+\.sv)['\"]", m.group(1))
    else:
        m = re.search(pattern, text, re.M | re.S)
        if not m:
            return None
        items = re.findall(r'"([^"]+\.sv)"', m.group(1))
    out = []
    for it in items:
        p = posix(it)
        # 【E-02 配套, 必须保留】build.tcl / build_ila.tcl 现在用 `$root/...` 前缀
        # (root 由 [info script] 反推仓库根, 见 build-repo 的去硬编码修复)。
        # 这里必须把前缀归一化掉 —— 否则 check 第 [1] 项会把 22 个文件全判成
        # "缺 22 / 多 22" 的**假 FAIL**, 而那是最容易被当成真问题的一类噪声。
        for pre in ("${root}/", "$root/"):
            if p.startswith(pre):
                p = p[len(pre):]
                break
        if os.path.isabs(p):
            p = posix(os.path.relpath(p, REPO))
        out.append(p)
    return out


# ==========================================================================
# 子进程执行: 恢复被剥离的会话环境
# ==========================================================================
def clean_env() -> dict:
    """DSH 会话的 process.env 是空的, 必须显式补齐, 否则 Vivado/ModelSim 都会失败。

    两个具体故障 (SKILL.md 第 2 节):
      - 缺 PROCESSOR_ARCHITECTURE -> Vivado 报 "32-bit platform is not supported"
      - 缺 LM_LICENSE_FILE        -> vsim 报 "Unable to checkout a license"
    """
    env = {
        "SystemRoot": r"C:\Windows",
        "windir": r"C:\Windows",
        "SystemDrive": "C:",
        "COMSPEC": r"C:\Windows\System32\cmd.exe",
        "PATHEXT": ".COM;.EXE;.BAT;.CMD",
        "PROCESSOR_ARCHITECTURE": "AMD64",
        "PROCESSOR_IDENTIFIER": "AMD64",
        "TEMP": tempfile.gettempdir(),
        "TMP": tempfile.gettempdir(),
        "USERPROFILE": os.path.expanduser("~"),
        "HOME": os.path.expanduser("~"),
        "NUMBER_OF_PROCESSORS": str(os.cpu_count() or 1),
        "OS": "Windows_NT",
        # powershell 不在精简 PATH 中, 显式补上
        "PATH": os.pathsep.join([
            MSIM,
            GCC_BIN,
            r"C:\Windows\System32",
            r"C:\Windows\System32\WindowsPowerShell\v1.0",
            r"C:\Windows",
        ]),
    }
    # 继承父进程里非空的值 (交互式运行时通常已经有了)
    for k, v in os.environ.items():
        if v:
            env[k] = v
    env["LM_LICENSE_FILE"] = MSIM_LIC
    env["MGLS_LICENSE_FILE"] = MSIM_LIC
    env["PATH"] = MSIM + os.pathsep + GCC_BIN + os.pathsep + env.get("PATH", "")
    return env


def run(cmd, cwd=None, timeout=1800, env=None):
    """执行命令, 返回 (returncode, 合并输出, 墙钟秒数)。"""
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=cwd, env=env or clean_env(), capture_output=True,
            text=True, errors="replace", timeout=timeout,
        )
        return r.returncode, (r.stdout or "") + (r.stderr or ""), time.time() - t0
    except subprocess.TimeoutExpired as e:
        out = ((e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes)
               else (e.stdout or ""))
        return 124, out + f"\n!! TIMEOUT after {timeout}s\n", time.time() - t0
    except FileNotFoundError as e:
        return 127, f"!! 找不到可执行文件: {e}\n", time.time() - t0


# ==========================================================================
# 内存布局契约 (PITFALLS R1 —— 改 link.ld 要同步 5 类文件)
# ==========================================================================
def check_memory_layout() -> list[dict]:
    """核对 link.ld 里的地址常量是否与 RTL / tb 同步。"""
    findings = []
    ld_path = "sw/link.ld"
    try:
        ld = read_text(abspath(ld_path))
    except OSError:
        return [{"sev": "FAIL", "msg": f"读不到 {ld_path}"}]

    def ld_value(name):
        m = re.search(name + r"\s*=\s*(0x[0-9A-Fa-f_]+)", ld)
        return int(m.group(1).replace("_", ""), 16) if m else None

    tohost_ld = ld_value(r"__tohost_addr|tohost")
    log_ld = ld_value(r"__log_start|log_start")

    # tb_sw.sv 的 TOHOST_ADDR 是 DMEM_BASE + 0x2000, 注释里还写着旧值 0x90001000
    for rel, pat, expect_name in [
        ("sim/tb_sw.sv", r"TOHOST_ADDR\s*=\s*DMEM_BASE\s*\+\s*32'h([0-9A-Fa-f_]+)", "tohost offset"),
    ]:
        try:
            txt = read_text(abspath(rel))
        except OSError:
            continue
        m = re.search(pat, txt)
        if m:
            off = int(m.group(1).replace("_", ""), 16)
            findings.append({
                "sev": "INFO",
                "msg": f"{rel}: {expect_name} = 0x{off:04X} (TOHOST = DMEM_BASE + 0x{off:X})",
            })
        # 注释里的旧地址: 行注释里出现 0x9000_1000 而代码已用 0x2000
        stale = re.findall(r"//.*?(0x9000[0-9A-Fa-f_]{4})", txt)
        for s in set(stale):
            val = int(s.replace("_", ""), 16)
            if val == 0x90001000 and m and int(m.group(1).replace("_", ""), 16) != 0x1000:
                findings.append({
                    "sev": "WARN",
                    "msg": f"{rel}: 注释里写着旧 tohost 0x90001000, 代码已改用 "
                           f"DMEM_BASE+0x{m.group(1)} —— 注释该更新了 (会误导下一个人)",
                })

    # dmem 空间 ASSERT 守卫: CoreMark .data+.bss 正好用满 4KB
    if "ASSERT" in ld:
        findings.append({"sev": "INFO", "msg": f"{ld_path}: 有 ASSERT 守卫 (布局溢出会在链接期暴露)"})
    else:
        findings.append({"sev": "WARN", "msg": f"{ld_path}: 没有 ASSERT 守卫, 布局溢出会静默"})

    if tohost_ld is None:
        findings.append({
            "sev": "WARN",
            "msg": f"{ld_path}: 没找到 tohost 的 PROVIDE 定义 (tb 靠绝对地址约定, 找不到就无法机器核对)",
        })
    return findings


def _sw_source_files() -> list[str]:
    """sw/ 下参与镜像构建的源文件 (C / S / 链接脚本)。"""
    out = []
    for d in ("sw", "sw/coremark"):
        dd = abspath(d)
        if os.path.isdir(dd):
            for n in sorted(os.listdir(dd)):
                if n.endswith((".c", ".h", ".S", ".ld")):
                    out.append(f"{d}/{n}")
    return out


def _sw_source_hash() -> str:
    """sw/ 源码的内容哈希 -> 用于判断镜像是否需要重建。"""
    h = hashlib.sha256()
    for rel in _sw_source_files():
        p = abspath(rel)
        h.update(rel.encode())
        h.update(sha256_file(p).encode() if os.path.exists(p) else b"MISSING")
    return h.hexdigest()[:32]


def _newest_sw_mtime() -> float:
    newest = 0.0
    for rel in _sw_source_files():
        p = abspath(rel)
        if os.path.exists(p):
            newest = max(newest, os.path.getmtime(p))
    return newest


def check_bitstream_freshness() -> list[dict]:
    """bitstream 新鲜度: 评分版 .bit 是否比 RTL / 程序镜像旧。

    为什么单列一项: imem/dmem 是**综合时**由 $readmemh 装进去的, 所以
    "改了软件 -> 只重建 .hex" 是不够的, 必须重新综合。这个坑曾经让板卡上
    跑的是旧程序镜像而不自知 —— 而且症状是"结果完全一致", 极易误判成通过。
    """
    out: list[dict] = []
    bit = abspath("fpga/cpu_a100t.bit")
    if not os.path.exists(bit):
        return [{"sev": "WARN", "msg": "fpga/cpu_a100t.bit 不存在 (还没构建过评分版?)"}]
    bit_t = os.path.getmtime(bit)

    # 评分版构建脚本里用哪个程序镜像。
    # build.tcl 现在是参数化的 (set PROG / set imem / set dmem), 所以不能只认
    # 字面量 `DMEM_INIT_FILE="..."` —— 那种写法在重构后会变成假报警。
    # 改为: 先看 verilog_define 那行到底传了哪几个宏, 再把 $imem/$dmem 解析成
    # 真实路径 (PROG 默认值 + 路径模板)。
    tcl = read_text(abspath("fpga/build.tcl"))
    m_prog = re.search(r"^set PROG\s+(\S+)\s*$", tcl, re.M)
    prog = m_prog.group(1) if m_prog else None
    has_imem = "IMEM_INIT_FILE" in tcl
    has_dmem = "DMEM_INIT_FILE" in tcl

    def resolve(var: str) -> str | None:
        """把 `set <var> "$root/sim/prog/$PROG.hex"` 解析成仓库相对路径。"""
        m = re.search(r'^set\s+' + var + r'\s+"([^"]+)"', tcl, re.M)
        if not m or not prog:
            return None
        p = m.group(1).replace("$root", "D:/USER/Desktop/FPGA/FPGA").replace("$PROG", prog)
        return posix(os.path.relpath(p, REPO))

    imem_img, dmem_img = resolve("imem"), resolve("dmem")

    if not has_dmem:
        out.append({
            "sev": "FAIL",
            "msg": "fpga/build.tcl 没有把 DMEM_INIT_FILE 传给 synth_design —— "
                   "本核是哈佛结构, .data/.rodata 靠 dmem 自己的镜像装入, "
                   "不传就跑不了真正的程序 (P0)",
        })
    if not has_imem:
        out.append({"sev": "FAIL", "msg": "fpga/build.tcl 没有传 IMEM_INIT_FILE"})
    if dmem_img and not os.path.exists(abspath(dmem_img)):
        out.append({
            "sev": "FAIL",
            "msg": f"build.tcl 指定的 dmem 镜像不存在: {dmem_img} "
                   f"(综合会因 file exists 守卫直接失败, 但先在此报出来更省事)",
        })
    if imem_img and not imem_img.endswith("cm_hw.hex"):
        out.append({
            "sev": "WARN",
            "msg": f"评分版装的是 {imem_img}, 不是跑分程序 cm_hw.hex —— "
                   f"这个 bitstream 上板跑不出 CoreMark 分数 (P0)",
        })

    # 比 bit 新的输入文件。
    # 【镜像用内容哈希判据, 不用 mtime】—— 重建一份"逐字节相同"的镜像会让 mtime
    # 变新, 用 mtime 就会误报 stale。synth 每次构建会记下当时用的镜像哈希,
    # 哈希没变就说明 bitstream 里的程序镜像仍然对得上。
    #
    # 【RTL 要按"那次综合实际用的输入清单"判, 不能按 canonical_rtl() 全清单】
    # 有些模块被宏门控 (例如 gemm_accel.sv 由 GEMM_EN 控制, 两个构建都不定义它),
    # 它们根本不在 bitstream 里 —— 拿它们的 mtime 去判 stale 会**误报**。
    # synth 会把当时用的输入清单记进 synth-cache.json, 这里优先采用。
    newer = []
    try:
        sc = json.loads(read_text(os.path.join(CACHE_DIR, "synth-cache.json")))
    except Exception:
        sc = {}
    rec = sc.get("image_hash", {}).get(prog) or {}
    recorded_inputs = rec.get("inputs")
    if recorded_inputs:
        inputs = list(recorded_inputs)
    else:
        # 旧版本构建没记录 -> 退回全清单 (偏保守, 可能误报但不会漏报)
        inputs = canonical_rtl() + [
            "fpga/top_a100t.sv", "fpga/mmio_subsys.sv", "fpga/daVinciPro.xdc",
        ]
    recorded_hashes = rec.get("input_sha256") or {}
    for f in inputs:
        fp = abspath(f)
        if not os.path.exists(fp):
            continue
        want = recorded_hashes.get(f)
        if want:
            # 【X-07 修复】按**内容**判, 不按 mtime。
            # mtime 判据可以被一次 `git checkout <file>` / `git stash` / 从归档拷贝
            # **伪造**: 内容已经变了, 但 mtime 看起来"比 bitstream 旧" -> 误判为新鲜。
            # 结果就是 check 在"工作区 RTL ≠ .bit 里的网表"时给出"一致"结论 ——
            # 而这正是上板前最后一道门。哈希本来就已经记在 synth-cache.json 里了。
            if sha256_file(fp) != want:
                newer.append(f"{f}  (内容已变)")
        elif os.path.getmtime(fp) > bit_t:
            newer.append(f"{f}  (仅按 mtime 判: 该次构建未记录内容哈希)")

    for label, rel in (("imem", imem_img), ("dmem", dmem_img)):
        if not rel or not os.path.exists(abspath(rel)):
            continue
        cur = sha256_file(abspath(rel))
        if rec and rec.get(label) == cur:
            # 内容与上次综合时一致 -> 即使 mtime 更新也不算 stale
            continue
        if os.path.getmtime(abspath(rel)) > bit_t:
            newer.append(f"{rel}  (内容已变)")

    if newer:
        shown = ", ".join(newer[:6]) + (" ..." if len(newer) > 6 else "")
        out.append({
            "sev": "FAIL",
            "msg": f"bitstream 比 {len(newer)} 个输入文件旧 -> 板上跑的不是当前代码 "
                   f"({shown})",
        })
    else:
        out.append({
            "sev": "INFO",
            "msg": f"bitstream 比所有输入新 "
                   f"({os.path.getsize(bit)//1024} KB, {time.strftime('%m-%d %H:%M', time.localtime(bit_t))})",
        })
    if imem_img:
        out.append({
            "sev": "INFO",
            "msg": f"评分版程序镜像: imem={imem_img}"
                   + (f", dmem={dmem_img}" if dmem_img else ", dmem=(未定义)"),
        })
    return out


# ==========================================================================
# check: R1 漂移检测
# ==========================================================================
# ==========================================================================
# [7] 综合警告门 (M-06) —— 用 Vivado 自己报的警告当 lint
# --------------------------------------------------------------------------
# 【为什么必须是 Vivado, 不是 ModelSim / verilator】
#   · 本机没有 verilator / slang / svlint (实测 Get-Command 全部 NOT FOUND);
#   · ModelSim 的 vlog 对全核 22 个文件**只报 3 条**警告, 且全是
#     "Defaulting port kind to var"(与设计无关);
#   · 而本项目真正吃过亏的警告**只在 Vivado 里**, 且都因为"看起来像标准提示"被放过:
#       [Synth 8-3331] design mmio_subsys has unconnected port wstrb[3..0]
#            -> 端口声明了却没人读。与 X-08(MMIO 写忽略 wstrb) 是同一处;
#               Vivado 报了 4 次, 从来没人看。
#       [Synth 8-6014] Unused sequential element amo_addr_r_reg was removed
#            -> A_EN 关闭时的死状态 (load_store_unit.sv:269)。
#       [Timing 38-1] DLL output pin(s) used on clock modifying cell ... without a
#                     feedback net
#            -> R43: 当时当成"标准提示"放过 => MMCM 不锁定 => 整颗 CPU 被按在
#               复位里, 两轮才定位。top_a100t.sv:63 自己写了判据:
#               "修复判据: 重新综合后该警告必须消失" —— 本节把它机器化。
#   所以把"综合警告"变成一道门: **新增的警告必须被分诊**(写进基线), 不能默认放过。
#
# 【键必须归一化】若键里带行号/绝对路径/位下标, 每次综合都会"看起来是新的",
#   门就会天天报警、然后被人忽略 —— 那比没有门更糟。
# ==========================================================================
SYNTH_WARN_BASELINE = os.path.join(CACHE_DIR, "synth-warn-baseline.json")
SYNTH_LOG = "fpga/build.log"
ILA_SYNTH_LOG = "fpga/ila_build.log"
# R43 的判据: 这条警告一旦出现, 说明 MMCM 反馈环断了, 必须立刻失败
R43_WARN_CODE = "Timing 38-1"


def _norm_warn_key(s: str) -> str:
    """把警告正文归一化成**跨次运行稳定**的键 (去行号/绝对路径/位下标/常量)。"""
    s = s.replace(REPO.replace("/", "\\"), "").replace(REPO, "")
    s = s.replace("\\", "/")
    s = re.sub(r"\[([\w./+-]+\.(?:sv|v|svh|tcl|xdc)):\d+(?::\d+)?\]", r"[\1]", s)
    s = re.sub(r"\[\d+(?::\d+)?\]", "[*]", s)          # wstrb[3] / addr[31:2] -> [*]
    s = re.sub(r"\b0x[0-9A-Fa-f]+\b", "0x*", s)
    return " ".join(s.split())


def parse_synth_warnings(text: str) -> dict:
    """从 Vivado 日志抽 WARNING / CRITICAL WARNING, 归一化成 {key: {count, sample}}。"""
    out: dict[str, dict] = {}
    for ln in text.splitlines():
        s = ln.strip()
        if "WARNING:" not in s:
            continue
        sev = "CRITICAL" if "CRITICAL WARNING:" in s else "WARN"
        m = re.search(r"\[([A-Za-z]\w* \d+-\d+)\]", s)      # [Synth 8-3331] / [Timing 38-1]
        code = m.group(1) if m else "(no-code)"
        body = s.split("]", 1)[1] if "]" in s else s
        key = f"{sev} {code} | {_norm_warn_key(body)}"
        e = out.setdefault(key, {"count": 0, "sample": s})
        e["count"] += 1
    return out


def synth_warn_report(log_rel: str = SYNTH_LOG, update_baseline: bool = False,
                      quiet: bool = False, top: int = 10) -> dict | None:
    """比对综合警告与基线。返回 {cur,new,gone,increased,baseline_exists} 或 None(无日志)。"""
    path = abspath(log_rel)
    if not os.path.exists(path):
        if not quiet:
            print(f"  (没有 {log_rel} —— 先跑一次 synth 才有警告可查)")
        return None
    cur = parse_synth_warnings(read_text(path))
    base: dict[str, int] = {}
    if os.path.exists(SYNTH_WARN_BASELINE):
        try:
            j = json.loads(read_text(SYNTH_WARN_BASELINE))
            base = j.get("warnings", {}) if isinstance(j, dict) else {}
        except Exception:                            # noqa: BLE001
            base = {}
    new = {k: v for k, v in cur.items() if k not in base}
    gone = {k: v for k, v in base.items() if k not in cur}
    increased = {k: (base[k], cur[k]["count"]) for k in cur
                 if k in base and cur[k]["count"] > base[k]}
    if update_baseline:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(SYNTH_WARN_BASELINE, "w", encoding="utf-8") as f:
            json.dump({"log": log_rel, "when": time.time(),
                       "warnings": {k: v["count"] for k, v in sorted(cur.items())}},
                      f, indent=1, ensure_ascii=False)
    if not quiet:
        total = sum(v["count"] for v in cur.values())
        print(f"  来源 {log_rel}: {len(cur)} 类 / {total} 条; 基线 {len(base)} 类"
              + ("   [基线已更新]" if update_baseline else ""))
        if new:
            print(f"    [新增] {len(new)} 类 —— 必须分诊 (确认无害后 --update-baseline)")
            for k in sorted(new)[:top]:
                print(f"      {k}  x{new[k]['count']}")
                print(f"        e.g. {new[k]['sample'][:150]}")
        if increased:
            print(f"    [变多] {len(increased)} 类")
            for k in sorted(increased)[:top]:
                a, b = increased[k]
                print(f"      {k}  {a} -> {b}")
        if gone:
            print(f"    [消失] {len(gone)} 类")
            for k in sorted(gone)[:top]:
                print(f"      {k}  (原 x{base[k]})")
        if not base:
            print("    [提示] 还没有基线 —— 用 `check --update-baseline` 固化当前警告,"
                  " 之后**新增**的才会报警")
    return {"cur": cur, "new": new, "gone": gone, "increased": increased,
            "baseline_exists": bool(base)}


def r43_clock_warning(log_rel: str) -> dict:
    """R43 判据: 日志里不得出现 'Timing 38-1 ... without a feedback net'。

    top_a100t.sv:55-65 记录了那次故障: MMCME2_BASE 漏接 .CLKFBIN 时, 综合期唯一
    的证据就是这条警告, 而它被当成"标准提示"放过了 => MMCM 永不锁定 => rst 恒有效
    => 整颗 CPU 被按在复位里 (ILA 抓到 2048 拍全是 ila_ctrl=0x00)。
    """
    path = abspath(log_rel)
    if not os.path.exists(path):
        return {}
    return {k: v for k, v in parse_synth_warnings(read_text(path)).items()
            if R43_WARN_CODE in k and "feedback net" in k}


def cmd_check(args) -> int:
    print("=" * 74)
    print("  R1 漂移检测  (PITFALLS: 改了 A 忘了同步 B —— 全部静默失败)")
    print("=" * 74)

    canon = canonical_rtl()
    canon_set = set(canon)
    problems: list[str] = []
    warnings: list[str] = []

    # ---- 1. 三处 RTL 清单 ----
    print(f"\n[1] RTL 文件清单  (规范集 = {len(canon)} 个文件)")
    core_set = set(discover_core_sv())
    listed_sets: dict[str, set[str]] = {}
    for key, spec in BUILD_FILES.items():
        expect = core_set if spec["expected"] == "core" else canon_set
        listed = parse_tcl_list(spec["path"], spec["pattern"])
        if listed is None:
            problems.append(f"{spec['path']}: 解析不出文件清单 (正则失效? 文件被改写了?)")
            print(f"  ??   {key:18s} {spec['path']:26s} 解析失败")
            continue
        listed_sets[key] = set(listed)
        missing = [f for f in canon if f in expect and f not in listed]
        extra = [f for f in listed if f not in canon_set]
        flag = "OK " if not missing and not extra else "!! "
        print(f"  {flag} {key:18s} {spec['path']:26s} {len(listed):2d} 项"
              + (f"  缺 {len(missing)}" if missing else "")
              + (f"  多 {len(extra)}" if extra else ""))
        for f in missing:
            mods = declared_modules(f)
            if f in KNOWN_UNWIRED:
                print(f"       [info] 缺 {f}  ({','.join(mods)} 尚未接线, P1 待办) -> 暂不强制")
            else:
                problems.append(f"{spec['path']} 缺少 {f} (模块 {','.join(mods)})")
                print(f"       [FAIL] 缺 {f}  模块 {','.join(mods)} -> 必须补进清单")
        for f in extra:
            problems.append(f"{spec['path']} 列了不存在的文件 {f}")
            print(f"       [FAIL] 多 {f}  (文件已不存在)")

    # 清单之间的互相漂移: 在 A 里有、在 B 里没有 —— 这是最典型的 R1 静默失败
    if len(listed_sets) >= 2:
        for a in listed_sets:
            for b in listed_sets:
                if a >= b:
                    continue
                only_a = listed_sets[a] - listed_sets[b]
                # 只在"两者本应一致"的文件上报警: 即 CPU 核文件
                only_a = {f for f in only_a if f in core_set}
                if only_a:
                    problems.append(
                        f"{BUILD_FILES[a]['path']} 有而 {BUILD_FILES[b]['path']} 没有: "
                        + ", ".join(sorted(only_a)))
                    print(f"       [FAIL] 清单互相漂移: {a} 有 {sorted(only_a)}, 但 {b} 没有")

    # ---- 2. 未接线模块 ----
    print("\n[2] 未接线模块 (RTL 存在但没有任何地方例化它)")
    unreg = unregistered_core_sv()
    if unreg:
        for f in unreg:
            print(f"  [FAIL] {f}: 仓库里存在但没登记进 tools/fpga_workflow.py 的 CORE_SV_ORDER")
            problems.append(f"{f} 未登记进 CORE_SV_ORDER -> 回归和检查都会漏掉它")
    used = instantiated_modules()
    unwired = []
    for f in canon:
        mods = [m for m in declared_modules(f) if m != "rv32_pkg"]
        # top 是顶层, 天然不会被例化
        if f.endswith("top_a100t.sv"):
            continue
        for mod in mods:
            if mod not in used:
                unwired.append((f, mod))
    if unwired:
        for f, mod in unwired:
            if f in KNOWN_UNWIRED:
                print(f"  [info] {f}: 模块 {mod} 未接线 (已知在研, P1 待办)")
            else:
                print(f"  [warn] {f}: 模块 {mod} 没有被任何地方例化")
                warnings.append(f"{f}:{mod} 未接线")
    else:
        print("  OK   所有 RTL 模块都被例化")

    # ---- 3. 软件 -> 镜像 新鲜度 ----
    # 【判据用内容哈希, 不用 mtime】(HANDOFF-WORKFLOW.md §5)
    # 踩过: 把 link.ld 改回原样(mtime 变了、内容没变) 就误报"镜像过期"。
    # mtime 会被 checkout/复制/改回原样打乱, 只有内容才算数。
    print("\n[3] 软件源 -> .hex 镜像 新鲜度 (内容哈希判据)")
    sw_hash = _sw_source_hash()
    hcache_path = os.path.join(CACHE_DIR, "sw-build.json")
    try:
        hcache = json.loads(read_text(hcache_path))
    except Exception:
        hcache = {}
    pairs = [
        ("sim/prog/hello.hex", "hello"),
        ("sim/prog/cm_rv32im.hex", "coremark"),
        ("sim/prog/cm_hw.hex", "coremark"),
    ]
    for img, group in pairs:
        ip = abspath(img)
        if not os.path.exists(ip):
            print(f"  [warn] {img} 不存在")
            warnings.append(f"{img} 不存在")
            continue
        built = hcache.get(group)
        if built is None:
            # 没有记录: 退回 mtime 比较, 但明确说明这是弱判据
            if os.path.getmtime(ip) < _newest_sw_mtime():
                print(f"  [warn] {img}: 无内容基线可比, 且比 sw/ 源文件旧 (建议重建)")
                warnings.append(f"{img} 可能过期 (无基线)")
            else:
                print(f"  OK    {img} (无内容基线, mtime 更新)")
            continue
        if built != sw_hash:
            print(f"  [FAIL] {img}: sw/ 源码内容已变 -> 需要重建镜像 "
                  f"(重跑 tools/build_sw.py 或 tools/asm.py)")
            problems.append(f"{img} 过期")
        else:
            print(f"  OK    {img} 与当前 sw/ 源码内容一致")

    # ---- 4. bitstream 新鲜度 ----
    print("\n[4] bitstream 新鲜度  (imem/dmem 是综合时装进去的, 改软件必须重新综合)")
    for f in check_bitstream_freshness():
        tag = {"FAIL": "[FAIL]", "WARN": "[warn]", "INFO": "  ok  "}[f["sev"]]
        print(f"  {tag} {f['msg']}")
        if f["sev"] == "FAIL":
            problems.append(f["msg"])
        elif f["sev"] == "WARN":
            warnings.append(f["msg"])

    # ---- 5. 内存布局 ----
    print("\n[5] 内存布局同步")
    for f in check_memory_layout():
        tag = {"FAIL": "[FAIL]", "WARN": "[warn]", "INFO": "  ok  "}[f["sev"]]
        print(f"  {tag} {f['msg']}")
        if f["sev"] == "FAIL":
            problems.append(f["msg"])
        elif f["sev"] == "WARN":
            warnings.append(f["msg"])

    # ---- 6. 文本编码 (R2) ----
    print("\n[6] .bat / .tcl 纯 ASCII 检查")
    print("      .bat: cmd 用 OEM 代码页解析 -> 中文会吃掉每行首字符 (硬故障, 必须修)")
    print("      .tcl: Vivado 对非 ASCII 注释敏感 -> 埋雷, 下次改动该文件时很危险")
    bat_bad, tcl_bad = [], []
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in (".git", ".fpga-cache", "ila_proj.runs",
                                                "ila_proj.cache", "ila_proj.srcs", ".Xil", "msim")]
        for n in files:
            low = n.lower()
            if not low.endswith((".bat", ".tcl")):
                continue
            p = os.path.join(root, n)
            try:
                with open(p, "rb") as fh:
                    raw = fh.read()
            except OSError:
                continue
            # 含 >127 的字节即非 ASCII (UTF-8 中文 / 7 位掩码残留都算)
            if any(b > 127 for b in raw):
                first = next(i for i, b in enumerate(raw) if b > 127)
                line = raw[:first].count(b"\n") + 1
                loc = "%s:%d" % (posix(os.path.relpath(p, REPO)), line)
                (bat_bad if low.endswith(".bat") else tcl_bad).append(loc)
    for b in bat_bad:
        print("  [FAIL] .bat 有非 ASCII: %s   <- 会直接报 'et 不是内部或外部命令'" % b)
        problems.append(".bat 非 ASCII: " + b)
    for b in tcl_bad:
        print("  [warn] .tcl 有非 ASCII: %s   (当前构建可用; 建议下次改动时转成 ASCII)" % b)
        warnings.append(".tcl 非 ASCII: " + b)
    if not bat_bad and not tcl_bad:
        print("  OK    所有 .bat/.tcl 都是纯 ASCII")

    # ---- [7] 综合警告门 (M-06) ---------------------------------------------
    # Vivado 才是本工程的真 lint: 它报的警告里同时藏着 X-08(wstrb 未连) 与
    # R43(MMCM 反馈环断) 这两类真问题。见上面 synth_warn_report 的注释。
    print("\n[7] 综合警告门  (Vivado 的警告 = 本工程的真 lint; 见 PITFALLS R43)")
    rep = synth_warn_report(SYNTH_LOG, update_baseline=getattr(args, "update_baseline", False))
    if rep:
        if rep["new"] and rep["baseline_exists"]:
            problems.append(f"综合出现 {len(rep['new'])} 类**新增**警告, 必须分诊")
        elif rep["new"]:
            warnings.append(f"综合有 {len(rep['new'])} 类警告, 但尚无基线可对比")
    # R43 判据: 评分版与 ILA 版两份日志都不能再出现 feedback net 警告
    for lg in (SYNTH_LOG, ILA_SYNTH_LOG):
        bad = r43_clock_warning(lg)
        if bad:
            for k in sorted(bad):
                print(f"  [FAIL] {lg}: {k}  x{bad[k]['count']}")
            problems.append(f"{lg}: 出现 {R43_WARN_CODE} (MMCM 反馈环断 -> CPU 会被按在复位里)")
    if os.path.exists(abspath(SYNTH_LOG)):
        print(f"  OK     {SYNTH_LOG} / {ILA_SYNTH_LOG} 均无 {R43_WARN_CODE} 反馈环警告 (R43 判据)")

    # ---- [HIL] 硬件在环健康度 ------------------------------------------------
    # 【为什么单独列一节】用户明确要求: 硬件在环是本项目的核心验证手段, 板子
    # 掉线就等于闭环断了 —— 必须**立刻发现并停下报告**, 而不是自己继续推代码
    # (那样就是在无验证的情况下积累未验证的改动, 正是本项目吃过三次亏的模式)。
    # 这里做两件互不依赖的检查:
    #   (a) 最后一次上板数据有多旧 -> 用来判断"当前代码是否已经上板验证过"
    #   (b) bitstream 相对 RTL 是否过期
    print("\n  [HIL] 硬件在环健康度")
    import time as _t
    hil_ok = True
    csv = abspath("results/ila_done.csv")
    if os.path.exists(csv):
        # 【X-02 修复】判据**不能是 CSV 的 mtime**: 该文件被 git 跟踪, 一次
        # `git checkout --` 就把 mtime 刷成"现在"而内容逐字节没变 —— 本节于是
        # 打印出"最后一次上板数据: 5 分钟前"这种**假新鲜度** (docs-sync 实测到现场:
        # mtime=10-11 10:51 而真实采集时间是 10-09 21:29:30)。
        # 现在与 `hw` 同源: 优先读采集清单, 缺失就明说"无见证", 不报假时间。
        man, man_problems = load_capture_manifest(csv)
        if man and not man_problems:
            try:
                epoch = float(man.get("captured_at_epoch") or 0)
            except (TypeError, ValueError):
                epoch = 0.0
            age_min = (_t.time() - epoch) / 60.0 if epoch else 1e9
            stamp = man.get("captured_at_local") or _t.strftime(
                "%m-%d %H:%M", _t.localtime(epoch))
            print(f"  最后一次上板数据: {stamp}  ({age_min:.0f} 分钟前)   [来源: 采集清单]")
        else:
            print("  [warn] results/ila_done.csv 存在, 但**没有采集见证** —— 不报新鲜度。")
            print("         该 CSV 被 git 跟踪, 一次 checkout 就能把 mtime 刷成'现在',")
            print("         伪造出'刚上板验证过'的假象 (X-02)。判据已改为采集清单:")
            for pb in man_problems:
                print(f"         · {pb}")
            print("         真实最近一次的旁证: fpga/cap_done.log 的 CSV_MTIME / CAPTURE_OK")
            warnings.append("上板数据缺少采集见证 (可能是 git 恢复出来的历史快照)")
            age_min = None
        if age_min is not None:                 # 只有拿到见证才谈"多久之前"
            if age_min > 60:
                print("  [warn] 已超过 1 小时没上板验证 —— 期间若改过 RTL, 那些改动**未经上板验证**")
                warnings.append("上板验证数据超过 1 小时")
    else:
        print("  [warn] 没有 results/ila_done.csv —— 从未上板验证过")
        warnings.append("从未上板验证")
        hil_ok = False

    if hil_ok:
        print("  提示: 板子掉线时 `hw` 会因数据过期而拒绝给结论 (exit!=0)。")
        print("        若要在改代码前确认板子在位, 跑: cmd /c fpga\\jtag_probe.bat")

    # ---- 汇总 ----
    print("\n" + "=" * 74)
    if problems:
        print(f"  结论: {len(problems)} 个必须修的问题, {len(warnings)} 个提醒")
        for p in problems:
            print(f"    [FAIL] {p}")
        print("=" * 74)
        return 1
    print(f"  结论: 同步检查通过 ({len(warnings)} 个非阻塞提醒)")
    print("=" * 74)
    return 0


# ==========================================================================
# verify: ModelSim 增量回归
# ==========================================================================
def test_matrix() -> list[dict]:
    """回归矩阵。rtl 用 'rtl' 表示全集, 'core' 表示仅 CPU 核 (不含 mmio/top)。"""
    core = discover_core_sv()
    return [
        # 【W-09】tb_units 的 AMO 译码检查在 `` `ifdef A_EN `` 里, 而原来这里 defines
        # 是空的 => 那 26 条 chk() 是**死代码**(实测检查点 243 -> 268, delta=25,
        # 与源码计数 26-1 完全吻合)。README/CODE-MAP 一直宣称 tb_units 含 AMO 译码检查,
        # 所以这里必须显式打开, 否则宣传的覆盖是假的。
        # desc 里的"194"也删掉了 —— 检查点数以**运行时打印**为准(本工程自己的口径),
        # 写死的数字会随 RTL 改动过期。
        {"name": "tb_units", "rtl": core, "tb": "sim/tb_units.sv",
         "defines": ("A_EN",),
         "plus": [], "max_cycles": None, "timeout": 300,
         "desc": "单元回归 (检查点数以运行时打印为准)"},
        {"name": "tb_core_basic", "top": "tb_core", "rtl": "rtl", "tb": "sim/tb_core.sv",
         "imem": "sim/prog/basic.hex",
         "plus": [], "max_cycles": 200000, "timeout": 600, "desc": "系统级自校验"},
        {"name": "tb_core_trap", "top": "tb_core", "rtl": "rtl", "tb": "sim/tb_core.sv",
         "imem": "sim/prog/trap.hex",
         # +illegal_trace 打开陷阱诊断探针 —— 这个测试讨论的就是陷阱语义,
         # 出问题时需要逐拍证据 (完整输出在 .fpga-cache/logs/tb_core_trap.log)。
         "plus": ["+illegal_trace"], "max_cycles": 200000, "timeout": 600,
         "desc": "异常/中断自校验 (Zicsr + 陷阱 + MRET)"},
        # 【必须用 tb_soc / tb_sw】tb_core 不接外设 (ext_rdata 恒 0、irq_timer 恒 0),
        # 定时器测试需要 mmio_subsys 真的在位, 否则 MMIO 读回 0、中断永远不来。
        {"name": "tb_soc_timer_irq", "top": "tb_soc", "rtl": "rtl", "tb": "sim/tb_soc.sv",
         "imem": "sim/prog/timer_irq.hex",
         # 【+irq_trace 于 2026-10-13 加上, 用于验证候选 E′ 的覆盖率】
         # 背景: E′ 把 `irq_take` 的安全门从 `!pipe_stall` 收窄成 `!hold_mem` ⇒ **有意**允许在
         # `load_use` 气泡停顿的那几拍取中断(那正是"指令字→load_use→pipe_stall→trap_enter→CE"
         # 这条锥的入口)。tb_soc.sv:126 的探针会打印 `>> irq_take=… trap_enter=… stall=…`。
         # 判据: 若接受行出现 `stall=1` ⇒ 该测试**确实**覆盖了"停顿期取中断"这一新相位 ✓;
         #      若从不出现 ⇒ 说明它本来就没覆盖 ⇒ E′ 的风险要按"未被测试覆盖"记录(不能算已验证)。
         # 平时默认**不带**(否则日志噪声大、且会让基线日志 diff 变脏)。
         # 平时默认**不带**(否则日志噪声大); 本次为验证 E′ 覆盖率**临时**打开:
         "plus": ["+irq_trace"], "max_cycles": 200000, "timeout": 600,
         "desc": "P2 定时器中断 (mtime/mtimecmp -> mip.MTIP)"},
        {"name": "tb_core_m_ext", "top": "tb_core", "rtl": "rtl", "tb": "sim/tb_core.sv",
         "imem": "sim/prog/m_ext.hex", "plus": [], "max_cycles": 200000, "timeout": 600,
         "desc": "RV32M 边界"},
        # RV32A 原子指令。**【必须定义 A_EN】** 否则 0x2F 译码为 illegal,
        # 程序第一步就掉陷阱 —— 所以这条测试自带 defines, 不跟随全局开关。
        # 程序用 .word 手工编码指令 (迷你汇编器不认 AMO 助记符, 但支持 .word)。
        #
        # 【2026-10-06: XFAIL -> 真 PASS, 已摘掉 expect_fail_ok】
        # 根因不是"读通路"也不是"写通路"(这两处前几轮查了很久, 都证明是对的),
        # 而是 LSU 在 AMO 的 **launch 拍漏拉了 lsu_stall**: 流水线因此多前进一拍,
        # 紧随 AMO 的那条 lw 的 WB 提前落到 amo_mode_r 仍为 1 的那一拍, 于是
        # lw 读的是 amo_old 而不是 dmem_rdata。
        # 修法: lsu_stall 补上 `(state == S_IDLE && amo_en)`, 与拆分访存完全对称。
        # 详见 load_store_unit.sv 的 lsu_stall 注释与 results/p6-a-extension.md 5.13。
        {"name": "tb_core_amo", "top": "tb_core", "rtl": "rtl", "tb": "sim/tb_core.sv",
         "imem": "sim/prog/amo.hex", "plus": [], "max_cycles": 200000, "timeout": 600,
         "defines": ("A_EN",),
         "desc": "RV32A 原子指令 (AMO 读-改-写 + min/max 符号语义)"},
        # 【单步诊断程序】amo.S 一次测 9 组, 失败时只知道"第 1 组错", 不知道错在
        # 地址/读/写哪一环。这一支只做最小一组, 并把**中间结论直接写进 tohost**:
        #   1=全过 2=sw 后 lw 读不回(基础读写坏) 4=AMO 读到 0 5=AMO 旧值不对 6=写回不对
        # 实测结论 (Round 32): tohost=5 —— sw/lw 基础读写**是好的**, 而 AMO 的
        # **地址比 sw/lw 多 4** (0x90003004 vs 0x90003000), 是 AMO 特有的偏移。
        {"name": "tb_core_amo_diag", "top": "tb_core", "rtl": "rtl", "tb": "sim/tb_core.sv",
         "imem": "sim/prog/amo_diag.hex", "plus": [], "max_cycles": 200000, "timeout": 600,
         "defines": ("A_EN",),
         "desc": "RV32A 单步诊断 (tohost 直接上报失败环节)"},
        {"name": "tb_soc", "top": "tb_soc", "rtl": "rtl", "tb": "sim/tb_soc.sv",
         "imem": "sim/prog/mmio.hex", "plus": [], "max_cycles": 400000, "timeout": 600,
         "desc": "外设 + UART 环回"},
        # ---- load-use 代价标定 (验证泳道 2026-10-11 新增, 第 12 项) ----------
        # 为什么必须有这一项: C-2 把 load-use 从 1 拍改成 2 拍, 并且**新扣住了距离 2
        # 的消费者**。后者是"停顿次数变了"而不是"每次多 1 拍", 任何靠推理算代价的做法
        # 都会算错(实测 +8.9% vs 推理 +6.9%)。这个微基准用三个阶段(距离 3/2/1)把
        # "每类事件各花几拍"直接量出来, 并检查被停顿的消费者读到正确结果。
        # ⚠ 加入这一项后全量回归是 **PASS 12**（文档已同步；历史版本曾为 PASS 11）。
        {"name": "tb_sw_lu_bench", "top": "tb_sw", "rtl": "rtl", "tb": "sim/tb_sw.sv",
         "imem": "sim/prog/lu_bench.hex",
         "plus": ["+lu_bubbles=2", "+expect_hazards=1024", "+expect_d2_cost=1024",
                  "+dump_words=3"],
         "max_cycles": 200000, "timeout": 600,
         "desc": "load-use 代价标定 (距离1/2 分别量 + 数据正确性 checksum)"},
        {"name": "tb_sw_hello", "top": "tb_sw", "rtl": "rtl", "tb": "sim/tb_sw.sv",
         "imem": "sim/prog/hello.hex", "dmem": "sim/prog/hello.dmem.hex",
         "plus": [], "max_cycles": 400000, "timeout": 600, "desc": "裸机 hello"},
        {"name": "tb_sw_coremark", "top": "tb_sw", "rtl": "rtl", "tb": "sim/tb_sw.sv",
         "imem": "sim/prog/cm_rv32im.hex", "dmem": "sim/prog/cm_rv32im.dmem.hex",
         "plus": [], "max_cycles": 5000000, "timeout": 1200, "desc": "CoreMark 仿真"},
        # 应用场景: 神经网络推理的 GEMM 核心。当前是"软件基线", 加了加速单元后
        # 同一程序走加速路径, 加速比才有可比基准。
        {"name": "tb_sw_nn_demo", "top": "tb_sw", "rtl": "rtl", "tb": "sim/tb_sw.sv",
         "imem": "sim/prog/nn_demo.hex", "dmem": "sim/prog/nn_demo.dmem.hex",
         "plus": [], "max_cycles": 3000000, "timeout": 900,
         "desc": "应用场景: 神经网络 GEMM 核心"},
    ]


def _global_defines(args) -> tuple[str, ...]:
    """全局架构宏 (来自命令行开关)。verify 与 fingerprint 必须用同一份口径。

    【默认 = imem/dmem 都用 BRAM】
      imem 的寄存读(->RAMB36)是**不定义任何宏**的默认路径; 定义 `IMEM_COMB_READ`
      才退回组合读(LUT ROM)。所以这里是一个**回退开关**, 不是开关式特性。
    """
    defs = []
    if getattr(args, "imem_comb_read", False):
        defs.append("IMEM_COMB_READ")
    if args.load_reg:
        defs.append("LOAD_RESULT_REG")
    if args.gshare:
        defs.append("BTB_GSHARE")
    if args.ras:
        defs.append("RAS_EN")
    if args.gemm:
        defs.append("GEMM_EN")
    if args.a_ext:
        defs.append("A_EN")
    return tuple(defs)


def test_fingerprint(t: dict, defines: tuple[str, ...] = ()) -> str:
    """一个测试的缓存键 = 参与编译/运行的所有文件内容哈希 + 参数。

    【defines 必须进指纹】曾经漏掉它, 于是 `verify` 跑完组合读配置后再跑
    `verify --imem_reg_read`, 所有测试都被判为"未受影响"而 SKIP —— 明明换了
    一套架构宏、跑的根本不是同一份设计。这类"缓存掩盖了配置差异"的坑与
    PITFALLS R17 (宏没传到 ModelSim) 是同一族: 两边跑的不是同一个东西。
    """
    h = hashlib.sha256()
    files = t["rtl"] if isinstance(t["rtl"], list) else canonical_rtl()
    for f in files + [t["tb"]]:
        p = abspath(f)
        h.update(f.encode())
        h.update(sha256_file(p).encode() if os.path.exists(p) else b"MISSING")
    for key in ("imem", "dmem"):
        if t.get(key):
            p = abspath(t[key])
            h.update(key.encode())
            h.update(sha256_file(p).encode() if os.path.exists(p) else b"MISSING")
    h.update(json.dumps({k: t.get(k) for k in ("plus", "max_cycles")}, sort_keys=True).encode())
    h.update(("gcc=" + gcc_version()).encode())
    h.update(("defines=" + ",".join(defines)).encode())
    return h.hexdigest()[:32]


_GCC_VER: str | None = None


def gcc_version() -> str:
    global _GCC_VER
    if _GCC_VER is None:
        rc, out, _ = run([GCC, "--version"], timeout=60)
        _GCC_VER = out.splitlines()[0] if rc == 0 and out else "unknown"
    return _GCC_VER


def load_state() -> dict:
    try:
        return json.loads(read_text(STATE_FILE))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, ensure_ascii=False)


SIM_CACHE: dict[str, str] = {}


def compile_sim(rtl_files: list[str], tb: str, defines: tuple[str, ...] = ()) -> tuple[int, str, str]:
    """vlib + vlog, 结果按 (文件集, 内容, defines) 缓存到各测试自己的 work 目录。"""
    key = hashlib.sha256(
        ("".join(rtl_files) + tb + "|".join(defines) + "".join(
            sha256_file(abspath(f)) for f in rtl_files + [tb] if os.path.exists(abspath(f)))
         ).encode()
    ).hexdigest()[:16]
    workdir = os.path.join(CACHE_DIR, "sim", key)
    if SIM_CACHE.get(key) == "ok":
        return 0, "(cached)", workdir
    os.makedirs(workdir, exist_ok=True)
    srcs = [abspath(f) for f in rtl_files] + [abspath(tb)]
    vlib = os.path.join(MSIM, "vlib.exe")
    vlog = os.path.join(MSIM, "vlog.exe")
    rc, out, _ = run([vlib, "work"], cwd=workdir)
    if rc != 0:
        return rc, out, workdir
    # 【必须写成 +define+NAME, 不能写 +NAME】
    # 实测: `vlog -sv +FOO ...` 里 `+FOO` 会被当成未知选项 —— 宏**根本不会被定义**,
    # 而编译还"成功"(只报个错然后继续), 于是仿真跑的是**没开宏的版本**。
    # 这个坑静默了很久: IMEM_REG_READ / LOAD_RESULT_REG / BTB_GSHARE 三个宏
    # 在 ModelSim 侧**从来没生效过** —— 综合(Tcl 的 -verilog_define)开了, 仿真没开,
    # 两边跑的不是同一份设计。见 PITFALLS R17。
    # 自证手段: 每个宏生效时该模块会 $display 一行 (如 "[btb] GSHARE ... ACTIVE")。
    flags = [(d if d.startswith("+define+") else "+define+" + d.lstrip("+")) for d in defines]
    rc, out2, _ = run([vlog, "-sv"] + flags + ["-work", "work"] + srcs, cwd=workdir)
    if rc == 0:
        SIM_CACHE[key] = "ok"
    return rc, out + out2, workdir


def cmd_verify(args) -> int:
    """verify 的入口: 先抢**仿真锁**, 再干活。

    为什么需要锁 (2026-10-11 加): `verify-state.json` 与 `.fpga-cache/sim/<hash>` 的
    ModelSim work 库是**全局共享**的。Agent Team / 多终端并行跑 verify 会:
      · 互相覆盖状态文件 (后写者赢) -> 缓存判据漂移;
      · 在同一个 work 库里并发 `vlog` -> 编译失败, 且报错看起来像 RTL 有问题。
    这把锁只串行化"仿真", 与 Vivado 构建锁是两把独立的锁 (两者可并行)。
    """
    try:
        with BuildLock(timeout=1800, path=SIM_LOCK_FILE, label="仿真回归"):
            return _cmd_verify(args)
    except TimeoutError as e:
        print(f"  [BLOCK] {e}")
        return 1


def _cmd_verify(args) -> int:
    only = set(args.only) if args.only else None
    matrix = [t for t in test_matrix() if not only or t["name"] in only]

    print("=" * 74)
    print("  ModelSim 增量回归")
    print(f"  RTL 规范集 {len(canonical_rtl())} 个文件 | 工具 {MSIM}")
    print("=" * 74)

    if not os.path.exists(os.path.join(MSIM, "vsim.exe")):
        print(f"  [BLOCK] 找不到 {MSIM}/vsim.exe")
        return 1

    # ---- 前置: LSU 字节通道等价性 (E-03 的机器化) --------------------------
    # tools/check_equiv.py 穷举 675 万组 (rd/head/off/size/uns/split), 验证交付版
    # load_store_unit 的"4:1 字节 mux"与旧 64 位桶形移位器**逐位等价** —— 这是
    # 交付版 LSU 唯一一处非平凡的等价性论证 (load_store_unit.sv:201 引用它)。
    # 它此前活在仓库之外(.rr2_scratch)、未被跟踪、无人运行; 现在每次回归都跑 (约 5 s),
    # 失败即中止 —— 否则"已穷举验证"这句话永远只是注释里的一句声称。
    eq = abspath("tools/check_equiv.py")
    if os.path.exists(eq):
        rc_eq, out_eq, _ = run([PYTHON, eq, "--quiet"], timeout=600)
        if rc_eq == 0 and "CHECK_EQUIV=PASS" in out_eq:
            print("  [前置] LSU 字节通道等价性: CHECK_EQUIV=PASS (675 万组穷举)")
        else:
            print("  [前置] LSU 字节通道等价性: CHECK_EQUIV=FAIL —— 中止回归")
            print(_indent(_tail_interesting(out_eq)))
            return 1
    else:
        print("  [前置] 缺 tools/check_equiv.py —— 跳过等价性检查 (建议恢复)")

    state = load_state()
    results = []

    # 【M-02】孤儿条目清理: state 里可能有已被删掉/改名的测试残留 (实测:
    # "tb_core_timer_irq": FAIL 一直躺在 verify-state.json 里, 而矩阵里早没这个测试了)。
    # 它们不参与汇总, 却会让读状态文件的人以为"有个测试在失败"。这里报出来并丢掉。
    known = {t["name"] for t in test_matrix()}
    orphans = [k for k in state if k not in known]
    if orphans:
        print(f"  (清理 {len(orphans)} 个已不在矩阵里的缓存条目: {', '.join(sorted(orphans))})")
        for k in orphans:
            state.pop(k, None)
        save_state(state)

    for t in matrix:
        name = t["name"]
        files = t["rtl"] if isinstance(t["rtl"], list) else canonical_rtl()

        # 前置文件检查
        missing = [f for f in files + [t["tb"]] if not os.path.exists(abspath(f))]
        if missing:
            print(f"\n--- {name}: [BLOCK] 缺文件 {missing}")
            results.append((name, "BLOCK", 0, "缺文件 " + ",".join(missing)))
            continue

        fp = test_fingerprint(t, _global_defines(args) + tuple(t.get("defines", ())))
        prev = state.get(name, {})
        # XFAIL 类的测试永远重跑: 它记录的是"还没做"的状态, 一旦 RTL 接线完成就该
        # 从 XFAIL 翻成 XPASS, 缓存命中会把那个翻转发掩盖掉。
        #
        # 【M-02 关键修复】**缓存命中必须同时要求上次是 PASS**。
        # 原实现只比 fingerprint, 于是:
        #   第一次跑 -> FAIL (exit 1);  第二次无改动重跑 -> 命中缓存 -> 报 SKIP;
        #   而汇总里 bad 只数 FAIL/BLOCK/XPASS, **SKIP 不计** -> **exit 0**。
        # 结果就是"失败"被缓存洗成"通过" —— 本工程最核心的教训 (R1/R17/R31) 正是静默失效。
        if (not args.force and prev.get("fingerprint") == fp
                and prev.get("status") == "PASS"
                and not t.get("expect_fail_ok")):
            print(f"\n--- {name}: [SKIP] 未受影响 (上次 {prev.get('status')}, "
                  f"{prev.get('seconds', 0):.1f}s)")
            results.append((name, "SKIP", 0, f"缓存命中 (上次 PASS)"))
            continue
        if (not args.force and prev.get("fingerprint") == fp
                and prev.get("status") not in (None, "PASS")):
            print(f"\n--- {name}: (上次状态是 {prev.get('status')!r} —— "
                  f"缓存**不作为通过依据**, 强制重跑)")

        print(f"\n--- {name}: {t['desc']}")
        t0 = time.time()
        # 【默认配置 = imem/dmem 都用 BRAM】= 上板验证过的配置。
        # 历史上那些宏曾默认为开, 但那是基于无效实验 —— ModelSim 侧宏传递写错
        # (见 PITFALLS R17), 宏从未在仿真生效, 所以"零代价"是假的。
        # 修好后实测: IMEM_REG_READ 已打通并**转为默认**(当时回归 PASS 11, ticks 逐位一致; 当前矩阵 12 项);
        # LOAD_RESULT_REG 会 timeout(已否决); BTB_GSHARE 能跑但慢 6%。
        # 回退 imem 组合读用 --imem_comb_read; 其余用 --gshare / --load_reg / --a-ext。
        defs = list(_global_defines(args))
        # 【每条测试可以自带 defines】例如 tb_core_amo 必须定义 A_EN, 否则 0x2F
        # 译码为 illegal、程序第一步就掉陷阱 —— 它不该依赖全局开关, 也不该因为
        # 别人跑了 --a-ext 与否而改变结果。这里做并集, 去重保持稳定顺序。
        for d in t.get("defines", ()):
            if d not in defs:
                defs.append(d)
        defines = tuple(defs)
        rc, out, workdir = compile_sim(files, t["tb"], defines)
        if rc != 0:
            print(f"    [FAIL] 编译失败 (vlog)")
            print(_indent(_vlog_errors(out)))
            results.append((name, "FAIL", time.time() - t0, "vlog 编译失败"))
            state[name] = {"fingerprint": fp, "status": "FAIL-COMPILE",
                           "seconds": time.time() - t0}
            save_state(state)
            continue

        vsim = os.path.join(MSIM, "vsim.exe")
        top = t.get("top", t["tb"].split("/")[-1][:-3])
        cmd = [vsim, "-c", "-do", "run -all; quit -f", "work." + top]
        # -sv_seed / defines 对 vsim 也要给, 否则 $test$plusargs 之外的宏不一致
        for d in defines:
            cmd.insert(1, "+define+" + d)

        blocked = None
        for key, plus in (("imem", "+imem="), ("dmem", "+dmem=")):
            if not t.get(key):
                continue
            if not os.path.exists(abspath(t[key])):
                if t.get(key + "_optional"):
                    print(f"    (跳过可选输入 {t[key]}: 不存在 —— 该测试台不用 dmem 镜像)")
                    continue
                blocked = f"缺镜像 {t[key]} (先跑 tools/build_sw.py)"
                break
            cmd.append(plus + abspath(t[key]))
        if blocked:
            print(f"    [BLOCK] {blocked}")
            results.append((name, "BLOCK", time.time() - t0, blocked))
            continue

        for p in t.get("plus", []):
            cmd.append(p)
        if t.get("max_cycles"):
            cmd.append(f"+max_cycles={t['max_cycles']}")

        rc, out2, _ = run(cmd, cwd=workdir, timeout=t["timeout"])
        dt = time.time() - t0

        # ---- 判定 ----
        # 依据是 testbench 自己打印的结论行, 不是 vsim 的退出码 ——
        # ModelSim 用 `quit -f` 结束时无论测试成败都返回 0。四个 tb 在成功时
        # 都会打印 "RESULT : PASS", 所以要求它**显式出现** (白名单, 不是黑名单):
        # 之前用 `"PASS" in out.upper()` 会把编译失败/超时误判成通过。
        saw_pass = "RESULT : PASS" in out2
        # 【W-10 配套】判据扩展: CoreMark 的 CRC 不匹配打的是 `ERROR! list crc ...`,
        # 既不是 "** Error" 也不是 "Fatal" —— 原来那种 CRC 失败会**绿灯通过**
        # (PITFALLS 的验收清单明确要求三个 CRC 匹配, 但那条从来没有自动化)。
        crashed = (("** Error" in out2) or ("Fatal" in out2)
                   or ("ERROR!" in out2) or ("Errors detected" in out2)
                   or ("Cannot validate operation" in out2))
        timed_out = "TIMEOUT after" in out2
        got_pass = saw_pass and not crashed and not timed_out

        xfail = t.get("expect_fail_ok", False)
        if xfail:
            status = "XFAIL" if not got_pass else "XPASS"
        else:
            status = "PASS" if got_pass else "FAIL"

        note = ""
        if not got_pass:
            note = _clean(_first_failure(out2)) or ("超时" if timed_out else "无 RESULT : PASS")
        print(f"    [{status}] {dt:.1f}s  rc={rc}  {note}")
        if status in ("FAIL", "XPASS") or args.verbose:
            print(_indent(_tail_interesting(out2)))

        results.append((name, status, dt, note))
        state[name] = {"fingerprint": fp, "status": status, "seconds": dt}
        save_state(state)
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, name + ".log"), "w", encoding="utf-8") as f:
            f.write(out2)
        # 【2026-10-16 加固】把**默认配置**的那一份另存为 canonical 副本。
        # 为什么: `mk_cm_hw.read_ticks_per_iter()` 必须拿"默认配置"的每迭代拍数, 但
        # verify 会按顺序跑 默认 / --a-ext / --imem_comb_read / --ras, **每次都覆盖同名日志**
        # => 最后留在 `tb_sw_coremark.log` 里的可能是 RAS 配置的数
        # (实测: 461,712 vs 默认 463,366, 差 0.36%), 而上板 tick 判据的门限只有 0.5%
        # —— 等于把大半余量**静默**吃掉, 而预期值看起来完全正常。这类"错得很安静"的
        # 数值正是本工程最防的一类缺陷, 所以默认配置的那一份必须单独留档。
        if name == "tb_sw_coremark" and not defines:
            try:
                with open(os.path.join(LOG_DIR, name + ".default.log"), "w",
                          encoding="utf-8") as f:
                    f.write(out2)
            except OSError:
                pass
        # 【X-03】给日志配一份**来源元数据**。
        # 下游 (compute_metrics -> latest_ticks) 会从 tb_sw_coremark.log 里读 CoreMark
        # ticks 去算 CoreMark/MHz, 但日志本身不携带"这是哪版 RTL 跑出来的"。
        # 实测出过事故 (PITFALLS R42.1): CSV 里某行的 ticks/cm 来自 RAS 配置、
        # 而 lut/ff/fmax 来自 JAL 配置, 只能靠人工修。这里把指纹写下来,
        # latest_ticks 会**重算指纹核对**, 对不上就拒绝使用该日志。
        with open(os.path.join(LOG_DIR, name + ".meta.json"), "w", encoding="utf-8") as f:
            json.dump({"name": name, "fingerprint": fp, "status": status,
                       "when": time.time(), "seconds": dt,
                       "defines": list(defines)}, f, indent=1, ensure_ascii=False)

    # ---- 汇总 ----
    print("\n" + "=" * 74)
    print("  回归汇总")
    print("=" * 74)
    counts = {}
    for name, status, dt, note in results:
        counts[status] = counts.get(status, 0) + 1
        print(f"  {status:6s} {name:18s} {dt:7.1f}s  {note}")
    print("\n  " + " | ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    if counts.get("XFAIL"):
        print("  XFAIL = 预期失败 (P1 接线尚未做); XPASS = 该更新 expect_fail_ok 了")
    executed = len(results) - counts.get("SKIP", 0)
    if executed == 0:
        # 【M-02 配套】全量 SKIP 时 exit 0, CI / 自动循环会把"没跑"当"通过"。
        # 实测今天 `verify` 就输出过 "SKIP 11" 且 exit 0。必须显式喊出来。
        print(f"\n  [WARN] 本次**没有执行任何测试** (全部 {counts.get('SKIP', 0)} 项缓存命中)。")
        print("         这只能证明'源文件没变', **不能**证明当前 RTL 通过。")
        print("         要真跑: python tools\\fpga_workflow.py verify --force")
    print("=" * 74)
    # XPASS 也要算异常: 说明预期已经过时
    bad = counts.get("FAIL", 0) + counts.get("BLOCK", 0) + counts.get("XPASS", 0)
    if getattr(args, "require_run", False) and executed == 0:
        return 1
    return 1 if bad else 0


def _vlog_errors(out: str, limit: int = 12) -> str:
    """只挑 vlog 的真错误行, 别把 60 行级联错误全刷出来。"""
    errs, seen = [], set()
    for ln in out.splitlines():
        if "** Error" in ln:
            key = ln.split("):")[-1].strip()
            if key not in seen:
                seen.add(key)
                errs.append(ln.strip())
    if not errs:
        return "\n".join(out.rstrip().splitlines()[-12:])
    head = errs[:limit]
    if len(errs) > limit:
        head.append(f"... 另有 {len(errs) - limit} 条同类错误")
    return "\n".join(head)


def _clean(s: str) -> str:
    """vsim 的控制台输出不保证编码 (中文会变 mojibake), 只保留可打印 ASCII + 通用字符。"""
    out = []
    for ch in s:
        o = ord(ch)
        if o < 128 and (ch.isprintable() or ch == " "):
            out.append(ch)
        elif o > 0x2E80:          # CJK 等: 说明编码本来就是对的, 保留
            out.append(ch)
        # 其余 (mojibake 字节 / 控制字符) 丢弃
    cleaned = "".join(out).strip()
    # 若清洗后几乎不剩东西, 给个占位说明
    return cleaned if len(cleaned) >= 3 else (s[:60].strip() + "  <编码乱码, 见 .fpga-cache/logs/>")


def _first_failure(out: str) -> str:
    """从 tb 输出里抓第一条有信息量的失败原因。"""
    for ln in out.splitlines():
        s = ln.strip().lstrip("#").strip()
        if re.search(r"RESULT : FAIL|FAIL:|timeout|illegal instruction", s, re.I):
            return s[:110]
    for ln in out.splitlines():
        s = ln.strip()
        if "ERROR!" in s or "Errors:" in s:
            return s[:110]
    return ""


def _indent(text: str, n: int = 6) -> str:
    pad = " " * n
    return "\n".join(pad + ln for ln in text.rstrip().splitlines()[-40:])


def _tail_interesting(out: str) -> str:
    """挑出输出里有信息量的部分, 而不是把 4000 行刷屏。"""
    keep = []
    for ln in out.splitlines():
        if re.search(r"PASS|FAIL|ERROR|Error|Total ticks|Iterations|CRC|crc|"
                     r"ALL TESTS|illegal|Mismatch|expected|timeout|TIMEOUT|"
                     r"cycles|fetch|retire|redirects|stalls|load-use|split-mem|"
                     r"muldiv|ctrl-flow|mispredict|cond branch|JAL|JALR|CPI|profile", ln):
            keep.append(ln)
    return "\n".join(keep[-70:]) if keep else "\n".join(out.splitlines()[-20:])


# ==========================================================================
# report: 三项指标 + 绝对吞吐
# ==========================================================================
def cmd_report(args) -> int:
    csv_path = abspath("results/coremark.csv")
    print("=" * 78)
    print("  三项指标 + 绝对吞吐  (赛题记三项: CoreMark/MHz, fmax, CoreMark/LUTs)")
    print("=" * 78)
    try:
        lines = [l for l in read_text(csv_path).splitlines() if l and not l.startswith("#")]
    except OSError:
        print(f"读不到 {csv_path}")
        return 1

    hdr = lines[0].split(",")
    rows = []
    for l in lines[1:]:
        parts = re.findall(r'[^,"]+|"[^"]*"', l)
        rows.append(dict(zip(hdr, parts)))

    def fnum(d, k):
        try:
            return float(str(d.get(k, "")).strip('"'))
        except ValueError:
            return None

    print(f"\n{'tag':22s} {'state':9s} {'CM/MHz':>9s} {'LUT':>6s} {'fmax':>6s} "
          f"{'CM/LUTs':>11s} {'绝对吞吐':>9s}")
    print("-" * 78)
    for d in rows:
        cm, lut, fmax = fnum(d, "coremark_per_mhz"), fnum(d, "lut"), fnum(d, "fmax_mhz")
        cmlut = cm / lut if (cm and lut) else None
        thr = cm * fmax if (cm and fmax) else None
        print(f"{d.get('tag',''):22s} {d.get('status',''):9s} "
              f"{(f'{cm:.4f}' if cm else '-'):>9s} {(f'{lut:.0f}' if lut else '-'):>6s} "
              f"{(f'{fmax:.1f}' if fmax else '-'):>6s} "
              f"{(f'{cmlut:.3e}' if cmlut else '-'):>11s} "
              f"{(f'{thr:.1f}' if thr else '-'):>9s}")

    kept = [d for d in rows if d.get("status") == "keep" and fnum(d, "coremark_per_mhz")]
    # 【X-04】锚点必须显式。原实现把 status 写死成 keep, 于是被否决的实验也会成为锚点;
    # 现在 status 由 --status 传入, 所以这里要把"锚点是谁"讲清楚, 不能默认。
    if not kept:
        print("\n  [WARN] CSV 里没有任何 status=keep 的行 —— 无法给出'最新保留配置 vs 基线'。")
        print("         一次实验要进入这个对比, 必须显式记成 --status keep。")
    elif rows[-1].get("tag") != kept[-1].get("tag"):
        print(f"\n  [WARN] 最后一行 {rows[-1].get('tag')!r} 的 status="
              f"{rows[-1].get('status')!r} 不是 keep —— 下方对比**不拿它当锚点**。")
    if len(kept) >= 2:
        base, cur = kept[0], kept[-1]
        print("\n  最新保留配置 vs 基线 (表内数据 = CSV 记录值):")
        for key, label, unit, fmt in [
            ("coremark_per_mhz", "CoreMark/MHz", "", "{:.4f}"),
            ("fmax_mhz", "fmax", " MHz", "{:.1f}"),
        ]:
            a, b = fnum(base, key), fnum(cur, key)
            if a and b:
                print(f"    {label:14s} {fmt.format(a)}{unit} -> {fmt.format(b)}{unit}"
                      f"   ({100*(b-a)/a:+.1f}%)")
        a = fnum(base, "coremark_per_mhz") / fnum(base, "lut")
        b = fnum(cur, "coremark_per_mhz") / fnum(cur, "lut")
        print(f"    {'CoreMark/LUTs':14s} {a:.3e} -> {b:.3e}   ({100*(b-a)/a:+.1f}%)")
        a = fnum(base, "coremark_per_mhz") * fnum(base, "fmax_mhz")
        b = fnum(cur, "coremark_per_mhz") * fnum(cur, "fmax_mhz")
        print(f"    {'绝对吞吐':14s} {a:.1f} -> {b:.1f}   ({100*(b-a)/a:+.1f}%)")
    print("\n  提示: 上表数字是 CSV 里当初记录的值 (那一次构建的实测)。"
          "\n        要看此刻的四项指标 (从最新仿真日志与归档报告实算), 用 metrics 子命令。")
    print("=" * 78)
    return 0


# ==========================================================================
# synth: 自动化"测量"半环 (Vivado 综合 -> 四项指标)
# ==========================================================================
LOCK_FILE = os.path.join(CACHE_DIR, "vivado.lock")
# 仿真回归的锁: `verify-state.json` 与 ModelSim work 库是全局共享的, 多写入者并行会
# 互相覆盖 / 并发 vlog 同一 work 库。Agent Team 并行时必须有这把锁。
SIM_LOCK_FILE = os.path.join(CACHE_DIR, "sim.lock")
BUILD_TCL = "fpga/build.tcl"


class BuildLock:
    """全局构建锁: 保证同一时刻只有一个重活 (Vivado / ModelSim 回归) 在跑。

    为什么必须串行化 (PITFALLS 四·构建工具链):
      两个 Vivado 实例重叠会让 IP 生成器初始化失败, 而且**谎报**
      `pkgIndex.tcl` 缺失 / `::ipgen_iptclns` 初始化失败 —— 报错指向完全无关的地方,
      极难定位。A(评分版) 与 C(测量) 并行推进时这条尤其致命。
    用 O_EXCL 原子创建 + 陈旧锁回收 (记 pid, 进程没了就抢)。

    【2026-10-11 扩展】同一个理由适用于**仿真回归**: `verify-state.json` 与
    `.fpga-cache/sim/<hash>` 的 ModelSim work 库都是**全局共享**的, 多写入者并行
    会互相覆盖状态文件、或在同一个 work 库里并发 vlog。Agent Team 并行时这条是硬要求,
    所以锁改成可指定路径 + 标签, verify 用一把独立的锁。
    """

    def __init__(self, timeout=1800, path=None, label="Vivado 构建"):
        self.timeout = timeout
        self.path = path or LOCK_FILE
        self.label = label

    def __enter__(self):
        os.makedirs(CACHE_DIR, exist_ok=True)
        t0 = time.time()
        announced = False
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, str(os.getpid()).encode())
                os.close(fd)
                return self
            except FileExistsError:
                # 陈旧锁回收
                try:
                    with open(self.path) as f:
                        pid = int(f.read().strip() or 0)
                except (OSError, ValueError):
                    pid = 0
                if pid and not _pid_alive(pid):
                    print(f"    (回收陈旧{self.label}锁: pid {pid} 已不存在)")
                    try:
                        os.remove(self.path)
                    except OSError:
                        pass
                    continue
                if time.time() - t0 > self.timeout:
                    raise TimeoutError(
                        f"等待{self.label}锁超过 {self.timeout}s (持有者 pid={pid})")
                if not announced:
                    print(f"    (等待{self.label}锁, 持有者 pid={pid} —— 必须串行)")
                    announced = True
                time.sleep(2)

    def __exit__(self, *exc):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


def _pid_alive(pid: int) -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                           capture_output=True, text=True, errors="replace", timeout=30,
                           env=clean_env())
        return str(pid) in (r.stdout or "")
    except Exception:
        return True      # 判断不了就当作活着, 宁可等


def patch_build_prog(prog: str) -> str:
    """把 build.tcl 里的 `set PROG <x>` 改成指定程序, 返回原文本以便还原。"""
    path = abspath(BUILD_TCL)
    orig = read_text(path)
    new, n = re.subn(r"^set PROG \S+$", f"set PROG {prog}", orig, count=1, flags=re.M)
    if n != 1:
        raise RuntimeError(f"{BUILD_TCL}: 找不到 `set PROG <name>` 那一行")
    if new != orig:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    return orig


def restore_build_prog(orig: str) -> None:
    with open(abspath(BUILD_TCL), "w", encoding="utf-8", newline="\n") as f:
        f.write(orig)


def parse_worst_setup_path(t: str) -> dict | None:
    """从 `report_timing_summary` 的 "Max Delay Paths" 段里取**第一条 Setup 路径**。

    【为什么不能用 Clock/Intra Clock 表格】
    那两张表是**按空白列对齐**的, 某个时钟没有 setup 端点时它的 WNS/TNS 列是
    **空的**; 用 `^\\s*<时钟名>\\s+([\\d.]+)` 这种正则会**跨过空列**、抓到后面的
    WPWS(脉宽余量)。这不是假想 —— 2026-10-09 引入 MMCM 之后, 核心从 `sys_clk`
    搬到 `sys_clk_70`, `sys_clk` 行就只剩脉宽端点, 于是 fmax 被算成
    1000/(20-7.000)=76.92 MHz, 而真值是 1000/(14.285714-0.888)=74.64 MHz。
    更糟的是那之后 30+ 次综合的 fmax 全部恒等于 76.92,**对 RTL 时序变化完全不敏感**。
    (本次修复见 CODE-REVIEW-PLAN M-01/ X-01。)

    路径块里的字段是**带标签**的, 与列对齐无关, 因此对 MMCM 之前/之后的报告都成立:
        Slack (MET) :             0.888ns  (required time - arrival time)
        Path Group:               sys_clk_70
        Path Type:                Setup (Max at Slow Process Corner)
        Requirement:              14.286ns  (... rise@14.286ns - ... rise@0.000ns)
    => fmax = 1000 / (Requirement - Slack), 时钟名与周期都从同一块里取, 不硬编码。
    """
    blocks = re.split(r"(?m)^Slack \((?:MET|VIOLATED)\)", t)[1:]
    for b in blocks:
        head = b[:2000]                              # 块头足够覆盖这几个字段
        mt = re.search(r"(?m)^\s*Path Type:\s*(\S+)", head)
        if not mt or not mt.group(1).lower().startswith("setup"):
            continue                                 # 跳过 hold / 脉宽 路径块
        m_req = re.search(r"(?m)^\s*Requirement:\s*(-?[\d.]+)\s*ns", head)
        if not m_req:
            continue
        m_slk = re.search(r"^\s*:\s*(-?[\d.]+)\s*ns", b)
        if not m_slk:
            continue
        m_grp = re.search(r"(?m)^\s*Path Group:\s*(\S+)", head)
        m_dp = re.search(r"(?m)^\s*Data Path Delay:\s*(-?[\d.]+)\s*ns"
                         r"\s*\(logic\s*([\d.]+)\s*ns\s*\(([\d.]+)%\)\s*"
                         r"route\s*([\d.]+)\s*ns\s*\(([\d.]+)%\)\)", head)
        m_ll = re.search(r"(?m)^\s*Logic Levels:\s*(\d+)", head)
        m_src = re.search(r"(?m)^\s*Source:\s*(\S+)", head)
        m_dst = re.search(r"(?m)^\s*Destination:\s*(\S+)", head)
        return {
            "slack_ns": float(m_slk.group(1)),
            "requirement_ns": float(m_req.group(1)),
            "clock": m_grp.group(1) if m_grp else None,
            "data_path_ns": float(m_dp.group(1)) if m_dp else None,
            "logic_ns": float(m_dp.group(2)) if m_dp else None,
            "logic_pct": float(m_dp.group(3)) if m_dp else None,
            "route_ns": float(m_dp.group(4)) if m_dp else None,
            "route_pct": float(m_dp.group(5)) if m_dp else None,
            "logic_levels": int(m_ll.group(1)) if m_ll else None,
            "source": m_src.group(1) if m_src else None,
            "destination": m_dst.group(1) if m_dst else None,
        }
    return None


def parse_reports(tim: str | None = None, util: str | None = None) -> dict:
    """从 Vivado 报告里抽 fmax 与资源。

    fmax 的口径 (2026-10-11 修正): **从最差 setup 路径取**, 不读表格、不硬编码周期。
        fmax = 1000 / (该路径的 Requirement - 该路径的 Slack)
            = 1000 / (运行时钟周期 - 建立余量)
    同时输出该路径所属时钟名与周期, 便于核对"核心实际跑在哪个时钟上"。

    `tim`/`util` 可显式指定报告路径。**缓存命中时必须传"那一次构建的归档副本"** ——
    直接读 fpga/timing.rpt 会张冠李戴 (该文件每次构建都被覆盖, 任何一次别的配置的构建
    都会把它改掉; 实测: 11 组 Pblock 重扫之后, fpga/timing.rpt 变成了最后那一组的数字,
    而 synth-cache.json 里存的却是另一组的指纹)。
    """
    out = {"lut": None, "ff": None, "bram": None, "dsp": None,
           "wns": None, "fmax": None,
           "clk": None, "period_ns": None, "clk_mhz": None, "path": None}

    util = util or abspath("fpga/util.rpt")
    if os.path.exists(util):
        u = read_text(util)

        def grab(label):
            m = re.search(re.escape(label) + r"\s*\|\s*(\d+)", u)
            return int(m.group(1)) if m else None

        out["lut"] = grab("Slice LUTs")
        out["ff"] = grab("Slice Registers")
        out["bram"] = grab("Block RAM Tile")
        out["dsp"] = grab("DSPs")

    if tim is None:
        tim = abspath("fpga/timing.rpt")
    if os.path.exists(tim):
        wp = parse_worst_setup_path(read_text(tim))
        if wp:
            out["wns"] = wp["slack_ns"]
            out["period_ns"] = wp["requirement_ns"]
            out["clk"] = wp["clock"]
            out["clk_mhz"] = (1000.0 / wp["requirement_ns"]) if wp["requirement_ns"] > 0 else None
            denom = wp["requirement_ns"] - wp["slack_ns"]
            # denom <= 0 => 时序已违约到"约束周期都被吃掉", fmax 无意义, 明确置空
            out["fmax"] = (1000.0 / denom) if denom > 0 else None
            out["path"] = wp
        else:
            # 【绝不回退到旧的按行抓数字写法】那正是本 bug 的来源。
            # 取不到就明确置空并告警, 让上层显示 '-' 而不是给一个看起来正常的错数。
            print("  [WARN] timing.rpt 里找不到 Setup 路径块 -> fmax 置空 (不再猜)")
    return out


def log_provenance_ok(meta_path: str) -> bool:
    """核对一份仿真日志的"来源元数据": 它是不是**当前文件内容**跑出来的。

    做法: 用 meta 里记录的 defines 对同名测试**重算** `test_fingerprint`。
    指纹覆盖参与编译/运行的全部文件内容 (见 test_fingerprint) —— 只要 RTL / TB /
    镜像里有一个字节变了, 重算值就不同 => 判定"这份日志不是当前代码跑出来的"。

    为什么要它 (CODE-REVIEW-PLAN X-03): `compute_metrics` 会从
    `.fpga-cache/logs/tb_sw_coremark.log` 读 CoreMark ticks 去算 CoreMark/MHz,
    而日志本身不携带来源。实测出过事故 (PITFALLS R42.1): CSV 某行的 ticks/cm 来自
    RAS 配置、lut/ff/fmax 来自 JAL 配置, 当时只能人工修。
    meta 缺失 => 返回 False (宁可退回 CSV 并告警, 也不要静默用来源不明的 ticks)。
    """
    if not os.path.exists(meta_path):
        return False
    try:
        meta = json.loads(read_text(meta_path))
    except Exception:                            # noqa: BLE001
        return False
    entry = next((t for t in test_matrix() if t["name"] == meta.get("name")), None)
    if entry is None:
        return False
    try:
        return test_fingerprint(entry, tuple(meta.get("defines") or [])) == meta.get("fingerprint")
    except Exception:                            # noqa: BLE001
        return False


def latest_ticks(iters: int | None = None) -> tuple[int, int] | None:
    """取 CoreMark 的 (ticks, iters)。

    【优先用仿真日志的真值, 而不是 CSV 里的历史值】
    CSV 里的 ticks 是"当初那次"的数字; 修改 RTL 后它可能已经变了 (或不变量本身
    就该重测)。实测踩过: CSV 写 423,486, 而干净的仿真日志是 423,527 —— 两者
    CoreMark/MHz 差 0.01%, 看起来无所谓, 但"用旧值算新架的指标"是错的口径。

    所以: 有 tb_sw_coremark 日志就用日志里的 CoreMark ticks (它还同时给出 CRC,
    可顺带校验正确性); 没有才回退到 CSV。
    【X-03 加固】用日志之前先核对它的来源指纹 (见 log_provenance_ok)。
    """
    log = os.path.join(LOG_DIR, "tb_sw_coremark.log")
    meta_path = os.path.join(LOG_DIR, "tb_sw_coremark.meta.json")
    p = parse_profile(read_text(log)) if os.path.exists(log) else {}
    cm_ticks, it = p.get("cm_ticks"), p.get("iterations")
    if cm_ticks and it and (iters is None or it == iters):
        if log_provenance_ok(meta_path):
            return cm_ticks, it
        print(f"  [WARN] {posix(os.path.relpath(log, REPO))} 的来源指纹与当前代码不一致 "
              f"(或缺少 .meta.json) -> **不采用它的 ticks** (R42.1), 改用 coremark.csv")
        print("         真值请重跑: python tools\\fpga_workflow.py verify --only tb_sw_coremark")

    try:
        lines = [l for l in read_text(abspath("results/coremark.csv")).splitlines()
                 if l and not l.startswith("#")]
    except OSError:
        return None
    hdr = lines[0].split(",")
    best = None
    for l in lines[1:]:
        parts = re.findall(r'[^,"]+|"[^"]*"', l)
        d = dict(zip(hdr, parts))
        tag = d.get("tag", "")
        if "HARDWARE" in tag:
            continue
        try:
            tk, it2 = int(d["ticks"]), int(d["iters"])
        except (KeyError, ValueError):
            continue
        if iters is not None and it2 != iters:
            continue
        best = (tk, it2)
    return best


def compute_metrics(res: dict, iters: int | None = None) -> dict:
    """四项指标: CoreMark/MHz, fmax, CoreMark/LUTs, 绝对吞吐。"""
    m = dict(res)
    cm = cmlut = thr = None
    tick = latest_ticks(iters)
    if tick:
        tk, it = tick
        cm = it * 1e6 / tk
        if res.get("lut"):
            cmlut = cm / res["lut"]
        if res.get("fmax"):
            thr = cm * res["fmax"]
    m.update({"cm_mhz": cm, "cm_lut": cmlut, "throughput": thr,
              "ticks": tick[0] if tick else None, "iters": tick[1] if tick else None})
    return m


def wns_note(m: dict) -> str:
    """把 fmax 的**推导过程**写在数字旁边。

    本工程反复吃亏在"数字来源没写在旁边" —— 只看 76.92 谁也发现不了它取自
    另一条时钟的脉宽余量。所以这里强制显示: 时钟名 / 周期 / WNS / 公式。
    """
    if m.get("wns") is None:
        return "无 timing 数据 (fmax 置空, 不再猜)"
    clk = m.get("clk") or "?"
    per = m.get("period_ns")
    if per:
        op = f", 运行 {m['clk_mhz']:.2f} MHz" if m.get("clk_mhz") else ""
        # 符号要念得出来: WNS=+0.888 应显示 "14.286 - 0.888", WNS=-0.5 显示 "14.286 + 0.500"
        sign = "-" if m["wns"] >= 0 else "+"
        return (f"{clk} @{per:.3f}ns{op}; WNS {m['wns']:+.3f}ns "
                f"-> fmax=1000/({per:.3f} {sign} {abs(m['wns']):.3f})")
    return f"WNS {m['wns']:+.3f} ns"


def print_metrics(m: dict) -> None:
    print("\n  " + "-" * 66)
    print("  四项指标  (赛题记三项, 但必须同时看绝对吞吐)")
    print("  " + "-" * 66)
    rows = [
        ("CoreMark/MHz", m.get("cm_mhz"), "{:.4f}", m.get("ticks"),
         f"{m['iters']} iters, {m['ticks']:,} ticks" if m.get("ticks") else "无 CoreMark 数据"),
        ("fmax", m.get("fmax"), "{:.1f} MHz", m.get("wns"), wns_note(m)),
        ("CoreMark/LUTs", m.get("cm_lut"), "{:.3e}", m.get("lut"),
         f"LUT {m.get('lut')} FF {m.get('ff')} BRAM {m.get('bram')} DSP {m.get('dsp')}"),
        ("绝对吞吐", m.get("throughput"), "{:.1f}", None, "CoreMark/MHz x fmax"),
    ]
    for label, val, fmt, _x, note in rows:
        shown = fmt.format(val) if val is not None else "n/a"
        print(f"    {label:16s} {shown:>14s}   {note}")
    print("  " + "-" * 66)


# 实验日志里 status 列的合法取值。
#   keep     = 决策为"保留"
#   rejected = 决策为"否决"(必须能由工具写入 —— 原实现硬编码 keep, 见 CODE-REVIEW-PLAN X-04)
#   measure  = 只测了、还没做决策 (默认值就是它, 不再冒充 keep)
CSV_STATUSES = ("keep", "rejected", "measure", "timing_fail")


def append_csv(tag: str, m: dict, status: str | None = None) -> None:
    """把一行追加进 results/coremark.csv, 并保持列顺序与表头一致。

    【X-04 修复】原实现把 `"status": "keep"` 写死, 于是**工具永远无法记录一个
    被否决的实验** —— 22 行 rejected 全是人工编辑出来的。实验日志的核心不变量
    (keep vs rejected) 因此没有机器约束, 而 cmd_report 又拿它当"最新保留配置"的
    锚点 => 可能锚到一个已被否决的构建上。

    现在: status 由调用方给 (CLI 的 --status), 默认 "measure" (诚实: 只测了)。
    【顺带修】CSV 单元格里的内层 `"` 没有转义成 `""`, 会让 read_csv_rows 的
    `re.findall(r'[^,"]+|"[^"]*"')` 解析错位 —— 本工程 notes 里大量出现引号。
    """
    st = status or m.get("status") or "measure"
    if st not in CSV_STATUSES:
        print(f"  [WARN] 未知 status={st!r}, 按 measure 记录 (合法值: {', '.join(CSV_STATUSES)})")
        st = "measure"
    path = abspath("results/coremark.csv")
    hdr_line = next(l for l in read_text(path).splitlines() if l.startswith("date,"))
    hdr = hdr_line.split(",")
    row = {
        "date": time.strftime("%Y-%m-%d"), "tag": tag,
        "iters": m.get("iters") or "", "ticks": m.get("ticks") or "",
        "coremark_per_mhz": f"{m['cm_mhz']:.4f}" if m.get("cm_mhz") else "",
        "lut": m.get("lut") or "", "ff": m.get("ff") or "",
        "bram": m.get("bram") or "", "dsp": m.get("dsp") or "",
        "fmax_mhz": f"{m['fmax']:.1f}" if m.get("fmax") else "",
        "status": st,
        # 调用方可以给更准确的 notes (例如 hw 是"上板实测"); 没给才用 synth 默认文案
        "notes": m.get("note") or f"fpga_workflow.py synth (PROG={m.get('prog','?')})",
    }
    cells = []
    for k in hdr:
        v = str(row.get(k, ""))
        if "," in v or '"' in v:
            cells.append('"' + v.replace('"', '""') + '"')
        else:
            cells.append(v)
    with open(path, "a", encoding="utf-8", newline="") as f:
        f.write(",".join(cells) + "\n")
    print(f"\n  -> 追加到 results/coremark.csv: {tag}  (status={st})")
    if st == "measure":
        print("     [i] status=measure 表示'只测了还没决策' —— 它不会成为 report 的基线/最新保留配置。")
        print("        要记成决策, 用 --status keep 或 --status rejected。")


def cmd_synth(args) -> int:
    prog = args.prog
    print("=" * 74)
    print("  Vivado 综合 -> 四项指标   (评分版, 非工程模式)")
    print(f"  程序镜像 PROG={prog}   part=xc7a100tfgg484-2")
    print("=" * 74)

    imem = abspath(f"sim/prog/{prog}.hex")
    dmem = abspath(f"sim/prog/{prog}.dmem.hex")
    for label, p in (("imem", imem), ("dmem", dmem)):
        if not os.path.exists(p):
            print(f"  [BLOCK] 缺 {label} 镜像 {p}")
            print("          先构建: python tools/build_sw.py sw/coremark/*.c -I sw/coremark \\")
            print("                    -D ITERATIONS=1200 --march=rv32im -o sim/prog/cm_hw.hex")
            return 1

    # 缓存键 = 所有综合输入的内容哈希 + PROG
    h = hashlib.sha256()
    for f in canonical_rtl() + ["fpga/top_a100t.sv", "fpga/mmio_subsys.sv",
                                "fpga/daVinciPro.xdc", "fpga/build.tcl"]:
        p = abspath(f)
        h.update(f.encode())
        h.update(sha256_file(p).encode() if os.path.exists(p) else b"MISSING")
    for p in (imem, dmem):
        h.update(sha256_file(p).encode())
    h.update(prog.encode())
    # 指纹必须覆盖**所有影响网表的开关**, 否则改开关会命中旧缓存、拿到旧数字。
    # 注意与 build.tcl 里的默认保持一致: **imem 默认 = 寄存读(BRAM)**, 其余宏关闭。
    h.update(b"combread" if args.imem_comb_read else b"bramread")
    h.update(b"loadreg" if args.load_reg else b"loadcomb")
    h.update(b"gshare" if args.gshare else b"smith")
    h.update(b"ras" if args.ras else b"noras")
    h.update(b"aen" if args.a_ext else b"aoff")
    h.update(b"gemm" if args.gemm else b"gemmoff")
    # 【物理约束也必须进指纹】否则改 Pblock / place 策略会命中旧缓存、
    # 直接拿到上一次的四个数字 —— 和 R31 是同一族静默失效。
    h.update(("place=" + (args.place_directive or "default")).encode())
    h.update(("pblock=" + (args.pblock_range or "off")).encode())
    h.update(("pblockcells=" + (args.pblock_cells or "u_core")).encode())
    h.update(b"containrt" if args.contain_routing else b"nortcontain")
    h.update(("route=" + (args.route_directive or "default")).encode())
    # 【新增两个 directive 也必须进指纹】2026-10-11: RVCoreP-32IM 的工程文件用
    # "Flow_PerfOptimized_high"(synth) + "Performance_ExplorePostRoutePhysOpt"(impl),
    # 而我们从来没扫过 synth/phys_opt 这两个旋钮。不进指纹 = 改策略却命中旧缓存。
    h.update(("synth=" + (getattr(args, "synth_directive", None) or "default")).encode())
    h.update(("rs=" + (getattr(args, "resource_sharing", None) or "default")).encode())
    h.update(("recipe=" + (getattr(args, "synth_recipe", None) or "default")).encode())
    h.update(("no_lc=" + ("1" if getattr(args, "no_lc", False) else "0")).encode())
    h.update(("folim=" + str(getattr(args, "fanout_limit", None))).encode())
    h.update(("optdir=" + (getattr(args, "opt_directive", None) or "default")).encode())
    h.update(("physopt=" + (getattr(args, "phys_opt_directive", None) or "default")).encode())
    h.update(b"retiming" if args.retiming else b"noretiming")
    fp = h.hexdigest()[:32]

    cache = os.path.join(CACHE_DIR, "synth-cache.json")
    try:
        cstate = json.loads(read_text(cache))
    except Exception:
        cstate = {}
    # 【必须从"那一次构建的归档"读数字, 不能读磁盘上的 fpga/timing.rpt】
    # fpga/timing.rpt / util.rpt **每次构建都被覆盖**: 跑过别的配置之后 (实测: 11 组
    # Pblock×place 重扫), 它描述的是**最后一次**构建, 而缓存里存的指纹属于**另一次**
    # 同配置构建。两者一旦不同, 缓存命中就会把别的配置的数字当成这次的报出来 ——
    # 与 M-01 同一族 (数字张冠李戴), 而且看起来完全正常。
    # 实测现场: 重扫 11 组之后 fpga/timing.rpt 变成"无 Pblock"那组的 72.45 MHz,
    # 而 synth-cache.json 里是另一组的指纹。
    hit = (not args.force and cstate.get("fingerprint") == fp
           and os.path.exists(abspath("fpga/cpu_a100t.bit")))
    arc = cstate.get("archive")
    arc_tim = os.path.join(REPO, arc, "timing.rpt") if arc else None
    arc_utl = os.path.join(REPO, arc, "util.rpt") if arc else None
    if hit and not (arc_tim and os.path.exists(arc_tim)):
        print("  [warn] 缓存命中, 但找不到该次构建的归档报告 -> **强制重建**"
              " (不能拿磁盘上别的构建的报告冒充这一份)")
        hit = False
    if hit:
        print(f"  [SKIP] RTL 与镜像都未变, 复用上次构建结果 "
              f"({time.strftime('%m-%d %H:%M', time.localtime(cstate.get('when', 0)))})")
        print(f"         (数字取自该次构建的归档: {arc})")
        res = parse_reports(arc_tim, arc_utl)
        res["prog"] = prog
        print_metrics(compute_metrics(res))
        if args.record:
            append_csv(args.tag or f"synth-{prog}", compute_metrics(res), args.status)
        return 0

    if not os.path.exists(VIVADO_BAT):
        print(f"  [BLOCK] 找不到 {VIVADO_BAT}")
        return 1

    with BuildLock(timeout=args.lock_timeout):
        try:
            # 【不再改写 build.tcl】PROG 走环境变量(见 fpga/build.tcl 顶部的注释)。
            # 旧实现用正则改 `set PROG` 行、结束再把**整个文件**写回 —— 构建期间
            # 别人的编辑会被静默覆盖。2026-10-11 实测: build-repo 给 read_verilog
            # 加了 bht.sv, 被写回抹掉 ⇒ 下一次综合 "module 'bht' not found"。
            # 现在 build.tcl 在构建期间是**只读**的, 这一类事故结构上不可能再发生。
            # 自证: build.tcl 打印 BUILD_PROG=$PROG, 下面把它列入 expect ⇒
            # 环境变量没传进去(或 build.tcl 又不认它)会**报错而不是静默跑错程序**。
            expect_prog = f"BUILD_PROG={prog}"
            print(f"  [1/2] PROG={prog} 经环境变量传入 (build.tcl 不再被改写)")
            log = os.path.join(CACHE_DIR, "synth.log")
            os.makedirs(CACHE_DIR, exist_ok=True)
            t0 = time.time()
            # 走 .bat: 它负责 call settings64.bat (Vivado 未加入 PATH)。
            # 架构宏通过环境变量传给 build.tcl (Tcl 读 $::env)。
            # 默认**一个都不设** = 三宏全关 = 上板验证过的配置。
            benv = clean_env()
            benv["PROG"] = prog          # 见上: PROG 走 env, 不改写 build.tcl
            if args.imem_comb_read:
                # [回退] imem 组合读 -> LUT ROM。默认走寄存读 -> RAMB36 (无需设 env)。
                benv["IMEM_COMB_READ"] = "1"
            if args.place_directive:
                benv["PLACE_DIRECTIVE"] = args.place_directive
            if args.pblock_range:
                benv["PBLOCK_RANGE"] = args.pblock_range
                if args.pblock_cells:
                    benv["PBLOCK_CELLS"] = args.pblock_cells
            if args.contain_routing:
                benv["CONTAIN_ROUTING"] = "1"
            if args.route_directive:
                benv["ROUTE_DIRECTIVE"] = args.route_directive
            if getattr(args, "synth_directive", None):
                benv["SYNTH_DIRECTIVE"] = args.synth_directive
            if getattr(args, "resource_sharing", None):
                benv["RESOURCE_SHARING"] = args.resource_sharing
            # 【RVCoreP 的 Flow_PerfOptimized_high 整套配方】见 build.tcl 注释:
            # 此前只搬了 -directive; 这一整套(resource_sharing/fanout_limit/fsm/
            # no_lc/keep_equivalent_registers/shreg_min_size)从未测过。
            if getattr(args, "synth_recipe", None):
                benv["SYNTH_RECIPE"] = args.synth_recipe
            # 【单选项实验】见 build.tcl 的注释: 对症"译码 LUT 与前递预选 LUT 被合并"
            # 造成的 5 级长锥 + fo=121/122 高扇出布线(占该路径 2.147ns/30%)。
            if getattr(args, "no_lc", False):
                benv["SYNTH_NO_LC"] = "1"
            if getattr(args, "fanout_limit", None) is not None:
                benv["SYNTH_FANOUT_LIMIT"] = str(args.fanout_limit)
            # 【opt_design directive】150 档留余量: ExploreWithRemap / NoLUTCombining 等
            if getattr(args, "opt_directive", None):
                benv["OPT_DIRECTIVE"] = args.opt_directive
            if getattr(args, "phys_opt_directive", None):
                benv["PHYS_OPT_DIRECTIVE"] = args.phys_opt_directive
            if args.retiming:
                benv["RETIMING"] = "1"
            if args.load_reg:
                benv["LOAD_REG"] = "1"
            if args.gshare:
                benv["BTB_GSHARE"] = "1"
            if args.ras:
                benv["RAS_EN"] = "1"
            if args.a_ext:
                # RV32A 原子指令 + 多周期读-改-写 (2026-10-06 起功能已打通:
                # tb_core_amo 由 XFAIL 翻 PASS)。默认关闭 —— 三条评分指标零收益。
                benv["A_EN"] = "1"
            if args.gemm:
                # GEMM 加速单元。默认关闭 (缓冲落成触发器阵列, FF +916%)。
                benv["GEMM_EN"] = "1"
            rc, out, dt = run(["cmd", "/c", abspath("fpga/build.bat")],
                              cwd=abspath("fpga"), timeout=args.timeout, env=benv)
            with open(log, "w", encoding="utf-8") as f:
                f.write(out)
            # ---- 【自证】请求了宏, 就必须在日志里看到它生效的标记 --------------
            # 为什么必须有: "改了没效果"有三种完全不同的成因, 而症状一样 ——
            #   ① 算法真的无用; ② 宏没传到编译器; ③ 宏传到了但 build.tcl 不认。
            # 本项目已踩过两次静默失效:
            #   R17  `vlog +FOO` 而不是 `+define+FOO` -> 宏从未在仿真生效;
            #   本次 `synth --a-ext/--gemm` 参数解析器接受, 但**没写进 benv**,
            #        于是静默按默认配置构建 (三项指标与默认一字不差, 极易误读成
            #        "A 扩展零代价" —— 我第一眼就差点这么认为)。
            # 所以这里把"宏是否生效"变成**构建后的硬判据**, 不靠人看。
            expect = []
            # PROG 必须真的传进去了(它现在走 env, 不再靠改写 build.tcl)。
            expect.append(expect_prog)
            # imem: 默认必须是 BRAM 版(不定义宏的路径); 只有显式回退才是组合读。
            expect.append("BUILD_IMEM_COMB_READ=1" if args.imem_comb_read
                          else "BUILD_IMEM_BRAM=1")
            # Pblock: build.tcl 默认已开启(该 part 专用), 只有传 off 才关。
            expect.append("BUILD_PBLOCK_OFF=1" if args.pblock_range == "off"
                          else "BUILD_PBLOCK=")
            if args.route_directive: expect.append("BUILD_ROUTE_DIRECTIVE=")
            # synth / phys_opt directive: 请求了就必须在日志里看到(自证开关生效)。
            # [2026-10-11] build.tcl 的 SYNTH_DIRECTIVE **默认值现在是 AlternateRoutability**
            # (实测在 post-C11 网表上 92.6 -> 97.2 MHz), 所以不再有 "_OFF=1" 这种形态。
            expect.append("BUILD_SYNTH_DIRECTIVE=" +
                          (getattr(args, "synth_directive", None) or "AlternateRoutability"))
            if getattr(args, "phys_opt_directive", None):
                expect.append("BUILD_PHYS_OPT_DIRECTIVE=" + args.phys_opt_directive)
            else:
                expect.append("BUILD_PHYS_OPT_DIRECTIVE=AggressiveExplore")
            if getattr(args, "resource_sharing", None):
                expect.append("BUILD_RESOURCE_SHARING=" + args.resource_sharing)
            else:
                expect.append("BUILD_RESOURCE_SHARING_OFF=1")
            if getattr(args, "synth_recipe", None):
                expect.append("BUILD_SYNTH_RECIPE=" + args.synth_recipe)
            else:
                expect.append("BUILD_SYNTH_RECIPE_OFF=1")
            expect.append("BUILD_SYNTH_NO_LC=1" if getattr(args, "no_lc", False)
                          else "BUILD_SYNTH_NO_LC_OFF=1")
            if getattr(args, "fanout_limit", None) is not None:
                expect.append("BUILD_SYNTH_FANOUT_LIMIT=" + str(args.fanout_limit))
            else:
                expect.append("BUILD_SYNTH_FANOUT_LIMIT_OFF=1")
            if getattr(args, "opt_directive", None):
                expect.append("BUILD_OPT_DIRECTIVE=" + args.opt_directive)
            else:
                expect.append("BUILD_OPT_DIRECTIVE_OFF=1")
            if args.load_reg:      expect.append("BUILD_LOAD_RESULT_REG=1")
            if args.gshare:        expect.append("BUILD_BTB_GSHARE=1")
            if args.ras:           expect.append("BUILD_RAS_EN=1")
            if args.a_ext:         expect.append("BUILD_A_EN=1")
            if args.gemm:          expect.append("BUILD_GEMM_EN=1")
            missing = [e for e in expect if e not in out]
            ok = "BUILD_OK" in out
            print(f"  [2/2] vivado rc={rc} {'BUILD_OK' if ok else 'FAILED'}  {dt:.0f}s")
            # 【X-06 配套】build.tcl 现在自己断言时序收敛: 违约时打 BUILD_TIMING=FAIL
            # 并**拒绝写 bitstream**。这里必须显式当致命错误 —— 否则一个时序违约的构建
            # 会被 parse_reports 算出一个"看起来很正常"的 fmax (例如 WNS=-1 -> 47.6 MHz),
            # 再被 append_csv 盖上 keep, 最终变成"一次正常的测量回归"记进 results/。
            #
            # [!] 判据必须**整行精确匹配**: Vivado 会把执行过的 Tcl 源行回显进日志,
            #   而 build.tcl 的注释里就写着 `#   puts "BUILD_TIMING=FAIL"` ——
            #   用 `"BUILD_TIMING=FAIL" in out` 会在**时序通过**时也误报致命错误
            #   (实测踩到, 见 2026-10-11 那次 synth: MET=1 却报 [FAIL])。
            tl = [l.strip() for l in out.splitlines()]
            for l in tl:
                if l.startswith("BUILD_TIMING"):
                    print("        " + l)
            if any(l == "BUILD_TIMING=FAIL" or l.startswith("BUILD_TIMING=FAIL:")
                   for l in tl):
                print("  [FAIL] 时序未收敛 (BUILD_TIMING=FAIL) —— build.tcl 已拒绝写 bitstream。")
                print("         这不是脚本坏了, 是**门生效**。修 RTL/约束后重跑; "
                      "不要用旁路开关绕过。")
                # 【2026-10-13 补】门的失败**也是数据**: 报告里已经写出了
                # **达成 fmax / WNS / 资源**, 而那正是"这一档还差多少"的唯一证据。
                # 此前失败构建**完全不进 CSV** ⇒ 冲 150 的全部失败点(含各自达成 fmax)
                # 只留在会被下一次构建覆盖的 fpga/timing.rpt 里, **负结果台账是残缺的**。
                # 现在把失败点记成 status=timing_fail —— 明确标注"这不是可交付结论,
                # 只是达成值", 于是它既不可被当成 keep 锚点, 又不会丢证据。
                try:
                    res_f = parse_reports()
                    res_f["prog"] = prog
                    print("       [计时] 该失败点的**达成值** (不是可交付结论):")
                    print_metrics(compute_metrics(res_f))
                    append_csv(args.tag or f"synth-{prog}", compute_metrics(res_f),
                               "timing_fail")
                except Exception as e:                       # noqa: BLE001
                    print(f"       [warn] 失败点归档时出错(不影响『门生效』的判定): {e}")
                return 1
            if ok and missing:
                print(f"  [FAIL] 请求的架构宏**没有生效**: {missing}")
                print("         -> build.tcl 不认这些 env, 或 synth 没把它们写进环境。")
                print("         这些数字对应的是**默认配置**, 不能当作该宏的结果。")
                return 1
            if not ok or rc != 0:
                # [2026-10-11 加固] 原来只判 `not ok`(即日志里没有 BUILD_OK)。
                # 但 "日志里有 BUILD_OK 且 rc != 0" 是可能的(Vivado 在 BUILD_OK 之后
                # 才报错/超时), 那种情况会**继续往下走** parse_reports() —— 把上一次
                # 构建留在磁盘上的 timing.rpt 当成这一次的结果, 再写进 CSV。
                # 这正是本工程反复抓的"静默沿用旧值"族(实测: 一次 4 秒的失败构建
                # 被上层脚本报成了上一次的 76.2 MHz)。失败就是失败, 不留后门。
                why = [l for l in out.splitlines()
                       if "BUILD_ERROR" in l or "ERROR:" in l or "BUILD_HINT" in l]
                for l in why[:12]:
                    print("        " + l.strip())
                if ok and rc != 0:
                    print(f"  [FAIL] 日志里有 BUILD_OK 但 vivado rc={rc} —— 不给结论。")
                print(f"       完整日志: {log}")
                return 1
        # 【已删除 finally: restore_build_prog】build.tcl 不再被改写(PROG 走 env),
        # 所以没有"写回原文件"这一步 —— 也就没有覆盖别人编辑的可能。
        # 保留 try 结构但不再需要任何清理动作(异常照常向上抛)。
        except Exception:
            raise

    res = parse_reports()
    res["prog"] = prog
    if not res.get("lut"):
        print("  [warn] util.rpt 里没解析到 LUT, 资源数字可能不准")
    print_metrics(compute_metrics(res))

    # 记录本次构建所用的镜像内容哈希。
    # 为什么: check 的 bitstream 新鲜度原来只能比 mtime —— 而"重建了逐字节相同的
    # 镜像"会让 mtime 变新从而误报 stale。有了哈希就能区分
    # "镜像真的变了" 与 "文件被重写但内容一样"。
    try:
        sstate = os.path.join(CACHE_DIR, "synth-cache.json")
        try:
            sc = json.loads(read_text(sstate))
        except Exception:
            sc = {}
        sc.setdefault("image_hash", {})[prog] = {
            "imem": sha256_file(imem), "dmem": sha256_file(dmem),
            # 记下**这次综合实际用的输入清单**。check 判"bitstream 是否过期"时
            # 用它而不是 canonical_rtl() 全清单 —— 否则被宏门控、根本没进 bitstream
            # 的模块会**误报** stale。
            "inputs": synth_inputs_for(args),
            # 【X-07】同时记**逐文件内容哈希**: check 之后按内容比对, 不再按 mtime。
            # mtime 会被 git checkout / 归档拷贝伪造, 从而把"内容已变"误判成"新鲜"。
            "input_sha256": {
                f: (sha256_file(abspath(f)) if os.path.exists(abspath(f)) else None)
                for f in synth_inputs_for(args)
            },
        }
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(sstate, "w", encoding="utf-8") as f:
            json.dump(sc, f, indent=1, ensure_ascii=False)
    except OSError as e:
        print(f"  [warn] 记录镜像哈希失败: {e}")

    # 归档本次报告: timing.rpt/util.rpt 每次构建都被覆盖, 不留档就没法回查
    # (本项目就出现过"旧报告被覆盖后无法复核历史数字"的情况)
    # 【2026-10-11 加固】归档目录**记进 synth-cache.json**, 供缓存命中时回读 ——
    # 见下面 cache-hit 分支的注释: 从磁盘读 fpga/timing.rpt 会张冠李戴。
    arch_rel = None
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        arch = os.path.join(CACHE_DIR, "reports", f"{stamp}-{prog}")
        os.makedirs(arch, exist_ok=True)
        for n in ("timing.rpt", "timing_paths.rpt", "util.rpt"):
            s = abspath("fpga/" + n)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(arch, n))
        with open(os.path.join(arch, "metrics.json"), "w", encoding="utf-8") as f:
            json.dump(compute_metrics(res), f, indent=1, ensure_ascii=False)
        arch_rel = posix(os.path.relpath(arch, REPO))
        print(f"  报告已归档: {arch_rel}")
    except OSError as e:
        print(f"  [warn] 报告归档失败: {e}")

    with open(cache, "w", encoding="utf-8") as f:
        json.dump({"fingerprint": fp, "when": time.time(), "prog": prog,
                   "archive": arch_rel}, f, indent=1)

    if args.record:
        # 【必须传 args.status】这里原来漏了第三个参数 => 正常构建路径下
        # `--status keep` 被静默丢弃, 一律记成默认的 "measure" (实测: 想记 keep
        # 的一行写成了 measure)。X-04 修的是"能不能记 rejected", 这条是"记了但记错"。
        append_csv(args.tag or f"synth-{prog}", compute_metrics(res), args.status)
    return 0


# ==========================================================================
# metrics: 四项指标 + keep/reject 判定 (把赛题纪律编码进来)
# ==========================================================================
def read_csv_rows() -> list[dict]:
    path = abspath("results/coremark.csv")
    lines = [l for l in read_text(path).splitlines() if l and not l.startswith("#")]
    hdr = [h.strip() for h in lines[0].split(",")]
    rows = []
    for l in lines[1:]:
        parts = [p.strip() for p in re.findall(r'[^,"]+|"[^"]*"', l)]
        d = dict(zip(hdr, parts))
        d["_raw"] = l
        rows.append(d)
    return rows


def _f(row: dict, key: str):
    try:
        return float(str(row.get(key, "")).strip('"'))
    except ValueError:
        return None


def metric_set(row: dict) -> dict:
    """由一行 CSV 推出四项指标 (派生量现算, 避免依赖是否记录过)。"""
    cm = _f(row, "coremark_per_mhz")
    lut = _f(row, "lut")
    fmax = _f(row, "fmax_mhz")
    return {
        "coremark_per_mhz": cm,
        "fmax_mhz": fmax,
        "coremark_per_lut": (cm / lut) if (cm and lut) else None,
        "absolute": (cm * fmax) if (cm and fmax) else None,
    }


def find_row(tag: str) -> dict | None:
    for r in read_csv_rows():
        if r.get("tag") == tag:
            return r
    return None


def fmt_metric(k: str, v) -> str:
    if v is None:
        return "n/a"
    if k == "fmax_mhz":
        return f"{v:.1f} MHz"
    if k == "coremark_per_lut":
        return f"{v:.3e}"
    if k == "absolute":
        return f"{v:.1f}"
    return f"{v:.4f}"


def verdict(now: dict, prev: dict) -> tuple[str, list[str]]:
    """赢 2 输 1 才留 —— 阈值按 HANDOFF-WORKFLOW.md 第 3.1 节。

    0.5% 不是随手写的: 仿真噪声 + 工具重排会带来约 1% 抖动, 低于它不该算赢。
    绝对吞吐单独看: 它是"每秒跑多少次迭代", 是三项指标互相矛盾时的最终裁判。
    """
    notes, win, lose, missing = [], 0, 0, []
    for k in ("coremark_per_mhz", "fmax_mhz", "coremark_per_lut"):
        a, b = prev.get(k), now.get(k)
        if not a or not b:
            # 【不能静默 continue】原实现直接跳过, 于是解析一坏, 判定就悄悄退化成
            # "2 项指标全赢 => KEEP", 而调用者看不出少了一项。必须留下痕迹。
            missing.append(k)
            continue
        if b > a * 1.005:
            win += 1
            notes.append(f"  WIN  {k:18s} {fmt_metric(k, a)} -> {fmt_metric(k, b)}  (+{100*(b-a)/a:.1f}%)")
        elif b < a * 0.995:
            lose += 1
            notes.append(f"  LOSE {k:18s} {fmt_metric(k, a)} -> {fmt_metric(k, b)}  ({100*(b-a)/a:+.1f}%)")
        else:
            notes.append(f"  ---- {k:18s} {fmt_metric(k, a)} -> {fmt_metric(k, b)}  (~持平)")
    if missing:
        notes.append(f"  [WARN] {len(missing)} 项缺数据, **未参与判定**: {', '.join(missing)}")
    # 只有 1 项可比时,"赢 2 输 1"的规则无从谈起 —— 给 UNKNOWN 而不是假装有结论
    if win + lose < 2:
        notes.append(f"  [WARN] 只有 {win + lose} 项可比 (需要 >=2) —— 本次不给 keep/reject 判定")
        return "UNKNOWN(指标不足)", notes
    if win > lose:
        v = "KEEP"
    elif lose > win:
        v = "REJECT"
    else:
        v = "TIE(看赛题取向)"
    a, b = prev.get("absolute"), now.get("absolute")
    if a and b:
        notes.append(f"  ABS  {'absolute':18s} {fmt_metric('absolute', a)} -> "
                     f"{fmt_metric('absolute', b)}  ({100*(b-a)/a:+.1f}%)")
        if b < a * 0.98:
            notes.append("  [WARN] 绝对吞吐下降 >2% —— 即使赢两项也要慎重")
    return v, notes


def cmd_metrics(args) -> int:
    rows = read_csv_rows()
    # 当前值: 优先命令行直传, 否则取最近一次综合的归档报告
    res = parse_reports()
    if args.ticks:
        it = args.iters or 1
        res["cm_mhz"] = it * 1e6 / args.ticks
    else:
        res = compute_metrics(res)
    now = {
        "coremark_per_mhz": res.get("cm_mhz"),
        "fmax_mhz": res.get("fmax"),
        "coremark_per_lut": res.get("cm_lut"),
        "absolute": res.get("throughput"),
    }
    print("=" * 74)
    print("  当前四项指标")
    print("=" * 74)
    for k, v in now.items():
        print(f"    {k:20s} {fmt_metric(k, v)}")
    if not any(now.values()):
        print("\n  [warn] 四项全空 —— 先跑一次 synth, 或用 --ticks 指定 ticks")
        return 1

    if not args.vs:
        print("\n  (加 --vs <tag> 可与历史某行对比并给出 keep/reject 判定)")
        return 0

    prev_row = find_row(args.vs)
    if not prev_row:
        tags = [r.get("tag") for r in rows][-8:]
        print(f"\n  [FAIL] coremark.csv 里没有 tag = {args.vs}")
        print(f"         最近的 tag: {', '.join(str(t) for t in tags)}")
        return 1

    prev = metric_set(prev_row)
    print(f"\n  对比基线: {args.vs}  ({prev_row.get('date','')})")
    print("  " + "-" * 70)
    v, notes = verdict(now, prev)
    for n in notes:
        print(n)
    print("  " + "-" * 70)
    print(f"  判定: {v}")
    print("=" * 74)
    print("\n  纪律提醒 (HANDOFF-WORKFLOW.md §1.2):")
    print("    · 一次只改一处, 否则归因不了")
    print("    · 结果与上次完全一致 -> 先怀疑镜像没重建, 别当成'改动无效'")
    print("    · 被否决的方案要记 rejected + 原因, 否则下一轮会重复试")
    return 0


# ==========================================================================
# profile: 剖析停顿构成 (优化前先测量)
# ==========================================================================
def parse_profile(out: str) -> dict:
    """从 tb_sw 的 profile 段落抽结构化数据。

    【分母要用 tb 的 cycles, 不能用 CoreMark 的 Total ticks】
    两者不同: CoreMark 的 ticks 只统计它自己的计时区间, 而 tb 的 cycles 从复位
    开始算, 多出启动/收尾的几万拍。算"某项占多少周期"必须用 tb cycles, 否则
    占比会被系统性低估。

    【ModelSim 会给每行加 "# " 前缀】所以先把行首的 '# ' 去掉再匹配 ——
    否则所有 ^ 锚定的正则都匹配不上 (踩过: 只有不带 ^ 的 mispredict/cond 匹配到了)。
    """
    # 去掉 ModelSim 的行首 "# " 注释前缀
    out = re.sub(r"^#\s?", "", out, flags=re.M)
    p: dict = {}

    def num(pat, key, grp=1):
        m = re.search(pat, out, re.M)
        if m:
            try:
                p[key] = int(m.group(grp))
            except ValueError:
                pass

    # 注意: 第一个 "cycles = N" 出现在 tb 启动日志里 ([tb_sw] reset released 之后
    # 也有一行), 所以用带标题的 "--- profile ---" 之后那一段更稳。
    seg = out.split("--- profile ---")[-1] if "--- profile ---" in out else out
    num(r"^\s*cycles\s*=\s*(\d+)", "ticks")
    num(r"^\s*fetch\s*=\s*(\d+)", "fetch")
    num(r"^\s*retire\(wb\)\s*=\s*(\d+)", "retire")
    num(r"^\s*redirects\s*=\s*(\d+)", "redirect_total")
    # 实测冲刷拍数(验证泳道新增的 n_flush)。tb_sw 打印:
    #   "redirects  = N   实测冲刷拍数 = M  -> 每次 x.xx 拍 ..."
    # ⚠ **不能按中文匹配**: ModelSim 会把 $display 里的中文在日志里写成 `?`
    #   (实测: "实测冲刷拍数" 落盘成 "???????????" —— 于是按中文写的正则永远不匹配,
    #    而它会静默退回"每次 2 拍"的旧假设)。这里改成**按 ASCII 结构匹配**:
    #   抓 `redirects = N` 之后、`->` 之前的那个 `= M`。
    num(r"^\s*redirects\s*=\s*\d+.*?=\s*(\d+)\s*->", "redirect_flush_cycles")
    num(r"^\s*stalls\s*=\s*(\d+)", "stall_total")
    num(r"^\s*load-use\s*=\s*(\d+)", "stall_load_use")
    num(r"^\s*split-mem\s*=\s*(\d+)", "stall_split_mem")
    num(r"muldiv busy=\s*(\d+)", "stall_muldiv")
    num(r"ctrl-flow\s*=\s*(\d+)", "ctrl_flow")
    num(r"mispredict\s*=\s*(\d+)", "mispredict")
    num(r"cond branch total\s*=\s*(\d+)", "cond_total")
    num(r"^\s*JAL\s*=\s*(\d+)", "jal")
    num(r"^\s*JALR\s*=\s*(\d+)", "jalr")
    num(r"^\s*Total ticks\s*:\s*(\d+)", "cm_ticks")
    num(r"^\s*Iterations\s*:\s*(\d+)", "iterations")

    # tb 的 cycles 必须从 profile 段取 (CoreMark 的 Total ticks 只覆盖它自己的
    # 计时区间, 少了启动/收尾的几万拍, 当分母会低估占比)
    m = re.search(r"cycles\s*=\s*(\d+)", seg)
    if m:
        p["ticks"] = int(m.group(1))

    if p.get("retire"):
        p["cpi"] = round(p["ticks"] / p["retire"], 3)
    if p.get("ctrl_flow"):
        p["predict_accuracy"] = round(1 - p["mispredict"] / p["ctrl_flow"], 4)
    cyc = p.get("ticks") or 0
    if cyc:
        # redirect 每次的冲刷拍数**不再按假设的 2 拍算** —— 验证泳道加了 n_flush
        # 计数器实测 it: 实测 = 1.00 拍/次(11530 次 / 11530 拍)。旧代码一律乘 2,
        # 把 2.3% 虚报成 4.6%(实测于 2026-10-11)。优先用实测值, 没有才退回旧假设并告警。
        if p.get("redirect_flush_cycles") is not None:
            redir_cyc = p["redirect_flush_cycles"]
        else:
            redir_cyc = p.get("redirect_total", 0) * 2
            if p.get("redirect_total"):
                print("  [warn] 日志里没有实测冲刷拍数(n_flush) -> redirect 占比按**假设 2 拍**计算, "
                      "可能虚报 2 倍。请在 tb_sw 里补 `实测冲刷拍数` 一行。")
        p["share_pct"] = {
            "load_use":  round(100 * p.get("stall_load_use", 0) / cyc, 1),
            "muldiv":    round(100 * p.get("stall_muldiv", 0) / cyc, 1),
            "split_mem": round(100 * p.get("stall_split_mem", 0) / cyc, 1),
            "redirect":  round(100 * redir_cyc / cyc, 1),
        }
        # ---- 把 load-use 停顿从 1 拍加到 2 拍的**时间代价** ----
        # [2026-10-11 更正] 旧模型 `lu/(cyc+lu)` 已被 C-2 的实测证伪: 它隐含
        #   ①"每次 load-use 只多 1 拍且**事件数不变**" —— 实测 C-2 还新扣住了
        #     **距离 2 的消费者**(窗口内新增 8213 个事件), 所以 Δticks = +36784 (+8.9%)
        #     而不是模型给的 +6.9%;
        #   ②分母用 TB cycles, 而判据要用 CoreMark 自报 ticks。
        # 现在优先用 tb_sw 打印的**窗口实测预测值**(它已经把两类事件都算进去了),
        # 拿不到就明确说"需要窗口计数行", 不再给一个会误导人的模型数字。
        m_cost = re.search(r"C-2\s*的\s*ticks\s*增量预测值\s*=\s*(\d+)\s*拍", out)
        if m_cost:
            d = int(m_cost.group(1))
            p["cost_if_load_use_2cyc_pct"] = round(100 * d / (cyc + d), 2)
        else:
            p["cost_if_load_use_2cyc_pct"] = None
            p["cost_if_load_use_2cyc_note"] = (
                "需要 tb_sw 的窗口计数行 `window load-use = ... (N events) -> C-2 的 ticks 增量预测值 = M 拍`;"
                " 旧模型 lu/(cyc+lu) 已被实测证伪(漏算距离2事件), 不再使用")
    return p


def cmd_profile(args) -> int:
    prog = args.prog
    print("=" * 74)
    print("  剖析: 周期花在哪 (优化前先测量, 不要猜)")
    print("=" * 74)
    imem = abspath(f"sim/prog/{prog}.hex")
    dmem = abspath(f"sim/prog/{prog}.dmem.hex")
    for label, p in (("imem", imem), ("dmem", dmem)):
        if not os.path.exists(p):
            print(f"  [BLOCK] 缺 {label} 镜像 {p}")
            return 1

    files = canonical_rtl()
    rc, out, workdir = compile_sim(files, "sim/tb_sw.sv")
    if rc != 0:
        print("  [FAIL] 编译失败")
        print(_indent(_vlog_errors(out)))
        return 1

    cmd = [os.path.join(MSIM, "vsim.exe"), "-c", "-do", "run -all; quit -f", "work.tb_sw",
           "+imem=" + imem, "+dmem=" + dmem, f"+max_cycles={args.max_cycles}"]
    rc, out, _ = run(cmd, cwd=workdir, timeout=args.timeout)
    if "RESULT : PASS" not in out:
        print("  [FAIL] 仿真未通过 (没有 RESULT : PASS), 剖析数据不可信")
        print(_indent(_tail_interesting(out)))
        return 1

    p = parse_profile(out)
    print("\n  周期构成 (占比最大的就是下一轮的靶子):")
    print("  " + "-" * 62)
    for k, v in sorted(p.get("share_pct", {}).items(), key=lambda kv: -kv[1]):
        bar = "#" * int(round(v))
        print(f"    {k:12s} {v:5.1f}%  {bar}")
    print("  " + "-" * 62)
    for k in ("ticks", "retire", "cpi", "predict_accuracy", "mispredict",
              "stall_load_use", "stall_muldiv", "stall_split_mem",
              "redirect_total", "cond_total", "jal", "jalr"):
        if k in p:
            print(f"    {k:18s} {p[k]}")
    if args.json:
        os.makedirs(os.path.dirname(abspath(args.json)), exist_ok=True)
        with open(abspath(args.json), "w", encoding="utf-8") as f:
            json.dump(p, f, indent=1, ensure_ascii=False)
        print(f"\n  -> {args.json}")
    print("=" * 74)
    return 0


# ==========================================================================
# hw: 解析上板 ILA 读回结果 (results/ila_done.csv)
# ==========================================================================
CAPTURE_MANIFEST = "results/ila_done.capture.json"
# RTL 内容指纹的算法标识。必须与 fpga/build_ila.tcl 里打印的 ILA_RTL_SHA 一致,
# 否则"这份上板证据是不是当前 RTL 跑出来的"就无从交叉校验 (PITFALLS R41)。
RTL_SHA_ALG = "sv-sorted-v1"


def sha256_tree() -> str:
    """仓库内 RTL 源文件的**内容指纹**。

    【算法必须与 fpga/build_ila.tcl 的 ILA_RTL_SHA 逐字节一致】(算法名 sv-sorted-v1):
        sha256("FPGA-RTL-SHA-V1\\n" + Σ(relpath + "\\n" + 文件原始字节))
    文件集 = 仓库根 `*.sv` + `fpga/*.sv`, 按仓库相对 POSIX 路径排序。
    两边不一致的话, `hw` 里"这份上板证据是不是当前 RTL 跑出来的"就无法交叉校验。
    (R41: 多写入者并行时, 建像前后各记一次哈希 —— 这条是它的机器化。)
    """
    files = sorted(
        glob.glob(os.path.join(REPO, "*.sv")) + glob.glob(os.path.join(REPO, "fpga", "*.sv")),
        key=lambda p: posix(os.path.relpath(p, REPO)))
    h = hashlib.sha256(b"FPGA-RTL-SHA-V1\n")
    for p in files:
        h.update((posix(os.path.relpath(p, REPO)) + "\n").encode())
        try:
            with open(p, "rb") as fh:
                h.update(fh.read())
        except OSError:
            h.update(b"MISSING")
    return h.hexdigest()


def load_capture_manifest(csv_path: str) -> tuple[dict | None, list[str]]:
    """读"采集清单"并校验它与 CSV 同源。返回 (manifest | None, 问题列表)。

    清单由 `fpga/ila_cap_done.tcl` 在**写完 CSV 之后**写出, 并被 .gitignore 忽略。
    它是"这一次真的在板子上采集过"的**唯一见证**。

    【为什么不能只靠 mtime】(CODE-REVIEW-PLAN X-02)
    `results/ila_done.csv` **是被 git 跟踪的**, 里面含一份完整的 PASS 采集
    (2048 行 ila_ctrl=0xC2 / score_result=0xC9DB2C66)。任何 `git clone/checkout/
    restore` 都会把它的 mtime 刷成"现在", 于是 `hw` 会在**没有板子**的情况下
    打印"上板验证 PASS" —— 正是 PITFALLS R20「验证工具可能制造虚假证据」要防的,
    而触发条件从"板子掉线"变成了日常 git 操作。
    见证文件不入库 => 全新克隆必然没有它 => 必然拒绝给结论。
    """
    p = abspath(CAPTURE_MANIFEST)
    if not os.path.exists(p):
        return None, [f"缺少采集清单 {CAPTURE_MANIFEST}"]
    try:
        man = json.loads(read_text(p))
    except Exception as e:                       # noqa: BLE001 - 任何解析失败都当成"没有见证"
        return None, [f"采集清单不是合法 JSON: {e}"]
    if not isinstance(man, dict):
        return None, ["采集清单不是 JSON 对象"]

    problems: list[str] = []
    want = man.get("csv_sha256")
    if want:
        try:
            got = sha256_file(csv_path)
        except OSError as e:
            got = f"<读不到: {e}>"
        if got != want:
            problems.append(f"csv_sha256 不匹配 (清单 {str(want)[:12]}… vs 实际 {str(got)[:12]}…)")
    else:
        # Tcl 侧可能算不出 sha256 (Vivado 2018.3 的 Tcl 没有内置命令)。退一步用
        # "同一分钟内写出" 作为弱见证 —— 仍比裸 mtime 强, 但明确记进 problems 之外的提示。
        try:
            d = abs(os.path.getmtime(p) - os.path.getmtime(csv_path))
        except OSError:
            d = 1e9
        if d > 120:
            problems.append(f"清单没有 csv_sha256, 且与 CSV 的写出时间相差 {d:.0f}s (>120s)")

    csvp = man.get("csv")
    if csvp:
        try:
            if posix(os.path.relpath(abspath(csvp), REPO)) != \
               posix(os.path.relpath(abspath(csv_path), REPO)):
                problems.append(f"清单记的是另一个 CSV: {csvp}")
        except ValueError:
            pass
    return man, problems


def load_build_json(prog: str = "cm_hw") -> dict:
    """读 `sim/prog/<prog>.build.json` (X-09 单一来源, 由 tools/mk_cm_hw.py 写出)。

    它给出该镜像的 ITERATIONS / 预期 tick / 等待秒数 / 真实时钟, 供 build.tcl、
    build_ila.tcl、ila_cap_done.tcl 与 `hw` 共用 —— 在此之前这四个数散落在三处
    手工同步的魔数里 (R35 要求每次提速后重算 ITERATIONS, 漏改一处就会抓空)。
    文件不存在时返回 {} (调用方回退到"必须显式传参", 不静默用旧默认值)。
    """
    p = abspath(f"sim/prog/{prog}.build.json")
    if not os.path.exists(p):
        return {}
    try:
        j = json.loads(read_text(p))
        return j if isinstance(j, dict) else {}
    except Exception:                                # noqa: BLE001
        return {}


def _resolve_hw_arg(cli_val, sources: dict, key: str):
    """hw 的参数取值: 命令行 > (采集清单 > 构建清单)。返回 (值, 来源说明)。

    M-03: 原来的默认值 (1200 / 50e6) 是 MMCM 之前的旧值, 直接跑就会静默少报
    CoreMark/MHz 2.5 倍 (0.9684 vs 2.4211)。现在默认 None, 且必须有一个来源,
    否则拒绝给结论。
    """
    if cli_val is not None:
        alt = sources.get(key)
        if alt and alt[0] != cli_val:
            return cli_val, f"命令行 (另有 {alt[1]} 写的是 {alt[0]} —— 不一致, 请确认)"
        return cli_val, "命令行"
    if key in sources:
        return sources[key][0], sources[key][1]
    return None, "无"


def cmd_hw(args) -> int:
    """解析 ILA over JTAG 读回的跑分结果。

    判据来自 HANDOFF-HW-LOOP.md: ila_ctrl 的位定义与 score_result 的打包方式。
    这是本板**唯一**能拿到上板结论的通道 (串口无 COM 口)。
    """
    path = abspath(args.csv)
    if not os.path.exists(path):
        print(f"  [BLOCK] 找不到 {args.csv}")
        print("          先跑: cmd /c fpga\\ila_cap_done.bat")
        return 1

    # ---- 【证据链: 采集清单 (capture manifest)】----------------------------
    # 本子命令只解析 results/ila_done.csv, 那是某一次抓取的**快照**。
    # 板子掉线/没重新抓取时这个文件不会更新, 于是它会**照样打印"上板验证 PASS"**
    # —— 把几小时前的陈旧结果当成新验证 (实测踩过: 板子 22:13 已掉线, 这里仍在报 PASS)。
    #
    # 【2026-10-11 加固】mtime 判据**可以被一次 git checkout 伪造**: 该 CSV 是被
    # git 跟踪的, 且里面就含一份完整 PASS 采集。所以现在要求"同一次采集写出的见证":
    # results/ila_done.capture.json (由 ila_cap_done.tcl 写出, 且被 .gitignore 忽略),
    # 并校验它的 csv_sha256 与当前 CSV 逐字节一致。见 CODE-REVIEW-PLAN X-02。
    man, man_problems = load_capture_manifest(path)
    if man:
        try:
            epoch = float(man.get("captured_at_epoch") or 0)
        except (TypeError, ValueError):
            epoch = 0.0
        age_min = (time.time() - epoch) / 60.0 if epoch else 1e9
        stamp = man.get("captured_at_local") or (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)) if epoch else "(清单无时间戳)")
    else:
        age_min = (time.time() - os.path.getmtime(path)) / 60.0
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(path)))
    fresh = age_min <= args.max_age_min
    witnessed = (man is not None) and not man_problems
    # 显式放行开关: 只有在**明知**没有见证时才用, 且结论会被降级成 UNVERIFIED
    allow_unwitnessed = getattr(args, "allow_unwitnessed", False)

    print("=" * 74)
    print("  上板读回结果 (ILA over JTAG)")
    print("=" * 74)
    print(f"  数据文件 : {args.csv}")
    print(f"  抓取时间 : {stamp}   ({age_min:.1f} 分钟前)"
          + ("   [来源: 采集清单]" if man else "   [来源: 文件 mtime —— 无见证!]"))
    if man:
        print(f"  采集清单 : {CAPTURE_MANIFEST}"
              f"   prog={man.get('prog')} iters={man.get('iterations')} "
              f"clk={man.get('clk_hz')} expect_tick={man.get('expect_tick')}")
        if man.get("rtl_sha256"):
            # 清单目前不写 rtl_sha_alg (只写 rtl_sha256)。这里**假定**它就是
            # RTL_SHA_ALG —— 这个假定是**实证过的**: 2026-10-11 用当前 RTL 做的
            # ILA 构建里, build_ila.tcl 打出的 ILA_RTL_SHA 与本文件 sha256_tree()
            # 的结果逐字节一致 (61e28d8c4b6a5d9e…), 两个独立实现 (Tcl+certutil vs
            # Python hashlib) 互证。若哪天算法改动, 这里会报"与当前 RTL 不一致",
            # 从而立刻暴露 (而不是静默放过)。
            alg = man.get("rtl_sha_alg") or RTL_SHA_ALG
            if alg != RTL_SHA_ALG:
                note = f" (清单声明的算法是 {alg!r}, 与 {RTL_SHA_ALG!r} 不同, 不做对比)"
            elif sha256_tree() == man["rtl_sha256"]:
                note = " (与当前 RTL 一致)"
            else:
                note = (" (**与当前 RTL 不一致** —— 这份上板证据不是当前 RTL 跑出来的! "
                        "上板结论对当前代码无效)")
            print(f"  RTL 指纹 : {str(man['rtl_sha256'])[:16]}…{note}")
    if not witnessed:
        print()
        print("  [BLOCK] **没有可信的采集见证** —— 不作为本次验证结论。")
        for pb in man_problems:
            print(f"          · {pb}")
        print("          这意味着: 这份 CSV 可能是被 git 恢复出来的历史快照,")
        print("            而不是这一次在板子上真的抓到的 (PITFALLS R20)。")
        print("          正确做法: cmd /c fpga\\jtag_probe.bat  然后  cmd /c fpga\\ila_cap_done.bat")
        if not allow_unwitnessed:
            print("          (确实要旁路见证, 请显式加 --allow-unwitnessed —— 结论会标为 UNVERIFIED)")
        print("-" * 74)
    elif not fresh:
        print()
        print(f"  [STALE] 这份数据已超过 {args.max_age_min} 分钟 —— **不作为本次验证结论**。")
        print("          大概率是板子没连上 / 没有重新抓取。请先:")
        print("            cmd /c fpga\\jtag_probe.bat     (确认能识别器件)")
        print("            cmd /c fpga\\ila_cap_done.bat   (重新抓取)")
        print("          以下内容仅作**历史参考**。")
        print("-" * 74)

    lines = [l for l in read_text(path).splitlines() if l.strip()]
    hdr = [h.strip().strip('"') for h in lines[0].split(",")]

    def col(name):
        for i, h in enumerate(hdr):
            if name in h:
                return i
        return None

    ic, ir = col("ila_ctrl"), col("score_result")
    if ic is None or ir is None:
        print(f"  [FAIL] CSV 里找不到 ila_ctrl / score_result 列。实际列: {hdr}")
        return 1
    ip, ii = col("dbg_pc"), col("dbg_instr")

    def px(s):
        """十六进制**容错**解析: 表头/空/非法字段返回 None, 不抛异常。

        实测 bug: ILA CSV 的表头列名就叫 `ila_ctrl[7:0]` / `score_result[31:0]`,
        旧实现直接 int("ila_ctrl[7:0]", 16) 会抛
          ValueError: invalid literal for int() with base 16
        把整个上板判读打断 —— 而这恰恰是"复位没释放"时最需要读到的证据
        (R43: 表头行落到数据循环里)。正常数据行仍是 xx / xxxxxxxx, 行为不变。
        """
        t = (s or "").strip().strip('"').strip()
        if t[:2].lower() == "0x":
            t = t[2:]
        if not t:
            return None
        try:
            return int(t, 16)
        except ValueError:
            return None

    # 逐行解析: 表头/汇总/非法行**跳过**(容错), 并统计跳过了多少行。
    # 抓到达 done=1 的那一拍 (程序跑完的状态)。
    hit = None
    rows = []
    skipped = 0
    for ln in lines[1:]:
        c = ln.split(",")
        if len(c) <= max(ic, ir):
            skipped += 1
            continue
        ctrl, res = px(c[ic]), px(c[ir])
        if ctrl is None or res is None:
            skipped += 1           # 表头重复行 / 非法行 -> 跳过, 绝不当成数据
            continue
        rows.append((c, ctrl, res))
        if hit is None and (ctrl >> 6) & 1:
            hit = (ctrl, res)

    if skipped:
        print(f"  (容错: 跳过 {skipped} 行表头/非法行)")

    # dbg_pc 序列: 用来看"PC 到底动没动"
    # (PC 冻结在复位向量 0x80000000 = 复位从未释放的指纹)
    pc_vals = []
    if ip is not None:
        for c, _ctrl, _res in rows:
            if len(c) > ip:
                v = px(c[ip])
                if v is not None:
                    pc_vals.append(v)
    pc_nuniq = len(set(pc_vals)) if pc_vals else 0
    last_instr = None
    if ii is not None and rows and len(rows[-1][0]) > ii:
        last_instr = px(rows[-1][0][ii])

    def pc_note():
        """PC 静止有**两种完全相反**的含义, 必须分开报, 否则会误判:
            0x80000000 (复位向量) 静止 -> 复位从未释放 (R43 的故障指纹)
            0x0000006F (`j .`) 静止    -> 程序已正常跑完, 停在结尾死循环
        抓取发生在编程后 22 s (程序 17.7 s 就跑完), 所以"跑完"这一态 PC 本来就
        该静止 —— 只看"PC 有没有动"会得出相反的结论。"""
        if not pc_vals:
            return "dbg_pc: n/a"
        head = (f"dbg_pc: 首 0x{pc_vals[0]:08X} 末 0x{pc_vals[-1]:08X}"
                f"  不同取值 {pc_nuniq} 个")
        if pc_nuniq > 1:
            return head + " -> 在动"
        if pc_vals[-1] == 0x80000000:
            return head + " -> **冻结在复位向量 0x80000000 (复位从未释放) !!**"
        if last_instr == 0x0000006F:
            return head + " -> 静止: 程序已结束, 停在结尾 `j .` (dbg_instr=0x0000006F)"
        if last_instr is not None:
            return head + f" -> 静止 (末条 dbg_instr=0x{last_instr:08X})"
        return head + " -> 静止"

    if hit is None:
        print("  [FAIL] 没有采到 done=1 —— 程序没跑完 (检查 ITERATIONS 与 after 等待时间)")
        if rows:
            c, lc, lr = rows[-1]
            locked_l = (lc >> 7) & 1
            print(f"         最后一拍: ila_ctrl=0x{lc:02x} score_result=0x{lr:08X}"
                  f"  (bit7 mmcm_locked={locked_l})")
            print("         " + pc_note())
            if ii is not None and len(c) > ii:
                iv = px(c[ii])
                print(f"         dbg_instr=0x{iv:08X}" if iv is not None else "         dbg_instr=?")
            if not locked_l:
                print("         [判定] bit7=0 => MMCM **没有锁定** => 复位从未释放。")
                print("                先查 top_a100t.sv 的 u_mmcm 是否接了 .CLKFBIN,")
                print("                以及综合日志里有没有 [Timing 38-1] DLL output pin")
                print("                ... without a feedback net。")
            else:
                print("         [判定] bit7=1 (MMCM 已锁) 但 done=0 => 问题不在时钟,")
                print("                查 ITERATIONS/after 等待时间或程序本身。")
        return 1

    ctrl, res = hit
    locked = (ctrl >> 7) & 1
    done, mark = (ctrl >> 6) & 1, (ctrl >> 5) & 1
    illegal, p, f = (ctrl >> 2) & 1, (ctrl >> 1) & 1, ctrl & 1
    crc, tick = (res >> 31) & 1, res & 0x7FFFFFFF
    # ---- M-03: --iterations / --clk-hz / --expect-tick 从清单自动推断 ----
    # 原默认值 1200 / 50e6 是 MMCM 之前的旧值, 直接用会**静默**算出
    #   CoreMark/MHz = 0.9684 (真值 2.4211, 少报 2.5 倍) 和 24.78s (真值 17.70s),
    # 而且同一份代码里 --clk-hz 那一半有告警、--iterations 那一半**没有任何告警**。
    # 现在两者默认都是 None: 命令行不给就向"采集清单"(描述这一次抓的那个 bitstream)
    # 或"构建清单"(X-09 单一来源) 要, 都没有就拒绝给结论 (不再猜)。
    bj = load_build_json(man.get("prog") if man and man.get("prog") else "cm_hw")
    sources: dict[str, tuple] = {}
    for k in ("iterations", "clk_hz", "expect_tick"):
        if bj.get(k) is not None:
            sources[k] = (bj[k], f"构建清单 sim/prog/{man.get('prog') if man and man.get('prog') else 'cm_hw'}.build.json")
    if man:                                   # 采集清单描述的是**实际抓到的那个 bitstream**, 优先级更高
        for k in ("iterations", "clk_hz", "expect_tick"):
            if man.get(k) is not None:
                sources[k] = (man[k], f"采集清单 {CAPTURE_MANIFEST}")
    iters, iters_src = _resolve_hw_arg(args.iterations, sources, "iterations")
    clk_hz, clk_src = _resolve_hw_arg(args.clk_hz, sources, "clk_hz")
    expect_tick, exp_src = _resolve_hw_arg(args.expect_tick, sources, "expect_tick")
    if iters is None or clk_hz is None:
        print("  [BLOCK] 无法确定 iterations / clk-hz: 命令行没给, 采集清单与构建清单里也没有。")
        print("          请显式传 --iterations <N> --clk-hz <Hz>, "
              "或先跑一次 tools/mk_cm_hw.py (会写出 sim/prog/cm_hw.build.json)。")
        return 1
    cmmhz = iters * 1e6 / tick if tick else 0
    secs = tick / clk_hz

    # ---- tick 与 --expect-tick 的偏差: 大了必须**明确提示**, 不能默默放过 ----
    # 偏差大通常只有三个原因: ① --clk-hz 不是这个 bitstream 的真实时钟
    # ② --iterations 不是镜像里编译进去的值 ③ CSV 是旧快照 (见上面的新鲜度门槛)
    tick_dev = None
    tick_ok = True
    if expect_tick:
        tick_dev = abs(tick - expect_tick) / expect_tick * 100.0
        tick_ok = tick_dev <= args.tick_tol_pct

    # 头部与新鲜度已经在函数开头打印过, 这里不再重复
    print(f"  ila_ctrl     = 0x{ctrl:02X}  -> locked={locked} done={done} pass={p} fail={f} illegal={illegal}")
    print(f"  score_result = 0x{res:08X}")
    # 【W-02 后 bit31 的语义变了】原来 = "三个 CRC 匹配"; 现在 = "CRC 匹配 **且** 时长>=10s"
    # (软件侧把 secs>=10 并进了这一位, 因为赛事规则要求成绩必须跑满 10 秒)。
    # 文案必须跟着改, 否则读的人会把"跑了 8 秒"当成"结果有效"。
    print(f"    bit31 = {crc}  -> 结果有效 (CRC 匹配 且 时长>=10s): "
          f"{'是' if crc else '否 !!'}")
    print(f"    tick  = {tick:,}")
    print(f"    iterations = {iters}      [来源: {iters_src}]")
    print(f"    CoreMark/MHz = {cmmhz:.4f}")
    secs_ok = secs >= 10.0
    print(f"    时长 = {secs:.2f} s @{clk_hz/1e6:.0f}MHz"
          f"  (赛题要求 >= 10 s: {'满足' if secs_ok else '不满足 !!'})"
          f"   [clk 来源: {clk_src}]")
    print("    " + pc_note())
    if expect_tick:
        tag = "OK" if tick_ok else "偏差过大 !!"
        print(f"    对照预期 {expect_tick:,}: 偏差 {tick_dev:.3f}%"
              f"  [{tag}, 门限 {args.tick_tol_pct}%]   [预期来源: {exp_src}]")
        if not tick_ok:
            print("    [WARN] tick 与预期不符 —— 先核对三件事 (别只看 PASS/FAIL):")
            print(f"           1) clk-hz {clk_hz:.0f} 必须等于该 bitstream 的真实系统时钟")
            print("              (70MHz 版应为 70000000; 50000000 是 MMCM 之前的旧默认)")
            print(f"           2) iterations {iters} 必须等于镜像里编译进去的 ITERATIONS")
            print("           3) results/ila_done.csv 必须来自**本次**抓取 (不是旧快照)")
    if abs(clk_hz - 50e6) < 1.0:
        print("    [注意] 当前用的是 50MHz —— 若 bitstream 已是 70MHz 版,")
        print("           时长/CoreMark-MHz 都会错 (必须显式传 --clk-hz 70000000)")

    # secs_ok 必须计入: 赛事硬性要求 >= 10 s, 而它此前**只被打印、不参与判定**
    # (W-02 之前 bit31 也不含它, 所以一个 8 秒的成绩会被判 PASS)。
    ok = bool(locked and done and p and not f and not illegal and crc
              and tick_ok and secs_ok)
    # 【证据门槛】没有采集见证 / 数据陈旧时,**绝不**给出 "上板验证 PASS"。
    # 注意这里用 `witnessed or allow_unwitnessed`: 旁路见证是显式行为, 且结论降级为
    # UNVERIFIED —— 让"没证据"在读的人眼里一眼可见, 而不是混在 PASS 里。
    if not (witnessed or allow_unwitnessed) or not fresh:
        why = []
        if not witnessed and not allow_unwitnessed:
            why.append("无可信采集见证")
        if not fresh:
            why.append(f"数据已 {age_min:.0f} 分钟未更新")
        print(f"\n  => 结果本身: {'PASS' if ok else 'FAIL'}"
              f"  (但 {'; '.join(why)}, **不作为验证结论**)")
        print("  => 本次验证: 未完成 (需要重新抓取: cmd /c fpga\\ila_cap_done.bat)")
        return 1
    if allow_unwitnessed and not witnessed:
        print("\n  [UNVERIFIED] 显式旁路了采集见证 (--allow-unwitnessed) —— "
              "下面的结论**不构成上板验证**。")
    print(f"\n  => 判据逐项: locked={locked}(需1) done={done}(需1) pass={p}(需1)"
          f" fail={f}(需0) illegal={illegal}(需0) crc={crc}(需1)"
          f" secs>=10={secs_ok}(需1)"
          + (f" tick偏差={tick_dev:.3f}%(需<={args.tick_tol_pct}%)" if tick_dev is not None else ""))
    print(f"  => 上板验证 "
          f"{'PASS' if (ok and witnessed and fresh) else ('UNVERIFIED' if (ok and not witnessed) else 'FAIL')}")
    if args.record:
        res_m = parse_reports()
        m = {"cm_mhz": cmmhz, "fmax": res_m.get("fmax"), "lut": res_m.get("lut"),
             "ff": res_m.get("ff"), "bram": res_m.get("bram"), "dsp": res_m.get("dsp"),
             "cm_lut": (cmmhz / res_m["lut"]) if res_m.get("lut") else None,
             "throughput": (cmmhz * res_m["fmax"]) if res_m.get("fmax") else None,
             "iters": iters, "ticks": tick,
             "note": ("上板实测 (ILA over JTAG): %d 迭代, %.2fs, CRC 判定=%d; "
                      "资源取自最近一次 synth 归档" % (iters, secs, crc))}
        append_csv(args.tag or "HARDWARE-RERUN", m, args.status)
    print("=" * 74)
    return 0 if ok else 1


# ==========================================================================
# doctor
# ==========================================================================
def cmd_doctor(args) -> int:
    print("=" * 74)
    print("  环境自检")
    print("=" * 74)
    checks = [
        ("ModelSim vsim", MSIM + "/vsim.exe"),
        ("ModelSim vlog", MSIM + "/vlog.exe"),
        ("ModelSim license", MSIM_LIC),
        ("RISC-V GCC", GCC),
        ("Vivado", VIVADO_BAT),
        ("Python", PYTHON),
    ]
    bad = 0
    for label, p in checks:
        ok = os.path.exists(p)
        bad += 0 if ok else 1
        print(f"  {'OK  ' if ok else 'FAIL'} {label:20s} {p}")

    print(f"\n  会话环境: process.env 有 {len([v for v in os.environ.values() if v])} 个非空变量")
    print(f"  PROCESSOR_ARCHITECTURE = {os.environ.get('PROCESSOR_ARCHITECTURE', '(缺, 由 clean_env 补)')}")
    print(f"  LM_LICENSE_FILE        = {os.environ.get('LM_LICENSE_FILE', '(缺, 由 clean_env 补)')}")
    print(f"  tempdir                = {tempfile.gettempdir()}")
    if not os.path.isabs(tempfile.gettempdir()):
        print("  [warn] tempdir 是相对路径 -> 临时文件会落进工作区 (SKILL 第 2.4 条)")
    print(f"  RISC-V GCC 版本        = {gcc_version()}")
    print(f"  仓库根                 = {REPO}")
    print("=" * 74)
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="fpga_workflow.py",
        description="RISC-V CPU 工程统一工作流 (校验/回归/报告)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("doctor", help="环境自检")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("check", help="R1 漂移检测 (文件清单/地址/编码/新鲜度/综合警告)")
    # 【M-06】把当前的综合警告固化成基线: 之后**新增**的警告才会被判为问题。
    # 分诊完一批无害警告 (例如 dmem 高位地址未连) 之后跑一次即可。
    p.add_argument("--update-baseline", action="store_true",
                   help="把当前 fpga/build.log 的综合警告固化为基线 (分诊用)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("verify", help="ModelSim 增量回归")
    p.add_argument("--only", nargs="*", help="只跑指定测试名")
    p.add_argument("--force", action="store_true", help="忽略缓存全量重跑")
    # 【M-02】全量缓存命中时 exit 0 会让 CI 把"没跑"当"通过"。自动化调用请带这个开关。
    p.add_argument("--require-run", action="store_true",
                   help="若本次一项都没真跑(全 SKIP)则返回非零 —— 给 CI / 自动循环用")
    # 【R31/R17 族 · 2026-10-11 风格审查抓出】argparse 把 `--a_b` 与 `--a-b` 当**两个
    # 不同选项**: 传错那一个**不会报错**, 而是**静默按默认配置运行**。历史上 verify 用
    # 下划线、synth 用连字符, 两组 help 文本逐字相同 —— 正是"宏/开关没生效却不报错"
    # 那一族(本仓库最忌讳的静默失效)。
    # 统一做法: **规范形式一律连字符**; 并在 main() 里把 argv 的长选项名 `_` 归一成 `-`,
    # 于是两种拼写都能落到同一个选项上, 老命令也不会失效。
    p.add_argument("--imem-comb-read", action="store_true",
                   help="[回退] imem 用组合读 (IMEM_COMB_READ, LUT ROM); "
                        "默认已是寄存读 -> BRAM, 一般不需要这个开关")
    p.add_argument("--imem-reg-read", action="store_true",
                   help="[已废弃] imem 寄存读现已是默认行为; 保留仅为兼容旧命令")
    p.add_argument("--load-reg", action="store_true",
                   help="load 结果由 MEM/WB 寄存器给出 (LOAD_RESULT_REG); 默认组合")
    p.add_argument("--gshare", action="store_true",
                   help="方向预测器用 gshare (BTB_GSHARE); 默认纯局部 Smith")
    p.add_argument("--ras", action="store_true",
                   help="启用返回地址栈 (RAS_EN); 默认关闭 (实测综合指标净亏)")
    p.add_argument("--gemm", action="store_true",
                   help="启用 GEMM 加速单元 (GEMM_EN); 默认关闭 (实测面积翻倍且功能未完成)")
    p.add_argument("--a-ext", action="store_true",
                   help="启用 RV32A 原子指令译码 (A_EN); 默认关闭 (实测纯代价, 且多周期未实现)")
    p.add_argument("-v", "--verbose", action="store_true", help="通过时也打印摘要")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("report", help="三项指标 + 绝对吞吐")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("metrics", help="四项指标 + keep/reject 判定 (含绝对吞吐裁判)")
    p.add_argument("--vs", metavar="TAG", help="与 coremark.csv 里某个 tag 对比")
    p.add_argument("--ticks", type=int, help="直接给 ticks (否则用最近归档报告 + CSV)")
    p.add_argument("--iters", type=int, default=1, help="配合 --ticks 的迭代次数")
    p.set_defaults(func=cmd_metrics)

    p = sub.add_parser("profile", help="剖析停顿构成 (优化前先测量)")
    p.add_argument("--prog", default="cm_rv32im", help="程序基名 (默认 cm_rv32im)")
    p.add_argument("--json", help="结果写到这个 JSON 路径")
    p.add_argument("--max-cycles", type=int, default=5000000)
    p.add_argument("--timeout", type=int, default=1200)
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("hw", help="解析上板 ILA 读回结果 (results/ila_done.csv)")
    p.add_argument("--csv", default="results/ila_done.csv", help="ILA 抓取的 CSV")
    # 【M-03 修复】这三个参数**默认不再猜**。
    # 原来的默认值 1200 / 50e6 是"MMCM 之前、ITERATIONS=1200"那一代的值; 现在镜像是
    # ITERATIONS=3000 / 70 MHz, 用默认值会静默少报 CoreMark/MHz 2.5 倍
    # (实测 hw 不带参数: 0.9684 而真值 2.4211) 且 --clk-hz 那一半有告警、--iterations
    # 那一半连告警都没有。现在: 不给就向采集清单 (results/ila_done.capture.json) 要,
    # 都没有就拒绝给结论。
    p.add_argument("--iterations", type=int, default=None,
                   help="镜像里编译进去的 ITERATIONS。省略则从采集清单推断(推荐)")
    p.add_argument("--clk-hz", type=float, default=None,
                   help="bitstream 的**真实**系统时钟 Hz。省略则从采集清单推断(推荐)")
    p.add_argument("--expect-tick", type=int,
                   help="预期 tick, 用于算偏差 (省略则从采集清单推断)")
    # 【X-02】没有采集见证时默认拒绝给结论。确实要旁路就用这个开关, 结论会标 UNVERIFIED。
    p.add_argument("--allow-unwitnessed", action="store_true",
                   help="[危险] 允许在没有采集清单见证时给结论 —— 结论标为 UNVERIFIED, "
                        "**不构成上板验证**")
    p.add_argument("--tick-tol-pct", type=float, default=0.5,
                   help="tick 允许偏差 %%, 超过则给出 [WARN] 并判 FAIL (默认 0.5)")
    p.add_argument("--tag", help="记录进 coremark.csv 的标签")
    p.add_argument("--record", action="store_true", help="追加一行到 results/coremark.csv")
    # 【X-04】status 必须由调用方给: 原实现硬编码 keep, 工具**永远无法记录一个被否决的
    # 实验**, 22 行 rejected 全是人工编辑出来的。默认 measure = "只测了, 还没决策"。
    p.add_argument("--status", choices=CSV_STATUSES, default="measure",
                   help="记进 coremark.csv 的 status (默认 measure=只测了还没决策; "
                        "做决策用 keep / rejected)")
    # 数据新鲜度门限: 超过就拒绝给结论 (板子掉线时 CSV 不会更新, 否则会
    # 把几小时前的旧结果当成新验证 —— 实测踩过)
    p.add_argument("--max-age-min", type=float, default=20.0,
                   help="CSV 超过这个分钟数就判为陈旧, 不给验证结论 (默认 20)")
    p.set_defaults(func=cmd_hw)

    p = sub.add_parser("synth", help="Vivado 综合 -> 四项指标 (自动串行化 + 缓存)")
    p.add_argument("--prog", default="cm_hw",
                   help="要综合进 imem/dmem 的程序基名 (默认 cm_hw = 跑分版)")
    p.add_argument("--tag", help="记录进 results/coremark.csv 用的标签")
    p.add_argument("--record", action="store_true", help="追加一行到 results/coremark.csv")
    # 【X-04】status 必须由调用方给: 原实现硬编码 keep, 工具**永远无法记录一个被否决的
    # 实验**, 22 行 rejected 全是人工编辑出来的。默认 measure = "只测了, 还没决策"。
    p.add_argument("--status", choices=CSV_STATUSES, default="measure",
                   help="记进 coremark.csv 的 status (默认 measure=只测了还没决策; "
                        "做决策用 keep / rejected)")
    p.add_argument("--force", action="store_true", help="忽略缓存强制重建")
    # 【注意默认值】imem/dmem **都用 BRAM** —— 这是交付配置。
    # imem 靠"寄存读"落 RAMB36 (不定义任何宏即为默认路径), 组合读只是回退。
    # 其余宏 (load/gshare/ras/gemm/a) 默认关闭, 与上板验证过的配置一致。
    p.add_argument("--imem-comb-read", action="store_true",
                   help="[回退] imem 用组合读 (IMEM_COMB_READ, LUT ROM); "
                        "默认已是寄存读 -> BRAM")
    p.add_argument("--imem-reg-read", action="store_true",
                   help="[已废弃] imem 寄存读现已是默认行为; 保留仅为兼容旧命令")
    p.add_argument("--load-reg", action="store_true",
                   help="load 结果由 MEM/WB 寄存器给出 (LOAD_RESULT_REG); 默认组合")
    p.add_argument("--gshare", action="store_true",
                   help="方向预测器用 gshare (BTB_GSHARE); 默认纯 Smith")
    p.add_argument("--ras", action="store_true",
                   help="启用返回地址栈 (RAS_EN); 默认关闭 (实测综合指标净亏)")
    p.add_argument("--gemm", action="store_true",
                   help="启用 GEMM 加速单元 (GEMM_EN); 默认关闭 (缓冲落成触发器阵列)")
    p.add_argument("--a-ext", action="store_true",
                   help="启用 RV32A 原子指令 + 多周期读-改-写 (A_EN); 默认关闭 (评分零收益)")
    p.add_argument("--timeout", type=int, default=3600, help="Vivado 超时秒数")
    p.add_argument("--lock-timeout", type=int, default=1800, help="等待构建锁的秒数")
    p.add_argument("--opt-directive", default=None,
                   help="opt_design -directive(150 档留余量用)。可取值同 Vivado:\n"
                        "ExploreWithRemap / NoLUTCombining / AggressiveExplore / Explore\n"
                        "等。ExploreWithRemap 会重映射 LUT 网表, NoLUTCombining 关 LUT 合并")
    p.add_argument("--place-directive", default=None,
                   help="覆盖 place_design 策略。**build.tcl 的默认是 ExtraTimingOpt**"
                        " (与 PB_RANGE 成对选定, 见 build.tcl 的实测表); "
                        "传 WLDrivenBlockPlacement 可回到配对前的那个点")
    p.add_argument("--pblock-range", default=None,
                   help="给 PBLOCK_CELLS 加 Pblock, 例 CLOCKREGION_X1Y0:CLOCKREGION_X1Y3")
    p.add_argument("--pblock-cells", default=None,
                   help="Pblock 约束的层次 (默认 u_core), 例 u_core/u_imem")
    p.add_argument("--contain-routing", action="store_true",
                   help="给 Pblock 加 CONTAIN_ROUTING (纯物理, 不改功能)")
    p.add_argument("--route-directive", default=None,
                   help="覆盖 route_design 策略 (默认 AggressiveExplore)")
    p.add_argument("--synth-directive", default=None,
                   help="覆盖 synth_design 策略。**Vivado 2018.3 只接受这 8 个**"
                        "(已用 synth_design -help 核实): default / RuntimeOptimized / "
                        "AreaOptimized_high / AreaOptimized_medium / AlternateRoutability / "
                        "AreaMapLargeShiftRegToBRAM / AreaMultThresholdDSP / FewerCarryChains。"
                        "注意 PerformanceOptimized **不存在**于 2018.3(会立刻 "
                        "[Common 17-161] 报错)。RVCoreP 的 main.xpr 用 Directive=7, "
                        "按该枚举 = FewerCarryChains")
    p.add_argument("--no-lc", action="store_true",
                   help="传给 synth_design 的 -no_lc(禁 LUT 合并)。对症:\n"
                        "150 战役 step1 发现译码 LUT 与前递预选 LUT 被合并, 造出 5 级长锥")
    p.add_argument("--fanout-limit", type=int, default=None,
                   help="synth_design -fanout_limit N(全局扇出指导)。对症 top-1 上\n"
                        "fo=121/122 的两跳布线(单跳 1.317ns)。默认不传(工具默认值)")
    p.add_argument("--synth-recipe", default=None, choices=["perf_high"],
                   help="整套综合配方。perf_high = RVCoreP 的 Flow_PerfOptimized_high 等价物: -resource_sharing off -fanout_limit 400 -fsm_extraction one_hot -no_lc -keep_equivalent_registers -shreg_min_size 5 (从他们的 main.xpr synth_1 Run Description 逐条抄出)")
    p.add_argument("--resource-sharing", default=None,
                   help="-resource_sharing 取值 (off/on/auto)。ref5 的 V4 用过 off "
                        "作为 +9.16 MHz 批次的一部分, 而本核此前完全没有这个旋钮")
    p.add_argument("--phys-opt-directive", default=None,
                   help="覆盖两处 phys_opt_design 的策略 (默认 AggressiveExplore)。"
                        "RVCoreP-32IM 的 impl 用 Performance_ExplorePostRoutePhysOpt。"
                        "常用: Explore / ExploreWithRemap / AggressiveExplore")
    p.add_argument("--retiming", action="store_true",
                   help="开启综合器寄存器重定时 (形式等价变换, ticks 必须不变)")
    p.set_defaults(func=cmd_synth)

    # 【长选项名归一: `_` -> `-`】argparse 把 `--imem_comb_read` 与 `--imem-comb-read`
    # 当成**两个不同选项**: 传了没注册的那一个**不会报错**, 而是**静默按默认配置运行**
    # (R31/R17 那一族, "开关没生效却不报错")。历史上 verify 子命令注册下划线、
    # synth 子命令注册连字符, 两组 help 文本还逐字相同 —— 2026-10-11 风格审查抓出。
    # 现在规范形式统一为连字符, 并在入口把 argv 的长选项名归一, 于是老命令(下划线)
    # 依然有效, 而两种拼写一定落到同一个选项上。
    # ⚠ 只归一 `=` **左边**的选项名 —— 否则 `--imem=sim/prog/lu_bench.hex` 的值会被
    #   改成 `lu-bench.hex`(我自己第一版就踩了这个, 写在这里免得下一个人重犯)。
    def _norm_long_opt(a: str) -> str:
        if not a.startswith("--"):
            return a
        name, eq, val = a.partition("=")
        return "--" + name[2:].replace("_", "-") + eq + val

    args = ap.parse_args([_norm_long_opt(a) for a in sys.argv[1:]])
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
