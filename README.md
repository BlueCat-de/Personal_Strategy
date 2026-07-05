# Personal Strategy

面向 A 股个人账户的量化研究项目。项目目标是用轻量 Python 工具完成数据获取、策略适配、离线回测和交易逻辑审计，重点贴近普通个人账户的真实约束，而不是追求复杂平台依赖。

> 本项目仅用于数据研究和策略验证，不构成投资建议，也不能直接作为自动交易系统使用。

## 核心能力

- 使用 AkShare / JQData 获取 A 股日线数据。
- 生成统一格式的离线行情数据，供所有策略复用。
- 将公开策略适配为轻量 `akshare_strategy.py`，移除 Lean、BigQuant、Backtrader 等重依赖。
- 批量运行策略并输出收益、回撤、Sharpe、交易次数、手续费等指标。
- 默认按个人 A 股账户约束回测：T+1、100 股整数手、45,000 元资金、手续费、滑点、涨跌停和容量约束。
- 为 54 个策略生成 `.trae/skills` 入口和说明文档。

## 当前交易约束

默认回测口径写在 `strategies/akshare_strategy_runtime.py`：

| 项目 | 默认值 |
| --- | --- |
| 初始资金 | `45,000` 元 |
| 交易方向 | long-only，不做空 |
| 最大仓位 | `100%` |
| 信号执行 | T 日收盘信号，T+1 日执行 |
| 执行价格 | 下一交易日 `open`，叠加滑点 |
| 最小交易单位 | `100` 股 |
| 滑点 | 单边 `0.1%` |
| 成交容量 | 单日成交额 `0.5%` |
| 涨跌停 | 9.5% 近似阈值，涨停禁买、跌停禁卖 |
| 佣金 | `0.03%`，最低 `5` 元 |
| 印花税 | 卖出 `0.1%` |
| 过户费 | `0.001%` |

可用环境变量覆盖：

```bash
AKSHARE_INITIAL_CASH=45000
AKSHARE_LOT_SIZE=100
AKSHARE_TRADE_DELAY_DAYS=1
AKSHARE_T_PLUS_ONE=1
AKSHARE_EXECUTION_PRICE_FIELD=open
AKSHARE_SLIPPAGE_RATE=0.001
AKSHARE_COMMISSION_RATE=0.0003
AKSHARE_MIN_COMMISSION=5
AKSHARE_STAMP_TAX_RATE=0.001
AKSHARE_TRANSFER_FEE_RATE=0.00001
```

## 股票池约束

默认排除：

- 创业板：`300`、`301`
- 科创板：`688`、`689`
- 北交所：`4`、`8`、`920`
- ST / *ST 股票

这是为了匹配当前个人账户限制和风险偏好。

## 安装

建议使用 Python 3.10+：

```bash
pip install -r requirements.txt
```

主要依赖：

- `akshare`
- `jqdatasdk`
- `pandas`
- `numpy`
- `requests`
- `beautifulsoup4`

## JQData 配置

如需使用 JQData，把账号信息写到本地 `.env.local`。该文件已被 `.gitignore` 忽略，不会提交。

```bash
JQDATA_USERNAME=你的账号
JQDATA_PASSWORD=你的密码
```

检查账号状态：

```bash
python jqdata_a_share_data.py status
```

拉取单只股票：

```bash
python jqdata_a_share_data.py history \
  --symbol 002460 \
  --start-date 20250101 \
  --end-date 20260403
```

## 生成离线数据

推荐使用 JQData 生成统一离线数据：

```bash
python jqdata_a_share_data.py offline \
  --output-dir data/offline/a_share_12m_jqdata \
  --months 12 \
  --warmup-days 180 \
  --overwrite
```

也可以使用多源 fallback：

```bash
python generate_offline_a_share_data.py \
  --output-dir data/offline/a_share_12m \
  --months 12 \
  --warmup-days 180 \
  --data-sources jqdata,tencent,eastmoney,sina \
  --sleep-seconds 0.5 \
  --overwrite
```

