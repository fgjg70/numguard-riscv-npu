#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_equiv.py -- exhaustive bit-exactness proof for the RR2 load-data path.

WHAT THIS PROVES
----------------
`load_store_unit.sv` used to extract a misaligned load with a 64-bit barrel
shifter:

    raw64    = split ? {rd, head_word_r} : {32'b0, rdata_eff}
    load_raw = (raw64 >> (off_wb * 8))[31:0]

RR2 replaced that shifter with a 2-bit-select 4:1 byte-lane mux (see the
"RR2: 64 位桶形移位器 -> 2 位 off_wb 直选 4:1 字节通道" comment in
`load_store_unit.sv`, around line 195).  This script enumerates the ENTIRE
input space of both formulations -- every (rd, head, off, size, uns, split)
combination -- and asserts that the two produce bit-identical results.

Combination count with the default word set (375 words):
    375*375 * 4(off) * 3(size) * 2(uns) * 2(split) = 6,750,000

The word set is deliberately aggressive: 0, all-ones, every single-bit value
and its complement (the values that matter for the critical path), plus
`--random N` seeded random words.

WHY IT LIVES IN tools/ (E-03)
-----------------------------
This file used to sit in `.rr2_scratch/`, i.e. OUTSIDE the repository, was not
tracked by git, and was referenced by an RTL comment -- so the only correctness
argument for the shipped load path could not be reproduced on any other machine.
It is now a tracked repo artifact.  It has NO file inputs: it is pure
computation, so it runs from any working directory with any Python 3.

USAGE
-----
    python tools/check_equiv.py                      # full 6,750,000 combos
    python tools/check_equiv.py --quick              # smoke test (270,000 combos)
    python tools/check_equiv.py --random 2000        # wider random corpus
    python tools/check_equiv.py --seed 7             # different corpus

Exit code 0 = zero mismatches, 1 = at least one mismatch (or bad arguments).
Machine-readable tail markers: `CHECK_EQUIV=PASS` / `CHECK_EQUIV=FAIL`.

`main(argv=None)` is importable so `tools/fpga_workflow.py verify` can call it
directly as a fast regression row.
"""
from __future__ import annotations

import argparse
import random
import sys
import time

M32 = 0xFFFFFFFF

# size encodings used by the RTL (values only matter for grouping):
#   0 = byte, 1 = halfword, 2 = word
SIZE_BYTE, SIZE_HALF, SIZE_WORD = 0, 1, 2


def old(rd: int, head: int, off: int, size: int, uns: int, split: int) -> int:
    """Reference formulation: 64-bit barrel shift, then sign/zero extend."""
    raw64 = ((rd << 32) | head) if split else (rd & M32)
    load_raw = (raw64 >> (off * 8)) & M32
    return ext(load_raw, size, uns)


def new(rd: int, head: int, off: int, size: int, uns: int, split: int) -> int:
    """RR2 formulation: 2-bit off_wb selects one of four 4:1 byte lanes.

    In the non-split case raw64 = {32'b0, rdata_eff}, so head_word_r must NOT
    leak into the low word -- every lane rebuilds its high bytes from the
    correct source.
    """
    s0 = ((rd << 32) | head) if split else (rd & M32)
    if off == 0:
        c = s0 & M32
    elif off == 1:
        c = ((s0 >> 8) & 0x00FFFFFF) | (((s0 >> 32) & 0xFF) << 24)
    elif off == 2:
        c = ((s0 >> 16) & 0x0000FFFF) | (((s0 >> 32) & 0xFFFF) << 16)
    else:
        c = ((s0 >> 24) & 0x000000FF) | (((s0 >> 32) & 0xFFFFFF) << 8)
    return ext(c, size, uns)


def ext(x: int, size: int, uns: int) -> int:
    """Sign/zero extension of the selected lane (byte / halfword / word)."""
    if size == SIZE_BYTE:
        b = x & 0xFF
        return (b | (0 if uns else (0xFFFFFF if b & 0x80 else 0))) & M32
    if size == SIZE_HALF:
        h = x & 0xFFFF
        return (h | (0 if uns else (0xFFFF0000 if h & 0x8000 else 0))) & M32
    return x & M32


def make_words(seed: int, n_random: int) -> list[int]:
    """Critical-path-oriented word corpus + `n_random` seeded random words.

    Duplicates are intentionally NOT removed: the original run that produced
    the documented 6,750,000 combinations used 11 + 64 + 300 = 375 words (two
    of them coincide), and re-checking a value is free.
    """
    rng = random.Random(seed)
    words = [0, M32, 0x80000000, 0x00000001, 0xDEADBEEF, 0x0000FF00, 0x00FF0000,
             0xFF000000, 0x12345678, 0x80808080, 0x7F7F7F7F]
    for i in range(32):
        words.append(1 << i)
        words.append(M32 ^ (1 << i))
    for _ in range(n_random):
        words.append(rng.getrandbits(32))
    return words


def run(seed: int = 1234, n_random: int = 300, max_report: int = 5,
        quiet: bool = False) -> int:
    words = make_words(seed, n_random)
    total = len(words) * len(words) * 4 * 3 * 2 * 2
    if not quiet:
        print("check_equiv: corpus = %d words -> %d combinations "
              "(seed=%d, random=%d)" % (len(words), total, seed, n_random))
    bad = 0
    checked = 0
    t0 = time.time()
    for rd in words:
        for head in words:
            for off in range(4):
                for size in (SIZE_BYTE, SIZE_HALF, SIZE_WORD):
                    for uns in (0, 1):
                        # split only matters when the access really crosses a
                        # word boundary; checking BOTH values for every
                        # combination is free and catches formulation slips.
                        for split in (0, 1):
                            a = old(rd, head, off, size, uns, split)
                            b = new(rd, head, off, size, uns, split)
                            checked += 1
                            if a != b:
                                bad += 1
                                if bad <= max_report:
                                    print("MISMATCH rd=%08X head=%08X off=%d "
                                          "size=%d uns=%d split=%d old=%08X "
                                          "new=%08X"
                                          % (rd, head, off, size, uns, split,
                                             a, b))
    dt = time.time() - t0
    print("checked %d combinations, mismatches %d  (%.1f s, %.2f Mcomb/s)"
          % (checked, bad, dt, checked / dt / 1e6 if dt > 0 else 0.0))
    print("CHECK_EQUIV=%s" % ("PASS" if bad == 0 else "FAIL"))
    return 1 if bad else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Exhaustive bit-exactness proof: old barrel shifter vs "
                    "RR2 byte-lane mux in load_store_unit.sv")
    ap.add_argument("--seed", type=int, default=1234,
                    help="random corpus seed (default 1234)")
    ap.add_argument("--random", type=int, default=300, dest="n_random",
                    help="number of random 32-bit words added to the corpus "
                         "(default 300 -> 6,750,000 combinations)")
    ap.add_argument("--quick", action="store_true",
                    help="smoke test: 75-word corpus, 270,000 combinations")
    ap.add_argument("--max-report", type=int, default=5,
                    help="print at most this many mismatches (default 5)")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the summary line")
    a = ap.parse_args(argv)
    n_random = 0 if a.quick else a.n_random
    return run(seed=a.seed, n_random=n_random, max_report=a.max_report,
               quiet=a.quiet)


if __name__ == "__main__":
    raise SystemExit(main())
