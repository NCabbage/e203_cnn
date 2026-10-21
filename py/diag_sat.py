#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_sat.py -- 各层激活饱和率 / OUT 层截断统计（解释 max 与 avg 的差异）"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from diag_weights import parse_hw, read_idx, conv  # noqa: E402

QUANT_SHIFT = 8
N = 200


def trace(img, w, b, pool):
    st = {}
    x = ((img.astype(np.int64) * 100) // 255)[:, :, None]
    c1 = conv(x, w["c1"], b["c1"], QUANT_SHIFT, True)
    st["C1"] = c1
    if pool == "max":
        f = lambda t: np.maximum(np.maximum(t[0::2, 0::2], t[0::2, 1::2]),
                                 np.maximum(t[1::2, 0::2], t[1::2, 1::2]))
    else:
        f = lambda t: np.clip((t[0::2, 0::2] + t[0::2, 1::2] + t[1::2, 0::2]
                               + t[1::2, 1::2]) >> 2, -128, 127)
    st["S2"] = f(c1)
    c3 = conv(st["S2"], w["c3"], b["c3"], QUANT_SHIFT, True)
    st["C3"] = c3
    st["S4"] = f(c3)
    c5 = conv(st["S4"], w["c5"], b["c5"], QUANT_SHIFT, True)[0, 0]
    st["C5"] = c5
    f6 = conv(c5.reshape(1, 1, 120), w["f6"], b["f6"], QUANT_SHIFT, True)[0, 0]
    st["F6"] = f6
    acc = np.zeros((1, 1, 10), dtype=np.int64)
    for kh in range(1):
        for kw in range(1):
            acc += f6.reshape(1, 1, 84) @ w["out"][kh, kw]
    acc = (acc + b["out"])[0, 0]
    st["_raw_out"] = acc
    st["OUT"] = np.clip(acc >> QUANT_SHIFT, -128, 127)
    return st


def main():
    w, b = parse_hw(REPO / "LeNet_oneP" / "include" / "lenet_weights_hw.h")
    raw = HERE / "data" / "MNIST" / "raw"
    imgs = read_idx(raw / "t10k-images-idx3-ubyte")
    labs = read_idx(raw / "t10k-labels-idx1-ubyte")
    n = min(N, len(imgs))

    for pool in ("max", "avg"):
        tot = {}
        ties = 0
        argdiff = 0
        for i in range(n):
            st = trace(imgs[i], w, b, pool)
            for k in ("C1", "S2", "C3", "S4", "C5", "F6", "OUT"):
                a = st[k]
                d = tot.setdefault(k, {"hi": 0, "zero": 0, "cnt": 0, "mx": 0})
                d["hi"] += int((a >= 127).sum())
                d["zero"] += int((a == 0).sum())
                d["cnt"] += a.size
                d["mx"] = max(d["mx"], int(a.max()))
            o = st["OUT"]
            if int((o >= 127).sum()) >= 2:
                ties += 1
            if int(np.argmax(o)) != int(np.argmax(st["_raw_out"])):
                argdiff += 1
        print("=" * 78)
        print(f"池化 = {pool}   （板级权重+偏置，{n} 张）")
        print("=" * 78)
        print(f"  {'层':<5}{'上饱和(>=127)占比':>20}{'为0占比':>12}{'最大值':>10}")
        for k in ("C1", "S2", "C3", "S4", "C5", "F6", "OUT"):
            d = tot[k]
            print(f"  {k:<5}{100.0*d['hi']/d['cnt']:>19.2f}%{100.0*d['zero']/d['cnt']:>11.2f}%"
                  f"{d['mx']:>10}")
        print(f"  OUT 层 >=2 个分量被截断到 127 的图片数: {ties}/{n}")
        print(f"  OUT 层 截断后 argmax 与原值 argmax 不同的图片数: {argdiff}/{n}")
        print()


if __name__ == "__main__":
    main()