离线数据格式：

```text
data/offline/<dataset>/
  manifest.json
  universe.csv
  prices_long.csv
  symbols/{symbol}.csv
```

单票行情字段：

```text
date,symbol,open,high,low,close,volume,amount,turnover
```

## 运行批量回测

当前推荐命令：

```bash
python batch_strategy_backtest.py \
  --data-dir data/offline/a_share_12m_jqdata \
  --output-dir data/backtests/jqdata_12m_realistic_tplus1_weekly_open_restore_45k_no_single_symbol \
  --universe-mode adaptive \
  --workers 2 \
  --timeout 1800
```

输出：

```text
data/backtests/<run_name>/
  REPORT.md
  summary.csv
  summary.json
  run_config.json
  excluded_single_symbol_strategies.csv
  {strategy_skill}/
    akshare_equity_curve.csv
    akshare_target_weights.csv
    akshare_trades.csv
    akshare_summary.json
```

## 运行单个策略

示例：

```bash
AKSHARE_OFFLINE_DATA_DIR=data/offline/a_share_12m_jqdata \
python .trae/skills/strategy-bigquant-multi-factor-17ed5d18/strategy.py \
  --start-date 20250403 \
  --end-date 20260403 \
  --output-dir data/backtests/single_strategy_smoke
```

## 当前重要文档

- `TRADE_LOGIC_DOCUMENTATION.md`：当前回测系统的完整交易逻辑文档。
- `ORIGINAL_RESTORE_AUDIT.md`：47 个有效策略的原始还原/代理复现审计。
- `strategies/AKSHARE_MIGRATION_SUMMARY.md`：策略迁移到 AkShare runtime 的摘要。
- `.trae/skills/STRATEGY_SKILLS_INDEX.md`：54 个策略 skill 索引。

## 策略还原说明

当前策略分三类：

1. **已还原**：公开源码完整，已按原始核心信号实现。
2. **部分还原**：信号或交易规则可还原，但资产池、平台数据或 A 股限制导致结果不完全等同原策略。
3. **代理复现**：原策略依赖 BigQuant 私有因子表、QuantConnect 基本面字段或缺失源码，只能用本地可得因子近似。

因此，批量回测结果只能说明“当前本地实现”在当前数据和撮合模型下的表现，不能简单等同于原始平台策略表现。

## 当前已知限制

- 仍是日线级回测，不模拟分钟级或逐笔成交。
- 涨跌停判断仍是日线近似，不是真实排队成交概率。
- 止损止盈只在部分技术策略中实现，且按收盘信号、下一交易日执行。
- 多空策略在 long-only 账户下会被改写，不能代表原始多空收益。
- JQData 12 个月样本较短，统计显著性有限。
- 多数 BigQuant 策略由于私有因子缺失，仍属于代理复现。

## 目录说明

```text
.
├── stock_feature_pipeline.py          # A 股特征工程
├── generate_offline_a_share_data.py   # 多源 fallback 离线数据生成
├── jqdata_provider.py                 # JQData provider
├── jqdata_a_share_data.py             # JQData CLI
├── batch_strategy_backtest.py         # 批量回测入口
├── strategies/                        # AkShare 策略适配脚本
├── .trae/skills/                      # 策略 skill 文档和入口
├── TRADE_LOGIC_DOCUMENTATION.md       # 交易逻辑文档
└── ORIGINAL_RESTORE_AUDIT.md          # 策略还原审计
```

## Git 忽略规则

不会提交以下内容：

- `.env.local` 等本地密钥
- `data/` 离线数据和回测结果
- `logs/` 日志
- `.crawler_state/` 爬虫状态
- 外部调研 clone：`akshare/`、`akshare-stock-data-fetcher/`

这些文件都可以在本地重新生成。
