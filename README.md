# Personal Strategy

面向 A 股个人账户的轻量量化研究与每日策略提醒项目。当前主线已经收敛为：

```text
闭市后自动取数 -> 更新本地离线行情 -> 运行小资金高置信度策略 -> 推送飞书持仓/调仓/收益日报
```

项目只保留当前生产链路和可复现实验所需代码。历史爬虫、外部调研 clone 和一次性测试脚本已经清理，避免仓库继续膨胀。

> 本项目仅用于个人研究和交易辅助，不构成投资建议，也不是自动下单系统。飞书推送是交易计划提醒，实盘下单前仍需核对账户持仓、价格、涨跌停和资金可用性。

## 当前核心策略

当前主策略为：

```text
small_account_high_conviction_policy v4
```

定位：小资金 A 股主板账户，最多持仓 2 只，周频开仓/换仓，日频风控退出。

策略实现：

- `strategies/` 为本地私有策略目录，不提交到公开仓库。
- 当前生产策略入口在本地为 `strategies/ai_native/small_account_high_conviction_policy.py`。
- `SMALL_ACCOUNT_HIGH_CONVICTION_POLICY.md`

核心逻辑：

- 每周第一个交易日生成选股信号；
- 大盘环境不合格时直接空仓；
- 股票池默认排除创业板、科创板、北交所和 ST；
- 候选股必须满足 MA20/MA60/MA120 趋势、20/60 日动量、波动率、回撤和跳空过滤；
- 多因子评分选择最多 2 只股票；
- 单票目标权重上限 `34%`；
- 强市场最高总仓位 `68%`，中性市场 `34%`，弱市场空仓；
- 风控日频检查：跌破 MA20、单票亏损 `6%`、持仓高点回撤 `10%`；
- 撮合层硬限制实际持仓数最多 2 只。

最新正式回测口径：

```text
预热区间：2025-01-06 至 2025-07-04
正式区间：2025-07-05 至 2026-07-05
数据目录：data/offline/a_share_12m_tencent_sina
初始资金：45,000 元
```

最新结果：

| 指标 | 数值 |
| --- | ---: |
| 最终权益 | `52,906.70` |
| 累计收益率 | `17.57%` |
| 年化收益率 | `18.53%` |
| 最大回撤 | `-9.49%` |
| Sharpe | `1.5975` |
| 交易记录数 | `62` |
| 最大实际持仓数 | `2` |

## 真实交易约束

共享撮合逻辑位于本地私有策略目录：

```text
strategies/akshare_strategy_runtime.py
```

默认约束：

| 项目 | 默认值 |
| --- | --- |
| 交易方向 | 只做多 |
| 信号执行 | T 日收盘生成信号，T+1 执行 |
| 执行价格 | 下一交易日开盘价 |
| 最小交易单位 | 100 股整数手 |
| 初始资金 | 回测默认 45,000 元，日报默认 100,000 元 |
| 滑点 | 单边 0.1% |
| 佣金 | 0.03%，最低 5 元 |
| 印花税 | 卖出 0.1% |
| 过户费 | 0.001% |
| 涨跌停 | 9.5% 近似阈值，涨停禁买、跌停禁卖 |
| 容量约束 | 默认关闭，适合当前小资金口径 |

常用环境变量：

```bash
AKSHARE_INITIAL_CASH=45000
AKSHARE_LOT_SIZE=100
AKSHARE_TRADE_DELAY_DAYS=1
AKSHARE_T_PLUS_ONE=1
AKSHARE_EXECUTION_PRICE_FIELD=open
AKSHARE_MAX_PARTICIPATION=0
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

该约束固化在数据生成、离线更新和策略运行流程中，用于匹配个人账户准入和风险偏好。

## 安装

建议 Python 3.10+。

```bash
pip install -r requirements.txt
```

主要依赖：

- `akshare`
- `jqdatasdk`
- `pandas`
- `numpy`
- `requests`

本地敏感文件：

```text
.env.local        # JQData 账号
.feishu_webhook  # 飞书机器人 webhook，可多行配置
```

这些文件已被 `.gitignore` 忽略。

## 数据目录格式

当前主数据目录：

```text
data/offline/a_share_12m_tencent_sina
```

目录结构：

```text
data/offline/<dataset>/
  manifest.json
  universe.csv
  prices_long.csv
  symbols/{symbol}.csv
