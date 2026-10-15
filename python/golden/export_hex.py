#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
export_hex.py -- 生成 INT8 卷积的测试向量，并导出成 Verilog $readmemh 能直接读的 hex

输出的数据在  python\data\<用例名>\  下，每个用例包含：
    x_w.hex / x_b.hex       输入激活    (w=1值/字, b=4个int8打包/字)
    w_w.hex / w_b.hex       权重
    b.hex                   偏置 (int32, 1值/字)
    expect_w.hex / expect_b.hex   黄金模型输出
    golden.txt              人看的期望输出
    meta.txt                形状与参数（TB 按这个配循环）

hex 约定（$readmemh 直接能用）：
    * 每行一个 32bit 十六进制数，8 位小写，无 0x 前缀
    * _w 结尾：1 个值占 1 个字。int8 做**符号扩展**（-1 -> ffffffff, -128 -> ffffff80）
    * _b 结尾：4 个 int8 打包进 1 个字，**小端**（第 0 个字节在 LSB）

用法：
    python export_hex.py              # 生成默认的小用例集
    python export_hex.py --big        # 额外生成 28x28 MNIST 规模的用例
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from int8_conv import conv2d_int8

SEED = 20260930
OUT_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ------------------------------------------------------------------ hex 写出
def _hex32(v: int) -> str:
    return format(int(v) & 0xFFFFFFFF, "08x")


def write_hex_word(path, values):
    """1 个元素占 1 个 32bit 字（int8 符号扩展 / int32 原样）"""
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for v in np.asarray(values).ravel():
            fh.write(_hex32(int(v)) + "\n")


def write_hex_byte4(path, values, signed=True):
    """4 个 int8 打包成 1 个 32bit 字，小端"""
    vals = [int(v) & 0xFF for v in np.asarray(values).ravel()]
    while len(vals) % 4:
        vals.append(0)
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for i in range(0, len(vals), 4):
            word = vals[i] | (vals[i + 1] << 8) | (vals[i + 2] << 16) | (vals[i + 3] << 24)
            fh.write(format(word & 0xFFFFFFFF, "08x") + "\n")


