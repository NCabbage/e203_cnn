#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lenet_golden.py -- 完整 LeNet-5 INT8 定点黄金模型

数值约定与 int8_conv.py / RTL 一致：
  input scale:  (uint8 * 100) // 255
  conv/FC:      acc = sum(x*w) + bias, y = acc >>> SHIFT, 可选 ReLU, 饱和 int8
  pooling:      2x2 求和后算术右移 2
  C5:           按硬件 32+32+32+24 分段
  F6:           完整 120->84；硬件内部再分 42+42，黄金模型两者等价

用法：
  python lenet_golden.py --sample 5
  python lenet_golden.py --all
  python lenet_golden.py --sample 5 --dump golden_layers
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from int8_conv import conv2d_int8, sat_int8, arith_shift  # noqa: E402

REPO = HERE.parents[1]  # repo root: <repo>/python/golden -> <repo>
WEIGHTS_H = REPO / "LeNet_hw" / "include" / "lenet_weights_hw.h"
C5_SPLIT_H = REPO / "LeNet_hw" / "include" / "lenet_c5_split.h"
MNIST100_H = REPO / "LeNet_hw" / "include" / "mnist_100.h"
MNIST_SAMPLES_H = REPO / "LeNet_oneP" / "include" / "mnist_samples.h"

SHIFT = 8


def parse_c_arrays(path: Path) -> dict[str, np.ndarray]:
    text = path.read_text(encoding="utf-8")
    # 去掉注释，避免注释里的数字被误读
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    pat = re.compile(
        r"static\s+const\s+(int8_t|int32_t|unsigned\s+char)\s+(\w+)\s*"
        r"((?:\[[^\]]*\])+)\s*=\s*\{(.*?)\};",
        re.S,
    )
    out = {}
    for dtype, name, dims_s, body in pat.findall(text):
        dims = [int(x) for x in re.findall(r"\[(\d+)\]", dims_s)]
        nums = [int(x) for x in re.findall(r"-?\d+", body)]
        n = int(np.prod(dims)) if dims else len(nums)
        if len(nums) != n:
            raise ValueError(f"{path.name}:{name}: got {len(nums)} values, expected {n}")
        arr = np.array(nums, dtype=np.int64).reshape(dims)
        if "int8" in dtype:
            arr = arr.astype(np.int8)
        elif "int32" in dtype:
            arr = arr.astype(np.int32)
        else:
            arr = arr.astype(np.uint8)
        out[name] = arr
    return out