```

行情字段：

```text
date,symbol,open,high,low,close,volume,amount,turnover
```

## 初始化离线数据

使用 Tencent 为主、Sina 兜底：

```bash
python generate_offline_a_share_data.py \
  --output-dir data/offline/a_share_12m_tencent_sina \
  --months 12 \
  --warmup-days 180 \
  --data-sources tencent,sina \
  --sleep-seconds 0.3 \
  --overwrite
```

如果使用 JQData：

```bash
python jqdata_a_share_data.py offline \
  --output-dir data/offline/a_share_12m_jqdata \
  --months 12 \
  --warmup-days 180 \
  --overwrite
```

JQData 账号配置写入 `.env.local`：

```bash
JQDATA_USERNAME=你的账号
JQDATA_PASSWORD=你的密码
```

## 每日闭市后自动取数

单次执行：

```bash
python update_offline_a_share_daily.py \
  --output-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --data-sources tencent,sina
```

脚本行为：

- 非交易日自动跳过；
- 默认沿用现有 `universe.csv`；
- 每只股票回刷最近 10 个自然日；
- 合并更新 `symbols/{symbol}.csv`；
- 重建 `prices_long.csv`；
- 更新 `manifest.json`；
- 读取 `.feishu_webhook`，向所有配置的飞书机器人推送成功、跳过或失败摘要；
- 使用文件锁避免并发写数据；
- `universe.csv`、单票 CSV、`prices_long.csv`、`manifest.json` 均采用临时文件写入后原子替换，避免半成品文件被读取；
- `run/offline_daily_update_scheduler_state.json` 记录已执行日期，避免 16:30 后重启 daemon 造成当天重复取数。

当前后台部署：

```text
取数时间：每天 16:30
重复执行保护：run/offline_daily_update_scheduler_state.json
PID 文件：run/offline_daily_update.pid
日志文件：logs/offline_daily_update.log
```

查看状态：

```bash
ps -p "$(cat run/offline_daily_update.pid)" -o pid,etime,command
tail -f logs/offline_daily_update.log
```

停止：

```bash
kill "$(cat run/offline_daily_update.pid)"
```

## 每日策略信号推送

取数任务完成后，策略任务每天 16:50 开始检查。脚本会先检查：

```text
data/offline/a_share_12m_tencent_sina/prices_long.csv
```

最新日期是否等于当天。策略运行前还会检查取数任务的 `.daily_update.lock`，如果取数仍在运行，则不会读取行情文件。

如果数据没有更新到当天，策略不会立即运行；后台任务会每 5 分钟重试一次，默认等待到 20:30。只有超过等待截止时间仍未取得当天数据，才会飞书说明“今日数据未更新，跳过策略”。策略端默认直接跳过周末；工作日不依赖额外网络判定交易日，避免行情源抖动导致真实交易日被误跳过。

单次执行：

```bash
python run_daily_strategy_signal.py \
  --data-dir /Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_tencent_sina \
  --output-base-dir /Users/bytedance/cqm/Personal_Strategy/data/backtests/daily_strategy_signals \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --initial-cash 100000
```

飞书日报内容：

- 策略是否运行成功；
- 当日是否有调仓；
- 当前目标持仓；
- 当日收益率；
- 从实盘模拟启用日开始重新计数的累计收益率；
- 当前权益和现金；
- 如无需操作，明确提示“无调整”。

当前后台部署：

```text
策略检查开始时间：每天 16:50
数据等待策略：每 5 分钟重试，默认等到 20:30
读写保护：等待取数锁释放后才读取行情
重复执行保护：run/daily_strategy_scheduler_state.json 记录已执行日期
日报本金：100,000 元
PID 文件：run/daily_strategy_signal.pid
日志文件：logs/daily_strategy_signal.log
```

查看状态：

```bash
ps -p "$(cat run/daily_strategy_signal.pid)" -o pid,etime,command
tail -f logs/daily_strategy_signal.log
```

停止：

```bash
kill "$(cat run/daily_strategy_signal.pid)"
```

完整 `nohup` 和 macOS `launchd` 自启动配置见：

```text
DAILY_UPDATE_DEPLOYMENT.md
```

## 飞书机器人配置

`.feishu_webhook` 支持多个机器人：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxx
https://open.larkoffice.com/open-apis/bot/v2/hook/yyy
```

