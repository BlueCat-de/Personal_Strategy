# Personal Strategy

面向 A 股个人账户的轻量量化研究与每日策略提醒项目。当前主线已经收敛为：

```text
闭市后自动取数 -> 更新本地离线行情 -> 运行小资金高置信度策略 -> 推送飞书持仓/调仓/收益日报
```

项目只保留当前生产链路和可复现实验所需代码。历史爬虫、外部调研 clone 和一次性测试脚本已经清理，避免仓库继续膨胀。

> 本项目仅用于个人研究和交易辅助，不构成投资建议，也不是自动下单系统。飞书推送是交易计划提醒，实盘下单前仍需核对账户持仓、价格、涨跌停和资金可用性。

## 公开仓库和本地私有内容

这个仓库是一个“生产框架 + 数据更新 + 调度 + 推送”的公开版本。为了保护个人隐私和策略资产，以下内容不会出现在 Git 远端：

| 路径 | 是否提交 | 说明 |
| --- | --- | --- |
| `strategies/` | 否 | 本地私有策略实现、撮合 runtime、研究适配代码。公开仓库只保留调用约定。 |
| `data/` | 否 | 离线行情、回测结果、每日策略输出，体积大且属于本地运行产物。 |
| `logs/` | 否 | 取数和策略 daemon 日志。 |
| `run/` | 否 | PID、lock、scheduler state、live state 等运行状态。 |
| `.feishu_webhook` | 否 | 飞书机器人 webhook。 |
| `.env.local` | 否 | JQData 或其他本地账号配置。 |
| `.trae/`、`.vscode/` | 否 | 本地 IDE 配置。 |

因此，别人 clone 这个仓库后可以直接使用数据生成、每日更新、调度和推送框架，但如果要运行“当前主策略”，需要自己在本地补齐 `strategies/` 目录中的策略实现，或者按本文的策略接口约定接入自己的策略。

## 系统架构

```text
AkShare/Tencent/Sina/JQData
        |
        v
generate_offline_a_share_data.py     # 首次生成 12 个月离线行情
        |
        v
data/offline/<dataset>/              # 本地私有数据目录，不提交
        |
        v
update_offline_a_share_daily.py      # 每天闭市后增量更新，原子写入
        |
        v
run_daily_strategy_signal.py         # 等待今日数据 -> 调用本地策略 -> 生成日报
        |
        v
Feishu / Lark bot                    # 推送取数状态、持仓、调仓、收益
```

生产保护已经内置在调度链路中：

- 取数任务使用 `fcntl` 文件锁，避免并发写数据；
- `universe.csv`、单票 CSV、`prices_long.csv`、`manifest.json` 使用临时文件写入后 `os.replace()` 原子替换；
- 策略读取前检查取数锁，避免读到半成品；
- 取数 daemon 和策略 daemon 都有本地 scheduler state，避免进程重启后重复执行当天任务；
- 策略端默认周末跳过，工作日等待数据更新到当天；
- 飞书支持多个 webhook，同一消息会发给所有机器人。

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

## 从零部署

建议 Python 3.10+。

### 1. 克隆项目

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy
```

### 2. 创建 Python 环境

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

主要依赖：

- `akshare`
- `jqdatasdk`
- `pandas`
- `numpy`
- `requests`

### 3. 准备本地敏感配置

飞书机器人配置写入 `.feishu_webhook`：

```bash
cat > .feishu_webhook <<'EOF'
https://open.feishu.cn/open-apis/bot/v2/hook/你的机器人token
EOF
```

如果有多个机器人，可以一行一个：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/xxx
https://open.larkoffice.com/open-apis/bot/v2/hook/yyy
```

如需使用 JQData，把账号写入 `.env.local`：

```bash
cat > .env.local <<'EOF'
JQDATA_USERNAME=你的账号
JQDATA_PASSWORD=你的密码
EOF
```

本地敏感文件不会被 Git 提交：

```text
.env.local        # JQData 账号
.feishu_webhook  # 飞书机器人 webhook，可多行配置
```

这些文件已被 `.gitignore` 忽略。

### 4. 准备本地策略目录

公开仓库不包含 `strategies/`。如果你只是想验证数据更新链路，可以先不准备策略目录；如果要运行每日策略信号，需要在本地创建：

```text
strategies/
  ai_native/
    small_account_high_conviction_policy.py
  akshare_strategy_runtime.py
```

