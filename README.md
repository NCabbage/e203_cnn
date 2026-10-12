# e203_cnn

E203 (RV32IMAC 蜂鸟) + ICB/AXI-Lite 桥 + INT8 CNN 硬件加速器

## 双人协作

| 代号 | 板卡 | 芯片 |
|---|---|---|
| **A** | 征服者7020 / AX7020 | XC7Z020-1CLG400C |
| **B** | PYNQ-Z2 | XC7Z020-1CLG400C |

> **同步说明与移植文档在 [`docs/`](docs/README.md)。每次改动前先看这里。**

## 目录

| 目录 | 内容 |
|---|---|
| `rtl/` | E203 SoC + CNN 加速器 RTL（共用区） |
| `LeNet_oneP/` `LeNet_hw/` | 裸机 C 软件工程 |
| `python/` | 黄金定点模型、测试向量、PMU/CSR 日志解析 |
| `docs/` | 同步说明、移植方式、验证报告 |

## 快速上手

```bash
git pull origin main
# 看 docs/sync/ 里最新一份同步说明，确认本次要不要重新综合 / 重编 ELF
```
