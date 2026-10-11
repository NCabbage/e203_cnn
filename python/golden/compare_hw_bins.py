#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare JTAG-dumped LeNet layer memories with the golden tensors."""
from __future__ import annotations
import re
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent


def load_golden(name: str, golden_dir: Path):
    p = golden_dir / f"{name.lower()}.txt"
    lines = p.read_text(encoding="utf-8").splitlines()
    shape = tuple(int(x) for x in re.findall(r"\d+", lines[0]))
    vals = np.array([int(x) for x in lines[1:] if x.strip() != ""], dtype=np.int64)
    return shape, vals


def compare(name, hw_arr, golden_dir: Path, golden_name=None):
    golden_name = golden_name or name
    gshape, gvals = load_golden(golden_name, golden_dir)
    hw = np.asarray(hw_arr, dtype=np.int64).ravel()
    g = gvals.ravel()
    assert len(hw) == len(g), f"{name}: size hw={len(hw)} golden={len(g)}"
    diff = np.abs(hw - g)
    bad = int(np.count_nonzero(diff))
    md = int(diff.max()) if len(diff) else 0
    print(f"{name:4s} shape_golden={gshape}  mismatch={bad:5d}  max_abs_diff={md:3d}  {'PASS' if bad == 0 else 'FAIL'}")
    if bad:
        for idx in np.nonzero(diff)[0][:8]:
            print(f"      idx={idx} golden={int(g[idx])} hw={int(hw[idx])}")
    return bad == 0


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", default=str(HERE / "examples" / "digit5" / "golden"))
    ap.add_argument("--hw", default=str(HERE / "examples" / "digit5" / "hw"))
    args = ap.parse_args()
    GOLD = Path(args.golden)
    HW = Path(args.hw)

    c1 = np.fromfile(HW / "c1_hw.bin", dtype=np.int8).reshape(6, 24, 24).transpose(1, 2, 0)
    s2 = np.fromfile(HW / "s2_hw.bin", dtype=np.int8).reshape(6, 12, 12).transpose(1, 2, 0)
    c3 = np.fromfile(HW / "c3_hw.bin", dtype=np.int8).reshape(16, 8, 8).transpose(1, 2, 0)
    s4 = np.fromfile(HW / "s4_hw.bin", dtype=np.int8).reshape(16, 4, 4).transpose(1, 2, 0)
    c5 = np.fromfile(HW / "c5_hw.bin", dtype=np.int8).reshape(120)
    f6 = np.fromfile(HW / "f6_hw.bin", dtype=np.int8).reshape(84)
    out = np.fromfile(HW / "out_hw.bin", dtype="<i4").reshape(10)

    print("=" * 76)
    print("LeNet layer comparison: JTAG hardware memory dump vs golden model")
    print("=" * 76)
    ok = True
    ok &= compare("C1", c1, GOLD)
    ok &= compare("S2", s2, GOLD)
    ok &= compare("C3", c3, GOLD)
    ok &= compare("S4", s4, GOLD)
    ok &= compare("C5", c5, GOLD)
    ok &= compare("F6", f6, GOLD)
    ok &= compare("OUT", out, GOLD)
    print("=" * 76)
    print("OVERALL:", "PASS" if ok else "FAIL")
    print("=" * 76)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())