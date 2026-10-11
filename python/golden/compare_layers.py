#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compare_layers.py -- 比较 UART 逐层 dump 与黄金模型逐层张量

用法：
  python compare_layers.py LeNet_layerdump_uart_20261011.txt --golden golden_layers_digit5_20261011
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np

LAYERS = ["C1", "S2", "C3", "S4", "C5", "F6", "OUT"]


def read_golden(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    shape = None
    vals = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("shape="):
            shape = tuple(int(x) for x in re.findall(r"\d+", line))
        else:
            vals.append(int(line))
    return shape, np.array(vals, dtype=np.int64)


def parse_uart(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = {}
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r"LAYER\s+(\w+)\s+(.*)$", line)
        if not m:
            i += 1
            continue
        name = m.group(1)
        dims = [int(x) for x in re.findall(r"\d+", m.group(2))]
        vals = []
        i += 1
        while i < len(lines):
            s = lines[i].strip()
            if s == f"END {name}":
                break
            if s:
                for tok in s.split():
                    if re.fullmatch(r"-?\d+", tok):
                        vals.append(int(tok))
            i += 1
        out[name] = (dims, np.array(vals, dtype=np.int64))
        i += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("uart")
    ap.add_argument("--golden", required=True)
    args = ap.parse_args()

    uart = parse_uart(Path(args.uart))
    golden_dir = Path(args.golden)
    ok = True
    print("=" * 72)
    print("Layer-by-layer compare: UART hardware dump vs golden model")
    print("=" * 72)
    for name in LAYERS:
        gp = golden_dir / f"{name.lower()}.txt"
        if not gp.exists():
            print(f"{name:4s} SKIP (missing {gp})")
            continue
        gshape, gvals = read_golden(gp)
        if name not in uart:
            print(f"{name:4s} FAIL (not found in UART)")
            ok = False
            continue
        udims, uvals = uart[name]
        if len(gvals) != len(uvals):
            print(f"{name:4s} FAIL size golden={len(gvals)} uart={len(uvals)}")
            ok = False
            continue
        diff = np.abs(gvals - uvals)
        md = int(diff.max()) if len(diff) else 0
        bad = int(np.count_nonzero(diff))
        status = "PASS" if bad == 0 else "FAIL"
        if bad != 0:
            ok = False
        print(f"{name:4s} shape golden={gshape} uart={tuple(udims)}  "
              f"mismatch={bad}  max_abs_diff={md}  {status}")
        if bad:
            for idx in np.nonzero(diff)[0][:8]:
                print(f"      idx={idx} golden={int(gvals[idx])} uart={int(uvals[idx])}")
    print("=" * 72)
    print("OVERALL:", "PASS" if ok else "FAIL")
    print("=" * 72)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())