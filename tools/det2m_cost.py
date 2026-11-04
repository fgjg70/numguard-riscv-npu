"""核算 04_models / bottle_cap 两包中 5 个 2M 检测器的真实算力与参数开销。

纯 Python、不依赖 torch/onnx：按 enhanced_anchor_free.py 的源码结构手工展开，
用 param 数反证结构与官方 training_summary.json 的 2,048,721 是否一致。
"""
from __future__ import annotations

import json
from dataclasses import dataclass


def make_divisible(v: float, div: int = 8) -> int:
    return max(div, int(v + div / 2) // div * div)


@dataclass
class Op:
    name: str
    cin: int
    cout: int
    k: int
    stride: int
    groups: int
    h: int
    w: int
    bn: bool = True

    @property
    def params(self) -> int:
        conv = (self.cin // self.groups) * self.cout * self.k * self.k
        return conv + (2 * self.cout if self.bn else 0)

    @property
    def macs(self) -> int:
        oh = (self.h + self.stride - 1) // self.stride
        ow = (self.w + self.stride - 1) // self.stride
        return oh * ow * (self.cin // self.groups) * self.cout * self.k * self.k

    @property
    def out_hw(self) -> tuple[int, int]:
        return ((self.h + self.stride - 1) // self.stride,
                (self.w + self.stride - 1) // self.stride)


def build(width: float = 0.55, fpn: int = 72, num_classes: int = 4, size: int = 224):
    c1 = make_divisible(32 * width)
    c2c = make_divisible(48 * width)
    c3c = make_divisible(80 * width)
    c4a = make_divisible(128 * width)
    c4c = make_divisible(160 * width)
    c5c = make_divisible(256 * width)

    ops: list[Op] = []

    def conv(name, cin, cout, k, s, g, h, w, bn=True):
        op = Op(name, cin, cout, k, s, g, h, w, bn)
        ops.append(op)
        return op.out_hw

    def ir(name, cin, cout, s, e, h, w):
        mid = cin * e
        if e != 1:
            h, w = conv(f"{name}.expand", cin, mid, 1, 1, 1, h, w, False)
        h, w = conv(f"{name}.dw", mid, mid, 3, s, mid, h, w)
        h, w = conv(f"{name}.proj", mid, cout, 1, 1, 1, h, w)
        return h, w

    def resblock(name, ch, h, w):
        h, w = conv(f"{name}.conv1", ch, ch, 3, 1, 1, h, w)
        h, w = conv(f"{name}.conv2", ch, ch, 3, 1, 1, h, w)
        return h, w

    # ---- backbone ----
    h, w = conv("stem", 3, c1, 3, 2, 1, size, size)
    for i in range(2):
        h, w = ir(f"s1.{i}", c1, c1, 1, 4, h, w)
    s2_hw = h, w = ir("s2.0", c1, c2c, 2, 6, h, w)          # 56x56
    for i in (1, 2):
        h, w = ir(f"s2.{i}", c2c, c2c, 1, 6, h, w)
    s3_hw = h, w = ir("s3.0", c2c, c3c, 2, 6, h, w)         # 28x28
    for i in (1, 2, 3):
        h, w = ir(f"s3.{i}", c3c, c3c, 1, 6, h, w)
    s4_hw = h, w = ir("s4.0", c3c, c4a, 2, 6, h, w)         # 14x14
    h, w = ir("s4.1", c4a, c4a, 1, 6, h, w)
    h, w = ir("s4.2", c4a, c4c, 1, 6, h, w)
    h, w = ir("s4.3", c4c, c4c, 1, 6, h, w)
    h, w = ir("s4.4", c4c, c4c, 1, 6, h, w)
    s5_hw = h, w = ir("s5.0", c4c, c5c, 2, 6, h, w)         # 7x7
    for i in (1, 2):
        h, w = ir(f"s5.{i}", c5c, c5c, 1, 6, h, w)

    # ---- FPN (top-down 到 p2=56x56) ----
    h5, w5 = s5_hw
    conv("lat5", c5c, fpn, 1, 1, 1, h5, w5)
    resblock("ref5", fpn, h5, w5)
    h4, w4 = s4_hw
    conv("lat4", c4c, fpn, 1, 1, 1, h4, w4)
    resblock("ref4", fpn, h4, w4)
    h3, w3 = s3_hw
    conv("lat3", c3c, fpn, 1, 1, 1, h3, w3)
    resblock("ref3", fpn, h3, w3)
    h2, w2 = s2_hw
    conv("lat2", c2c, fpn, 1, 1, 1, h2, w2)
    resblock("ref2", fpn, h2, w2)

    # ---- multi_scale_fusion @56x56 ----
    conv("msf.proj", fpn * 3, fpn, 1, 1, 1, h2, w2)
    resblock("msf.res", fpn, h2, w2)

    # ---- heads @56x56 ----
    for i in range(3):
        conv(f"obj_tower.{i}", fpn, fpn, 3, 1, 1, h2, w2)
    for i in range(3):
        conv(f"cls_tower.{i}", fpn, fpn, 3, 1, 1, h2, w2)
    for i in range(2):
        conv(f"box_tower.{i}", fpn, fpn, 3, 1, 1, h2, w2)
    conv("obj_head", fpn, 1, 1, 1, 1, h2, w2)
    conv("cls_head", fpn, num_classes, 1, 1, 1, h2, w2)
    conv("box_head", fpn, 4, 1, 1, 1, h2, w2)

    return ops, dict(c1=c1, c2=c2c, c3=c3c, c4a=c4a, c4=c4c, c5=c5c, fpn=fpn)


def summarize(ops: list[Op]) -> dict:
    macs = sum(o.macs for o in ops)
    params = sum(o.params for o in ops)
    conv_only = sum((o.cin // o.groups) * o.cout * o.k * o.k for o in ops)
    return dict(macs=macs, params=params, conv_params=conv_only, ops=len(ops))


if __name__ == "__main__":
    ops, widths = build()
    s = summarize(ops)
    print("通道宽度:", widths)
    print(f"层数(op) = {s['ops']}")
    print(f"参数  = {s['params']:,}   (官方 2,048,721)")
    print(f"  差  = {s['params'] - 2048721:+,}")
    print(f"MAC   = {s['macs']:,}  ({s['macs']/1e6:.1f} MMAC)")
    print()

    print("按阶段拆账（MAC 占比）:")
    stage = {}
    for o in ops:
        key = o.name.split(".")[0]
        stage[key] = stage.get(key, 0) + o.macs
    total = s["macs"]
    for k, v in sorted(stage.items(), key=lambda x: -x[1]):
        print(f"  {k:<12}{v/1e6:>8.2f} MMAC  {v/total*100:>6.1f}%")
    print()

    print("拓扑清单（前 12 大开销）:")
    for o in sorted(ops, key=lambda x: -x.macs)[:12]:
        print(f"  {o.name:<16} cin={o.cin:>4} cout={o.cout:>4} k={o.k} g={o.groups:>4} "
              f"@{o.h}x{o.w:<4} MAC={o.macs/1e6:>7.2f}M")


def build_slim(width: float = 0.5, fpn: int = 32, num_classes: int = 4, size: int = 224):
    """SlimMobileNetNutAnchorFree：无 multi_scale_fusion，单一 head_features。"""
    c_stem = make_divisible(16 * width)
    c1 = make_divisible(16 * width)
    c2c = make_divisible(24 * width)
    c3c = make_divisible(32 * width)
    c4a = make_divisible(64 * width)
    c4b = make_divisible(96 * width)
    c5c = make_divisible(160 * width)
    ops: list[Op] = []

    def conv(name, cin, cout, k, s, g, h, w, bn=True):
        op = Op(name, cin, cout, k, s, g, h, w, bn)
        ops.append(op)
        return op.out_hw

    def ir(name, cin, cout, s, e, h, w):
        mid = cin * e
        if e != 1:
            h, w = conv(f"{name}.expand", cin, mid, 1, 1, 1, h, w)
        h, w = conv(f"{name}.dw", mid, mid, 3, s, mid, h, w)
        h, w = conv(f"{name}.proj", mid, cout, 1, 1, 1, h, w)
        return h, w

    def ds(name, cin, cout, s, h, w):
        h, w = conv(f"{name}.dw", cin, cin, 3, s, cin, h, w)
        h, w = conv(f"{name}.pw", cin, cout, 1, 1, 1, h, w)
        return h, w

    h, w = conv("stem", 3, c_stem, 3, 2, 1, size, size)
    h, w = ir("s1.0", c_stem, c1, 1, 2, h, w)
    s2_hw = h, w = ir("s2.0", c1, c2c, 2, 4, h, w)
    h, w = ir("s2.1", c2c, c2c, 1, 4, h, w)
    s3_hw = h, w = ir("s3.0", c2c, c3c, 2, 4, h, w)
    for i in (1, 2):
        h, w = ir(f"s3.{i}", c3c, c3c, 1, 4, h, w)
    s4_hw = h, w = ir("s4.0", c3c, c4a, 2, 4, h, w)
    h, w = ir("s4.1", c4a, c4a, 1, 4, h, w)
    h, w = ir("s4.2", c4a, c4a, 1, 4, h, w)
    h, w = ir("s4.3", c4a, c4b, 1, 4, h, w)
    h, w = ir("s4.4", c4b, c4b, 1, 4, h, w)
    s5_hw = h, w = ir("s5.0", c4b, c5c, 2, 4, h, w)
    h, w = ir("s5.1", c5c, c5c, 1, 4, h, w)

    conv("lat5", c5c, fpn, 1, 1, 1, *s5_hw)
    conv("lat4", c4b, fpn, 1, 1, 1, *s4_hw)
    conv("lat3", c3c, fpn, 1, 1, 1, *s3_hw)
    conv("lat2", c2c, fpn, 1, 1, 1, *s2_hw)
    ds("ref4", fpn, fpn, 1, *s4_hw)
    ds("ref3", fpn, fpn, 1, *s3_hw)
    ds("ref2", fpn, fpn, 1, *s2_hw)
    ds("hf.0", fpn, fpn, 1, *s2_hw)
    ds("hf.1", fpn, fpn, 1, *s2_hw)
    conv("obj_head", fpn, 1, 1, 1, 1, *s2_hw)
    conv("cls_head", fpn, num_classes, 1, 1, 1, *s2_hw)
    conv("box_head", fpn, 4, 1, 1, 1, *s2_hw)
    return ops, dict(c_stem=c_stem, c1=c1, c2=c2c, c3=c3c, c4a=c4a, c4b=c4b, c5=c5c, fpn=fpn)


def report_slim():
    ops, wd = build_slim()
    s = summarize(ops)
    print("=== SlimMobileNetNutAnchorFree (w=0.5, fpn=32) ===")
    print("通道:", wd)
    print(f"参数 = {s['params']:,}   int8 = {s['params']/1024:.0f} KB")
    print(f"MAC  = {s['macs']:,}  ({s['macs']/1e6:.1f} MMAC)")
    return s