也支持逗号分隔。脚本会向所有 webhook 推送相同信息。不要把该文件提交到 Git。

## 运行当前策略回测

以下命令依赖本地私有 `strategies/` 目录：

```bash
AKSHARE_OFFLINE_DATA_DIR=data/offline/a_share_12m_tencent_sina \
python strategies/ai_native/small_account_high_conviction_policy.py \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --end-date 20260705 \
  --output-dir data/backtests/small_account_high_conviction_v4_optimized \
  --limit 3046
```

输出：

```text
akshare_equity_curve.csv
akshare_target_weights.csv
akshare_trades.csv
akshare_summary.json
```

## 批量回测

保留批量回测脚本，用于横向比较历史迁移策略。历史策略实现、策略索引和本地技能入口属于个人研究资产，不再提交到公开仓库。

```bash
python batch_strategy_backtest.py \
  --data-dir data/offline/a_share_12m_tencent_sina \
  --output-dir data/backtests/batch_latest \
  --universe-mode adaptive \
  --workers 2 \
  --timeout 1800
```

批量结果只代表本地适配版本，不等同于原始 BigQuant、QuantConnect 或 GitHub 策略。

## 重要文档

- `SMALL_ACCOUNT_HIGH_CONVICTION_POLICY.md`：当前主策略说明。
- `AI_NATIVE_BASE_POLICY.md`：AI 原生基础策略设计。
- `TRADE_LOGIC_DOCUMENTATION.md`：撮合、手续费、T+1、仓位和风控文档。
- `DAILY_UPDATE_DEPLOYMENT.md`：每日取数、策略推送、后台和开机自启动部署。
- `ORIGINAL_RESTORE_AUDIT.md`：历史策略还原/代理复现审计。

## 目录说明

```text
.
├── stock_feature_pipeline.py                  # A 股数据源和特征工程
├── generate_offline_a_share_data.py           # 初始化离线数据
├── update_offline_a_share_daily.py            # 每日闭市后增量取数
├── run_daily_strategy_signal.py               # 每日策略信号和飞书日报
├── jqdata_provider.py                         # JQData 数据封装
├── jqdata_a_share_data.py                     # JQData CLI
├── batch_strategy_backtest.py                 # 批量策略回测
├── DAILY_UPDATE_DEPLOYMENT.md                 # 后台部署说明
├── TRADE_LOGIC_DOCUMENTATION.md               # 交易逻辑说明
└── README.md                                  # 当前文档
```

## Git 忽略规则

不会提交：

- `.env.local`、`.feishu_webhook` 等密钥；
- `.trae/` 本地 IDE 和技能配置；
- `strategies/` 本地策略实现和研究适配代码；
- `data/` 离线行情和回测结果；
- `logs/` 日志；
- `run/` pid 和 lock 文件；
- 外部调研 clone；
- Python 缓存和本地 IDE 配置。

## 已清理内容

为了符合奥卡姆剃刀原则，仓库已移除：

- 历史策略爬虫脚本；
- BigQuant / QuantConnect 单独 scraper；
- 爬虫配置文件；
- EastMoney 反爬调研 clone；
- AkShare 外部数据抓取调研 clone。

后续如果需要重新做策略采集，建议另开独立仓库，不再污染当前生产策略仓库。

## 已知限制

- 当前是日线级策略，不模拟分钟级或逐笔成交。
- 涨跌停使用日线近似，无法模拟真实排队成交概率。
- 当前主策略主要依赖价格和有限 OHLCV 信息，缺少财务、资金流和盘口因子。
- 每日飞书收益是从本地 `run/daily_strategy_live_state.json` 记录的实盘模拟启用日开始计数，不会自动读取真实券商账户。
- 实盘下单前必须人工核对账户持仓、可用资金、涨跌停状态和委托成交情况。
