#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag_before.py -- 复现"改动前的 95%"这个数字，确认它的口径"""
import pickle
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "python" / "golden"))

QUANT_SHIFT = 8


def read_idx(path):
    with path.open("rb") as f:
        magic = int.from_bytes(f.read(4), "big")
        ndim = magic & 0xFF
        dims = [int.from_bytes(f.read(4), "big") for _ in range(ndim)]
        return np.frombuffer(f.read(), dtype=np.uint8).reshape(dims)


def hw_from_pkl(q):
    w = {}
    w["c1"] = q["c1"]["w"].reshape(6, 1, 5, 5).transpose(2, 3, 1, 0).astype(np.int64)
    w["c3"] = q["c3"]["w"].reshape(16, 6, 5, 5).transpose(2, 3, 1, 0).astype(np.int64)
    t = q["c5"]["w"].reshape(120, 256)
    w5 = np.zeros((4, 4, 16, 120), dtype=np.int64)
    for oc in range(120):
        for c in range(16):
            for kh in range(4):
                for kw in range(4):
                    w5[kh, kw, c, oc] = t[oc, c * 16 + kh * 4 + kw]
    w["c5"] = w5
    w["f6"] = q["f6"]["w"].reshape(84, 120).transpose(1, 0).reshape(1, 1, 120, 84).astype(np.int64)
    w["out"] = q["out"]["w"].reshape(10, 84).transpose(1, 0).reshape(1, 1, 84, 10).astype(np.int64)
    b = {k: q[k]["b"].astype(np.int64) for k in w}
    return w, b


def conv(x, w, b, shift, relu, sat=True):
    H, W, C = x.shape
    K, OC = w.shape[0], w.shape[3]
    OH, OW = H - K + 1, W - K + 1
    acc = np.zeros((OH, OW, OC), dtype=np.int64)
    for kh in range(K):
        for kw in range(K):
            acc += x[kh:kh + OH, kw:kw + OW, :] @ w[kh, kw]
    acc += b
    v = acc >> shift
    if relu:
        v = np.maximum(v, 0)
    if sat:
        v = np.clip(v, -128, 127)
    return v


def run(img, w, b, pool, out_quant):
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
    out = conv(f6.reshape(1, 1, 84), w["out"], b["out"], QUANT_SHIFT, False, out_quant)[0, 0]
    return int(np.argmax(out))


def main():
    with (HERE / "qmodel.pkl").open("rb") as f:
        q = pickle.load(f)
    w, b = hw_from_pkl(q)
    raw = HERE / "data" / "MNIST" / "raw"
    imgs = read_idx(raw / "t10k-images-idx3-ubyte")
    labs = read_idx(raw / "t10k-labels-idx1-ubyte")

    print("=" * 74)
    print("py/qmodel.pkl + py 偏置（= 改动前 CPU 写权重的那套），MNIST test 前 N 张")
    print("=" * 74)
    for pool in ("max", "avg"):
        for oq in (False, True):
            p = np.array([run(imgs[i], w, b, pool, oq) for i in range(200)])
            a200 = (p == labs[:200]).mean()
            a100 = (p[:100] == labs[:100]).mean()
            tag = "OUT不量化(原 infer_int8)" if not oq else "OUT量化(饱和int8)"
            print(f"  {pool}池化 / {tag:<26}: 前100 = {(p[:100]==labs[:100]).sum():>3}/100 "
                  f"= {a100*100:5.1f}%   前200 = {(p==labs[:200]).sum():>3}/200 = {a200*100:5.1f}%")


if __name__ == "__main__":
    main()
