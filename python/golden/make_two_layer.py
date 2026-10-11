#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_two_layer.py -- 生成"两层连跑"用例的数据（给 sim/tb_w3/tb_two_layer.v 用）

层1: x 8x8x1, k3, OC=2, stride1, pad0, shift0, relu  -> y1 6x6x2
层2: 输入 = y1 (6x6x2), k3, OC=2, stride1, pad0      -> y2 4x4x2

产出（写到 python\data\two_layer\）：
    x.hex  w1.hex  b1.hex  e1.hex     # 层1 输入/权重/偏置/期望输出
    w2.hex b2.hex e2.hex              # 层2 权重/偏置/期望输出（输入就是 e1）
    info.txt                          # 各层形状与元素个数
"""
import os, sys
import numpy as np
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass
from int8_conv import conv2d_int8

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "two_layer")
os.makedirs(OUT, exist_ok=True)
rng = np.random.default_rng(20260930)

def wh(path, vals, sign8=True):
    with open(path, "w", encoding="ascii", newline="\n") as fh:
        for v in np.asarray(vals).ravel():
            fh.write(format(int(v) & 0xFFFFFFFF, "08x") + "\n")

# ---------------- 层1 ----------------
H1,W1,C1,K1,OC1,ST1,PD1,SH1,RL1 = 8,8,1,3,2,1,0,0,1
x1 = rng.integers(-128,128,size=(H1,W1,C1),dtype=np.int64).astype(np.int8)
w1 = rng.integers(-128,128,size=(K1,K1,C1,OC1),dtype=np.int64).astype(np.int8)
b1 = np.array([7,-3],dtype=np.int64)
y1 = conv2d_int8(x1,w1,b1,stride=ST1,pad=PD1,shift=SH1,relu=RL1)

# ---------------- 层2（输入 = 层1 输出）----------------
H2,W2,C2 = y1.shape
K2,OC2,ST2,PD2,SH2,RL2 = 3,2,1,0,0,1
w2 = rng.integers(-128,128,size=(K2,K2,C2,OC2),dtype=np.int64).astype(np.int8)
b2 = np.array([5,-5],dtype=np.int64)
y2 = conv2d_int8(y1,w2,b2,stride=ST2,pad=PD2,shift=SH2,relu=RL2)

wh(os.path.join(OUT,"x.hex"),  x1)
wh(os.path.join(OUT,"w1.hex"), w1)
wh(os.path.join(OUT,"b1.hex"), b1)
wh(os.path.join(OUT,"e1.hex"), y1)
wh(os.path.join(OUT,"w2.hex"), w2)
wh(os.path.join(OUT,"b2.hex"), b2)
wh(os.path.join(OUT,"e2.hex"), y2)

with open(os.path.join(OUT,"info.txt"),"w",encoding="utf-8",newline="\n") as fh:
    fh.write("# 层1\n")
    fh.write(f"H1={H1} W1={W1} C1={C1} K1={K1} OC1={OC1} ST1={ST1} PD1={PD1} SH1={SH1} RL1={RL1}\n")
    fh.write(f"XC1={x1.size} WC1={w1.size} BC1={b1.size} YC1={y1.size}\n")
    fh.write("# 层2（输入 = 层1 输出 y1）\n")
    fh.write(f"H2={H2} W2={W2} C2={C2} K2={K2} OC2={OC2} ST2={ST2} PD2={PD2} SH2={SH2} RL2={RL2}\n")
    fh.write(f"XC2={y1.size} WC2={w2.size} BC2={b2.size} YC2={y2.size}\n")
    fh.write(f"# y1 范围 [{y1.min()},{y1.max()}]  y2 范围 [{y2.min()},{y2.max()}]\n")

print("two_layer 数据已生成:", OUT)
print(f"  层1: x{x1.shape} w{w1.shape} -> y1{y1.shape}  范围[{y1.min()},{y1.max()}]")
print(f"  层2: x{y1.shape} w{w2.shape} -> y2{y2.shape}  范围[{y2.min()},{y2.max()}]")
print(f"  元素数: XC1={x1.size} WC1={w1.size} BC1={b1.size} YC1={y1.size} "
      f"WC2={w2.size} BC2={b2.size} YC2={y2.size}")