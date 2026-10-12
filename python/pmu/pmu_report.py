#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pmu_report.py -- PMU / CPU-CSR 日志解析与可视化（任务 4-3）

支持三种输入，自动识别，可混用：
  1) 上板串口日志（UART）：
       Total cycles: 865337 / Total mul num: 799 / ...
       PMU MAC=12 WLOAD=9 XLOAD=16 BUBBLE=4 TOTAL=17 AXIWR=34
  2) OpenOCD 读寄存器输出：
       0x40000030: 00010000 00000001 00011f2a ...
  3) 人工整理的表格记录：
       0x40000038 PMU_MAC  0x00011f2a  73514  ...

输出：
  <outdir>/pmu_summary.csv        每条记录一行（原始 + 派生指标）
  <outdir>/pmu_report.md          可读报告：表格 + 口径说明 + 图
  <outdir>/fig_pmu_<name>.png     单条记录的 PMU 周期构成饼图
  <outdir>/fig_compare.png        多条记录对比图

用法：
  python pmu_report.py report\\*.txt -o report\\pmu_out
  python pmu_report.py a.txt b.txt -o out --title "LeNet PMU"

口径（重要，不要混加）：
  PMU_MAC    : CNN 处于 S_MAC 的周期数（acc 计算）
  PMU_BUBBLE : S_MAC 中至少 1 个 lane 无效的周期数（是 MAC 的子集）
  PMU_XLOAD  : x_wen 有效周期数（CPU 写 IDATA，发生在推理前）
  PMU_WLOAD  : w_wen 有效周期数（BRAM 固化版恒为 0）
  PMU_TOTAL  : start 拍 + busy 周期（不含 CPU 收尾）
  PMU_AXIWR  : AXI-Lite 写事务数（含配置/输入/bias/启动/OINDEX）
  CSR 侧     : cycles 是端到端计时；mul/load/store/stall 是指令或周期计数
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager

NUM = r"([\d,]+)"

# 0x4000_00xx 偏移 -> 统一字段名
ADDR_MAP = {
    0x30: "version",
    0x34: "pmu_ctrl",
    0x38: "pmu_mac",
    0x3C: "pmu_wload",
    0x40: "pmu_xload",
    0x44: "pmu_bubble",
    0x48: "pmu_total",
    0x4C: "pmu_axiwr",
    0x50: "pmu_version",
}

# 顺序用于报告展示
KEY_ORDER = [
    "predicted", "cycles", "time_ms", "cpu_hz",
    "pmu_mac", "pmu_bubble", "pmu_total", "pmu_xload", "pmu_wload", "pmu_axiwr",
    "pmu_ctrl", "version", "pmu_version",
    "mul", "load", "store", "stall",
]

COL_LABEL = {
    "predicted": "预测类",
    "cycles": "cycles",
    "time_ms": "耗时ms",
    "cpu_hz": "CPU Hz",
    "pmu_mac": "MAC",
    "pmu_bubble": "BUBBLE",
    "pmu_total": "TOTAL",
    "pmu_xload": "XLOAD",
    "pmu_wload": "WLOAD",
    "pmu_axiwr": "AXIWR",
    "pmu_ctrl": "PMU_CTRL",
    "version": "VER",
    "pmu_version": "PMU_VER",
    "mul": "mul",
    "load": "load",
    "store": "store",
    "stall": "stall",
}


def setup_font() -> str:
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "Source Han Sans SC"):
        if cand in names:
            plt.rcParams["font.sans-serif"] = [cand]
            plt.rcParams["axes.unicode_minus"] = False
            return cand
    plt.rcParams["axes.unicode_minus"] = False
    return "default"


def _int(s: str) -> int:
    return int(s.replace(",", "").replace("_", ""))


@dataclass
class Record:
    name: str
    path: Path
    sources: set = field(default_factory=set)
    values: dict = field(default_factory=dict)
    derived: dict = field(default_factory=dict)

    def set(self, key: str, val, source: str, overwrite: bool = False) -> None:
        if overwrite or key not in self.values:
            self.values[key] = val
            self.sources.add(source)


