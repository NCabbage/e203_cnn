#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
int8_conv.py -- INT8 卷积 + ReLU 黄金模型（E203 CNN 外设的软件参考）

【冻结的数值约定】（RTL / Python / 驱动 三方必须一致）
  1. 激活 activation : int8 有符号, -128..127
  2. 权重 weight     : int8 有符号, -128..127
  3. 偏置 bias       : int32
  4. 乘累加          : int32 累加器（K*K*C*127*127 远小于 2^31，不会溢出，脚本会校验）
  5. 量化            : acc = sum(x*w) + bias
                       y   = acc >>> SHIFT        (算术右移, 负数向下取整)
  6. 激活函数        : ReLU（可选） y = max(y, 0)
  7. 输出饱和        : 饱和到 int8  [-128, 127]
  8. 输出 y          : int8

【为什么是"算术右移"】
  Verilog 必须写 >>> (signed 算术右移)，不能写 >>。
  Python 的 >> 对负整数就是"向下取整的算术右移"，语义一致。

用法：
    python int8_conv.py selftest          # 自检：numpy 版 == 纯 python 逐点版
    python int8_conv.py demo              # 打一个 4x4x1 的小例子给人看
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

INT8_MIN, INT8_MAX = -128, 127
INT32_MIN, INT32_MAX = -(2 ** 31), 2 ** 31 - 1


# ---------------------------------------------------------------- 基础工具
def sat_int8(v):
    """饱和到 int8"""
    return int(np.clip(v, INT8_MIN, INT8_MAX))


def sat_int32(v):
    """饱和到 int32（正常题量下不会触发，保险用）"""
    return int(np.clip(v, INT32_MIN, INT32_MAX))


def arith_shift(v: int, shift: int) -> int:
    """算术右移（和 Verilog 的 >>> 一致；Python 的 >> 对负数即向下取整）"""
    if shift <= 0:
        return int(v)
    return int(v) >> int(shift)


# ---------------------------------------------------------------- 主模型
def conv2d_int8(x, w, b=None, stride=1, pad=0, shift=0, relu=True):
    """
    INT8 2D 卷积 + bias + ReLU + 量化 + 饱和

    参数
      x : int8 数组, 形状 [H, W, C]  或 [H, W]（自动视作 C=1）
      w : int8 数组, 形状 [K, K, C, OC] 或 [K, K]（自动视作 C=1, OC=1）
      b : int32 数组, 形状 [OC] 或 None
      stride, pad : 步长 / 零填充
      shift      : 量化右移位数
      relu       : 是否做 ReLU

    返回
      int8 数组, 形状 [OH, OW, OC]
    """
    x = np.asarray(x, dtype=np.int64)
    w = np.asarray(w, dtype=np.int64)
    if x.ndim == 2:
        x = x[:, :, None]
    if w.ndim == 2:
        w = w[:, :, None, None]

    H, W, C = x.shape
    K, K2, C2, OC = w.shape
    assert K == K2, "kernel 必须是方形"
    assert C == C2, f"通道数不匹配: 输入 C={C}, 权重 C={C2}"

    if pad:
        xp = np.zeros((H + 2 * pad, W + 2 * pad, C), dtype=np.int64)
        xp[pad:pad + H, pad:pad + W, :] = x
    else:
        xp = x
    Hp, Wp = xp.shape[0], xp.shape[1]

    OH = (Hp - K) // stride + 1
    OW = (Wp - K) // stride + 1
    assert OH > 0 and OW > 0, "输出尺寸非法，检查 K/stride/pad"

    if b is None:
        b = np.zeros(OC, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)
    assert b.shape[0] == OC

    # 溢出体检：最坏情况 |acc| 上界
    worst = K * K * C * 127 * 127 + int(np.max(np.abs(b))) if OC else 0
    assert worst <= INT32_MAX, f"累加器可能溢出 int32: 最坏 {worst}"

    y = np.zeros((OH, OW, OC), dtype=np.int8)
    for oh in range(OH):
        for ow in range(OW):
            hs, ws = oh * stride, ow * stride
            patch = xp[hs:hs + K, ws:ws + K, :]          # [K,K,C]
            for oc in range(OC):
                acc = int(np.sum(patch * w[:, :, :, oc]))  # 用 int64 精确求和
                acc = sat_int32(acc + int(b[oc]))
                v = arith_shift(acc, shift)
                if relu:
                    v = max(v, 0)
                y[oh, ow, oc] = sat_int8(v)
    return y


