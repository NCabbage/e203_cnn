# 第八章复现验证（2026-10-30）

## 结论

在 PYNQ-Z2（`xc7z020clg400-1`）上完成了第八章所列的工程重建、综合实现、比特流生成、固件编译、仿真回归、上板自检和 1000 张 MNIST 批量验证。功能链路全部通过；严格时序标准仍有一个 `-0.025547 ns` 的 hold 余量缺口，综合资源数据以设计报告原文为准。

## 环境

| 项目 | 配置 |
|---|---|
| 开发板 | PYNQ-Z2，`xc7z020clg400-1` |
| Vivado | 2025.2 |
| CPU / 加速器 | E203 RV32IMAC + INT8 CNN 加速器 |
| 串口 | FTDI Dual RS232，COM9；`LeNet_oneP` 自检 115200，`LeNet_hw` 批量服务器 57600 8N1 |
| JTAG | OpenOCD，E203 IDCODE `0x1e200a6d` |
| 仓库基线 | `bc883a4282e73ed041b4d7fe078fc892fa44b5ae` |

## 复现结果

| 步骤 | 结果 |
|---|---|
| 重建工程 | 无 ERROR |
| 综合 | 0 errors / 0 critical warnings |
| 实现与 DRC | 0 errors；WNS 0.000 ns，WHS -0.025547 ns |
| 比特流 | 生成成功，4,045,684 bytes |
| 软件编译 | `LeNet_oneP.elf`、`LeNet_hw.elf` 均生成成功 |
| 仿真回归 | 9 PASS / 0 FAIL |
| 上板自检 | `Predicted class: 3`，Pool 9/9 PASS，Sobel 48 点 PASS |
| 批量测试 | 989/1000，98.90%，平均 308,644 cycles |

本次 1000 张的 `label/pred` 序列与原基线 `py/batch_result.csv` 逐行一致，差异为 0 行。上板自检 PMU：`MAC=74634 WLOAD=0 XLOAD=6588 BUBBLE=4480 TOTAL=86269 AXIWR=12678`，总周期 `353521`，约 `22 ms`。

## 本次复现修正的问题

1. 仓库 ROM IP 已统一为 Vivado 2025.2、BMG rev12；构建脚本仍会检查版本和 PART，并在新工程中强制重新生成。
2. 仅替换 COE 后直接综合会复用旧 IP DCP。构建流程必须先 `generate_target all -force`，再单独综合 `rom_all_tap_synth_1`，最后综合顶层。
3. ROM 不能直接用 golden 模型的 11066 字整体覆盖。正确布局是：原 LeNet 权重 11060 字保持不变，仅追加 Sobel 的 6 字。`python/golden/assemble_rom_tap_sobel.py` 负责该拼接。
4. ROM 化后 `PMU_WLOAD` 应为 0；`rtl/cnn/cnn_top.v` 已恢复该语义。
5. 原始 `RUN + 784 字节裸流` 在 PYNQ-Z2 长批次中会偶发丢字节并卡在 UART 接收。批量固件保留 `RUN`，正式批测使用 `BEG/DATA/END` 可靠协议；每 128 字节回 ACK，主机超时重传。

## 基线与其他板卡适配

本仓库现在以 **Vivado 2025.2 + PYNQ-Z2（`xc7z020clg400-1`）** 为唯一验收基线。RTL 功能结论可以复用，但时序、资源、引脚约束、IP 生成产物和实测数据必须按其他目标板重新验证，不得直接沿用 PYNQ-Z2 的结果。

移植到其他 Zynq-7020 板时：

1. 以 PYNQ-Z2 工程为源基线，替换目标板 XDC 和时钟/复位约束。
2. 运行 `board/pynqz2/repro_build.tcl`，让 ROM IP 的 PART 跟随目标工程并强制重新生成。
3. 确认 ROM 总字数为 11066，最后 6 字为 `fe0100ff,00ff0200,00000001,00fffeff,02010000,00000001`。
4. 重新执行综合、实现和上板自检；不得跨板引用周期数、时序和 UART 实测结果。

## 仓库内复现入口

```powershell
python python/golden/assemble_rom_tap_sobel.py
$env:E203_XPR = "D:\path\to\e203_zynq.xpr"
vivado -mode batch -source board/pynqz2/repro_build.tcl
$env:E203_BIT = "D:\path\to\aic_rv32.bit"
vivado -mode batch -source board/pynqz2/program_bit.tcl
```

软件侧：先编译 `sdk/LeNet_hw`，用 OpenOCD 下载 ELF；随后运行 `py/uart_batch_mnist.py --port COM9 --count 1000`。脚本会自动下载 MNIST 原始测试集到 `dataset/mnist`。

## 证据文件

- `batch_result.csv`：本次 1000 张逐样本结果
- `batch_1000.log`：本次 1000 张完整串口日志
- `board_selftest.log`：Pool/Sobel/PRED 上板自检日志
- `batch_compat_20.log`：P0 协议修正后的 20/20 回归日志
- `../../sim/run/run_all.ps1`：仿真一键回归入口