# ------------------------------------------------------------------ 解析器
def parse_uart(text: str, rec: Record) -> bool:
    got = False
    pats = {
        "cycles": r"Total cycles:\s*" + NUM,
        "mul": r"Total mul num:\s*" + NUM,
        "load": r"Total load num:\s*" + NUM,
        "store": r"Total store num:\s*" + NUM,
        "stall": r"Total stall num:\s*" + NUM,
        "time_ms": r"Total time:\s*" + NUM + r"\s*ms",
        "cpu_hz": r"CPU Frequency:?\s*" + NUM + r"\s*Hz",
    }
    for k, p in pats.items():
        m = re.search(p, text)
        if m:
            rec.set(k, _int(m.group(1)), "uart")
            got = True
    m = re.search(r"Predicted class:\s*(-?\d+)", text)
    if m:
        rec.set("predicted", int(m.group(1)), "uart")
        got = True
    m = re.search(
        r"PMU MAC=(\d+)\s+WLOAD=(\d+)\s+XLOAD=(\d+)\s+BUBBLE=(\d+)\s+TOTAL=(\d+)"
        r"(?:\s+AXIWR=(\d+))?", text)
    if m:
        for i, k in enumerate(["pmu_mac", "pmu_wload", "pmu_xload", "pmu_bubble", "pmu_total"]):
            rec.set(k, int(m.group(i + 1)), "uart")
        if m.group(6) is not None:
            rec.set("pmu_axiwr", int(m.group(6)), "uart")
        got = True
    return got


def parse_openocd(text: str, rec: Record) -> bool:
    words = {}
    for m in re.finditer(r"0x([0-9a-fA-F]{8}):\s*((?:[0-9a-fA-F]{8}\s+)+)", text):
        base = int(m.group(1), 16)
        for i, w in enumerate(m.group(2).split()):
            words[base + 4 * i] = int(w, 16)
    got = False
    for off, key in ADDR_MAP.items():
        val = words.get(0x40000000 + off)
        if val is not None:
            rec.set(key, val, "jtag")
            got = True
    return got


def parse_manual(text: str, rec: Record) -> bool:
    got = False
    for m in re.finditer(
            r"0x4000_?00([0-9a-fA-F]{2})\s+\S+\s+0x([0-9a-fA-F]{1,8})", text):
        off = int(m.group(1), 16)
        key = ADDR_MAP.get(off)
        if key:
            rec.set(key, int(m.group(2), 16), "manual")
            got = True
    return got


def derive(rec: Record) -> None:
    v, d = rec.values, rec.derived
    mac, bub, tot = v.get("pmu_mac"), v.get("pmu_bubble"), v.get("pmu_total")
    cyc, stall = v.get("cycles"), v.get("stall")
    if mac and tot:
        d["mac_share"] = mac / tot
        d["busy_share"] = max(0.0, 1.0 - mac / tot)
    if mac and bub is not None:
        d["lane_eff"] = max(0.0, 1.0 - bub / mac)
    if cyc and stall is not None:
        d["stall_share"] = stall / cyc
    if cyc and v.get("load") is not None:
        d["load_per_1k_cyc"] = v["load"] * 1000.0 / cyc
    if cyc and v.get("store") is not None:
        d["store_per_1k_cyc"] = v["store"] * 1000.0 / cyc
    if cyc and v.get("cpu_hz"):
        d["infer_ms"] = cyc / v["cpu_hz"] * 1e3


def load_record(path: Path) -> Record | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    rec = Record(name=path.stem, path=path)
    parse_uart(text, rec)
    parse_openocd(text, rec)
    parse_manual(text, rec)
    if not rec.values:
        return None
    derive(rec)
    return rec


# ------------------------------------------------------------------ 输出
HEX_KEYS = {"version", "pmu_version", "pmu_ctrl"}


def fmt_key(key: str, v) -> str:
    if v is None:
        return "-"
    if key in HEX_KEYS and isinstance(v, int):
        return f"0x{v:08X}"
    return fmt(v)


def fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}"
    return str(v)


def write_csv(recs: list[Record], out: Path) -> None:
    keys = sorted({k for r in recs for k in r.values} |
                  {k for r in recs for k in r.derived})
    cols = [k for k in KEY_ORDER if k in keys] + [k for k in keys if k not in KEY_ORDER]
    with out.open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["record", "sources", "file"] + cols)
        for r in recs:
            row = [r.name, "+".join(sorted(r.sources)), str(r.path)]
            for k in cols:
                if k in r.values:
                    row.append(r.values[k])
                elif k in r.derived:
                    row.append(round(r.derived[k], 6))
                else:
                    row.append("")
            w.writerow(row)


