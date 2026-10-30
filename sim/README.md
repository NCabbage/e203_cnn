# sim - CNN 内核仿真与一键回归

当前回归用例共 9 个：`t1_tiny`、`t2_stride2`、`t3_saturate`、`t4_multi`、`t5_requant`、`t6_edge`、`t8_pool`、`t9_fc`、`t10_sobel`。

## 运行

先生成测试向量：

```powershell
cd python/golden
python export_hex.py --big
```

再运行回归：

```powershell
cd ../../sim/run
powershell -ExecutionPolicy Bypass -File run_all.ps1
```

已安装 Vivado 不在默认路径时，可设置：

```powershell
$env:XV_BIN = 'D:\path\to\Vivado\bin'
```

通过时末尾输出 `REGRESSION PASS`。
