# LeNet-5 INT8 黄金定点模型

本目录提供与 RTL / 板级实现一致的 LeNet-5 INT8 黄金模型，以及逐层比对工具。

## 运行环境

- Python 3
- numpy

## 目录

```text
int8_conv.py         INT8 卷积 + ReLU 基础模型
golden_fc.py         INT8 全连接 + argmax 基础模型
lenet_golden.py      完整 LeNet-5 黄金模型
compare_hw_bins.py   JTAG 内存 dump vs 黄金模型逐层比对
compare_layers.py    UART 文本逐层 dump vs 黄金模型逐层比对
examples/digit5/     digit5 的黄金张量和硬件 JTAG dump 样例
```

`lenet_golden.py` 直接从仓库中的 C 头文件解析权重：

```text
LeNet_hw/include/lenet_weights_hw.h
LeNet_hw/include/lenet_c5_split.h
LeNet_hw/include/mnist_100.h
LeNet_oneP/include/mnist_samples.h
```

路径按仓库根目录相对定位，不依赖某个人的绝对路径。

## 1. 运行黄金模型

在仓库根目录执行：

```bash
cd python/golden
python lenet_golden.py --sample 5
python lenet_golden.py --all
```

digit5 期望输出：

```text
out = [-128, -128, -44, -107, -62, 127, 74, -128, 48, -3]
pred = 5
```

10 张 MNIST 前 10 张期望：

```text
9/10
[2] label=2 pred=4 MISMATCH
```

## 2. 导出逐层张量

```bash
python lenet_golden.py --sample 5 --dump golden_layers_digit5
```

会生成：

```text
input.txt
c1.txt
s2.txt
c3.txt
s4.txt
c5.txt
f6.txt
out.txt
```

每个文件第一行是 shape，后面是逐点 int8 值。

## 3. 用 JTAG dump 做逐层比对

板子上运行 `LeNet_oneP.elf` 后，用 JTAG 直接读取 DTCM 中的数组：

```text
c1_out      0x9000011c  3456 B
s2_out      0x900036a0   864 B
c3_out      0x90000e9c  1024 B
s4_out      0x90003a00   256 B
c5_out      0x9000129c   120 B
f6_out      0x90001314    84 B
final_out   0x90001368    40 B
```

注意：地址可能随工程改动变化，务必用当前 ELF 的符号表确认：

```bash
riscv-nuclei-elf-nm -n LeNet_oneP.elf | grep -E "c1_out|s2_out|c3_out|s4_out|c5_out|f6_out|final_out"
```

OpenOCD dump 示例：

```tcl
dump_image {c1_hw.bin} 0x9000011c 3456
dump_image {s2_hw.bin} 0x900036a0 864
dump_image {c3_hw.bin} 0x90000e9c 1024
dump_image {s4_hw.bin} 0x90003a00 256
dump_image {c5_hw.bin} 0x9000129c 120
dump_image {f6_hw.bin} 0x90001314 84
dump_image {out_hw.bin} 0x90001368 40
```

然后用：

```bash
python compare_hw_bins.py --golden examples/digit5/golden --hw <你的dump目录>
```

仓库自带 digit5 样例，直接运行即可：

```bash
python compare_hw_bins.py
```

期望：

```text
C1  mismatch=0  max_abs_diff=0  PASS
S2  mismatch=0  max_abs_diff=0  PASS
C3  mismatch=0  max_abs_diff=0  PASS
S4  mismatch=0  max_abs_diff=0  PASS
C5  mismatch=0  max_abs_diff=0  PASS
F6  mismatch=0  max_abs_diff=0  PASS
OUT mismatch=0  max_abs_diff=0  PASS
OVERALL: PASS
```

## 4. 数据布局说明

- `c1_out/s2_out/c3_out/s4_out` 在 C 中是 `[C][H][W]`；
- 黄金模型输出是 `[H][W][C]`；
- `compare_hw_bins.py` 会自动 transpose 后比较；
- `c5_out/f6_out` 是 1D 输出通道顺序；
- `final_out` 是 10 个 int32，但数值已经饱和到 int8 范围。

## 5. 结论说明

当前 digit5 黄金模型和板级 RTL 逐层完全一致：

```text
C1/S2/C3/S4/C5/F6/OUT  全部 mismatch=0
```

10 张 MNIST 的 9/10 和 `label=2 -> pred=4` 错例也完全一致。
因此该错例来自当前 INT8 量化模型本身，不是 RTL/硬件链路错误。