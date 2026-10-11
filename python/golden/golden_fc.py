#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
golden_fc.py -- INT8 全连接 + argmax 黄金模型
（任务表 W1 的"顺手"项：全连接 golden 一并导出，MNIST demo 要用）

数值约定与 int8_conv.py 完全一致：
    int8 输入/权重, int32 累加, 可选算术右移, 可选 ReLU, 输出饱和到 int8

用法：
    python golden_fc.py selftest
    python golden_fc.py demo
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from int8_conv import arith_shift, sat_int8, sat_int32


def fc_int8(x, w, b=None, shift=0, relu=False):
    """
    x : int8 [C]  或 [N, C]
    w : int8 [C, OC]        （w[c, oc]）
    b : int32 [OC] 或 None
    返回 y : int8 [OC] 或 [N, OC]
    """
    x = np.asarray(x, dtype=np.int64)
    w = np.asarray(w, dtype=np.int64)
    single = (x.ndim == 1)
    if single:
        x = x[None, :]
    N, C = x.shape
    C2, OC = w.shape
    assert C == C2, f"维度不匹配: x.C={C}, w.C={C2}"
    if b is None:
        b = np.zeros(OC, dtype=np.int64)
    b = np.asarray(b, dtype=np.int64)

    worst = C * 127 * 127 + int(np.max(np.abs(b)))
    assert worst <= 2 ** 31 - 1, f"累加器可能溢出 int32: {worst}"

    y = np.zeros((N, OC), dtype=np.int8)
    for n in range(N):
        for oc in range(OC):
            acc = 0
            for c in range(C):
                acc += int(x[n, c]) * int(w[c, oc])
            v = arith_shift(sat_int32(acc + int(b[oc])), shift)
            if relu:
                v = max(v, 0)
            y[n, oc] = sat_int8(v)
    return y[0] if single else y


def argmax_int8(y):
    """返回最大值所在下标（并列时取最小下标，和 RTL 保持一致）"""
    y = np.asarray(y).ravel()
    best, bi = int(y[0]), 0
    for i in range(1, y.size):
        if int(y[i]) > best:
            best, bi = int(y[i]), i
    return bi, best


def _selftest():
    rng = np.random.default_rng(20260930)
    ok = True
    print("=" * 78)
    print("INT8 全连接自检")
    print("=" * 78)

    for C, OC, N, sh, rl in [(16, 4, 1, 0, False), (64, 10, 3, 4, True), (128, 10, 2, 6, True)]:
        x = rng.integers(-128, 128, size=(N, C), dtype=np.int64).astype(np.int8)
        w = rng.integers(-128, 128, size=(C, OC), dtype=np.int64).astype(np.int8)
        b = rng.integers(-2000, 2000, size=(OC,), dtype=np.int64)
        y = fc_int8(x, w, b, shift=sh, relu=rl)
        # 纯 python 再算一遍交叉验证
        ref = np.zeros_like(y)
        for n in range(N):
            for oc in range(OC):
                acc = sum(int(x[n, c]) * int(w[c, oc]) for c in range(C)) + int(b[oc])
                v = arith_shift(sat_int32(acc), sh)
                if rl:
                    v = max(v, 0)
                ref[n, oc] = sat_int8(v)
        same = np.array_equal(y, ref)
        ok &= same
        print(f"  C={C:<4} OC={OC:<3} N={N}  shift={sh} relu={int(rl)}  "
              f"输出范围[{y.min():4d},{y.max():4d}]  一致={'OK' if same else 'FAIL'}")

    # argmax 并列取小下标
    t = np.array([5, 9, 9, 1], dtype=np.int8)
    i, v = argmax_int8(t)
    print(f"  argmax([5,9,9,1]) = 下标 {i} 值 {v}  (并列取小下标, 应为 1, 9)")
    ok &= (i == 1 and v == 9)

    print("=" * 78)
    print("SELFTEST " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def _demo():
    """模拟 MNIST 最后一层：10 类打分 -> argmax 出类别"""
    rng = np.random.default_rng(7)
    C, OC = 32, 10
    x = rng.integers(-128, 128, size=C, dtype=np.int64).astype(np.int8)
    w = rng.integers(-128, 128, size=(C, OC), dtype=np.int64).astype(np.int8)
    b = rng.integers(-1000, 1000, size=OC, dtype=np.int64)
    y = fc_int8(x, w, b, shift=6, relu=False)
    idx, val = argmax_int8(y)
    print(f"输入 C={C} -> 输出 {OC} 类")
    print("打分:", y.tolist())
    print(f"argmax -> 第 {idx} 类 (分数 {val})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="INT8 全连接 + argmax 黄金模型")
    ap.add_argument("cmd", choices=["selftest", "demo"], nargs="?", default="selftest")
    a = ap.parse_args()
    return _selftest() if a.cmd == "selftest" else _demo()


if __name__ == "__main__":
    raise SystemExit(main())