def conv2d_naive(x, w, b=None, stride=1, pad=0, shift=0, relu=True):
    """
    纯 Python 逐点实现（不依赖 numpy 的求和路径），用来交叉验证 conv2d_int8。
    两者必须逐点完全相等。
    """
    x = np.asarray(x, dtype=np.int64)
    w = np.asarray(w, dtype=np.int64)
    if x.ndim == 2:
        x = x[:, :, None]
    if w.ndim == 2:
        w = w[:, :, None, None]

    H, W, C = x.shape
    K, _, _, OC = w.shape
    if pad:
        xp = [[[0] * C for _ in range(W + 2 * pad)] for _ in range(H + 2 * pad)]
        for i in range(H):
            for j in range(W):
                for c in range(C):
                    xp[i + pad][j + pad][c] = int(x[i, j, c])
    else:
        xp = x.tolist()

    if b is None:
        b = [0] * OC
    b = [int(v) for v in np.asarray(b).ravel()]

    OH = (len(xp) - K) // stride + 1
    OW = (len(xp[0]) - K) // stride + 1

    out = []
    for oh in range(OH):
        row = []
        for ow in range(OW):
            col = []
            for oc in range(OC):
                acc = 0
                for kh in range(K):
                    for kw in range(K):
                        for c in range(C):
                            acc += xp[oh * stride + kh][ow * stride + kw][c] * int(w[kh, kw, c, oc])
                v = arith_shift(sat_int32(acc + b[oc]), shift)
                if relu:
                    v = max(v, 0)
                col.append(sat_int8(v))
            row.append(col)
        out.append(row)
    return np.array(out, dtype=np.int8)


