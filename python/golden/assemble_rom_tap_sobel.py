#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Assemble LeNet tap-order weights and the 6-word Sobel tail into ROM images."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SRC_COE = ROOT / "py" / "rom_all_tap.coe"
SOBEL_HEX = ROOT / "python" / "golden" / "rom_all_tap.hex"
OUT_COE = ROOT / "rom_all_tap.coe"
OUT_PY_COE = ROOT / "py" / "rom_all_tap.coe"
OUT_HEX = ROOT / "py" / "rom_all_tap.hex"
LENET_WORDS = 11060
TOTAL_WORDS = 11066

def read_hex_words(path):
    words = []
    for line in path.read_text(encoding="ascii").splitlines():
        m = re.fullmatch(r"\s*([0-9a-fA-F]{8})\s*[,;]?\s*", line)
        if m:
            words.append(m.group(1).lower())
    return words

def write_coe(path, words):
    path.write_text(
        "memory_initialization_radix=16;\nmemory_initialization_vector=\n" +
        ",\n".join(words) + ";\n",
        encoding="ascii",
        newline="\n",
    )

def write_hex(path, words):
    path.write_text("\n".join(words) + "\n", encoding="ascii", newline="\n")

lenet = read_hex_words(SRC_COE)
golden = read_hex_words(SOBEL_HEX)
if len(lenet) < LENET_WORDS:
    raise SystemExit(f"LeNet source too short: {len(lenet)} < {LENET_WORDS}")
if len(golden) < TOTAL_WORDS:
    raise SystemExit(f"Golden source too short: {len(golden)} < {TOTAL_WORDS}")
words = lenet[:LENET_WORDS] + golden[LENET_WORDS:TOTAL_WORDS]
assert len(words) == TOTAL_WORDS
for p in (OUT_COE, OUT_PY_COE):
    write_coe(p, words)
write_hex(OUT_HEX, words)
print(f"wrote {TOTAL_WORDS} words: {OUT_COE}, {OUT_PY_COE}, {OUT_HEX}")
print("sobel tail:", ",".join(words[LENET_WORDS:TOTAL_WORDS]))
