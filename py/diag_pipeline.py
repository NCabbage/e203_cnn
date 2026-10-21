#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_pipeline.py -- 定位"Python 参考 95% / 板级实测 82%"的差异来源

做法：
  1. 校验 ROM(.coe) 与 C 头文件权重是否一致
  2. 实现 bit-accurate 的"硬件语义"整条流水线
  3. 在 MNIST test set 前 200 张上跑多种消融，和 py/batch_result.csv 对齐
"""
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent          # <repo>/py
REPO = HERE.parent
sys.path.insert(0, str(REPO / "python" / "golden"))
from lenet_golden import LeNetGolden  # noqa: E402

QUANT_SHIFT = 8
N = 200


# ---------------------------------------------------------------- MNIST raw
def read_idx(path: Path):
    with path.open("rb") as f:
        magic = int.from_bytes(f.read(4), "big")
        ndim = magic & 0xFF          # IDX 格式：第 4 个字节才是维数
        dims = [int.from_bytes(f.read(4), "big") for _ in range(ndim)]
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.reshape(dims), magic


def load_test_set():
    raw = HERE / "data" / "MNIST" / "raw"
    imgs, _ = read_idx(raw / "t10k-images-idx3-ubyte")
    labs, _ = read_idx(raw / "t10k-labels-idx1-ubyte")
    return imgs, labs


# ---------------------------------------------------------------- 逐层算子
def conv(x, w, b, shift, relu, sat=True):
    """x[H,W,C] int64, w[K,K,C,OC] int64, b[OC] -> [OH,OW,OC] int64 (硬件语义)"""
    H, W, C = x.shape
    K, _, _, OC = w.shape
    OH, OW = H - K + 1, W - K + 1
    acc = np.zeros((OH, OW, OC), dtype=np.int64)
    for kh in range(K):
        for kw in range(K):
            acc += x[kh:kh + OH, kw:kw + OW, :] @ w[kh, kw]
    acc += b.astype(np.int64)
    v = acc >> shift
    if relu:
        v = np.maximum(v, 0)
    if sat:
        v = np.clip(v, -128, 127)
    return v


def pool_max(x):
    return np.maximum(np.maximum(x[0::2, 0::2], x[0::2, 1::2]),
                      np.maximum(x[1::2, 0::2], x[1::2, 1::2]))


def pool_avg(x):
    s = (x[0::2, 0::2].astype(np.int64) + x[0::2, 1::2] +
         x[1::2, 0::2] + x[1::2, 1::2])
    return np.clip(s >> 2, -128, 127)


# ---------------------------------------------------------------- 流水线
class Pipe:
    def __init__(self, g: LeNetGolden):
        w = g.w
        self.c1w = w["c1_weight_hw"].reshape(5, 5, 1, 6).astype(np.int64)
        self.c1b = w["c1_bias"]
        self.c3w = w["c3_weight_hw"].reshape(5, 5, 6, 16).astype(np.int64)
        self.c3b = w["c3_bias"]
        # C5：用完整 c5 权重（板子 LeNet_oneP 是一次 120-OC 调用）
        self.c5w = w["c5_weight_hw"].reshape(4, 4, 16, 120).astype(np.int64)
        self.c5b = w["c5_bias"]
        self.f6w = w["f6_weight_hw"].reshape(1, 1, 120, 84).astype(np.int64)
        self.f6b = w["f6_bias"]
        self.outw = w["out_weight_hw"].reshape(1, 1, 84, 10).astype(np.int64)
        self.outb = w["out_bias"]

    def scores(self, img_u8, pool="max", out_quant=True, layers=None):
        x = ((img_u8.astype(np.int64) * 100) // 255)[:, :, None]
        c1 = conv(x, self.c1w, self.c1b, QUANT_SHIFT, True)
        pf = pool_max if pool == "max" else pool_avg
        s2 = pf(c1)
        c3 = conv(s2, self.c3w, self.c3b, QUANT_SHIFT, True)
        s4 = pf(c3)
        c5 = conv(s4, self.c5w, self.c5b, QUANT_SHIFT, True)[0, 0]
        f6 = conv(c5.reshape(1, 1, 120), self.f6w, self.f6b, QUANT_SHIFT, True)[0, 0]
        out = conv(f6.reshape(1, 1, 84), self.outw, self.outb,
                   QUANT_SHIFT, False, sat=out_quant)[0, 0]
        if layers is not None:
            layers.update(c1=c1, s2=s2, c3=c3, s4=s4, c5=c5, f6=f6, out=out)
        return out


# ---------------------------------------------------------------- ROM 校验
def pack_tap(words, w_flat, K, C, OC, base):
    """按 py/main.py 的 pack_layer_words_tap_order 打回 ROM，和 .coe 比对"""
    KKC = K * K * C
    ntg = (KKC + 3) // 4
    for oc in range(OC):
        for tg in range(ntg):
            word = 0
            for lane in range(4):
                tap = tg * 4 + lane
                if tap < KKC:
                    kh, rem = divmod(tap, K * C)
                    kw, c = divmod(rem, C)
                    idx = ((kh * K + kw) * C + c) * OC + oc
                    val = int(w_flat[idx]) & 0xFF
                else:
                    val = 0
                word |= val << (8 * lane)
            words[base + oc * ntg + tg] = word


def load_coe(path: Path):
    txt = path.read_text(encoding="ascii", errors="replace")
    body = txt.split("memory_initialization_vector=", 1)[1]
    body = body.replace(";", " ").replace(",", " ")
    toks = [t for t in body.split() if t]
    return np.array([int(t, 16) for t in toks], dtype=np.uint64)


def check_rom(g: LeNetGolden):
    print("=" * 78)
    print("① ROM 校验：.coe 内容 是否 == C 头文件权重（按 tap 顺序打包）")
    print("=" * 78)
    LAYERS = [("c1", "c1_weight_hw", 5, 1, 6, 0),
              ("c3", "c3_weight_hw", 5, 6, 16, 42),
              ("c5", "c5_weight_hw", 4, 16, 120, 650),
              ("f6", "f6_weight_hw", 1, 120, 84, 8330),
              ("out", "out_weight_hw", 1, 84, 10, 10850)]
    expect = {}
    for name, key, K, C, OC, base in LAYERS:
        pack_tap(expect, g.w[key].astype(np.int8).ravel(), K, C, OC, base)

    # Sobel 段
    gx = [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]]
    gy = [[-1, -2, -1], [0, 0, 0], [1, 2, 1]]
    for oc, kern in enumerate((gx, gy)):
        flat = [kern[a][b] for a in range(3) for b in range(3)]
        for tg in range(3):
            word = 0
            for lane in range(4):
                tap = tg * 4 + lane
                val = (flat[tap] & 0xFF) if tap < 9 else 0
                word |= val << (8 * lane)
            expect[11060 + oc * 3 + tg] = word

    for path in [REPO / "rom_all_tap.coe",
                 REPO / "prj" / "e203_zynq.ip_user_files" / "mem_init_files" / "rom_all_tap.coe",
                 HERE / "rom_all_tap.coe"]:
        if not path.exists():
            print(f"  MISSING {path}")
            continue
        words = load_coe(path)
        n = min(len(words), 11066)
        bad = [i for i in range(n) if i in expect and int(words[i]) != expect[i]]
        missing = [i for i in expect if i >= len(words)]
        print(f"  {str(path.relative_to(REPO)):<58} words={len(words):>6} "
              f"mismatch={len(bad):>4} missing={len(missing)}"
              f"{'  <-- 不一致' if (bad or missing) else '  OK'}")
        for i in bad[:5]:
            print(f"       addr {i}: coe={int(words[i]):08x} expect={expect[i]:08x}")

    # hex（若 Vivado 实际用的是 $readmemh）
    for path in [REPO / "python" / "golden" / "rom_all_tap.hex", HERE / "rom_all_tap.hex"]:
        if not path.exists():
            continue
        toks = [int(t, 16) for t in path.read_text().split()]
        n = min(len(toks), 11066)
        bad = [i for i in range(n) if i in expect and toks[i] != expect[i]]
        print(f"  {str(path.relative_to(REPO)):<58} words={len(toks):>6} "
              f"mismatch={len(bad):>4}{'  <-- 不一致' if bad else '  OK'}")
        for i in bad[:5]:
            print(f"       addr {i}: hex={toks[i]:08x} expect={expect[i]:08x}")


# ---------------------------------------------------------------- 主实验
def main():
    g = LeNetGolden()
    check_rom(g)

    imgs, labs = load_test_set()
    n = min(N, len(imgs))
    pipe = Pipe(g)

    csv = np.genfromtxt(HERE / "batch_result.csv", delimiter=",", names=True,
                        dtype=None, encoding="ascii")
    csv_idx = csv["idx"].astype(int)
    csv_lab = csv["label"].astype(int)
    csv_pred = csv["pred"].astype(int)
    csv_acc = (csv_lab == csv_pred).mean()
    print()
    print("=" * 78)
    print(f"② 板级实测（batch_result.csv）：{int((csv_lab==csv_pred).sum())}/{len(csv_lab)}"
          f" = {csv_acc*100:.1f}%   （前 100 张 {int((csv_lab[:100]==csv_pred[:100]).sum())}/100）")
    print("=" * 78)

    variants = {
        "HW语义 / max池化 / OUT量化(实机)": dict(pool="max", out_quant=True),
        "max池化 / OUT不量化(Py参考)": dict(pool="max", out_quant=False),
        "平均池化 / OUT量化": dict(pool="avg", out_quant=True),
        "平均池化 / OUT不量化": dict(pool="avg", out_quant=False),
    }
    results = {}
    for name, kw in variants.items():
        preds = np.zeros(n, dtype=int)
        for i in range(n):
            preds[i] = int(np.argmax(pipe.scores(imgs[i], **kw)))
        acc = (preds == labs[:n]).mean()
        acc100 = (preds[:100] == labs[:100]).mean()
        agree = (preds == csv_pred).mean()
        results[name] = preds
        print(f"  {name:<34} acc={acc*100:5.1f}%  前100={acc100*100:5.1f}%  "
              f"与板级一致={agree*100:5.1f}%")

    # 逐张对比：板级 vs HW语义 vs Py参考
    hw = results["HW语义 / max池化 / OUT量化(实机)"]
    py = results["max池化 / OUT不量化(Py参考)"]
    print()
    print("=" * 78)
    print("③ 分歧明细（板级 != HW语义 的样本）")
    print("=" * 78)
    diff = np.nonzero(hw != csv_pred)[0]
    print(f"  共 {len(diff)} 张不同：{diff[:40].tolist()}")
    for i in diff[:12]:
        print(f"   idx={i:3d} label={labs[i]} 板级={csv_pred[i]} HW语义={hw[i]} Py参考={py[i]}")
    print()
    print(f"  「板级 vs Py参考」共 {(py != csv_pred).sum()} 张不同")
    print(f"  「板级 vs HW语义」共 {(hw != csv_pred).sum()} 张不同")


if __name__ == "__main__":
    main()
