#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ftdi_probe.py -- 用 FTDI D2XX 直接查看 FT2232H 的两个通道（免装 VCP 驱动）。

背景: 板载 FT2232H (VID_0403&PID_6014, 序列号 210512180081 == JTAG 目标) 的
  A 通道 = JTAG (Vivado/Digilent 运行时用 D2XX 访问)
  B 通道 = 我们软核的 UART (uart_txd=D17 / uart_rxd=E14, 115200 8N1)
本机没有给它装 VCP 驱动 => 看不到 COM 号, 但 `ftd2xx.dll` 在
(C:\\Windows\\System32 与 Vivado 的 lib 目录), 所以可以直接用 D2XX 打开 B 通道。

本脚本**只查询设备列表**, 不打开任何通道 —— 避免干扰 JTAG。
用法: python tools\\ftdi_probe.py
"""
import ctypes as C
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

for dll in ("ftd2xx.dll", r"D:\software\Learn\Xilinx\Vivado\2018.3\lib\win64.o\ftd2xx.dll"):
    try:
        ft = C.WinDLL(dll)
        print("  DLL: %s  OK" % dll)
        break
    except OSError as e:
        print("  DLL: %s  失败 (%s)" % (dll, e))
else:
    raise SystemExit("  找不到可用的 ftd2xx.dll")

ft.FT_CreateDeviceInfoList.argtypes = [C.POINTER(C.c_ulong)]
ft.FT_CreateDeviceInfoList.restype = C.c_ulong
ft.FT_GetDeviceInfoDetail.argtypes = [
    C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.c_ulong),
    C.POINTER(C.c_ulong), C.c_char_p, C.c_char_p, C.POINTER(C.c_void_p)]
ft.FT_GetDeviceInfoDetail.restype = C.c_ulong

n = C.c_ulong(0)
st = ft.FT_CreateDeviceInfoList(C.byref(n))
print("  FT_CreateDeviceInfoList -> status=%d, 设备数=%d" % (st, n.value))
print("  (FT2232H 正常应出现 **两个** 条目: ...Converter A 与 ...Converter B)")

FLAG_NAMES = {0x01: "FT_FLAGS_OPENED", 0x02: "FT_FLAGS_HISPEED"}
TYPE_NAMES = {0: "BM", 1: "AM", 2: "100AX", 3: "UNKNOWN", 4: "2232C", 5: "232R",
              6: "2232H", 7: "4232H", 8: "232H", 9: "X_SERIES"}
for i in range(n.value):
    fl, ty, idv, loc = C.c_ulong(), C.c_ulong(), C.c_ulong(), C.c_ulong()
    ser, desc = C.create_string_buffer(64), C.create_string_buffer(64)
    h = C.c_void_p()
    st = ft.FT_GetDeviceInfoDetail(C.c_ulong(i), C.byref(fl), C.byref(ty), C.byref(idv),
                                   C.byref(loc), ser, desc, C.byref(h))
    flags = "|".join(v for k, v in FLAG_NAMES.items() if fl.value & k) or "-"
    print("  [%d] status=%d flags=%s type=%s id=0x%08X loc=%d"
          % (i, st, flags, TYPE_NAMES.get(ty.value, ty.value), idv.value, loc.value))
    print("      serial=%r  desc=%r" % (ser.value.decode(errors="replace"),
                                        desc.value.decode(errors="replace")))
