---
description: "对指定策略运行本地回测（train+val 或全期揭盲）"
---

# 回测：$ARGUMENTS

对 `$ARGUMENTS` 策略运行本地回测。

## 使用方式

### train+val 回测（开发阶段）
```bash
.venv/Scripts/python.exe -m ashare_quant.strategies.$ARGUMENTS \
  --output-dir data/backtests/${ARGUMENTS}_trainval
```

### test 揭盲（最终验证，只跑一次）
```bash
.venv/Scripts/python.exe -m ashare_quant.strategies.$ARGUMENTS \
  --include-test --end-date 2026-07-17 \
  --output-dir data/backtests/${ARGUMENTS}_test
```

### 指定参数扫描
```bash
# 例如调整滑点、持仓数
.venv/Scripts/python.exe -m ashare_quant.strategies.$ARGUMENTS \
  --slippage 0.005 --positions 10 \
  --output-dir data/backtests/${ARGUMENTS}_slip50bp_n10
```

## 样本隔离铁律
- 开发阶段：`--end-date 2020-12-31`（默认）
- test 揭盲：`--include-test --end-date 2026-07-17`（**只跑一次**）
- 揭盲后不回头调参

## 产出
```
data/backtests/<name>/
├── local_raw_perf.csv     # 日频净值（17列）
├── performance.json       # 三段指标
├── signal_debug.csv       # 每次调仓选股详情
└── strategy_config.json   # 运行配置
```
