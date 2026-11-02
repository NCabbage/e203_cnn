# E203 INT8 CNN 实板演示 UI

> **边界说明：本目录只用于比赛现场展示和录屏，不属于第八章复现流程，也不作为 RTL、黄金模型或上板验收依据。**
>
> 演示页不会改变加速器结果。单张预测、1000 张评测进度、准确率、PMU 和自检 PASS/FAIL 均由当前板卡串口实时返回；没有板卡连接时不会伪造在线状态。

## 功能

- 单张 MNIST 实板推理
- 1000 张 MNIST 连续评测，显示实时进度、准确率、周期和错误样本
- Pool / Sobel / PMU 自检
- 自动复位界面，不改变板卡固件
- UART 分片重传、整张图失败重试，适合长时间批测

## 前置条件

1. PYNQ-Z2 已烧录与当前 RTL 匹配的比特流。
2. E203 已运行 `sdk/LeNet_hw` 批处理固件，串口通常为 `COM9 @ 57600 8N1`。
3. 本机安装 Python 3.10+、NumPy 和 pyserial。
4. MNIST t10k 原始文件放在同一目录：
   - `t10k-images-idx3-ubyte.gz`
   - `t10k-labels-idx1-ubyte.gz`
5. 若要使用页面里的 Pool / Sobel 自检，还需要 Nuclei `riscv-nuclei-elf-objcopy` 和 OpenOCD。

## 启动

```powershell
python -m pip install -r requirements.txt
python app.py `
  --port COM9 `
  --baud 57600 `
  --data-dir "D:\path\to\mnist" `
  --objcopy "D:\path\to\riscv-nuclei-elf-objcopy.exe" `
  --openocd "D:\path\to\openocd.exe"
```

然后打开：

```text
http://127.0.0.1:8765/
```

如果不需要 Pool / Sobel 自检，可以省略 `--objcopy` 和 `--openocd`；实时推理和 1000 张批测仍然可用。

## 常用参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--port` | `COM9` | E203 UART 串口 |
| `--baud` | `57600` | `LeNet_hw` 批处理固件波特率 |
| `--http-port` | `8765` | 本地页面端口 |
| `--data-dir` | `<repo>/dataset/mnist` | MNIST 原始文件目录 |
| `--results-dir` | `demo/live_dashboard/results` | CSV 结果目录 |
| `--batch-elf` | `sdk/LeNet_hw/Debug/LeNet_hw.elf` | 批处理固件 |
| `--selftest-elf` | `LeNet_oneP/Debug/LeNet_oneP.elf` | 自检固件 |
| `--objcopy` | 环境变量 `NUCLEI_OBJCOPY` | Nuclei objcopy |
| `--openocd` | 环境变量 `OPENOCD_EXE` | OpenOCD 可执行文件 |

## 操作说明

- **重新握手**：只重新打开串口并确认固件返回 `READY`，不清空历史结果。
- **复位演示**：清空页面结果、日志、批测计数和曲线，不重启板卡。
- **停止**：批测中请求停止，当前一张图片处理完成后结束。

## 证据边界

- 页面数据只代表当前连接的 PYNQ-Z2 和当前固件。
- 换到征服者 AX7020 或其他板卡时，必须使用对应板卡的 bit、XDC、时钟、复位和实测数据。
- 本目录不得被写成第八章的必备复现步骤。
