#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diag_weights.py -- 验证"ROM 里的权重"和"板子 C 代码里的权重/偏置"是不是同一套

背景：
  - 权重现在走 BRAM ROM(IP rom_all_tap)，初始化文件 = py/rom_all_tap.coe
  - 偏置仍然走 CPU 写的寄存器，来自 LeNet_oneP/include/lenet_weights_hw.h
  - 这两个文件由不同的导出脚本产生，可能来自不同的训练结果
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "python" / "golden"))
from lenet_golden import parse_c_arrays  # noqa: E402

QUANT_SHIFT = 8
N = 200

LAYERS = [("c1", 5, 1, 6, 0), ("c3", 5, 6, 16, 42), ("c5", 4, 16, 120, 650),
          ("f6", 1, 120, 84, 8330), ("out", 1, 84, 10, 10850)]


def load_mif(path: Path) -> np.ndarray:
    words = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and set(line) <= {"0", "1"}:
            words.append(int(line, 2))
    return np.array(words, dtype=np.uint64)


def unpack_rom(rom, key):
    """按硬件寻址把 ROM 还原成 w[kh,kw,c,oc]"""
    for name, K, C, OC, base in LAYERS:
        if name != key:
            continue
        ntg = (K * K * C + 3) // 4
        w = np.zeros((K, K, C, OC), dtype=np.int64)
        for oc in range(OC):
            for tg in range(ntg):
                word = int(rom[base + oc * ntg + tg])
                for lane in range(4):
                    tap = tg * 4 + lane
                    if tap >= K * K * C:
                        continue
                    val = (word >> (8 * lane)) & 0xFF
                    if val >= 128:
                        val -= 256
                    kh, rem = divmod(tap, K * C)
                    kw, c = divmod(rem, C)
                    w[kh, kw, c, oc] = val
        return w
    raise KeyError(key)


def pack_tap(w, K, C, OC):
    """按 py/main.py 的 tap 顺序打包，用于反查 ROM 来源"""
    KKC = K * K * C
    ntg = (KKC + 3) // 4
    out = []
    flat = w.reshape(-1)          # [KH][KW][C][OC]
    for oc in range(OC):
        for tg in range(ntg):
            word = 0
            for lane in range(4):
                tap = tg * 4 + lane
                if tap < KKC:
                    val = int(flat[tap * OC + oc]) & 0xFF
                else:
                    val = 0
                word |= val << (8 * lane)
            out.append(word)
    return np.array(out, dtype=np.uint64)


def parse_hw(path: Path):
    a = parse_c_arrays(path)
    w = {
        "c1": a["c1_weight_hw"].astype(np.int64).reshape(5, 5, 1, 6),
        "c3": a["c3_weight_hw"].astype(np.int64).reshape(5, 5, 6, 16),
        "c5": a["c5_weight_hw"].astype(np.int64).reshape(4, 4, 16, 120),
        "f6": a["f6_weight_hw"].astype(np.int64).reshape(1, 1, 120, 84),
        "out": a["out_weight_hw"].astype(np.int64).reshape(1, 1, 84, 10),
    }
    b = {k: a[f"{k}_bias"] for k in w}
    return w, b


def read_idx(path: Path):
    with path.open("rb") as f:
        magic = int.from_bytes(f.read(4), "big")
        ndim = magic & 0xFF
        dims = [int.from_bytes(f.read(4), "big") for _ in range(ndim)]
        data = np.frombuffer(f.read(), dtype=np.uint8)
    return data.reshape(dims)


def conv(x, w, b, shift, relu, sat=True):
    H, W, C = x.shape
    K = w.shape[0]
    OC = w.shape[3]
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