def fig_pmu(rec: Record, outdir: Path) -> Path | None:
    mac, tot = rec.values.get("pmu_mac"), rec.values.get("pmu_total")
    if not mac or not tot:
        return None
    bub = rec.values.get("pmu_bubble")
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4))
    axes[0].pie([mac, max(tot - mac, 0)],
                labels=["MAC 计算周期", "其他 busy 周期"],
                autopct="%1.1f%%", startangle=90,
                colors=["#4C78A8", "#E45756"], textprops={"fontsize": 11})
    axes[0].set_title(f"{rec.name}\nPMU_TOTAL 构成（TOTAL={tot:,}）", fontsize=12)
    if bub is not None and mac:
        axes[1].pie([max(mac - bub, 0), bub],
                    labels=["有效 lane 周期", "MAC 气泡周期"],
                    autopct="%1.1f%%", startangle=90,
                    colors=["#54A24B", "#F58518"], textprops={"fontsize": 11})
        axes[1].set_title(
            f"S_MAC 内 lane 利用率 = {(1 - bub / mac) * 100:.1f}%\n"
            f"(MAC={mac:,}  BUBBLE={bub:,})", fontsize=12)
    fig.tight_layout()
    p = outdir / f"fig_pmu_{rec.name}.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def fig_compare(recs: list[Record], outdir: Path, title: str) -> Path | None:
    csr = [r for r in recs if r.values.get("cycles")]
    pmu = [r for r in recs if r.values.get("pmu_mac")]
    if len(csr) < 2 and len(pmu) < 2:
        return None
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 8))
    ax = axes.ravel()

    if csr:
        names = [r.name for r in csr]
        vals = [r.values["cycles"] for r in csr]
        ax[0].bar(names, vals, color="#4C78A8")
        ax[0].set_title("端到端 cycles")
        for i, v in enumerate(vals):
            ax[0].text(i, v, f"{v:,}", ha="center", va="bottom", fontsize=9)
        ax[0].tick_params(axis="x", rotation=15)

        labels = ["load", "store", "stall"]
        width = 0.8 / len(csr)
        for j, r in enumerate(csr):
            xs = [i + j * width for i in range(len(labels))]
            ys = [r.values.get(k, 0) for k in labels]
            ax[1].bar(xs, ys, width=width, label=r.name)
        ax[1].set_xticks([i + 0.4 - width / 2 for i in range(len(labels))])
        ax[1].set_xticklabels(labels)
        ax[1].set_title("CSR 计数：load / store / stall")
        ax[1].legend(fontsize=8)
    else:
        for a in ax[:2]:
            a.axis("off")

    if pmu:
        names = [r.name for r in pmu]
        eff = [(r.derived.get("lane_eff", 0) * 100) for r in pmu]
        ax[2].bar(names, eff, color="#54A24B")
        ax[2].set_ylim(0, 105)
        ax[2].set_ylabel("%")
        ax[2].set_title("MAC lane 利用率（100% - BUBBLE/MAC）")
        for i, v in enumerate(eff):
            ax[2].text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
        ax[2].tick_params(axis="x", rotation=15)

        shares = [(r.derived.get("mac_share", 0) * 100) for r in pmu]
        ax[3].bar(names, shares, color="#4C78A8")
        ax[3].set_ylim(0, 105)
        ax[3].set_ylabel("%")
        ax[3].set_title("MAC 占 PMU_TOTAL 比例")
        for i, v in enumerate(shares):
            ax[3].text(i, v, f"{v:.1f}%", ha="center", va="bottom", fontsize=9)
        ax[3].tick_params(axis="x", rotation=15)
    else:
        for a in ax[2:]:
            a.axis("off")

    fig.suptitle(title, fontsize=13)
    fig.tight_layout()
    p = outdir / "fig_compare.png"
    fig.savefig(p, dpi=150)
    plt.close(fig)
    return p


