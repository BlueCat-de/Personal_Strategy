# Personal Strategy

BigQuant SDK-only A 股量化研究与回测项目。

这个分支按“奥卡姆剃刀”原则重构：数据只来自 BigQuant SDK，回测只使用 BigQuant BigTrader。旧的 AkShare、Tencent/Sina、JQData、本地撮合 runtime、每日 daemon、飞书推送和批量策略代理复现链路已经从公开代码中移除。

> 本项目仅用于个人研究和交易辅助，不构成投资建议，也不是自动下单系统。

## 当前架构

```text
BigQuant DAI
    |
    |  读取 cn_stock_bar1d 必要字段
    v
bigquant_provider.py
    |
    |  后复权价 -> qfq 口径，成交量股 -> 手
    v
data/offline/a_share_12m_bigquant/      # 本地缓存，不提交
    |
    v
bigquant_strategy.py
    |
    |  生成周频权重信号
    v
BigQuant BigTrader
    |
    v
data/backtests/bigquant_strategy/       # 回测结果，不提交
```

## 文件说明

```text
bigquant_provider.py
  BigQuant DAI 数据适配层。负责认证、股票代码转换、字段选择、复权转换、成交量单位转换。

generate_offline_a_share_bigquant.py
  使用 BigQuant SDK 生成本地行情缓存，避免重复消耗每周 cell 额度。

bigquant_strategy.py
  当前唯一策略入口。用 retirement-safe 的 BigQuant 本地缓存生成信号，并用 BigTrader 回测。

BIGQUANT_SDK_USAGE.md
  BigQuant SDK 安装、认证、DAI、BigTrader 使用笔记。

requirements.txt
  只保留 pandas/numpy。BigQuant SDK 使用官方私有源单独安装。
```

## 本地私有内容

以下路径不会提交到 Git：

```text
.env.local          # BigQuant API Key
.bigquant/          # BigQuant SDK 本地日志和 telemetry
data/               # BigQuant 数据缓存和回测结果
logs/               # 临时日志
run/                # 运行状态
strategies/         # 个人实验策略
.trae/ .vscode/     # IDE 配置
```

`.env.local` 示例：

```bash
BIGQUANT_API_KEY=YOUR_AK.YOUR_SK
```

不要把真实 key 写入 README、代码、日志或提交历史。

## 环境安装

BigQuant SDK 要求 Python 3.11。推荐单独 conda 环境：

```bash
conda create -n bigquant python=3.11 pip -y
conda activate bigquant

python -m pip install --upgrade pip setuptools wheel
python -m pip install 'bigquant[bigtrader]' -i https://pypi.bigquant.com/simple/
python -m pip install -r requirements.txt
```

验证：

```bash
conda activate bigquant
python -c "import bigquant; from bigquant import dai, bigtrader; print('ok')"
```

## BigQuant 认证

优先使用本项目的 `.env.local`：

```bash
cat > .env.local <<'EOF'
BIGQUANT_API_KEY=YOUR_AK.YOUR_SK
EOF
```

也可以使用官方配置：

```bash
bq auth --apikey YOUR_AK.YOUR_SK
```

## 数据字段口径

当前使用数据表：

```text
cn_stock_bar1d
```

官方文档：

```text
https://bigquant.com/data/datasources/cn_stock_bar1d
```

关键口径：

- `open/high/low/close` 是后复权价格。
- `adjust_factor` 是累计后复权因子。
- `volume` 单位是股。
- `turn` 是换手率。

项目转换规则：

```text
qfq_price = hfq_price / latest_adjust_factor
volume_hand = volume_share / 100
turnover = turn
```

这样可以和此前本地 qfq 行情口径对齐。

## 生成本地 BigQuant 数据缓存

BigQuant 试用权限有每周 cell 限额。应先下载到本地缓存，后续回测优先读本地 CSV。

小样本测试：

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 1 \
  --warmup-days 5 \
  --limit 5 \
  --output-dir data/offline/a_share_1m_bigquant_smoke \
  --overwrite
