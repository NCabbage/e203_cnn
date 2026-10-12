# PMU / CSR 日志解析工具（任务 4-3）

把上板日志里的 PMU 与 CPU-CSR 原始数值自动变成**表格 + CSV + 饼图**，用于多版本对比，
不再手工抄数算占比。

## 文件

| 文件 | 说明 |
|---|---|
| `pmu_report.py` | 解析 + 出图主脚本 |
| 输出目录 `pmu_out/` | `pmu_summary.csv`、`pmu_report.md`、`fig_*.png` |

## 支持的三种输入（自动识别，可混用）

1. **上板串口日志**
   ```text
   Predicted class: 5
   Total cycles: 865337
   Total mul num: 799
   Total load num: 99669
   Total store num: 38752
   Total stall num: 487532
   CPU Frequency: 15937372 Hz
   Total time: 54 ms
   PMU MAC=73514 WLOAD=0 XLOAD=2996 BUBBLE=4480 TOTAL=78217 AXIWR=52161
   ```
2. **OpenOCD 读寄存器输出**
   ```text
   0x40000030: 00010000 00000001 00011f2a 0000ac9e 00000bb4 00001180 00013189 0000cbc1
   0x40000050: 00010000
   ```
3. **人工整理的表格记录**
   ```text
   0x40000038 PMU_MAC   0x00011f2a   73514
   ```

## 用法

```powershell
cd python\pmu

# 单文件
python pmu_report.py ..\..\report\PMU_print_board_uart_bram_weights_20261012.txt -o ..\..\report\pmu_out

# 多个版本对比（UART 与 JTAG 分文件时，用正则把同一版本合并）
python pmu_report.py `
  ..\..\report\PMU_board_uart_LeNet_oneP_explore_20261009.txt `
  ..\..\report\PMU_board_regs_LeNet_oneP_explore_20261009.txt `
  ..\..\report\BRAM_board_uart_bram_weights_20261011.txt `
  -o ..\..\report\pmu_out --merge-regex '^(PMU|ACSR|BRAM)_\w+_(\d{8})' `
  --title "LeNet PMU/CSR 解析报告"

# 只要表格不要图
python pmu_report.py a.txt -o out --no-plot
```

`--merge-regex` 的捕获组用 `_` 拼接作为记录名，同名文件的值互补合并（适合"串口一份 + JTAG 一份"）。

## 派生指标

| 指标 | 定义 |
|---|---|
| `mac_share` | `PMU_MAC / PMU_TOTAL` |
| `lane_eff` | `1 - PMU_BUBBLE / PMU_MAC`，4-lane MAC 阵列利用率 |
| `stall_share` | `stall / cycles` |
| `load_per_1k_cyc` / `store_per_1k_cyc` | 每千周期访存指令数 |
| `infer_ms` | `cycles / CPU 频率` 推算耗时 |

## 口径说明（不可混加）

- `PMU_MAC/BUBBLE/XLOAD/WLOAD/TOTAL/AXIWR` 是**加速器内部口径**
- `cycles/mul/load/store/stall` 是**CPU 侧 CSR 口径**
- `PMU_BUBBLE` 是 `PMU_MAC` 的子集，不能和 `PMU_MAC` 相加
- 权重固化到 BRAM 后 `PMU_WLOAD` 恒为 0，属正常现象

## 已验证实例（2026-10-12）

| 记录 | cycles | MAC | TOTAL | lane 利用率 | stall/cycles |
|---|---:|---:|---:|---:|---:|
| PMU_20261009 | 1,489,676 | 73,514 | 78,217 | 93.91% | - |
| ACSR_20261009 | 1,489,638 | 73,514 | 78,217 | 93.91% | 55.71% |
| BRAM_20261011 | 865,337 | - | - | - | 56.34% |

BRAM_20261011 缺 PMU 数据是因为当时固件还没打印 PMU；`main.c` 已补上
`CNN_WR(CNN_PMU_CTRL, CLEAR|EN)` 与 `PMU MAC=... WLOAD=...` 输出，需重新上板采集。
