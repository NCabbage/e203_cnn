#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_rom_consistency.py -- 上板前必跑的"权重一致性"闸门

为什么需要它：
  权重走 BRAM ROM（Vivado IP `rom_all_tap`，初始化文件是 .coe/.mif），
  偏置仍然由 CPU 通过 CNN_BIAS 寄存器写入（来自 C 头文件）。
  这两条路是分开生成的，一旦不配套，板子就会出现
  "Python 参考 95%、上板 82%" 这种非常难查的现象。

它检查：
  1. 三份 C 头文件（LeNet_hw / LeNet_oneP / py/）是不是同一套权重和偏置
  2. ROM 的三个副本（IP 的 .mif、仓库根的 .coe、Vivado 工程里的 .coe）
     以及 python/golden/rom_all_tap.hex 的内容，是否等于
     **板级 C 代码实际编译的那份头文件**（LeNet_hw/include/lenet_weights_hw.h）
     按 tap 顺序打包的结果

用法（在仓库根目录）：
    python python/golden/check_rom_consistency.py
退出码 0 = 一致；1 = 不一致（不要综合、不要上板）
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
from lenet_golden import parse_c_arrays  # noqa: E402

# (层, K, C, OC, w_base, 是否为 1x1)
LAYERS = [("c1", 5, 1, 6, 0), ("c3", 5, 6, 16, 42), ("c5", 4, 16, 120, 650),
          ("f6", 1, 120, 84, 8330), ("out", 1, 84, 10, 10850)]
SOBEL_BASE = 11060
TOTAL_WORDS = 11066

HEADERS = {
    "LeNet_hw/include   (板级·基准)":
        REPO / "LeNet_hw" / "include" / "lenet_weights_hw.h",
    "LeNet_oneP/include (板级)":
        REPO / "LeNet_oneP" / "include" / "lenet_weights_hw.h",
    "py/                (本地·未跟踪)":
        REPO / "py" / "lenet_weights_hw.h",
}

ROM_FILES = [
    ("IP rom_all_tap.mif  (综合实际用的)", REPO / "ip" / "rom_all_tap" / "rom_all_tap.mif"),
    ("仓库根 rom_all_tap.coe", REPO / "rom_all_tap.coe"),
    ("Vivado 工程 mem_init_files/rom_all_tap.coe",
     REPO / "prj" / "e203_zynq.ip_user_files" / "mem_init_files" / "rom_all_tap.coe"),
    ("python/golden/rom_all_tap.hex", HERE / "rom_all_tap.hex"),
]


# ------------------------------------------------------------------ 解析
def header_arrays(path: Path) -> dict:
    a = parse_c_arrays(path)
    w, b = {}, {}
    for name, K, C, OC, _ in LAYERS:
        w[name] = a[f"{name}_weight_hw"].astype(np.int64).reshape(K, K, C, OC)
        b[name] = a[f"{name}_bias"].astype(np.int64)
    return {"w": w, "b": b}


def pack_tap_words(w: dict) -> np.ndarray:
    """按硬件寻址 [OC][TAP_GROUP][LANE] 打包，含 Sobel 段，共 11066 字"""
    words = np.zeros(TOTAL_WORDS, dtype=np.uint64)
    for name, K, C, OC, base in LAYERS:
        KKC = K * K * C
        ntg = (KKC + 3) // 4
        flat = w[name].reshape(-1)               # [KH][KW][C][OC]
        for oc in range(OC):
            for tg in range(ntg):
                word = 0
                for lane in range(4):
                    tap = tg * 4 + lane
                    val = int(flat[tap * OC + oc]) & 0xFF if tap < KKC else 0
                    word |= val << (8 * lane)
                words[base + oc * ntg + tg] = word
    # Sobel：oc0=Gx, oc1=Gy, K=3, C=1
    for oc, kern in enumerate(([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]],
                               [[-1, -2, -1], [0, 0, 0], [1, 2, 1]])):
        flat = [kern[kh][kw] for kh in range(3) for kw in range(3)]
        for tg in range(3):
            word = 0
            for lane in range(4):
                tap = tg * 4 + lane
                val = (flat[tap] & 0xFF) if tap < 9 else 0
                word |= val << (8 * lane)
            words[SOBEL_BASE + oc * 3 + tg] = word
    return words


def read_rom(path: Path) -> np.ndarray | None:
    if not path.exists():
        return None
    if path.suffix == ".mif":
        vals = [int(l.strip(), 2) for l in path.read_text().splitlines()
                if l.strip() and set(l.strip()) <= {"0", "1"}]
    elif path.suffix == ".coe":
        txt = path.read_text(errors="replace")
        if "memory_initialization_vector=" not in txt:
            return None
        body = txt.split("memory_initialization_vector=", 1)[1]
        vals = [int(t, 16) for t in body.replace(";", " ").replace(",", " ").split()]
    else:
        vals = [int(t, 16) for t in path.read_text().split()]
    return np.array(vals, dtype=np.uint64)


# ------------------------------------------------------------------ 主流程
def main() -> int:
    print("=" * 78)
    print("① 三份 C 头文件是否同源（权重 + 偏置）")
    print("=" * 78)
    arrs = {}
    for label, path in HEADERS.items():
        if not path.exists():
            print(f"  {label:<34} 不存在")
            continue
        arrs[label] = header_arrays(path)
    base_label = next(iter(arrs))
    base = arrs[base_label]
    ok = True
    for label, a in arrs.items():
        wd = max(int(np.abs(a["w"][k] - base["w"][k]).max()) for k in base["w"])
        bd = max(int(np.abs(a["b"][k] - base["b"][k]).max()) for k in base["b"])
        same = (wd == 0 and bd == 0)
        print(f"  {label:<34} 与基准权重最大差={wd:>4} 偏置最大差={bd:>5} "
              f"{'同一套' if same else '<-- 不是同一套!'}")
        if not same and "py/" not in label:
            ok = False

    print()
    print("=" * 78)
    print(f"② ROM / hex 内容是否 == 基准头文件({base_label.strip()})按 tap 顺序打包")
    print("=" * 78)
    expect = pack_tap_words(base["w"])
    for label, path in ROM_FILES:
        rom = read_rom(path)
        if rom is None:
            print(f"  {label:<46} 缺失/无法解析")
            continue
        n = min(len(rom), TOTAL_WORDS)
        bad = int((rom[:n] != expect[:n]).sum())
        short = TOTAL_WORDS - len(rom)
        flag = "OK" if bad == 0 and short <= 0 else "不一致!"
        print(f"  {label:<46} words={len(rom):>6} 不符={bad:>6} "
              f"缺 Sobel 段={max(short, 0):>3}  {flag}")
        if bad or short > 0:
            ok = False
            for i in np.nonzero(rom[:n] != expect[:n])[0][:3]:
                print(f"        addr {i}: rom={int(rom[i]):08x} 期望={int(expect[i]):08x}")

    print()
    print("=" * 78)
    if ok:
        print("结论：PASS —— 权重(ROM) 与 偏置(C头文件) 配套，可以综合/上板")
    else:
        print("结论：FAIL —— 权重(ROM) 和 偏置(C头文件) 不是同一套，必须重新生成 ROM")
        print("       重新生成：cd python/golden && python export_wbase_tap.py")
        print("       然后更新 rom_all_tap 这个 ROM IP 的初始化文件（.coe/.mif）")
    print("=" * 78)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