```

完整 12 个月缓存：

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 12 \
  --warmup-days 180 \
  --output-dir data/offline/a_share_12m_bigquant \
  --batch-size 100 \
  --overwrite
```

说明：

- `HOME="$(pwd)"` 让 BigQuant SDK 的 `.bigquant/` 日志写到项目目录内，避免某些 IDE 沙盒拦截用户目录。
- `data/` 被 `.gitignore` 忽略，不会提交。
- 完整数据生成后优先复用本地文件，避免重复消耗 quota。

当前本机已验证生成结果：

```text
data/offline/a_share_12m_bigquant
rows=1,084,914
symbols=3,045
start=2025-01-07
end=2026-07-06
```

## 运行 BigTrader 回测

小样本 smoke test：

```bash
HOME="$(pwd)" conda run -n bigquant python bigquant_strategy.py \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --limit 500 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_smoke_500
```

完整股票池回测：

```bash
HOME="$(pwd)" conda run -n bigquant python bigquant_strategy.py \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_v4/20260706
```

输出文件：

```text
bigquant_weight_signals.csv  # 传给 BigTrader 的目标权重信号
bigtrader_raw_perf.csv       # BigTrader 原生逐日绩效
bigtrader_summary.json       # BigTrader summary 和运行配置
universe.csv                 # 本次股票池
```

## 当前策略逻辑

策略定位：`small_account_high_conviction_policy v4` 的 BigQuant/BigTrader 迁移版。它面向小资金 A 股主板账户，最多持仓 2 只，周频调仓，日频风控退出。

股票池过滤：

- 排除创业板：`300/301`
- 排除科创板：`688/689`
- 排除北交所：`4/8/920`
- 排除 ST/*ST

市场过滤：

- 计算全市场 MA20/MA60/MA120 宽度。
- 强市场：总仓位 68%。
- 中性市场：总仓位 34%。
- 弱市场：空仓。

选股评分：

- 候选股票必须同时满足价格、MA20/MA60/MA120 趋势、20/60/120 日动量、不过热、低波动、60 日回撤、信号日跳空和流动性过滤。
- 评分因子包括 20/60/120 日动量、相对 MA60 趋势强度、低波动、下行波动、60 日回撤修复和成交活跃度。
- 最终最多选 2 只，按 20 日波动率倒数分配权重，单票上限 34%。

风控退出：

- 跌破 `trend_exit_window`，默认 MA20；
- 相对信号入场价亏损超过 6%；
- 相对持仓后高点回撤超过 10%。

BigTrader 设置：

- 初始资金默认 `100000`。
- 使用 `context.order_target_percent` 调仓。
- 买卖成交价使用下一根日线 `open`。
- `context.set_stock_t1(1)` 开启 T+1。
- `PerOrder(buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0)` 近似 A 股手续费和印花税。

## 已验证结果

完整股票池 BigTrader 回测：

```text
strategy=small_account_high_conviction_policy_v4_bigquant
universe_count=3,045
signal_rows=42
traded_instruments=17
return_ratio=2.93
annual_return_ratio=3.06
benchmark_ratio=22.11
max_drawdown=1.16
win_ratio=47.83
```

该结果使用 BigQuant 数据和 BigTrader 撮合，已经不再沿用旧本地 runtime 的收益口径。与旧 v4 回测结果不同是正常的，主要来自回测引擎、订单执行、分红处理、股票池和信号权重执行细节差异。

## 注意事项

- BigTrader 会按传入的 `instruments` 再读取行情。若信号为空，脚本会直接失败，避免 BigTrader 加载全市场消耗 quota。
- `cn_stock_prefactors` 等宽表字段很多，不要 `SELECT *`。
- 本项目只读取必要字段。
- 当前分支不再维护旧每日 daemon 和飞书推送，后续如果要恢复自动化，应基于 BigQuant SDK 重新设计，而不是复活旧链路。
