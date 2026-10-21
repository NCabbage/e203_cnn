#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_matrix.py -- 权重来源 x 偏置来源 x 池化方式 的完整矩阵（200 张）"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
from diag_weights import (LAYERS, load_mif, unpack_rom, parse_hw,  # noqa: E402
                          read_idx, conv)

QUANT_SHIFT = 8
N = 200


def predict(img, w, b, pool):
    x = ((img.astype(np.int64) * 100) // 255)[:, :, None]
    c1 = conv(x, w["c1"], b["c1"], QUANT_SHIFT, True)
    if pool == "max":
        f = lambda t: np.maximum(np.maximum(t[0::2, 0::2], t[0::2, 1::2]),
                                 np.maximum(t[1::2, 0::2], t[1::2, 1::2]))
    else:
        f = lambda t: np.clip((t[0::2, 0::2] + t[0::2, 1::2] + t[1::2, 0::2]
                               + t[1::2, 1::2]) >> 2, -128, 127)
    c3 = conv(f(c1), w["c3"], b["c3"], QUANT_SHIFT, True)
    c5 = conv(f(c3), w["c5"], b["c5"], QUANT_SHIFT, True)[0, 0]
    f6 = conv(c5.reshape(1, 1, 120), w["f6"], b["f6"], QUANT_SHIFT, True)[0, 0]
    out = conv(f6.reshape(1, 1, 84), w["out"], b["out"], QUANT_SHIFT, False)[0, 0]
    return int(np.argmax(out))


def main():
    _, b_py = parse_hw(HERE / "lenet_weights_hw.h")
    _, b_brd = parse_hw(REPO / "LeNet_oneP" / "include" / "lenet_weights_hw.h")
    rom = load_mif(REPO / "ip" / "rom_all_tap" / "rom_all_tap.mif")
    w_rom = {k: unpack_rom(rom, k) for k, *_ in LAYERS}
    w_brd, _ = parse_hw(REPO / "LeNet_oneP" / "include" / "lenet_weights_hw.h")

    raw = HERE / "data" / "MNIST" / "raw"
    imgs = read_idx(raw / "t10k-images-idx3-ubyte")
    labs = read_idx(raw / "t10k-labels-idx1-ubyte")
    n = min(N, len(imgs))

    print("=" * 92)
    print(f"{'权重来源':<22}{'偏置来源':<18}{'池化':<8}{'前100':>10}{'前200':>12}")
    print("=" * 92)
    for wn, w in (("ROM (py/ 旧权重)", w_rom), ("C头文件 (板级新权重)", w_brd)):
        for bn, b in (("C头文件 (板级)", b_brd), ("py/ 旧偏置", b_py)):
            for pool in ("avg", "max"):
                p = np.array([predict(imgs[i], w, b, pool) for i in range(n)])
                a100 = (p[:100] == labs[:100]).mean() * 100
                a200 = (p == labs[:n]).mean() * 100
                star = ""
                if wn.startswith("ROM") and bn.startswith("C头文件"):
                    star = "   <== 实机现在的组合"
                print(f"{wn:<22}{bn:<18}{pool:<8}{a100:>9.1f}%{a200:>11.1f}%{star}")
            print("-" * 92)


if __name__ == "__main__":
    main()