def avg_pool2x2(x: np.ndarray) -> np.ndarray:
    H, W, C = x.shape
    assert H % 2 == 0 and W % 2 == 0
    out = np.zeros((H // 2, W // 2, C), dtype=np.int8)
    for oh in range(H // 2):
        for ow in range(W // 2):
            for c in range(C):
                s = (int(x[2*oh,   2*ow,   c]) +
                     int(x[2*oh,   2*ow+1, c]) +
                     int(x[2*oh+1, 2*ow,   c]) +
                     int(x[2*oh+1, 2*ow+1, c]))
                out[oh, ow, c] = sat_int8(arith_shift(s, 2))
    return out


class LeNetGolden:
    def __init__(self):
        w = parse_c_arrays(WEIGHTS_H)
        c5 = parse_c_arrays(C5_SPLIT_H)
        self.w = {**w, **c5}

        # 检查 C5 split 与完整 c5 权重一致
        c5_full = self.w["c5_weight_hw"].reshape(4, 4, 16, 120)
        c5_cat = np.concatenate([
            self.w["c5a_weight_hw"].reshape(4, 4, 16, 32),
            self.w["c5b_weight_hw"].reshape(4, 4, 16, 32),
            self.w["c5c_weight_hw"].reshape(4, 4, 16, 32),
            self.w["c5d_weight_hw"].reshape(4, 4, 16, 24),
        ], axis=3)
        if not np.array_equal(c5_full, c5_cat):
            raise ValueError("C5 split 权重与完整 C5 权重不一致")
        b_cat = np.concatenate([
            self.w["c5a_bias"], self.w["c5b_bias"],
            self.w["c5c_bias"], self.w["c5d_bias"]])
        if not np.array_equal(self.w["c5_bias"], b_cat):
            raise ValueError("C5 split bias 与完整 C5 bias 不一致")

        self.mnist_samples = parse_c_arrays(MNIST_SAMPLES_H)
        m100 = parse_c_arrays(MNIST100_H)
        self.mnist_100 = m100["mnist_100"]
        self.mnist_100_labels = m100["mnist_100_labels"]

    def forward(self, img_u8: np.ndarray):
        x = (img_u8.astype(np.int64) * 100 // 255).astype(np.int8)
        x = x[:, :, None]

        c1 = conv2d_int8(x, self.w["c1_weight_hw"].reshape(5, 5, 1, 6),
                         self.w["c1_bias"], shift=SHIFT, relu=True)
        s2 = avg_pool2x2(c1)
        c3 = conv2d_int8(s2, self.w["c3_weight_hw"].reshape(5, 5, 6, 16),
                         self.w["c3_bias"], shift=SHIFT, relu=True)
        s4 = avg_pool2x2(c3)

        c5a = conv2d_int8(s4, self.w["c5a_weight_hw"].reshape(4, 4, 16, 32),
                          self.w["c5a_bias"], shift=SHIFT, relu=True)
        c5b = conv2d_int8(s4, self.w["c5b_weight_hw"].reshape(4, 4, 16, 32),
                          self.w["c5b_bias"], shift=SHIFT, relu=True)
        c5c = conv2d_int8(s4, self.w["c5c_weight_hw"].reshape(4, 4, 16, 32),
                          self.w["c5c_bias"], shift=SHIFT, relu=True)
        c5d = conv2d_int8(s4, self.w["c5d_weight_hw"].reshape(4, 4, 16, 24),
                          self.w["c5d_bias"], shift=SHIFT, relu=True)
        c5 = np.concatenate([c5a, c5b, c5c, c5d], axis=2).reshape(120)

        f6 = conv2d_int8(c5.reshape(1, 1, 120),
                         self.w["f6_weight_hw"].reshape(1, 1, 120, 84),
                         self.w["f6_bias"], shift=SHIFT, relu=True).reshape(84)
        out = conv2d_int8(f6.reshape(1, 1, 84),
                          self.w["out_weight_hw"].reshape(1, 1, 84, 10),
                          self.w["out_bias"], shift=SHIFT, relu=False).reshape(10)
        pred = int(np.argmax(out))
        return {"input": x[:, :, 0], "c1": c1, "s2": s2, "c3": c3, "s4": s4,
                "c5": c5, "f6": f6, "out": out, "pred": pred}

    def sample_by_digit(self, d: int) -> np.ndarray:
        return self.mnist_samples[f"digit{d}_mnist"]


def dump_tensors(result: dict, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, val in result.items():
        if name == "pred":
            continue
        arr = np.asarray(val)
        p = out_dir / f"{name}.txt"
        with p.open("w", encoding="utf-8") as f:
            f.write(f"shape={arr.shape}\n")
            for v in arr.ravel():
                f.write(f"{int(v)}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=5, help="digit0..digit9 from mnist_samples.h")
    ap.add_argument("--all", action="store_true", help="run 10 images from mnist_100.h")
    ap.add_argument("--dump", type=str, default="", help="dump per-layer tensors to directory")
    args = ap.parse_args()

    g = LeNetGolden()
    if args.all:
        correct = 0
        for i in range(10):
            r = g.forward(g.mnist_100[i])
            label = int(g.mnist_100_labels[i])
            ok = (r["pred"] == label)
            correct += int(ok)
            print(f"[{i}] label={label} pred={r['pred']} {'OK' if ok else 'MISMATCH'}")
            if args.dump:
                dump_tensors(r, Path(args.dump) / f"img{i}")
        print(f"TOTAL {correct}/10")
        return

    d = args.sample
    img = g.sample_by_digit(d)
    r = g.forward(img)
    print(f"sample digit={d}")
    print("shapes:", {k: np.asarray(v).shape for k, v in r.items() if k != 'pred'})
    print("out:", r["out"].tolist())
    print("pred:", r["pred"])
    if args.dump:
        dump_tensors(r, Path(args.dump))


if __name__ == "__main__":
    raise SystemExit(main())