#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""uart_capture.py -- 通过串口读回 CoreMark 的上板输出（UART 验证通道）。

为什么需要它: 150 MHz 在**评分流**（无 ILA）上时序过门(+0.018), 但在**带 ILA 的上板流**
上过不去(-0.293) —— 调试核在高频下抢布线。若能从 UART 读回 CoreMark 的输出, 就可以用
**评分流那份网表**（= 真正要交付的网表）上板验证, 从而绕开调试核的时序代价。

硬件要求（板载 FT232H 只是 JTAG, 不提供串口）:
    板子 uart_txd = **D17**  ->  USB-TTL 适配器的 RXD
    板子 uart_rxd = **E14**  <-  USB-TTL 适配器的 TXD
    板子 GND                 --  USB-TTL 适配器的 GND
    参数: **115200 8N1**（mmio_subsys.v 里 BAUD_HZ=115200, BAUD_DIV=CLK_HZ/BAUD_HZ 是频率无关的）

判据（与 ILA 通道等价的那几条）:
    · 出现 `Correct operation validated` / `CoreMark 1.0 : ...`  => 功能通过(CRC 已由程序自检)
    · `Iterations/Sec` 与 `Total time (secs)` => 可反推每迭代拍数与 CoreMark/MHz
    · 板载 led[1] = PASS(tohost 写 1) 可做交叉确认

用法:
    python tools\\uart_capture.py --list
    python tools\\uart_capture.py --port COM8 --seconds 25 --out .fpga-cache\\logs\\uart_150.txt
"""
import argparse
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def list_ports():
    try:
        from serial.tools import list_ports as lp          # pyserial 可选
    except ImportError:
        lp = None
    try:
        import serial
    except ImportError:
        print("  [warn] 没装 pyserial; 退回用 Windows 自带 API 列口")
        serial = None

    if lp is not None:
        ports = list(lp.comports())
        if not ports:
            print("  (没有发现任何串口)")
        for p in ports:
            print("  %-8s %-40s hwid=%s" % (p.device, p.description, p.hwid))
        return [p.device for p in ports]
    import winreg                                            # noqa: E402
    key = r"HARDWARE\DEVICEMAP\SERIALCOMM"
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as k:
            i = 0
            while True:
                try:
                    name, val, _ = winreg.EnumValue(k, i)
                except OSError:
                    break
                print("  %-8s <- %s" % (val, name))
                out.append(val)
                i += 1
    except OSError as e:
        print("  读取注册表失败: %s" % e)
    return out


def capture(port, seconds, out_path, baud):
    try:
        import serial
    except ImportError:
        raise SystemExit("  需要 pyserial:  pip install pyserial   (或用你惯用的串口工具, "
                         "把 115200 8N1 的输出存成文件后跑 --parse)")
    ser = serial.Serial(port, baud, timeout=0.5)
    buf = bytearray()
    t0 = time.time()
    print("  监听 %s @ %d 8N1, %.0f 秒..." % (port, baud, seconds))
    while time.time() - t0 < seconds:
        n = ser.in_waiting or 0
        chunk = ser.read(n if n else 1)
        if chunk:
            buf += chunk
            sys.stdout.write(chunk.decode("utf-8", "replace"))
            sys.stdout.flush()
    ser.close()
    with open(out_path, "wb") as f:
        f.write(bytes(buf))
    print("\n  -> 已存 %s (%d 字节)" % (out_path, len(buf)))
    return bytes(buf)


def parse(text):
    """从 CoreMark 输出里抽判据。"""
    import re
    verdict = {}
    verdict["validated"] = "Correct operation validated" in text
    m = re.search(r"Iterations/Sec\s*:\s*([\d.]+)", text)
    verdict["iter_per_sec"] = float(m.group(1)) if m else None
    m = re.search(r"Total time \(secs\)\s*:\s*([\d.]+)", text)
    verdict["secs"] = float(m.group(1)) if m else None
    m = re.search(r"Iterations\s*:\s*(\d+)", text)
    verdict["iterations"] = int(m.group(1)) if m else None
    m = re.search(r"CoreMark 1\.0\s*:\s*([\d.]+)", text)
    verdict["coremark"] = float(m.group(1)) if m else None
    m = re.search(r"Total ticks\s*:\s*(\d+)", text)
    verdict["ticks"] = int(m.group(1)) if m else None
    return verdict


def main():
    ap = argparse.ArgumentParser(description="UART 通道上板验证（CoreMark 输出）")
    ap.add_argument("--list", action="store_true", help="列出串口")
    ap.add_argument("--port", help="如 COM8")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=25.0)
    ap.add_argument("--out", default=".fpga-cache/logs/uart_capture.txt")
    ap.add_argument("--parse", help="只解析已有的输出文件（不需要串口）")
    a = ap.parse_args()

    if a.list:
        list_ports()
        return 0
    if a.parse:
        txt = open(a.parse, encoding="utf-8", errors="replace").read()
        v = parse(txt)
        for k, val in v.items():
            print("  %-14s %s" % (k, val))
        return 0
    if not a.port:
        raise SystemExit("  需要 --port（先 --list 看有哪些口）")
    txt = capture(a.port, a.seconds, a.out, a.baud).decode("utf-8", "replace")
    print("\n  === 判据 ===")
    for k, val in parse(txt).items():
        print("  %-14s %s" % (k, val))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