def dump_case(name, x, w, b, expect, stride, pad, shift, relu, note="", pool=0):
    d = os.path.join(OUT_ROOT, name)
    os.makedirs(d, exist_ok=True)

    write_hex_word(os.path.join(d, "x_w.hex"), x)
    write_hex_byte4(os.path.join(d, "x_b.hex"), x)
    write_hex_word(os.path.join(d, "w_w.hex"), w)
    write_hex_byte4(os.path.join(d, "w_b.hex"), w)
    write_hex_word(os.path.join(d, "b.hex"), b)
    write_hex_word(os.path.join(d, "expect_w.hex"), expect)
    write_hex_byte4(os.path.join(d, "expect_b.hex"), expect)

    H, W, C = x.shape
    K, _, _, OC = w.shape
    OH, OW, OC2 = expect.shape
    with open(os.path.join(d, "meta.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# case        : {name}\n")
        fh.write(f"# note        : {note}\n")
        fh.write(f"X_SHAPE      = {H} {W} {C}\n")
        fh.write(f"W_SHAPE      = {K} {K} {C} {OC}\n")
        fh.write(f"Y_SHAPE      = {OH} {OW} {OC2}\n")
        fh.write(f"STRIDE       = {stride}\n")
        fh.write(f"PAD          = {pad}\n")
        fh.write(f"SHIFT        = {shift}\n")
        fh.write(f"RELU         = {1 if relu else 0}\n")
        fh.write(f"POOL         = {int(pool)}\n")
        fh.write(f"X_COUNT      = {x.size}\n")
        fh.write(f"W_COUNT      = {w.size}\n")
        fh.write(f"B_COUNT      = {b.size}\n")
        fh.write(f"Y_COUNT      = {expect.size}\n")

    # 给 testbench 用的用例参数：params.hex（13 个 32bit 值，$readmemh 直接读）
    # 顺序：H W C K OC STRIDE PAD SHIFT RELU X_COUNT W_COUNT B_COUNT Y_COUNT
    params = [H, W, C, K, OC, stride, pad, shift, (1 if relu else 0),
              int(x.size), int(w.size), int(b.size), int(expect.size), int(pool)]
    with open(os.path.join(d, "params.hex"), "w", encoding="ascii", newline="\n") as fh:
        for v in params:
            fh.write(format(int(v) & 0xFFFFFFFF, "08x") + "\n")
    with open(os.path.join(d, "golden.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"=== {name}  ({note}) ===\n")
        fh.write(f"输入 {x.shape}  权重 {w.shape}  输出 {expect.shape}\n")
        fh.write(f"stride={stride} pad={pad} shift={shift} relu={int(relu)}\n\n")
        fh.write("--- 输入 x ---\n")
        fh.write(np.array2string(x[:, :, 0] if C == 1 else x[..., 0], max_line_width=200) + "\n\n")
        fh.write("--- 权重 w ---\n")
        fh.write(np.array2string(w[:, :, 0, 0] if (C == 1 and OC == 1) else w[..., 0, 0], max_line_width=200) + "\n\n")
        fh.write(f"--- 偏置 b --- {b.tolist()}\n\n")
        fh.write("--- 期望输出 y ---\n")
        fh.write(np.array2string(expect[:, :, 0] if expect.shape[2] == 1 else expect[..., 0], max_line_width=200) + "\n")
        if expect.shape[2] > 1:
            for oc in range(1, expect.shape[2]):
                fh.write(f"--- 期望输出 y[.., .., {oc}] ---\n")
                fh.write(np.array2string(expect[..., oc], max_line_width=200) + "\n")

    print(f"  [{name}] x{x.shape} w{w.shape} -> y{expect.shape}   "
          f"y范围[{expect.min():4d},{expect.max():4d}]   -> {os.path.relpath(d, OUT_ROOT)}")


# ------------------------------------------------------------------ 用例集
def make_small_cases():
    rng = np.random.default_rng(SEED)
    cases = []

    # t1: 最小可跑用例（RTL 上板 first light 用）
    x = np.array([[1, 2, 3, -4],
                  [5, -6, 7, 8],
                  [-9, 10, -11, 12],
                  [13, -14, 15, -16]], np.int8)[:, :, None]
    w = np.array([[1, 0, -1],
                  [2, 0, -2],
                  [1, 0, -1]], np.int8)[:, :, None, None]
    cases.append(("t1_tiny", x, w, np.array([3], np.int64), 1, 0, 0, True,
                  "最小用例 4x4x1 k3 -> 2x2x1，先让数据通路跑起来"))

    # t2: 跨步窗口 stride=2
    x = rng.integers(-128, 128, size=(5, 5, 1), dtype=np.int64).astype(np.int8)
    w = rng.integers(-128, 128, size=(3, 3, 1, 1), dtype=np.int64).astype(np.int8)
    cases.append(("t2_stride2", x, w, np.array([0], np.int64), 2, 0, 0, True,
                  "stride=2 跨步窗口，5x5 -> 2x2"))

    # t3: 饱和测试（全 127 -> 输出必须饱和到 127）
    x = np.full((4, 4, 1), 127, np.int8)
    w = np.full((3, 3, 1, 1), 127, np.int8)
    cases.append(("t3_saturate", x, w, np.array([0], np.int64), 1, 0, 0, True,
                  "全 127 输入+权重 -> 累加 145161 -> 必须饱和到 127"))

    # t4: 多通道多输出
    x = rng.integers(-128, 128, size=(5, 5, 2), dtype=np.int64).astype(np.int8)
    w = rng.integers(-128, 128, size=(3, 3, 2, 2), dtype=np.int64).astype(np.int8)
    cases.append(("t4_multi", x, w, np.array([11, -11], np.int64), 1, 0, 0, True,
                  "C=2 OC=2，5x5 -> 3x3x2，测多通道累加"))

    # t5: 量化右移 + ReLU
    x = rng.integers(-128, 128, size=(6, 6, 2), dtype=np.int64).astype(np.int8)
    w = rng.integers(-128, 128, size=(3, 3, 2, 2), dtype=np.int64).astype(np.int8)
    cases.append(("t5_requant", x, w, np.array([100, -100], np.int64), 1, 0, 4, True,
                  "shift=4 算术右移 + ReLU，测量化与负数右移"))

    # t6: 边界激励（0 / 极值 / 交替）
    x = np.zeros((4, 4, 1), np.int8)
    x[0, :, 0] = -128
    x[3, :, 0] = 127
    x[1, 0, 0] = 127
    x[2, 3, 0] = -128
    base = np.array([[127, -128, 127],
                       [-128, 127, -128],
                       [127, -128, 127]], np.int8)
    w = base[:, :, None, None]
    cases.append(("t6_edge", x, w, np.array([-128], np.int64), 1, 0, 0, False,
                  "边界：0/±极值/交替权重，且关掉 ReLU 看负数饱和"))

    out = []
    for name, x, w, b, st, pd, sh, rl, note in cases:
        y = conv2d_int8(x, w, b, stride=st, pad=pd, shift=sh, relu=rl)
        out.append((name, x, w, b, y, st, pd, sh, rl, note))
    return out


def maxpool2d_int8(x, k=2, stride=2):
    """INT8 2x2 max pooling 黄金实现（逐通道）"""
    H, W, C = x.shape
    OH = (H - k) // stride + 1
    OW = (W - k) // stride + 1
    out = np.zeros((OH, OW, C), np.int8)
    for oh in range(OH):
        for ow in range(OW):
            for c in range(C):
                win = x[oh * stride:oh * stride + k, ow * stride:ow * stride + k, c]
                out[oh, ow, c] = np.int8(win.max())
    return out


def make_pool_case():
    """t8_pool: 2x2 max pooling（硬件 CFG1[20]=1）。6x6x1 -> 3x3x1。
    权重不参与，SHIFT=0/RELU=0，输出即 max 值本身。"""
    x = np.array([[-5, -2,   3,   4,  -5,   6],
                  [-7, -8,  -9,  10,  11, -12],
                  [-13, 14, 15, -16,  17,  18],
                  [19, -20, 21,  22, -23,  24],
                  [25,  26, -27, 28,  29, -30],
                  [31, -32, 33,  34, -35,  36]], np.int8)[:, :, None]
    k, stride = 2, 2
    expect = maxpool2d_int8(x, k, stride)
    w = np.zeros((k, k, 1, 1), np.int8)   # 池化不用权重，占位
    b = np.array([0], np.int64)
    dump_case("t8_pool", x, w, b, expect, stride, 0, 0, 0,
              "2x2 max pooling, 6x6x1 -> 3x3x1 (SHIFT=0 RELU=0, 权重不参与)",
              pool=1)
    return True


def make_big_case():
    rng = np.random.default_rng(SEED + 1)
    # 28x28 灰度（模拟 MNIST 一张图），3x3 卷积 + pad1，4 个输出通道
    x = rng.integers(-128, 128, size=(28, 28, 1), dtype=np.int64).astype(np.int8)
    w = rng.integers(-128, 128, size=(3, 3, 1, 4), dtype=np.int64).astype(np.int8)
    b = np.array([0, 0, 0, 0], np.int64)
    y = conv2d_int8(x, w, b, stride=1, pad=1, shift=0, relu=True)
    return [("t7_mnist28_pad1", x, w, b, y, 1, 1, 0, True,
             "28x28x1 pad1 k3 OC=4 -> 28x28x4，给之后 MNIST demo 用（文件较大）")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--big", action="store_true", help="额外生成 28x28 用例")
    a = ap.parse_args()

    print("=" * 78)
    print("生成 INT8 卷积测试向量 -> " + OUT_ROOT)
    print("=" * 78)
    cases = make_small_cases()
    if a.big:
        cases += make_big_case()

    for name, x, w, b, y, st, pd, sh, rl, note in cases:
        dump_case(name, x, w, b, y, st, pd, sh, rl, note)

    make_pool_case()   # t8_pool: 2x2 max pooling

    with open(os.path.join(OUT_ROOT, "manifest.txt"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("INT8 卷积测试向量清单\n")
        fh.write("hex 约定: _w=1值/字(符号扩展)  _b=4个int8打包/字(小端)\n")
        fh.write("=" * 70 + "\n")
        for name, x, w, b, y, st, pd, sh, rl, note in cases:
            fh.write(f"{name:<22} x{x.shape} w{w.shape} -> y{y.shape} "
                     f"stride={st} pad={pd} shift={sh} relu={int(rl)}\n")
            fh.write(f"{'':<22} {note}\n")
    print("-" * 78)
    print(f"共 {len(cases)} 个用例，清单见 {os.path.join(OUT_ROOT, 'manifest.txt')}")
    print("MANIFEST 已写")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
