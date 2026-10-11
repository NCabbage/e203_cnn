#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_wbase_tap.py -- 生成 A 侧权重 BRAM 版本使用的 rom_all_tap.hex

布局：
  word_addr = w_base + oc * num_tap_groups + tap_group
  32bit word:
    [7:0]   = tap_group*4 + 0 权重
    [15:8]  = tap_group*4 + 1 权重
    [23:16] = tap_group*4 + 2 权重
    [31:24] = tap_group*4 + 3 权重

用法：
  cd python/golden
  python export_wbase_tap.py
"""
from __future__ import annotations
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from lenet_golden import parse_c_arrays, REPO  # noqa: E402

WEIGHTS_H = REPO / "LeNet_hw" / "include" / "lenet_weights_hw.h"
OUT_HEX = HERE / "rom_all_tap.hex"

# (name, weights_key, tap_total, oc, base)
LAYERS = [
    ("C1",  "c1_weight_hw",  5*5*1,     6,   0),
    ("C3",  "c3_weight_hw",  5*5*6,    16,  42),
    ("C5",  "c5_weight_hw",  4*4*16,  120, 650),
    ("F6",  "f6_weight_hw",  1*1*120,  84, 8330),
    ("OUT", "out_weight_hw", 1*1*84,   10, 10850),
]
TOTAL_WORDS = 11060


def pack_layer(w, tap_total, oc, groups, base, words):
    wf = w.ravel()
    assert len(wf) == tap_total * oc, f"weight size mismatch: {len(wf)} != {tap_total}*{oc}"
    for o in range(oc):
        for g in range(groups):
            word = 0
            for lane in range(4):
                tap = g * 4 + lane
                val = int(wf[tap * oc + o]) if tap < tap_total else 0
                word |= (val & 0xFF) << (8 * lane)
            words[base + o * groups + g] = word


def main():
    arrays = parse_c_arrays(WEIGHTS_H)
    words = [0] * TOTAL_WORDS
    for name, key, tap_total, oc, base in LAYERS:
        groups = (tap_total + 3) // 4
        if key not in arrays:
            raise SystemExit(f"missing weight array: {key}")
        pack_layer(arrays[key], tap_total, oc, groups, base, words)
        print(f"{name:4s} base={base:5d} groups={groups:3d} oc={oc:3d} words={groups*oc:5d} end={base+groups*oc:5d}")

    with OUT_HEX.open("w", encoding="ascii") as f:
        for w in words:
            f.write(f"{w & 0xFFFFFFFF:08x}\n")
    print(f"WROTE {OUT_HEX}  words={len(words)}")


if __name__ == "__main__":
    main()