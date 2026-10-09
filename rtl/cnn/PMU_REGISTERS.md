# CNN 加速器侧 PMU 寄存器说明

适用模块：`rtl/cnn/cnn_top.v`、`rtl/cnn/cnn_regs.v`

## 兼容性

- 原有 `0x00` - `0x30` CNN 寄存器映射不变。
- `cnn_regs` 对外端口不变，A 侧已有驱动可以不修改。
- PMU 是新增可选项，默认 `enable=1`，不读 PMU 不影响原功能。
- 如果 A 侧顶层直接例化 `cnn_top`，需要连接新增端口：
  - `pmu_en = 1'b1`
  - `pmu_clr = 1'b0`
  - `pmu_*_cycles` 输出可以悬空
- 本次改动不修改 `cnn_mac.v`、`cnn_quant.v` 的计算逻辑。

## 寄存器映射

基址：`0x4000_0000`

| 偏移 | 名称 | 读写 | 说明 |
|---:|---|---|---|
| `0x34` | `PMU_CTRL` | W/R | bit0=enable，bit1=clear（写 1 清计数） |
| `0x38` | `PMU_MAC` | R | 处于 `S_MAC` 的周期数 |
| `0x3C` | `PMU_WLOAD` | R | `w_wen` 有效周期数，即权重装载周期 |
| `0x40` | `PMU_XLOAD` | R | `x_wen` 有效周期数，即输入装载周期 |
| `0x44` | `PMU_BUBBLE` | R | `S_MAC` 中至少 1 个 lane 无效的周期数 |
| `0x48` | `PMU_TOTAL` | R | start 拍 + busy 周期的有效推理周期 |
| `0x4C` | `PMU_AXIWR` | R | AXI-Lite 写事务数 |
| `0x50` | `PMU_VERSION` | R | 固定 `0x0001_0000` |

## 用法

```c
// 清计数并保持 enable
CNN_WR(0x40000034, 0x3);

// 读计数
uint32_t mac   = CNN_RD(0x40000038);
uint32_t wload = CNN_RD(0x4000003C);
uint32_t xload = CNN_RD(0x40000040);
uint32_t bub   = CNN_RD(0x40000044);
uint32_t total = CNN_RD(0x40000048);
uint32_t axi_wr= CNN_RD(0x4000004C);
```

`PMU_BUBBLE` 统计的是 MAC lane 无效周期，覆盖 padding、尾部空 lane 和越界，不是 CPU 侧 AXI 等待周期。

## 验证摘要

- CNN 内核回归：6 PASS / 0 FAIL。
- CPU -> ICB -> AXI-Lite -> `cnn_regs` -> `cnn_top` 全通路：PASS。
- PYNQ-Z2 上板 `LeNet_oneP` 验证：
  - `PMU_WLOAD = 44190`
  - `PMU_XLOAD = 2996`
  - `PMU_AXIWR = 52161`
  - 与理论写入次数一致。
- 最终 Explore 时序：WNS `0.000 ns`，WHS `0.023 ns`。