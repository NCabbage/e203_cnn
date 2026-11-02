# E203 + INT8 CNN 加速器项目总览

> 文档编写基线：`main@f26af25`（2026-11-02）
>
> 适用范围：本文件是整个仓库的总入口，说明项目目标、系统架构、RTL、寄存器、软件、构建、上板、验证、证据和交接方式。具体细节仍以源码、同步说明和验证报告为准。
>
> 正式验收基线：**Vivado 2025.2 + PYNQ-Z2（xc7z020clg400-1）**。其他板卡只有在重新完成板级约束、综合、实现、上板和实测后，才能采信其性能与时序结论。

## 目录

- [1. 项目定位与边界](#1-项目定位与边界)
- [2. 系统总体架构](#2-系统总体架构)
- [3. RTL 模块说明](#3-rtl-模块说明)
- [4. CNN 寄存器映射](#4-cnn-寄存器映射)
- [5. 软件分层与 UART 协议](#5-软件分层与-uart-协议)
- [6. 一次 LeNet 推理流程](#6-一次-lenet-推理流程)
- [7. PMU、Pool、Sobel 与黄金定点模型](#7-pmu-pool-sobel-与黄金定点模型)
- [8. Vivado 构建、实现、比特流与 OpenOCD](#8-vivado-构建实现比特流与-openocd)
- [9. PYNQ-Z2 上板与冷启动](#9-pynqz2-上板与冷启动)
- [10. 仿真、100/1000 张验证与结果口径](#10-仿真1001000-张验证与结果口径)
- [11. 演示 UI 与正式复现流程的边界](#11-演示-ui-与正式复现流程的边界)
- [12. 证据文件与提交记录](#12-证据文件与提交记录)
- [13. 交接入口与修改检查表](#13-交接入口与修改检查表)
- [14. 已知限制与常见问题](#14-已知限制与常见问题)
- [15. 快速命令索引](#15-快速命令索引)

---

## 1. 项目定位与边界

本项目在蜂鸟 E203 RISC-V 软核上接入一个面向 LeNet-5 的 INT8 CNN 加速器，并通过 ICB/AXI-Lite 桥完成 CPU 对加速器寄存器的访问。项目同时提供：

- E203 SoC、ICB/AXI-Lite 桥和 CNN 加速器 RTL；
- LeNet-5 裸机 C 推理程序；
- 权重 ROM、定点黄金模型和逐层比对工具；
- 加速器侧 PMU、硬件 Pooling、Sobel 边缘检测；
- Vivado 构建、仿真回归、上板验证和 1000 张 MNIST 批测；
- 比赛现场使用的实板演示 UI。

### 1.1 双人协作与板卡

| 代号 | 开发板 | FPGA | 主要职责边界 |
|---|---|---|---|
| A | 征服者 7020 / AX7020 | XC7Z020-1CLG400C | 桥、A 侧板级工程、联合调试 |
| B | PYNQ-Z2 | XC7Z020-1CLG400C | CNN RTL、PMU、Pool/Sobel、PYNQ-Z2 上板验证 |

两块板使用同一型号 FPGA，因此 RTL 功能与仿真结论可以互通；但以下内容**不得跨板覆盖或跨板采信**：

- 顶层文件、XDC、PLL/MMCM 配置；
- 复位极性、板载时钟频率、引脚定义；
- 比特流、时序余量、资源数字；
- UART/JTAG 端口与下载器链路；
- 上板周期数、耗时和准确率记录。

详细规则见 [`docs/porting/00_双板差异规范.md`](docs/porting/00_双板差异规范.md)。

### 1.2 项目不包含什么

- 不包含 CNN 训练流程；仓库从已有 LeNet-5 权重开始，完成定点化、导出、RTL 推理和板级验证。
- 不保证任意板卡直接复用 PYNQ-Z2 的 bit、XDC、PLL 或串口路径。
- `demo/live_dashboard/` 只是展示层，不属于第八章复现步骤，也不作为功能或性能验收证据。
- 历史日志中的数字不能替代当前代码、当前 bit 和当前固件的重新实测。

---

## 2. 系统总体架构

### 2.1 控制与数据通路

```text
上位机 PC
  │
  ├─ UART ───────────────► E203 程序 / MNIST 图像 / 命令
  │
  └─ JTAG ── OpenOCD ───► E203 Debug Module，加载并启动固件

PYNQ-Z2 PL
  │
  ├─ E203 RV32IMAC Core
  │    ├─ ILM / ITCM  0x8000_0000
  │    ├─ DTCM         0x9000_0000
  │    └─ NICE 自定义指令：cnn.start / cnn.wait
  │
  ├─ ICB 总线
  │    └─ O5 窗口 0x4000_0000 - 0x4FFF_FFFF
  │         └─ ICB → AXI4 → AXI-Lite
  │              └─ cnn_regs
  │                   ├─ 配置寄存器 / 输入 / bias / 输出
  │                   ├─ PMU
  │                   └─ cnn_top
  │                        ├─ 4-lane INT8 MAC
  │                        ├─ 量化 / ReLU / 饱和
  │                        ├─ 2×2 max pooling 模式
  │                        └─ 权重 ROM：11066 × 32 bit
  │
  └─ UART / GPIO / JTAG / 其他 E203 外设
```

### 2.2 两种启动控制方式

1. **NICE 紧耦合控制**
   - `cnn.start`：Custom3 opcode `0x7B`、`funct3=1`，启动后立即返回。
   - `cnn.wait`：Custom3 opcode `0x7B`、`funct3=2`，阻塞等待完成并返回加速器 PMU 总周期。
   - 当前 `cnn.h` 默认使用这种方式。

2. **MMIO 兼容方式**
   - `CTRL[31]=0` 时，`cnn_regs` 使用 `CTRL[0]` 作为 start。
   - 当前软件调用 `cnn_clear_ptrs()` 时会同时保持 `CTRL[31]=1`，避免误切到 MMIO 模式。

### 2.3 存储器与地址窗口

| 区域 | 地址 | 说明 |
|---|---|---|
| ILM / ITCM | `0x8000_0000` 起 | 程序、常量和只读数据；OpenOCD 按 raw BIN 加载 |
| DTCM | `0x9000_0000` 起 | 栈、数据、BSS 和运行时张量 |
| CNN 外设窗口 | `0x4000_0000 - 0x4FFF_FFFF` | E203 ICB O5 端口，经 AXI-Lite 访问 `cnn_regs` |
| CNN 寄存器基址 | `0x4000_0000` | 低 8 位用于寄存器译码 |

### 2.4 数据布局

- 输入激活 `x`：`[H][W][C]`
  - 地址：`(h * W + w) * C + c`
- 输出 `y`：`[OH][OW][OC]`
  - 读出序号：`(oh * OW + ow) * OC + oc`
- 权重 ROM：`[OC][TAP_GROUP][LANE]`
  - 32 bit 字地址：`w_base + oc * num_tap_groups + tap_group`
  - 一个字打包 4 个 int8 权重：byte0/1/2/3 分别对应 lane0/1/2/3

---

## 3. RTL 模块说明

### 3.1 目录职责

| 目录 | 主要内容 |
|---|---|
| `rtl/core` | E203 CPU、IFU、EXU、LSU、CSR、ITCM/DTCM、时钟与复位控制 |
| `rtl/debug` | RISC-V Debug Module、JTAG DTM、Debug RAM/ROM |
| `rtl/fab` | E203 ICB 总线分叉与互联 |
| `rtl/subsys` | SoC 子系统、存储映射、NICE wrapper、外设集成 |
| `rtl/axi_lite_slave` | AXI4-Lite slave 与寄存器访问桥 |
| `rtl/cnn` | CNN MAC、量化、寄存器、顶层、PMU |
| `rtl/perips` | UART、SPI、GPIO、PWM、CLINT、PLIC 等外设 |
| `rtl/soc` | SoC 顶层集成 |
| `rtl/aic_rv32.v` | 当前仓库中的板级顶层；**注意其内容对应 AX7020 口径，不能直接当 PYNQ-Z2 顶层使用** |

### 3.2 CNN 关键模块

| 模块 | 职责 |
|---|---|
| `rtl/cnn/cnn_top.v` | 卷积/全连接/池化状态机、4-lane MAC、权重 ROM 接口、输出缓存、PMU |
| `rtl/cnn/cnn_mac.v` | int8 × int8 → int16 有符号乘法，32 bit 累加 |
| `rtl/cnn/cnn_quant.v` | `acc + bias` → 算术右移 → 可选 ReLU → int8 饱和 |
| `rtl/cnn/cnn_regs.v` | `0x4000_0000` 寄存器组、地址译码、PMU、Pool 配置和 `cnn_top` 例化 |
| `rtl/subsys/cnn_nice_wrapper.v` | Custom3 指令解码，产生 `nice_start_r`，等待 `cnn_done` 并返回周期 |
| `rtl/axi_lite_slave/Axi4_lite_slave.v` | AXI4-Lite 事务转换为内部单周期读写控制 |

### 3.3 `cnn_top` 内部资源与状态机

- `X_DEPTH = 4096`：输入 / 中间激活缓存，int8。
- `Y_DEPTH = 4096`：输出缓存，int8。
- `B_DEPTH = 256`：bias 缓存，int32。
- `PAR = 4`：每拍并行 4 个 MAC lane。
- `W_TOTAL_WORDS = 11066`：LeNet 11060 字 + Sobel 6 字。
- 状态机：`S_IDLE → S_MAC → S_QUANT → S_DONE`。
- `cfg_pool=1` 时：
  - 4 个 lane 固定在 `(kh,kw)=(0,0)/(0,1)/(1,0)/(1,1)`；
  - 不读权重、不加 bias；
  - 初始值为 `-128`，取 4 点最大值；
  - 每个输出点只需 1 拍。

### 3.4 权重 ROM 布局

| 层 | `w_base`（32 bit 字） | taps | OC | 占用字数 |
|---|---:|---:|---:|---:|
| C1 | 0 | 25 | 6 | 42 |
| C3 | 42 | 150 | 16 | 608 |
| C5 | 650 | 256 | 120 | 7680 |
| F6 | 8330 | 120 | 84 | 2520 |
| OUT | 10850 | 84 | 10 | 210 |
| Sobel | 11060 | 9 | 2 | 6 |
| 合计 | - | - | - | **11066** |

ROM IP：`ip/rom_all_tap/rom_all_tap.xci`，单端口 ROM，32 bit 宽，初始化来源为 `py/rom_all_tap.coe`。Vivado 实际使用的是 IP 生成后的 `.mif`，因此修改权重后必须强制重新生成 IP，不能只替换 COE 后直接综合。

---
## 4. CNN 寄存器映射

### 4.1 完整寄存器表

基址：`0x4000_0000`。以下定义以当前 `rtl/cnn/cnn_regs.v` 和 `sdk/LeNet_hw/include/cnn.h` 为准。

| 偏移 | 名称 | 读写 | 位定义 / 说明 |
|---:|---|---|---|
| `0x00` | `CTRL` | W | `[0]` start；`[1]` 历史 WPTR 清位，保留；`[2]` 清输入指针；`[3]` 清 bias 指针；`[31]` NICE 模式选择 |
| `0x04` | `STATUS` | R | `[0]` busy；`[1]` done |
| `0x08` | `WDATA` | W | 保留；权重已固化到 ROM，不再由 CPU 写 |
| `0x0C` | `IDATA` | W | 写一个输入激活，低 8 位有效，写后 `iptr++` |
| `0x10` | `BIAS` | W | 写一个 int32 bias，写后 `bptr++` |
| `0x14` | `CFG0` | W | `H[7:0] W[15:8] C[23:16] K[31:24]` |
| `0x18` | `CFG1` | W | `OC[7:0] STRIDE[9:8] PAD[13:10] SHIFT[18:14] RELU[19] POOL[20]` |
| `0x1C` | `OINDEX` | W | 设置要读取的输出序号 |
| `0x20` | `ODATA` | R | 读取 `y[OINDEX]`，int8 符号扩展到 32 bit |
| `0x24` | `WPTR` | R | 保留，恒为 0 |
| `0x28` | `IPTR` | R | 当前输入写指针 |
| `0x2C` | `YCOUNT` | R | 当前已完成输出个数 |
| `0x30` | `VERSION` | R | 固定 `0x0001_0000` |
| `0x34` | `PMU_CTRL` | W/R | `[0]` enable；`[1]` clear |
| `0x38` | `PMU_MAC` | R | `S_MAC` 有效计算周期 |
| `0x3C` | `PMU_WLOAD` | R | 权重装载周期；ROM 固化版恒为 0 |
| `0x40` | `PMU_XLOAD` | R | 输入 `x_wen` 有效周期 |
| `0x44` | `PMU_BUBBLE` | R | MAC 至少一个 lane 无效的周期，是 `PMU_MAC` 子集 |
| `0x48` | `PMU_TOTAL` | R | start 拍 + busy 周期 |
| `0x4C` | `PMU_AXIWR` | R | AXI-Lite 写事务数，包含配置/输入/bias/启动/OINDEX 写 |
| `0x50` | `PMU_VERSION` | R | 固定 `0x0001_0000` |
| `0x54` | `WBASE` | W/R | 当前层权重起始 32 bit 字地址 |

### 4.2 数值计算约定

RTL、C 和 Python 黄金模型必须遵守同一顺序：

```text
acc = Σ(int8 x × int8 w)                 // int32 累加
acc_b = acc + int32_bias
sh = acc_b >>> SHIFT                     // 算术右移
r = RELU ? max(sh, 0) : sh
y = clamp(r, -128, 127)                  // int8 饱和
```

注意：

- 输入激活、权重都是有符号 `int8`。
- 累加器必须是 `int32`，中途不能截断为 `int16`。
- ReLU 必须在右移之后。
- 饱和必须在最后一步。
- 输出顺序中 `OC` 是最内层。

### 4.3 推荐驱动流程

```c
/* 1. 先读 VERSION，确认桥和寄存器通路 */
if (cnn_init() != 0) {
    /* 0x40000030 != 0x00010000 */
}

/* 2. 每层清指针，设置 W_BASE，配置形状和量化参数 */
cnn_set_wbase(W_BASE_C1);
cnn_clear_ptrs();
cnn_config(H, W, C, K, OC, STRIDE, PAD, SHIFT, RELU);

/* 3. 写输入和 bias；权重不再写 */
cnn_load_input(x, H * W * C);
cnn_load_bias(bias, OC);

/* 4. NICE start + wait */
cnn_run();

/* 5. 写 OINDEX 后读 ODATA */
for (i = 0; i < OUT_N; i++)
    y[i] = cnn_read_output(i);
```

`cnn_clear_ptrs()` 会自动带上 `CNN_CTRL_NICE_MODE`，不要改成只写 `CTRL=0x0E` 的旧代码，否则可能退回 MMIO 模式。

### 4.4 快速连通性检查

| 检查项 | 期望 |
|---|---|
| 读 `0x40000030` | `0x00010000` |
| 读 `0x40000050` | `0x00010000` |
| 写 `PMU_CTRL=0x3` 后读回 | bit0=1 |
| 写 `CTRL` 清指针后读 `IPTR` | 0 |
| 写一个合法小卷积后 | `STATUS.done=1`，`YCOUNT` 正确增加 |
| 任意未定义寄存器读 | `0xDEADBEEF`，说明默认译码路径生效 |

---

## 5. 软件分层与 UART 协议

### 5.1 软件目录

| 路径 | 用途 |
|---|---|
| `sdk/LeNet_hw/` | 当前 1000 张批处理服务器；软件 Pooling；UART 命令循环 |
| `LeNet_oneP/` | 单张 LeNet + PMU + 硬件 Pool/Sobel 自检固件 |
| `LeNet_hw/` | 历史 LeNet 工程副本；正式批测优先使用 `sdk/LeNet_hw/` |
| `sdk/coremark/` | E203 CoreMark 基线工程 |
| `include/cnn.h` | CNN 寄存器、配置、NICE 指令和层调用封装 |
| `include/wbase_tap.h` | 权重 ROM 各层基地址和总字数 |
| `include/lenet_weights_hw.h` | LeNet 定点权重和 bias 数组 |
| `include/lenet_c5_split.h` | C5 分块相关权重数据 |
| `include/mnist_samples.h` | 单张自检用 MNIST 样本 |
| `python/golden/` | 定点黄金模型、权重导出、逐层比对 |
| `python/pmu/` | PMU / CSR 日志解析和图表生成 |
| `py/uart_batch_mnist.py` | 正式 UART 批量测试客户端 |

### 5.2 两套固件的差异

| 项目 | `sdk/LeNet_hw` 批处理固件 | `LeNet_oneP` 自检固件 |
|---|---|---|
| 主要用途 | 1000 张连续评测 | 单张推理、PMU、Pool/Sobel 自检 |
| 串口波特率 | 57600 | 115200 |
| Pooling | 软件 2×2 max pooling | 硬件 2×2 max pooling |
| 量化 shift | 9 | 8 |
| 输入来源 | PC 通过 UART 发送 | 固件内置 MNIST 样本 |
| 输出 | `PRED <pred> <label> <cycles>` | 预测、PMU、Pool PASS、Sobel PASS |
| 复现角色 | 正式批量验证 | 链路/模块自检 |

两套固件使用不同量化参数和 Pool 路径，**准确率和周期不能直接混在一起比较**。

### 5.3 UART 批处理协议

正式批测使用带 ACK 的协议，避免 784 字节裸流偶发丢字节。

1. 上电后等待：

```text
CNN VERSION OK
Waiting for PC commands...
```

2. 开始一张图片：

```text
PC  → BEG <label>\n
E203→ READY\n
```

3. 分片发送 784 字节输入。每一片为十六进制：

```text
PC  → DATA <offset> <hex_payload>\n
E203→ ACK <offset> <accepted_bytes>\n
```

4. 全部字节收完后启动推理：

```text
PC  → END\n
E203→ PRED <pred> <label> <cycles>\n
```

5. 退出批处理服务：

```text
PC  → EXIT\n
E203→ BYE\n
```

说明：

- 正式脚本 `py/uart_batch_mnist.py` 默认按 128 字节分片并等待 ACK。
- `RUN <label>\n + 784 字节裸流` 仍保留用于兼容，但在 PYNQ-Z2 长批次中曾出现丢字节，不作为正式批测路径。
- 现场演示 UI 为降低长批次风险使用 64 字节分片、异常 ACK 重发和整张图重试；该实现只属于演示层。

---

## 6. 一次 LeNet 推理流程

### 6.1 LeNet-5 层参数

| 层 | 输入形状 | 卷积/全连接参数 | 输出形状 | 备注 |
|---|---|---|---|---|
| C1 | 28×28×1 | 5×5, OC=6, S=1 | 24×24×6 | int8 卷积 |
| S2 | 24×24×6 | 2×2 max, S=2 | 12×12×6 | 批处理固件为软件 Pool |
| C3 | 12×12×6 | 5×5, OC=16, S=1 | 8×8×16 | int8 卷积 |
| S4 | 8×8×16 | 2×2 max, S=2 | 4×4×16 | 批处理固件为软件 Pool |
| C5 | 4×4×16 | 4×4, OC=120, S=1 | 120 | 使用 `W_BASE_C5` |
| F6 | 120 | 1×1, C=120, OC=84 | 84 | 也是 1×1 卷积 |
| OUT | 84 | 1×1, C=84, OC=10 | 10 | ReLU=0，取 argmax |

C5 的全部 120 个输出通道已经在当前 ROM 方案中完整存放。早期“F6 超过 8192 需要分块”的记录属于旧权重 BRAM/动态装载方案，不应拿来解释当前 `cnn_top`。

### 6.2 详细执行顺序

1. CPU 从 ILM 启动，初始化 UART、时钟、堆栈和 `.data/.bss`。
2. 读取 `CNN VERSION`，确认桥和寄存器通路。
3. 对每一层：
   - 写 `WBASE`；
   - 清输入/bias 指针；
   - 写 `CFG0/CFG1`；
   - 将输入张量按 `[H][W][C]` 写入 `IDATA`；
   - 写入 bias；
   - 通过 NICE `start` 启动；
   - 通过 NICE `wait` 等待 `done`；
   - 读取 `YCOUNT` 个输出。
4. 层间数据由 CPU 在 `hw_x/hw_y` 之间搬运；批处理固件额外执行软件 max pooling。
5. 最后一层输出 10 个分数，CPU 执行 argmax。
6. 批处理固件计算 `mcycle` 差值并输出 `PRED pred label cycles`。
7. 单张 self-test 固件额外读取 PMU 和 CPU 侧 CSR。

### 6.3 为什么同时有软件和硬件 Pool

- `sdk/LeNet_hw` 的 1000 张正式批测流程经过验证，采用软件 Pool + shift=9，便于和已有基线逐行比对。
- `LeNet_oneP` 用硬件 Pool + shift=8 验证 Pool RTL、PMU 和 Sobel。
- 两者都是真硬件上的 LeNet 推理，但不是同一套固件口径；报告和测试结果必须注明固件名称。

---
## 7. PMU、Pool、Sobel 与黄金定点模型

### 7.1 PMU 统计口径

| 计数器 | 含义 | 注意事项 |
|---|---|---|
| `PMU_MAC` | CNN 处于 `S_MAC` 且跳过首拍流水线后的计算周期 | 是加速器内部周期 |
| `PMU_WLOAD` | 权重写有效周期 | ROM 固化方案恒为 0，正常 |
| `PMU_XLOAD` | 输入写有效周期 | 对应 CPU 写 `IDATA` 的次数 |
| `PMU_BUBBLE` | `S_MAC` 中至少一个 lane 无效的周期 | 是 `PMU_MAC` 的子集，不能相加 |
| `PMU_TOTAL` | start 拍 + busy 周期 | 不含 CPU 收尾和 UART 打印 |
| `PMU_AXIWR` | AXI-Lite 写事务数 | 包含配置、输入、bias、start、OINDEX 写 |
| CPU `cycles` | 软件 `mcycle` 端到端差值 | 包含 CPU 搬运、调用和读结果，不是 PMU_TOTAL |
| CPU `mul/load/store/stall` | E203 CSR 指令/周期统计 | 与 PMU 不是同一统计空间 |

派生指标：

```text
lane_eff   = 1 - PMU_BUBBLE / PMU_MAC
mac_share  = PMU_MAC / PMU_TOTAL
stall_rate = CPU_stall / CPU_cycles
infer_ms   = CPU_cycles / CPU_Frequency
```

使用：

```c
/* 清零并保持使能 */
CNN_WR(CNN_PMU_CTRL, CNN_PMU_CTRL_CLEAR | CNN_PMU_CTRL_EN);

/* 运行后读取 */
mac   = CNN_RD(CNN_PMU_MAC);
xload = CNN_RD(CNN_PMU_XLOAD);
bubble= CNN_RD(CNN_PMU_BUBBLE);
total = CNN_RD(CNN_PMU_TOTAL);
axiwr = CNN_RD(CNN_PMU_AXIWR);
```

日志解析：

```powershell
cd python\pmu
python pmu_report.py <日志或OpenOCD输出> -o <输出目录>
```

### 7.2 硬件 Pool

- 配置：`K=2, STRIDE=2, CFG1[20]=1`。
- 复用 4 个 MAC lane 读取 2×2 窗口的 4 个输入，取最大值。
- bias 硬件置 0。
- 无效 lane 使用 `-128` 参与 max，不改变负值窗口的正确性。
- 验证：6×6 输入 -> 3×3 输出，期望 `[-2,10,11,19,22,24,31,34,36]`。
- 官方上板结果：9/9 点一致，`POOL SELFTEST: PASS`。
- 资源增量历史记录：+98 LUT、+8 FF，BRAM/DSP 零增加；以当前综合报告为准。

### 7.3 Sobel 双通道边缘检测

Sobel 复用同一个 `cnn_top`，没有新增卷积运算逻辑：

```text
输入：8×8×1
卷积：K=3, C=1, OC=2, STRIDE=1
输出：6×6×2
oc0 = Gx，oc1 = Gy
```

两个核：

```text
Gx = [-1  0  1]      Gy = [-1 -2 -1]
     [-2  0  2]           [ 0  0  0]
     [-1  0  1]           [ 1  2  1]
```

Sobel 权重追加在 LeNet 权重之后，`W_BASE_SOBEL=11060`，占 6 个 ROM 字。最终 ROM 总字数必须为 `11066`，末尾 6 字为：

```text
fe0100ff
00ff0200
00000001
00fffeff
02010000
00000001
```

官方上板结果：`nonzero=48`，Gx/Gy 与黄金结果逐点一致，`SOBEL SELFTEST: PASS`。

### 7.4 黄金定点模型

入口：`python/golden/lenet_golden.py`。

```powershell
cd python/golden
python lenet_golden.py --sample 5
python lenet_golden.py --all
```

digit5 期望输出：

```text
out = [-128,-128,-44,-107,-62,127,74,-128,48,-3]
pred = 5
```

前 10 张期望：

```text
9/10
[2] label=2 pred=4 MISMATCH
```

逐层比对：

```powershell
python compare_hw_bins.py --golden examples\digit5\golden --hw <JTAG dump目录>
```

digit5 的 C1/S2/C3/S4/C5/F6/OUT 全部为 `mismatch=0`。因此 10 张中的 `label=2 -> pred=4` 来自当前量化模型，不是 RTL 数据通路错误。

权重 ROM 生成和拼接：

```powershell
# 权重源变化时重新生成基础 hex（含 Sobel 尾段）
cd python\golden
python export_wbase_tap.py

# 把已跟踪的 LeNet COE 与 Sobel 尾段拼成 11066 字
cd ..\..
python python\golden\assemble_rom_tap_sobel.py
```

生成后至少检查：

- `python/golden/rom_all_tap.hex` 总行数 = 11066；
- 根目录 `rom_all_tap.coe` 和 `py/rom_all_tap.coe` 各 11066 字；
- `W_TOTAL_WORDS`、`cnn_regs` 例化参数和 ROM IP 三处一致；
- `check_rom_consistency.py` 对当前 IP 生成结果的检查通过。

---

## 8. Vivado 构建、实现、比特流与 OpenOCD

### 8.1 环境

| 工具/文件 | 推荐值 |
|---|---|
| Vivado | 2025.2 |
| 目标器件 | `xc7z020clg400-1` |
| PYNQ-Z2 工程 | 本地已有 `.xpr`，由 `E203_XPR` 指向 |
| RTL 顶层/PLL/XDC | 使用 PYNQ-Z2 板级版本，不得使用 AX7020 顶层 |
| 软件工具链 | Nuclei RISC-V GCC / objcopy / OpenOCD |
| 串口终端 | 支持 57600/115200 8N1 |

### 8.2 构建前确认

1. `rom_all_tap` IP 已升级到当前 Vivado 版本。
2. IP 的 PART 与当前工程器件一致。
3. COE 已重新拼接成 11066 字。
4. ROM IP 已强制 `reset_target/generate_target -force`，没有复用旧 DCP。
5. 当前工程的顶层、XDC、PLL/MMCM 属于 PYNQ-Z2。

### 8.3 一键重建、综合、实现和生成 bit

`board/pynqz2/repro_build.tcl` 假定已有 Vivado 工程：

```powershell
python python\golden\assemble_rom_tap_sobel.py
$env:E203_XPR = "D:\path\to\e203_zynq.xpr"
vivado -mode batch -source board\pynqz2\repro_build.tcl
```

脚本执行：

1. 打开工程并检查器件；
2. 加入或定位 `ip/rom_all_tap/rom_all_tap.xci`；
3. 升级锁定 IP、同步 PART、设置 COE；
4. 强制重新生成 ROM IP；
5. 单独综合 `rom_all_tap_synth_1`；
6. 综合顶层；
7. 用 Explore 策略完成 implementation 和 write_bitstream；
8. 输出 `utilization.rpt` 和 `timing.rpt`；
9. 打印 `REPRO_BUILD_OK`。

输出目录：

```text
docs/reports/2026-10-30_第八章复现验证/build/
```

比特流路径由本地 Vivado run 决定，通常是：

```text
<project>.runs/impl_1/aic_rv32.bit
```

### 8.4 官方构建结果

| 项目 | 结果 |
|---|---|
| 综合 | 0 errors / 0 critical warnings |
| 实现 / DRC | 0 errors |
| WNS | `0.000000 ns` |
| WHS | `-0.025547 ns` |
| 比特流 | 生成成功，`4,045,684 bytes` |
| 软件编译 | `LeNet_oneP.elf`、`LeNet_hw.elf` 均成功 |
| 时序边界 | WNS 为 0，WHS 有 `-0.025547 ns` 缺口，报告必须如实写明 |
| 资源 | 以设计报告原文和本地 `utilization.rpt` 为准，仓库不把演示页数字当正式数据 |

### 8.5 烧写比特流

```powershell
$env:E203_BIT = "D:\path\to\aic_rv32.bit"
vivado -mode batch -source board\pynqz2\program_bit.tcl
```

脚本通过 Vivado Hardware Manager/hw_server 查找 `xc7z020`，烧录后打印：

```text
PROGRAM_OK: <bit path>
```

### 8.6 通过 OpenOCD 加载 E203 固件

PYNQ-Z2 上 E203 使用外接 FT2232 的 JTAG 通道；板载 TUL 只用于 PL，不用于 E203。

通用顺序：

1. 确认 FT2232 被识别为 `Dual RS232`，且没有被 Nuclei Studio/上次残留 OpenOCD 占用。
2. 连接 OpenOCD PYNQ-Z2 配置，确认 TAP：

```text
JTAG tap: e203.cpu tap/device found: 0x1e200a6d
```

3. 将 ELF 转成 raw BIN：

```powershell
riscv-nuclei-elf-objcopy -O binary LeNet_hw.elf LeNet_hw.bin
```

4. 用 OpenOCD 的 `load_image <bin> 0x80000000 bin` 写入 ILM。
5. 设置 `pc=0x80000000`，执行 `resume`，释放 JTAG。
6. 串口应看到 `CNN VERSION OK`。

当前 E203 调试模块对 OpenOCD 的 ELF/CRC verify 路径不兼容，长 ELF 可能报 CRC timeout。此时使用 **ELF → raw BIN → load_image**，不要反复重试 ELF verify。

`demo/live_dashboard/openocd/` 中的 loader 是可运行的参考实现，但演示目录不属于正式复现流程；正式板级工程应维护自己的 OpenOCD 配置和工具链路径。

---

## 9. PYNQ-Z2 上板与冷启动

### 9.1 板级关键参数

| 项目 | PYNQ-Z2 |
|---|---|
| FPGA | XC7Z020-1CLG400C |
| PL 输入时钟 | H16 = 125 MHz |
| E203 内核时钟 | 16 MHz |
| 复位 | D19，按下为高，顶层应使用 `ck_rst = ~mcu_rst` |
| E203 JTAG | TCK W18 / TDO W19 / TMS V6 / TDI U7 |
| UART | V7/U8，外接 FT2232 通道 B |
| 外部下载器 | FT2232 `Dual RS232` |
| 板载 TUL | 只用于 PL 烧写，不连接 E203 JTAG |

AX7020 对照：

| 项目 | AX7020 |
|---|---|
| PL 输入时钟 | H16 = 50 MHz |
| 复位 | H17，按下为低，顶层使用 `ck_rst = mcu_rst` |
| E203 JTAG | M17/J19/F19/D19 |
| UART | B20/B19 |

### 9.2 断电后哪些东西会丢

断电会丢失：

- PL 内的比特流；
- E203 ILM/DTCM 中的固件和运行状态。

因此冷启动必须重新执行：

```text
上电 → 烧 bit → 加载固件 → 打开 UART → 检查 CNN VERSION
```

如果只是浏览器/演示 UI 重启而板子仍供电，不需要重新烧 bit 和固件。

### 9.3 冷启动步骤

1. 接好 PYNQ-Z2 电源、外接 FT2232 的 JTAG/UART/GND。
2. 在设备管理器中确认 `Dual RS232` 和 UART 串口出现；端口号可能随 USB 口变化，不要硬编码旧 COM 号。
3. 烧写 bit：

```powershell
$env:E203_BIT = "D:\path\to\aic_rv32.bit"
vivado -mode batch -source board\pynqz2\program_bit.tcl
```

4. 用 OpenOCD 加载 `sdk/LeNet_hw/Debug/LeNet_hw.elf` 对应的 raw BIN。
5. 打开串口：
   - `LeNet_hw`：57600 8N1；
   - `LeNet_oneP`：115200 8N1。
6. 看到 `CNN VERSION OK` 后，再做单张或 1000 张测试。
7. 若要做 Pool/Sobel/PMU 自检，加载 `LeNet_oneP/Debug/LeNet_oneP.elf`；完成后重新加载 `LeNet_hw` 回到批处理。

### 9.4 冷启动常见失败

| 现象 | 优先检查 |
|---|---|
| JTAG `all ones` | PL 没有 bit，重新烧写 |
| 串口无输出 | 端口/波特率/固件是否运行 |
| 读 `VERSION=0xDEADBEEF` | AXI-Lite 地址、寄存器译码或桥连接错误 |
| `POOL SELFTEST: FAIL` | `CFG1[20]`、Pool 配置或 ROM/固件版本不一致 |
| Sobel 全 0 | ROM IP 是否重新生成，是否读到含 6 字 Sobel 尾段的 COE |
| 长批次中途卡住 | 使用 `BEG/DATA/END` ACK 协议，检查分片重传和串口占用 |
| 串口打不开 | Nuclei Studio/串口助手/残留 OpenOCD 占用了端口 |
| 重插后无 COM 口 | 等 Windows 重新枚举；确认 FT2232 而不是只插了板载 USB |

---
## 10. 仿真、100/1000 张验证与结果口径

### 10.1 仿真回归

入口：

```powershell
cd python\golden
python export_hex.py --big
cd ..\..\sim\run
powershell -ExecutionPolicy Bypass -File run_all.ps1
```

官方回归结果：`9 PASS / 0 FAIL`。

| 用例 | 覆盖点 | 周期 |
|---|---|---:|
| `t1_tiny` | 最小卷积 bring-up | 22 |
| `t2_stride2` | stride=2 | 22 |
| `t3_saturate` | int8 饱和 | 22 |
| `t4_multi` | 多输入通道/多输出通道 | 128 |
| `t5_requant` | 量化右移 | 226 |
| `t6_edge` | 边界/padding | 22 |
| `t8_pool` | 2×2 max pooling | 29 |
| `t9_fc` | FC 与 1×1 卷积等价 | 18 |
| `t10_sobel` | Sobel Gx/Gy 双通道 | 362 |

### 10.2 黄金模型验证

- digit5：逐层 C1/S2/C3/S4/C5/F6/OUT 全部 `mismatch=0`。
- 前 10 张：`9/10`，其中 `[2] label=2 pred=4`，与 RTL 上板结果一致。
- 结论：当前误差来自量化模型，不是 CNN RTL 数据通路。

### 10.3 单张上板自检

`LeNet_oneP` 官方记录（PYNQ-Z2）：

```text
Test image: MNIST real digit 3
CNN VERSION OK
Predicted class: 3
Total cycles: 353521
PMU MAC=74634 WLOAD=0 XLOAD=6588 BUBBLE=4480 TOTAL=86269 AXIWR=12678
CPU Frequency: 15939338 Hz
Total time: 22 ms
POOL SELFTEST: PASS
SOBEL SELFTEST: PASS
```

推导：

```text
lane_eff   = 1 - 4480 / 74634 ≈ 94.00%
mac_share  = 74634 / 86269 ≈ 86.51%
```

这些数值只对应当前 PYNQ-Z2 bit、当前 `LeNet_oneP` 固件、当前 shift=8/Pool 口径。

### 10.4 1000 张正式批测

正式证据：

- 日志：[`docs/reports/2026-10-30_第八章复现验证/batch_1000.log`](docs/reports/2026-10-30_第八章复现验证/batch_1000.log)
- 逐样本 CSV：[`docs/reports/2026-10-30_第八章复现验证/batch_result.csv`](docs/reports/2026-10-30_第八章复现验证/batch_result.csv)
- 基线 CSV：[`py/batch_result.csv`](py/batch_result.csv)

官方结果：

| 指标 | 数值 |
|---|---:|
| 总数 | 1000 |
| 正确 | 989 |
| 准确率 | 98.90% |
| 平均 CPU cycles | 308,644 |
| label/pred 序列 | 与原基线逐行一致，差异 0 行 |

注意：

- 该结果使用 `sdk/LeNet_hw` 的软件 Pool + shift=9 批处理固件。
- `cycles` 是 CPU 端到端循环计数，不是 `PMU_TOTAL`。
- 1000 张中部分样本周期略有差异，报告只声明 label/pred 序列一致；不要声称所有 cycles 完全相同。

### 10.5 100 张验证的口径

仓库当前**没有独立的正式 100 张验收日志或 CSV**。历史上 100 张主要作为调通和准确率趋势抽查，不能替代 1000 张正式结果，也不能直接引用成比赛最终指标。

如果需要补充 100 张正式证据，应在固定 bit、固定 ELF、固定 MNIST 数据目录下执行：

```powershell
python py\uart_batch_mnist.py --port COM9 --count 100 --start 0 --csv docs\reports\100_result.csv
```

并同时保存：

- 串口完整日志；
- CSV；
- bit 文件哈希；
- ELF 哈希；
- 仓库 commit；
- 板卡型号和固件波特率。

### 10.6 20 张兼容性回归

`batch_compat_20.log` 记录为 20/20、100.00%。它的作用是验证 ACK 重传协议和短批次稳定性，不是正式准确率结论。

### 10.7 结果采信规则

| 结果 | 同芯片异板能否直接采信 | 说明 |
|---|---|---|
| RTL 功能、仿真 PASS/FAIL | 可以 | 逻辑与板级约束无关 |
| 黄金模型逐层一致性 | 可以 | 数值约定与板无关 |
| 1000 张 label/pred 序列 | 需相同权重、固件、数据和量化口径 | 不能引用不同 shift/Pool 组合 |
| cycles、耗时、CPU 频率 | 不可跨板采信 | 必须用本板重新实测 |
| 资源、WNS/WHS | 不可跨板采信 | PLL/XDC/器件实现均可能不同 |
| 演示 UI 截图 | 只作为展示 | 不作为复现验收证据 |

---

## 11. 演示 UI 与正式复现流程的边界

演示 UI 位于 [`demo/live_dashboard/`](demo/live_dashboard/)，用途是比赛现场展示和录屏。

### 11.1 它做什么

- 从真实 COM 口读取当前板卡返回；
- 发送单张 MNIST 图像；
- 展示 1000 张批测进度和准确率；
- 展示 PMU、Pool、Sobel 自检结果；
- 提供“重新握手”和“演示复位”。

### 11.2 它不做什么

- 不生成 RTL；
- 不替代 Vivado、OpenOCD 或正式 ELF；
- 不改变第八章复现结论；
- 不把历史数据伪装成实时数据；
- 不作为 100/1000 张正式验收证据；
- 不保证直接适配 AX7020；换板仍要匹配 bit、串口和固件。

### 11.3 录屏推荐顺序

1. Vivado Hardware Manager 展示 `Programmed`。
2. 串口/演示 UI 展示 `E203 ONLINE`。
3. 单张实板推理并显示 `PRED`。
4. 1000 张进度或已完成结果。
5. Pool/Sobel 自检 PASS。
6. 最后展示汇总指标和架构页。

演示录制时不要把演示 UI 的历史状态当成正式 1000 张验证；正式结论仍引用 `docs/reports/2026-10-30_第八章复现验证/`。

---

## 12. 证据文件与提交记录

### 12.1 证据索引

| 证据 | 路径 | 用途 |
|---|---|---|
| 第八章复现总报告 | `docs/reports/2026-10-30_第八章复现验证/README.md` | 环境、构建、结果、边界 |
| 1000 张日志 | `docs/reports/2026-10-30_第八章复现验证/batch_1000.log` | 完整批测过程 |
| 1000 张 CSV | `docs/reports/2026-10-30_第八章复现验证/batch_result.csv` | 逐样本 label/pred/cycles |
| 上板自检日志 | `docs/reports/2026-10-30_第八章复现验证/board_selftest.log` | LeNet/Pool/Sobel/PMU |
| 仿真汇总 | `docs/reports/2026-10-30_第八章复现验证/regression_summary.txt` | 9 个用例结果 |
| 时序汇总 | `docs/reports/2026-10-30_第八章复现验证/timing_summary.txt` | WNS/WHS |
| 20 张兼容回归 | `docs/reports/2026-10-30_第八章复现验证/batch_compat_20.log` | ACK 协议回归 |
| 黄金模型 | `python/golden/README.md` | 定点模型、逐层比对 |
| PMU 工具 | `python/pmu/README.md` | PMU/CSR 解析口径 |
| 仿真入口 | `sim/README.md`、`sim/run/run_all.ps1` | 9 用例回归 |
| 双板差异 | `docs/porting/00_双板差异规范.md` | 板级边界 |
| A 侧接口契约 | `docs/porting/02_移植到A侧的步骤与接口契约.md` | 桥/寄存器/驱动交接 |
| 演示 UI | `demo/live_dashboard/README.md` | 仅展示用途 |

### 12.2 关键提交

| 提交 | 内容 |
|---|---|
| `32a4514` | 初始 INT8 CNN RTL，接入 mem 子系统 |
| `45055dc` | 加速器侧 PMU |
| `df079f1` / `097f524` / `40f6883` | 权重 BRAM 方案及合并 |
| `659f40d` | BRAM 权重预取修复、PMU 日志工具 |
| `7d470a4` | LeNet INT8 黄金模型与逐层比对 |
| `d2d535e` | 2×2 max pooling |
| `0d8bc19` | FC 复用和 Sobel |
| `82cbb00` | W4 LeNet 驱动移植与 F6 修复（历史节点） |
| `88ce157` | 第八章复现验证与板级批量流程 |
| `83e6c44` | 确认 Vivado 2025.2 + PYNQ-Z2 为参考基线 |
| `f26af25` | 新增实板演示 UI |

`f26af25` 是本文编写前的 HEAD；后续提交以 `git log -1` 为准。

### 12.3 不应提交的产物

仓库中已有 `.gitignore`，但本地构建仍可能产生：

```text
*.elf
*.hex
*.lst
*.map
*.o
*.jou
*.log
*.bit
prj/
```

除非任务明确要求更新固件交付物，否则不要用 `git add .`；应只添加源码、文档和明确需要跟踪的产物。

---

## 13. 交接入口与修改检查表

### 13.1 新成员第一次阅读顺序

1. 本文件；
2. `README.md`；
3. `docs/README.md`；
4. `docs/porting/00_双板差异规范.md`；
5. `docs/porting/02_移植到A侧的步骤与接口契约.md`；
6. `rtl/cnn/cnn_regs.v` 和 `sdk/LeNet_hw/include/cnn.h`；
7. `docs/reports/2026-10-30_第八章复现验证/README.md`；
8. `python/golden/README.md` 和 `sim/README.md`。

### 13.2 修改 CNN RTL 前

- 确认改的是共用区还是板级区。
- 确认是否改变寄存器地址、位定义、数据布局或数值约定。
- 同步修改 RTL、C 头文件、Python 黄金模型和文档。
- 更新 `rom_all_tap` 的 11066 字约定，若权重或 Sobel 改变。
- 先跑 9 个仿真用例，再做 Vivado 综合和上板。
- 每个板卡分别保存 bit、XDC、PLL 和实测结果。

### 13.3 修改 AXI-Lite/桥前

必须保证 `cnn_regs` 端口和寄存器低 8 位译码不变。若必须改接口，先更新：

- `rtl/axi_lite_slave/`；
- `rtl/subsys/e203_subsys_mems.v`；
- `rtl/cnn/cnn_regs.v`；
- `sdk/LeNet_hw/include/cnn.h`；
- `python/golden/` 测试向量；
- A 侧移植文档和同步说明。

### 13.4 修改软件前

- 确认当前使用的是 `sdk/LeNet_hw` 还是 `LeNet_oneP`。
- 不要混用 shift=9/软件 Pool 与 shift=8/硬件 Pool 的结果。
- 修改 UART 协议后同步更新 `py/uart_batch_mnist.py` 和演示 UI 的后端。
- 长批次必须保留 ACK、超时和重试，不要退回裸流传输。

### 13.5 推送前

```powershell
git pull origin main
git status --short
git diff --check
git add <明确的源码或文档路径>
git diff --cached --stat
git commit -m "..."
git push origin main
```

---

## 14. 已知限制与常见问题

1. **`rtl/aic_rv32.v` 当前是 AX7020 口径。**
   - 文件中的 `ck_rst = mcu_rst`、`CLK_I_50M(clk)`、LED 引脚等不能直接用于 PYNQ-Z2。
   - PYNQ-Z2 必须使用自己的顶层、PLL 和 XDC；正式工程不要把 A 侧顶层放进共用区。

2. **WHS 仍有缺口。**
   - 官方复现记录：`WNS=0.000000 ns`，`WHS=-0.025547 ns`。
   - 不能写成“完全满足时序”；必须保留原始报告和路径说明。

3. **旧 W_DEPTH/F6 分块文档是历史口径。**
   - 当前 ROM IP 方案权重已固化，C5/F6 不再依赖旧的 8192 字外部权重 BRAM。
   - 读旧报告时先确认是 `LeNet_hw`、`LeNet_oneP` 还是旧 BRAM 方案。

4. **ROM IP 不强制重建就会读到旧 COE/MIF。**
   - 必须 `generate_target all -force`，并先综合 `rom_all_tap_synth_1`。
   - 合成日志显示 “read successfully” 不代表读到了最新 COE。

5. **100 张没有正式验收证据。**
   - 100 张只能作为趋势抽查；正式结果引用 1000 张报告。
   - 若补测，必须同时保存日志、CSV、bit/ELF hash 和 commit。

6. **演示 UI 不是正式复现入口。**
   - 演示 UI 的在线状态、实时结果和页面截图不能替代第八章证据。

7. **串口端口号可能变化。**
   - COM7/COM9 只是历史实际端口，换 USB 口后要重新识别 FT2232 A/B 通道。

8. **JTAG/OpenOCD 的 ELF CRC 路径不可靠。**
   - 使用 raw BIN 的 `load_image`，并在 `resume` 后检查 UART。

9. **不同板卡不能直接比较性能。**
   - 功能结论可比较，cycle、ms、资源、时序必须按板重测。

---

## 15. 快速命令索引

```powershell
# 1. 生成/拼接权重 ROM
python python\golden\assemble_rom_tap_sobel.py

# 2. PYNQ-Z2 重新综合、实现、生成 bit
$env:E203_XPR = "D:\path\to\e203_zynq.xpr"
vivado -mode batch -source board\pynqz2\repro_build.tcl

# 3. 烧录 bit
$env:E203_BIT = "D:\path\to\aic_rv32.bit"
vivado -mode batch -source board\pynqz2\program_bit.tcl

# 4. 仿真回归
cd python\golden
python export_hex.py --big
cd ..\..\sim\run
powershell -ExecutionPolicy Bypass -File run_all.ps1

# 5. 1000 张批测
python py\uart_batch_mnist.py --port COM9 --count 1000 --csv py\batch_result.csv

# 6. PMU 日志解析
python python\pmu\pmu_report.py <日志> -o <输出目录>

# 7. 演示 UI
cd demo\live_dashboard
python app.py --port COM9 --data-dir <MNIST t10k目录>
```

最后更新：2026-11-02。详细变更请以 Git 提交、`docs/sync/` 最新说明和当前板级实测日志为准。
