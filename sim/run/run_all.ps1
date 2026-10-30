﻿# ============================================================================
#  run_all.ps1  --  CNN 内核一键回归（对照 Python 黄金模型）
#
#  用法：
#     powershell -ExecutionPolicy Bypass -File run_all.ps1
#     powershell -ExecutionPolicy Bypass -File run_all.ps1 t1_tiny
#     powershell -ExecutionPolicy Bypass -File run_all.ps1 t1_tiny,t4_multi
#
#  原理：xvlog 编译一次、xelab 精化一次；每个用例只换数据文件
#        （x_w.hex / w_w.hex / b.hex / expect_w.hex / params.hex），
#        参数由 params.hex 传给 TB，所以不用为每个用例重新精化。
#
#  注意：本文件必须存成 UTF-8 with BOM，否则 Windows PowerShell 按 GBK 读中文会报语法错。
# ============================================================================
param([string[]]$Cases = @('t1_tiny','t2_stride2','t3_saturate','t4_multi','t5_requant','t6_edge','t8_pool','t9_fc','t10_sobel'))

$ErrorActionPreference = 'Stop'
$XV   = if ($env:XV_BIN) { $env:XV_BIN } else { 'D:\AMDDesignTools\2025.2\Vivado\bin' }
$HERE = $PSScriptRoot
$DATA = Join-Path $HERE '..\..\python\data'
$LOGS = Join-Path $HERE 'logs'
New-Item -ItemType Directory -Force -Path $LOGS | Out-Null
Set-Location $HERE

$rtl = @(
    '..\..\rtl\cnn\cnn_mac.v',
    '..\..\rtl\cnn\cnn_quant.v',
    '..\..\rtl\cnn\cnn_top.v',
    '..\..\sim\tb\rom_all_tap_sim.v',
    '..\..\sim\tb\tb_cnn_conv.v'
)

Write-Host '=== [1/3] xvlog compile ===' -ForegroundColor Cyan
& "$XV\xvlog.bat" @rtl | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host 'xvlog FAILED' -ForegroundColor Red; exit 1 }

Write-Host '=== [2/3] xelab elaborate ===' -ForegroundColor Cyan
& "$XV\xelab.bat" -debug typical tb_cnn_conv -s tb_sim 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Write-Host 'xelab FAILED' -ForegroundColor Red; exit 1 }

Write-Host '=== [3/3] run cases ===' -ForegroundColor Cyan
$rows = @()
foreach ($c in $Cases) {
    $src = Join-Path $DATA $c
    if (-not (Test-Path $src)) {
        $rows += [pscustomobject]@{ Case = $c; Result = 'SKIP(no data)'; Cycles = '-' }
        continue
    }

    foreach ($fn in 'x_w.hex','w_w.hex','b.hex','expect_w.hex','params.hex') {
        Copy-Item (Join-Path $src $fn) $HERE -Force
    }

    $out = & "$XV\xsim.bat" tb_sim -runall 2>&1 | Out-String
    Set-Content -LiteralPath (Join-Path $LOGS "$c.log") -Value $out -Encoding UTF8

    $cyc = '-'
    $m = [regex]::Match($out, 'CYCLES\s*=\s*(\d+)')
    if ($m.Success) { $cyc = $m.Groups[1].Value }

    if ($out -match '\*\*\* PASS \*\*\*') {
        $rows += [pscustomobject]@{ Case = $c; Result = 'PASS'; Cycles = $cyc }
        Write-Host ("  {0,-14} PASS   ({1} cycles)" -f $c, $cyc) -ForegroundColor Green
    } elseif ($out -match '\*\*\* FAIL \*\*\*') {
        $rows += [pscustomobject]@{ Case = $c; Result = 'FAIL'; Cycles = $cyc }
        Write-Host ("  {0,-14} FAIL   ({1} cycles)" -f $c, $cyc) -ForegroundColor Red
    } else {
        $rows += [pscustomobject]@{ Case = $c; Result = 'ERROR'; Cycles = $cyc }
        Write-Host ("  {0,-14} ERROR  (no PASS/FAIL marker)" -f $c) -ForegroundColor Yellow
    }
}

Write-Host ''
Write-Host '================ REGRESSION SUMMARY ================' -ForegroundColor Cyan
$rows | Format-Table -AutoSize | Out-String | Write-Host
$pass = ($rows | Where-Object { $_.Result -eq 'PASS' }).Count
$bad  = ($rows | Where-Object { $_.Result -ne 'PASS' -and $_.Result -ne 'SKIP(no data)' }).Count
Write-Host ("TOTAL: {0} PASS / {1} FAIL-OR-ERROR    logs: {2}" -f $pass, $bad, $LOGS)
Write-Host '===================================================='
if ($bad -eq 0 -and $pass -gt 0) { Write-Host 'REGRESSION PASS' -ForegroundColor Green; exit 0 }
else { Write-Host 'REGRESSION FAIL' -ForegroundColor Red; exit 1 }