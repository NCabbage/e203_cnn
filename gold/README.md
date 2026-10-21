# python —— INT8 CNN 黄金模型（B / W1 任务）

状态：**已完成**（2026-09-30）  自检日志见 `selftest_20260930.log`

## 目录

```
python\
├── golden\
│   ├── int8_conv.py    INT8 卷积 + bias + ReLU + 量化 + 饱和（核心）
│   ├── golden_fc.py    INT8 全连接 + argmax（任务表的"顺手"项）
│   └── export_hex.py   生成测试向量并导出 $readmemh 用的 hex
├── data\               导出的测试向量（每个用例一个子目录）
└── selftest_20260930.log   自检证据（给报告用）
```

## 一、数值约定（已冻结，RTL / Python / 驱动 三方必须一致）

| 项 | 约定 |
|---|---|
| 激活 activation | **int8 有符号**，-128..127 |
| 权重 weight | **int8 有符号**，-128..127 |
| 偏置 bias | **int32** |
| 累加器 | **int32**（K*K*C*127*127 远小于 2^31，脚本会 assert 校验） |
| 量化 | `acc = sum(x*w) + bias;  y = acc >>> SHIFT` |
| 右移 | **算术右移**（Verilog 必须写 `>>>`，不能写 `>>`） |
| 激活函数 | ReLU（可选）`y = max(y, 0)` |
| 输出饱和 | 饱和到 int8 [-128, 127] |

> `>>>` 那条特别重要：Python 的 `>>` 对负整数就是向下取整，和 Verilog 的 `>>>` 语义一致；
> 如果 RTL 写成 `>>`（逻辑右移），负数会算错 —— 这是最典型的定点坑。

## 二、怎么用

```bat
:: 自检（numpy 版 vs 纯 python 逐点版，必须完全一致）
python int8_conv.py selftest
python golden_fc.py selftest

:: 看一个小例子
python int8_conv.py demo

:: 生成测试向量（--big 才会生成 28x28 的 MNIST 规模用例）
python export_hex.py
python export_hex.py --big
```

## 三、导出的数据格式

`data\<用例名>\` 下：

| 文件 | 含义 |
|---|---|
| `x_w.hex` / `w_w.hex` | 输入 / 权重，**1 个值占 1 个 32bit 字**，int8 做符号扩展 |
| `x_b.hex` / `w_b.hex` | 同上，但**4 个 int8 打包进 1 个字，小端**（省 BRAM） |
| `b.hex` | 偏置，int32，1 值/字 |
| `expect_w.hex` / `expect_b.hex` | 黄金模型输出（TB 直接比对用） |
| `golden.txt` | 人看的输入/权重/期望输出 |
| `meta.txt` | 形状与参数（H W C K OC stride pad shift relu 和元素个数） |

hex 格式：每行一个 8 位十六进制数、小写、无 `0x` 前缀 —— **`$readmemh` 直接能读**。

## 四、用例清单

| 用例 | 规模 | 测什么 |
|---|---|---|
| `t1_tiny` | x4x4x1, k3, 1oc -> y2x2x1 | **上板 first light 用**，最小可跑 |
| `t2_stride2` | x5x5x1, stride=2 -> y2x2 | 跨步窗口 |
| `t3_saturate` | 全 127 -> 累加 145161 | 上饱和到 127 |
| `t4_multi` | x5x5x2, k3x3x2x2 -> y3x3x2 | 多通道 + 多输出通道 |
| `t5_requant` | shift=4 + ReLU | 算术右移 + ReLU |
| `t6_edge` | 0 / ±极值 / 交替权重, 关 ReLU | 边界激励 + 下饱和到 -128 |
| `t7_mnist28_pad1` | x28x28x1, pad1, 4oc（`--big`） | 之后 MNIST demo 用 |

## 五、自检结果（2026-09-30，全部 PASS）

```
1) int8_conv.py selftest : 6 个用例逐点一致=OK，与 float 参考最大差 0.0
   上饱和 145161 -> 127 OK ；下饱和 -146304 -> -128 OK ；(-7)>>1 = -4 OK
2) golden_fc.py selftest : C=16/64/128 三档一致=OK，argmax 并列取小下标 OK
3) export_hex.py         : 6 个用例导出成功，manifest 已写
```

**额外人工校验**：`t1_tiny` 的期望输出 `[0, 0, 3, 0]` 我手算复核过（Sobel 型权重 + bias=3 + ReLU），正确。

## 六、给 RTL 作者（我自己）的接口约定

1. 乘累加必须在 **int32** 里做，不要中途截断
2. 右移必须用 `>>>`
3. ReLU 在右移**之后**、饱和**之前**
4. 饱和：`>127 -> 127`，`<-128 -> -128`
5. 输出排列顺序（TB 与 RTL 必须一致）：
   `y[oh][ow][oc]`，其中 `oc` **最内层**（同一像素的所有输出通道连续）
   —— 也就是 flatten 顺序 = `((oh*OW)+ow)*OC+oc`