def predict(img, w, b, pool="max"):
    x = ((img.astype(np.int64) * 100) // 255)[:, :, None]
    c1 = conv(x, w["c1"], b["c1"], QUANT_SHIFT, True)
    pf = (lambda t: np.maximum(np.maximum(t[0::2, 0::2], t[0::2, 1::2]),
                               np.maximum(t[1::2, 0::2], t[1::2, 1::2])))
    if pool == "avg":
        def pf(t):
            return np.clip((t[0::2, 0::2] + t[0::2, 1::2] + t[1::2, 0::2]
                            + t[1::2, 1::2]) >> 2, -128, 127)
    s2 = pf(c1)
    c3 = conv(s2, w["c3"], b["c3"], QUANT_SHIFT, True)
    s4 = pf(c3)
    c5 = conv(s4, w["c5"], b["c5"], QUANT_SHIFT, True)[0, 0]
    f6 = conv(c5.reshape(1, 1, 120), w["f6"], b["f6"], QUANT_SHIFT, True)[0, 0]
    out = conv(f6.reshape(1, 1, 84), w["out"], b["out"], QUANT_SHIFT, False)[0, 0]
    return int(np.argmax(out))


def main():
    w_py, b_py = parse_hw(HERE / "lenet_weights_hw.h")
    w_brd, b_brd = parse_hw(REPO / "LeNet_oneP" / "include" / "lenet_weights_hw.h")
    rom = load_mif(REPO / "ip" / "rom_all_tap" / "rom_all_tap.mif")

    print("=" * 78)
    print("① 头文件 vs 头文件：py/ 与 板级 include/ 是不是同一套权重")
    print("=" * 78)
    for k in w_py:
        same = np.array_equal(w_py[k], w_brd[k])
        d = int(np.abs(w_py[k] - w_brd[k]).max())
        sb = np.array_equal(b_py[k], b_brd[k])
        print(f"  {k:<4} 权重相同={str(same):<5} 最大差={d:>4}   偏置相同={sb}")
        if not sb:
            print(f"       py  bias[:4]={b_py[k][:4].tolist()}")
            print(f"       板级 bias[:4]={b_brd[k][:4].tolist()}")

    print()
    print("=" * 78)
    print("② ROM(mif) 的来源：是 py/ 权重还是 板级 权重（tap 顺序打包）")
    print("=" * 78)
    for key, K, C, OC, base in LAYERS:
        ntg = (K * K * C + 3) // 4
        n = ntg * OC
        seg = rom[base:base + n]
        p_py = pack_tap(w_py[key], K, C, OC)
        p_brd = pack_tap(w_brd[key], K, C, OC)
        m_py = int((seg != p_py).sum())
        m_brd = int((seg != p_brd).sum())
        print(f"  {key:<4} 与py权重不符={m_py:>5}/{n:<5}  与板级权重不符={m_brd:>5}/{n:<5}"
              f"   -> {'来自 py/' if m_py == 0 else ('来自板级' if m_brd == 0 else '都不是')}")

    print()
    print("=" * 78)
    print("③ ROM 权重 + C 头文件偏置 = 板子实际用的组合，跑 200 张")
    print("=" * 78)
    raw = HERE / "data" / "MNIST" / "raw"
    imgs = read_idx(raw / "t10k-images-idx3-ubyte")
    labs = read_idx(raw / "t10k-labels-idx1-ubyte")
    n = min(N, len(imgs))

    w_rom = {k: unpack_rom(rom, k) for k, *_ in LAYERS}

    csv = np.genfromtxt(HERE / "batch_result.csv", delimiter=",", names=True,
                        dtype=None, encoding="ascii")
    csv_pred = csv["pred"].astype(int)
    csv_lab = csv["label"].astype(int)
    print(f"  板级实测 CSV                          : "
          f"{(csv_lab==csv_pred).sum()}/{len(csv_lab)} = {(csv_lab==csv_pred).mean()*100:.1f}%")

    combos = [
        ("ROM权重 + 板级偏置  (=实机)", w_rom, b_brd),
        ("ROM权重 + py偏置    (=旧模型)", w_rom, b_py),
        ("板级权重 + 板级偏置 (=Python参考)", w_brd, b_brd),
        ("板级权重 + ROM偏置  (混搭反例)", w_brd, b_py),
    ]
    preds = {}
    for name, w, b in combos:
        p = np.array([predict(imgs[i], w, b) for i in range(n)])
        preds[name] = p
        acc = (p == labs[:n]).mean()
        agree = (p == csv_pred).mean()
        print(f"  {name:<36}: {(p==labs[:n]).sum():>3}/{n} = {acc*100:5.1f}%   "
              f"与板级CSV一致率={agree*100:5.1f}%")

    # 逐张对照
    key = "ROM权重 + 板级偏置  (=实机)"
    p = preds[key]
    diff = np.nonzero(p != csv_pred)[0]
    print()
    print(f"  实机组合 vs 板级CSV：{len(diff)} 张不同 -> {diff[:30].tolist()}")


if __name__ == "__main__":
    main()