也可以不用这个策略名称，但需要在执行 `run_daily_strategy_signal.py` 时通过 `--strategy-path` 指向你自己的策略文件。

策略脚本需要满足本文“策略接口约定”一节。

### 5. 创建运行目录

```bash
mkdir -p data/offline data/backtests logs run
```

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
  --output-dir "$(pwd)/data/offline/a_share_12m_tencent_sina" \
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

后台常驻启动：

```bash
nohup python update_offline_a_share_daily.py \
  --daemon \
  --run-at 16:30 \
  --workers 8 \
  --output-dir "$(pwd)/data/offline/a_share_12m_tencent_sina" \
  --data-sources tencent,sina \
  --log-file "$(pwd)/logs/offline_daily_update.log" \
  > "$(pwd)/logs/offline_daily_update.nohup.log" 2>&1 &

ps axo pid=,command= | awk '/[u]pdate_offline_a_share_daily.py --daemon/ {print $1; exit}' > run/offline_daily_update.pid
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
  --data-dir "$(pwd)/data/offline/a_share_12m_tencent_sina" \
  --output-base-dir "$(pwd)/data/backtests/daily_strategy_signals" \
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

后台常驻启动：

```bash
nohup python run_daily_strategy_signal.py \
  --daemon \
  --run-at 16:50 \
  --data-ready-retry-seconds 300 \
  --data-ready-deadline 20:30 \
  --data-lock-wait-seconds 1800 \
  --data-dir "$(pwd)/data/offline/a_share_12m_tencent_sina" \
  --output-base-dir "$(pwd)/data/backtests/daily_strategy_signals" \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --initial-cash 100000 \
  --log-file "$(pwd)/logs/daily_strategy_signal.log" \
  > "$(pwd)/logs/daily_strategy_signal.nohup.log" 2>&1 &

ps axo pid=,command= | awk '/[r]un_daily_strategy_signal.py --daemon/ {print $1; exit}' > run/daily_strategy_signal.pid
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

## 策略接口约定

`run_daily_strategy_signal.py` 本身不包含具体选股逻辑。它负责检查数据、等待取数锁释放、调用本地策略、读取策略输出、生成日报并推送飞书。

被调用的策略脚本需要支持以下参数：

```bash
python your_strategy.py \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --end-date 20260706 \
  --output-dir data/backtests/example \
  --limit 3046
```

策略脚本需要读取环境变量：

```text
AKSHARE_OFFLINE_DATA_DIR  # 离线行情目录
AKSHARE_INITIAL_CASH      # 初始资金
```

策略脚本执行结束后，必须在 `--output-dir` 下生成：

| 文件 | 必需字段 | 用途 |
| --- | --- | --- |
| `akshare_equity_curve.csv` | `date,equity,cash` | 计算最终权益、现金、当日收益。 |
| `akshare_target_weights.csv` | `date,<symbol columns...>` | 读取最后一日目标持仓。 |
| `akshare_trades.csv` | `date,symbol,executed_shares,trade_notional,requested_weight_change` | 读取目标日期调仓动作。 |

如果策略没有成交记录，也需要生成空的 `akshare_trades.csv` 文件，至少保留表头。这样每日信号脚本可以稳定判断“无调仓”。

最小可运行策略目录示例：

```text
strategies/
  my_strategy.py
```

启动每日信号时指定：

```bash
python run_daily_strategy_signal.py \
  --strategy-path strategies/my_strategy.py \
  --data-dir "$(pwd)/data/offline/a_share_12m_tencent_sina" \
  --output-base-dir "$(pwd)/data/backtests/daily_strategy_signals" \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --initial-cash 100000
```

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

## 部署检查清单

新机器部署完成后，按下面顺序检查：

```bash
# 1. Python 依赖
python -m py_compile \
  stock_feature_pipeline.py \
  generate_offline_a_share_data.py \
  update_offline_a_share_daily.py \
  run_daily_strategy_signal.py

# 2. 敏感配置
test -f .feishu_webhook && echo "feishu webhook exists"

# 3. 数据目录
test -f data/offline/a_share_12m_tencent_sina/prices_long.csv && echo "offline data exists"

# 4. 取数 daemon
ps -p "$(cat run/offline_daily_update.pid)" -o pid,etime,command

# 5. 策略 daemon
ps -p "$(cat run/daily_strategy_signal.pid)" -o pid,etime,command

# 6. 最近日志
tail -n 20 logs/offline_daily_update.log
tail -n 20 logs/daily_strategy_signal.log
```