def write_report(recs: list[Record], outdir: Path, title: str, figs: dict) -> Path:
    lines = [f"# {title}", ""]
    lines.append(f"记录数：{len(recs)}　生成工具：`python/pmu/pmu_report.py`")
    lines.append("")
    lines.append("## 1. 汇总表")
    lines.append("")
    cols = [k for k in KEY_ORDER if any(k in r.values for r in recs)]
    lines.append("| 记录 | 来源 | " + " | ".join(COL_LABEL.get(k, k) for k in cols) + " |")
    lines.append("|---|---|" + "---|" * len(cols))
    for r in recs:
        row = [r.name, "+".join(sorted(r.sources))]
        row += [fmt_key(k, r.values.get(k)) for k in cols]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    dcols = [k for k in ("mac_share", "lane_eff", "stall_share",
                         "load_per_1k_cyc", "store_per_1k_cyc", "infer_ms")
             if any(k in r.derived for r in recs)]
    if dcols:
        label = {
            "mac_share": "MAC/TOTAL",
            "lane_eff": "lane 利用率",
            "stall_share": "stall/cycles",
            "load_per_1k_cyc": "load 每千周期",
            "store_per_1k_cyc": "store 每千周期",
            "infer_ms": "推算耗时 ms",
        }
        lines.append("## 2. 派生指标")
        lines.append("")
        lines.append("| 记录 | " + " | ".join(label[k] for k in dcols) + " |")
        lines.append("|---|" + "---|" * len(dcols))
        for r in recs:
            row = [r.name]
            for k in dcols:
                v = r.derived.get(k)
                if v is None:
                    row.append("-")
                elif k in ("mac_share", "lane_eff", "stall_share"):
                    row.append(f"{v * 100:.2f}%")
                else:
                    row.append(f"{v:.3f}")
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")

    if figs:
        lines.append("## 3. 图表")
        lines.append("")
        for name, p in figs.items():
            lines.append(f"- `{p.name}`　{name}")
        lines.append("")
        lines.append("```markdown")
        for p in figs.values():
            lines.append(f"![{p.name}]({p.name})")
        lines.append("```")
        lines.append("")

    lines.append("## 4. 口径说明（不可混加）")
    lines.append("")
    lines.append("| 字段 | 含义 |")
    lines.append("|---|---|")
    lines.append("| `PMU_MAC` | CNN 处于 `S_MAC` 的周期数 |")
    lines.append("| `PMU_BUBBLE` | `S_MAC` 中至少 1 个 lane 无效的周期数，是 MAC 的子集 |")
    lines.append("| `PMU_XLOAD` | `x_wen` 有效周期数，发生在推理前的输入装载 |")
    lines.append("| `PMU_WLOAD` | `w_wen` 有效周期数；权重固化到 BRAM 后恒为 0 |")
    lines.append("| `PMU_TOTAL` | `start` 拍 + `busy` 周期，不含 CPU 收尾 |")
    lines.append("| `PMU_AXIWR` | AXI-Lite 写事务数（配置/输入/bias/启动/OINDEX） |")
    lines.append("| `cycles` | CSR 侧端到端计时 |")
    lines.append("| `mul/load/store/stall` | CPU 侧 CSR 指令/周期计数 |")
    lines.append("")
    lines.append("注意：`PMU_*` 是加速器内部口径，`cycles/mul/load/store/stall` 是 CPU 侧口径，两者不能相加。")
    lines.append("")
    p = outdir / "pmu_report.md"
    p.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="PMU / CSR 日志解析与可视化")
    ap.add_argument("logs", nargs="+", help="日志文件路径")
    ap.add_argument("-o", "--out", default="pmu_out", help="输出目录")
    ap.add_argument("--title", default="PMU / CSR 解析报告", help="报告标题")
    ap.add_argument("--no-plot", action="store_true", help="只出表格不出图")
    ap.add_argument("--merge-regex", default=None,
                    help="按正则捕获组合并多条记录，例如 \"^(PMU|ACSR)_\\w+_(\\d{8})\"")
    args = ap.parse_args()

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    setup_font()

    recs: list[Record] = []
    skipped: list[Path] = []
    for a in args.logs:
        p = Path(a)
        if not p.exists():
            skipped.append(p)
            continue
        rec = load_record(p)
        if rec is None:
            skipped.append(p)
            continue
        recs.append(rec)

    if not recs:
        print("没有解析到任何有效记录。请检查输入日志。", file=sys.stderr)
        for s in skipped:
            print(f"  skipped: {s}", file=sys.stderr)
        return 1

    if args.merge_regex:
        rx = re.compile(args.merge_regex)
        merged: dict[str, Record] = {}
        order: list[str] = []
        for r in recs:
            m = rx.search(r.path.stem)
            key = "_".join(g for g in m.groups() if g) if (m and m.groups()) else r.name
            if key not in merged:
                merged[key] = Record(name=key, path=r.path)
                order.append(key)
            tgt = merged[key]
            for k, v in r.values.items():
                tgt.set(k, v, "+".join(sorted(r.sources)))
            tgt.sources |= r.sources
        for key in order:
            merged[key].derived.clear()
            derive(merged[key])
        recs = [merged[k] for k in order]

    figs: dict[str, Path] = {}
    if not args.no_plot:
        for r in recs:
            p = fig_pmu(r, outdir)
            if p:
                figs[f"{r.name} PMU 构成"] = p
        p = fig_compare(recs, outdir, args.title)
        if p:
            figs["多记录对比"] = p

    csv_p = outdir / "pmu_summary.csv"
    write_csv(recs, csv_p)
    md_p = write_report(recs, outdir, args.title, figs)

    cols = [k for k in KEY_ORDER if any(k in r.values for r in recs)]
    print("=" * 96)
    print(args.title)
    print("=" * 96)
    print("| record | source | " + " | ".join(COL_LABEL.get(k, k) for k in cols) + " |")
    print("|---|---|" + "---|" * len(cols))
    for r in recs:
        row = [r.name, "+".join(sorted(r.sources))]
        row += [fmt_key(k, r.values.get(k)) for k in cols]
        print("| " + " | ".join(row) + " |")
    print("=" * 96)
    for r in recs:
        if r.derived:
            ds = ", ".join(f"{k}={v:.4g}" for k, v in r.derived.items())
            print(f"  {r.name}: {ds}")
    if skipped:
        print(f"跳过 {len(skipped)} 个文件：")
        for s in skipped:
            print(f"  - {s}")
    print(f"CSV : {csv_p}")
    print(f"报告: {md_p}")
    for name, p in figs.items():
        print(f"图  : {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