def conv2d_float_ref(x, w, b=None, stride=1, pad=0, shift=0, relu=True):
    """float 参考（只用来做趋势比对，不做逐点判等）"""
    x = np.asarray(x, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    if x.ndim == 2:
        x = x[:, :, None]
    if w.ndim == 2:
        w = w[:, :, None, None]
    H, W, C = x.shape
    K, _, _, OC = w.shape
    if pad:
        xp = np.zeros((H + 2 * pad, W + 2 * pad, C))
        xp[pad:pad + H, pad:pad + W, :] = x
    else:
        xp = x
    OH = (xp.shape[0] - K) // stride + 1
    OW = (xp.shape[1] - K) // stride + 1
    if b is None:
        b = np.zeros(OC)
    b = np.asarray(b, dtype=np.float64)
    y = np.zeros((OH, OW, OC))
    for oh in range(OH):
        for ow in range(OW):
            p = xp[oh * stride:oh * stride + K, ow * stride:ow * stride + K, :]
            for oc in range(OC):
                v = float(np.sum(p * w[:, :, :, oc]) + b[oc])
                v = v / (2 ** shift) if shift > 0 else v
                if relu:
                    v = max(v, 0.0)
                y[oh, ow, oc] = float(np.clip(v, INT8_MIN, INT8_MAX))
    return y


# ---------------------------------------------------------------- 自检
def _selftest():
    rng = np.random.default_rng(20260930)
    cases = [
        # (名字, H, W, C, K, OC, stride, pad, shift, relu)
        ("tiny 4x4x1 k3", 4, 4, 1, 3, 1, 1, 0, 0, True),
        ("stride2 5x5", 5, 5, 1, 3, 1, 2, 0, 0, True),
        ("multi 5x5x2 k3 oc2", 5, 5, 2, 3, 2, 1, 0, 0, True),
        ("padding 4x4 pad1", 4, 4, 1, 3, 1, 1, 1, 0, True),
        ("requant shift4", 6, 6, 2, 3, 2, 1, 0, 4, True),
        ("no relu", 4, 4, 1, 3, 1, 1, 0, 0, False),
    ]
    ok = True
    print("=" * 78)
    print("INT8 卷积自检：numpy 版  vs  纯 python 逐点版")
    print("=" * 78)
    for name, H, W, C, K, OC, st, pd, sh, rl in cases:
        x = rng.integers(-128, 128, size=(H, W, C), dtype=np.int64).astype(np.int8)
        w = rng.integers(-128, 128, size=(K, K, C, OC), dtype=np.int64).astype(np.int8)
        b = rng.integers(-5000, 5000, size=(OC,), dtype=np.int64)
        a = conv2d_int8(x, w, b, stride=st, pad=pd, shift=sh, relu=rl)
        c = conv2d_naive(x, w, b, stride=st, pad=pd, shift=sh, relu=rl)
        same = np.array_equal(a, c)
        f = conv2d_float_ref(x, w, b, stride=st, pad=pd, shift=sh, relu=rl)
        dmax = float(np.max(np.abs(a.astype(np.float64) - f))) if a.size else 0.0
        ok &= same
        print(f"  {name:<24} out{a.shape}  逐点一致={'OK' if same else 'FAIL'}"
              f"  vs float 最大差={dmax:.1f}")

    # 边界激励：全 -128 / 全 127 / 交替 / 全 0
    print("-" * 78)
    print("边界激励（0 / 极值 / 交替）")
    edges = {
        "all_min(-128)": np.full((4, 4, 1), -128, np.int8),
        "all_max(127)": np.full((4, 4, 1), 127, np.int8),
        "zero": np.zeros((4, 4, 1), np.int8),
        "alt": np.where(np.indices((4, 4, 1)).sum(axis=0) % 2 == 0, 127, -128).astype(np.int8),
    }
    wk = np.full((3, 3, 1, 1), 127, np.int8)
    for name, x in edges.items():
        a = conv2d_int8(x, wk, np.array([0]), relu=True)
        c = conv2d_naive(x, wk, np.array([0]), relu=True)
        same = np.array_equal(a, c)
        ok &= same
        print(f"  {name:<16} 输出范围 [{a.min():4d},{a.max():4d}]  逐点一致={'OK' if same else 'FAIL'}")

    # 饱和验证：强制超出 int8
    x = np.full((3, 3, 1), 127, np.int8)
    w1 = np.full((3, 3, 1, 1), 127, np.int8)
    y_over = conv2d_int8(x, w1, np.array([0]), relu=True)
    y_neg = conv2d_int8(x, w1, np.array([0]), relu=False)   # 全正，不会负
    x_neg = np.full((3, 3, 1), -128, np.int8)
    y_neg2 = conv2d_int8(x_neg, w1, np.array([0]), relu=False)
    print(f"  上饱和: 9*127*127=145161 -> 输出 {int(y_over[0,0,0])} (应为 127)")
    print(f"  下饱和: 9*(-128*127)=-146304 -> 输出 {int(y_neg2[0,0,0])} (应为 -128)")
    ok &= (int(y_over[0, 0, 0]) == 127) and (int(y_neg2[0, 0, 0]) == -128)

    # 算术右移验证（负数是向下取整）
    print(f"  算术右移: -7>>1={arith_shift(-7,1)} (应为 -4, 不是 -3)")
    ok &= arith_shift(-7, 1) == -4

    print("=" * 78)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ---------------------------------------------------------------- demo
def _demo():
    x = np.array([
        [  1,   2,   3,  -4],
        [  5,  -6,   7,   8],
        [ -9,  10, -11,  12],
        [ 13, -14,  15, -16],
    ], dtype=np.int8)[:, :, None]
    w = np.array([
        [ 1,  0, -1],
        [ 2,  0, -2],
        [ 1,  0, -1],
    ], dtype=np.int8)[:, :, None, None]
    b = np.array([3], dtype=np.int64)
    y = conv2d_int8(x, w, b, relu=True)
    print("输入 x (4x4x1):")
    print(x[:, :, 0])
    print("\n权重 w (3x3x1x1):")
    print(w[:, :, 0, 0])
    print(f"\nbias = {b.tolist()}, ReLU = on, shift = 0")
    print(f"输出 y (2x2x1):\n{y[:, :, 0]}")
    print("\n和 float 参考对比：")
    print(conv2d_float_ref(x, w, b, relu=True)[:, :, 0])


def main():
    ap = argparse.ArgumentParser(description="INT8 卷积+ReLU 黄金模型")
    ap.add_argument("cmd", choices=["selftest", "demo"], nargs="?", default="selftest")
    a = ap.parse_args()
    return _selftest() if a.cmd == "selftest" else (_demo() or 0)


if __name__ == "__main__":
    raise SystemExit(main())