# e203_cnn

蜂鸟 E203（RV32IMAC）软核 + ICB/AXI-Lite 桥 + INT8 CNN 硬件加速器，面向 LeNet-5 推理、PMU 性能分析和 PYNQ-Z2 上板验证。

> **完整项目说明：[`PROJECT_GUIDE.md`](PROJECT_GUIDE.md)**
>
> 包含系统架构、RTL 模块、寄存器映射、软件/UART 协议、PMU/Pool/Sobel、黄金模型、Vivado 构建、上板冷启动、仿真与 1000 张验证口径、证据索引和交接步骤。

## 当前正式基线

| 项目 | 配置 |
|---|---|
| 开发板 | PYNQ-Z2，`xc7z020clg400-1` |
| 工具 | Vivado 2025.2，Nuclei RISC-V GCC / OpenOCD |
| CPU | E203 RV32IMAC，16 MHz |
| 加速器 | 4-lane INT8 MAC，11066 字权重 ROM，PMU，2×2 max Pool，Sobel |
| 正式批测 | MNIST 1000 张，989/1000，98.90% |
| 已知时序边界 | WNS 0.000 ns，WHS -0.025547 ns |

## 双人协作

| 代号 | 板卡 | 芯片 |
|---|---|---|
| **A** | 征服者 7020 / AX7020 | XC7Z020-1CLG400C |
| **B** | PYNQ-Z2 | XC7Z020-1CLG400C |

两块板为同一 FPGA 型号，RTL 功能结论可互通；bit、XDC、PLL、复位极性、串口、周期数、资源和时序结果必须分别验证。详细规则见 [`docs/porting/00_双板差异规范.md`](docs/porting/00_双板差异规范.md)。

## 主要目录

| 目录 | 内容 |
|---|---|
| `rtl/` | E203 SoC、ICB/AXI-Lite 桥、CNN 加速器、PMU、Pool/Sobel |
| `sdk/LeNet_hw/` | 当前 UART 批处理固件 |
| `LeNet_oneP/` | 单张推理、PMU、Pool/Sobel 自检固件 |
| `python/golden/` | INT8 黄金定点模型、权重导出、逐层比对 |
| `python/pmu/` | PMU/CSR 日志解析与图表 |
| `sim/` | CNN RTL 仿真与一键回归 |
| `board/pynqz2/` | PYNQ-Z2 重建和烧 bit 脚本 |
| `docs/` | 同步说明、移植契约、验证报告 |
| `demo/live_dashboard/` | 比赛现场实板演示 UI（不计入正式复现） |

## 推荐阅读顺序

1. [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md)：项目总说明
2. [`docs/reports/2026-10-30_第八章复现验证/README.md`](docs/reports/2026-10-30_第八章复现验证/README.md)：正式复现结果
3. [`docs/porting/02_移植到A侧的步骤与接口契约.md`](docs/porting/02_移植到A侧的步骤与接口契约.md)：A 侧移植
4. [`python/golden/README.md`](python/golden/README.md)：黄金模型与逐层比对
5. [`sim/README.md`](sim/README.md)：9 个仿真用例
6. [`demo/live_dashboard/README.md`](demo/live_dashboard/README.md)：演示 UI 边界与启动方式

## 快速命令

```powershell
# 拉取最新代码
git pull origin main

# 仿真回归
cd python\golden
python export_hex.py --big
cd ..\..\sim\run
powershell -ExecutionPolicy Bypass -File run_all.ps1

# PYNQ-Z2 重建、综合、实现和生成 bit
$env:E203_XPR = "D:\path\to\e203_zynq.xpr"
vivado -mode batch -source board\pynqz2\repro_build.tcl

# 1000 张批测
python py\uart_batch_mnist.py --port COM9 --count 1000
```

## 重要提醒

- 仓库中的 `rtl/aic_rv32.v` 当前为 AX7020 口径，不能直接作为 PYNQ-Z2 顶层。
- 修改 ROM/权重后必须强制重新生成 `rom_all_tap` IP，不能只替换 COE。
- 正式准确率以 1000 张报告为准；仓库目前没有独立的正式 100 张验收日志。
- 演示 UI 只用于现场展示和录屏，不作为第八章复现或性能验收证据。