确认数据最新日期：

```bash
python - <<'PY'
from pathlib import Path
import pandas as pd

path = Path("data/offline/a_share_12m_tencent_sina/prices_long.csv")
df = pd.read_csv(path, usecols=["date", "symbol"], dtype={"date": str, "symbol": str})
print("latest_data_date=", pd.to_datetime(df["date"], errors="coerce").max().strftime("%Y%m%d"))
print("symbols=", df["symbol"].nunique())
print("rows=", len(df))
PY
```

确认实盘模拟收益口径：

```bash
cat run/daily_strategy_live_state.json
```

如果你要从某一天重新开始统计飞书日报收益，可以先停止策略 daemon，然后删除或备份 `run/daily_strategy_live_state.json`，再用 `--reset-live-state` 做一次初始化运行。

## macOS 运行注意事项

当前后台部署依赖本机 Python 进程。Mac 盒盖或进入睡眠后，进程通常不会被杀死，但会暂停执行；开盖唤醒后才会继续运行。因此：

- 如果开盖时间早于 `16:30`，通常不影响当天取数和策略；
- 如果开盖时间在 `16:30` 到 `20:30` 之间，取数和策略可能在开盖后补跑；
- 如果开盖时间晚于 `20:30`，可能产生延迟信号，不适合作为严格生产执行；
- 如果系统重启、断电、升级或进程被杀，需要依赖 `launchd` 自动拉起。

更稳妥的方式：

- Mac 接电；
- 系统设置中关闭自动睡眠，只允许关闭显示器；
- 使用 `launchd` 开机/登录自启动，配置见 `DAILY_UPDATE_DEPLOYMENT.md`；
- 对严格生产环境，优先迁移到云服务器、NAS 或长期在线主机。

## 常见问题

### clone 后为什么不能直接运行当前主策略？

因为 `strategies/` 是本地私有目录，不提交到公开仓库。公开仓库保留的是数据、调度、推送和策略调用框架。你需要自己补齐策略实现，或通过 `--strategy-path` 接入自己的策略。

### 为什么 `.feishu_webhook` 不存在？

这是本地密钥文件，需要自己创建。它已被 `.gitignore` 忽略，不应该提交。

### 为什么没有 `data/`？

行情数据体积大，而且是运行产物。首次使用需要运行 `generate_offline_a_share_data.py` 生成，之后由 `update_offline_a_share_daily.py` 每日增量更新。

### 为什么策略提示今日数据未更新？

策略脚本会检查 `prices_long.csv` 的最新日期。如果数据源延迟、电脑睡眠、网络异常或取数任务失败，策略会等待到 `20:30`；仍未更新时会跳过，避免基于旧数据生成信号。

### 为什么飞书收益率不是历史回测收益？

飞书日报使用 `run/daily_strategy_live_state.json` 从部署基准日重新计数，目的是模拟“从今天开始严格执行策略”的账户权益变化。历史回测收益会保留在 `backtest_total_return` 字段中，但不会作为实盘模拟累计收益。

### 如何彻底重启两个后台任务？

```bash
kill "$(cat run/offline_daily_update.pid)" 2>/dev/null || true
kill "$(cat run/daily_strategy_signal.pid)" 2>/dev/null || true

# 然后重新执行本文的两个“后台常驻启动”命令。
```

### 如何避免重复推送？

不要手动删除下面两个状态文件，除非你明确要重新跑当天任务：

```text
run/offline_daily_update_scheduler_state.json
run/daily_strategy_scheduler_state.json
```

它们记录 daemon 当天是否已经执行成功或跳过。

## 已知限制

- 当前是日线级策略，不模拟分钟级或逐笔成交。
- 涨跌停使用日线近似，无法模拟真实排队成交概率。
- 当前主策略主要依赖价格和有限 OHLCV 信息，缺少财务、资金流和盘口因子。
- 每日飞书收益是从本地 `run/daily_strategy_live_state.json` 记录的实盘模拟启用日开始计数，不会自动读取真实券商账户。
- 实盘下单前必须人工核对账户持仓、可用资金、涨跌停状态和委托成交情况。
