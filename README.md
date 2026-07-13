# Personal Strategy

Personal Strategy 是一个个人 A 股量化研究与每日策略提醒项目。当前代码只保留四条主线：

```text
历史离线快照 -> Tushare 增量补齐 -> 本地策略/回测 -> 飞书提醒
```

项目不自动下单。每日推送给出的持仓和调仓建议，实盘执行前仍需要人工核对账户现金、实际持仓、停牌、涨跌停、100 股整数手和成交价格。

## 当前功能

- 使用 `packages/a_share_history_snapshot_20260709.tar.gz` 作为历史行情基座。
- 使用 Tushare Pro 从历史数据最后一天开始补齐新增交易日。
- 输出统一行情文件 `data/offline/a_share_history_tushare/prices_long.csv`。
- 使用 `local_strategy.py` 生成 v4 小资金高置信度策略信号。
- 使用 `local_backtest.py` 做本地撮合回测。
- 使用 `tushare_daily_daemon.py` 做每日定时取数、策略运行和飞书推送。

## 代码结构

```text
.env.example
  本地配置模板。

.feishu_webhook.example
  飞书 / Lark 机器人 webhook 模板。

tushare_provider.py
  Tushare Pro 接口适配层，负责 token、交易日、日线、复权因子、换手率、涨跌停、停牌数据。

update_offline_a_share_history_tushare_daily.py
  当前默认取数脚本。先从历史快照初始化，再用 Tushare 补齐新增交易日。

generate_offline_a_share_tushare.py
  Tushare 标准化取数工具。当前默认补数脚本复用其中的单日数据整理函数。

local_strategy.py
  本地策略入口，默认读取 `data/offline/a_share_history_tushare/prices_long.csv`。

local_backtest.py
  本地回测撮合引擎。

tushare_daily_daemon.py
  每日自动任务：定时取数、策略运行、飞书推送、PID 和状态管理。

packages/a_share_history_snapshot_20260709.tar.gz
  历史行情快照。用于初始化本地行情，不包含密钥。
```

## Git 忽略策略

提交到 Git：

```text
源码
README
requirements.txt
.env.example
.feishu_webhook.example
历史行情快照压缩包
```

不提交到 Git：

```text
.env.local          # 真实 Tushare Token / 其他密钥
.feishu_webhook     # 真实机器人 webhook
data/               # 本地行情缓存和回测结果
logs/               # 运行日志
run/                # PID 和 daemon 状态
strategies/         # 私有策略资产
.trae/ .vscode/     # 本机 IDE 配置
```

## 环境安装

推荐环境：

```text
Python: 3.11
数据源: Tushare Pro
调度: systemd 或 nohup
```

安装：

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy

python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

验证依赖：

```bash
python - <<'PY'
import pandas as pd
import numpy as np
import tushare as ts
print("env ok")
PY
```

## 配置文件

```bash
cp .env.example .env.local
cp .feishu_webhook.example .feishu_webhook
```

`.env.local` 示例：

```text
TUSHARE_TOKEN=你的 Tushare Pro Token
LOCAL_STRATEGY_VERSION=v4
LOCAL_STRATEGY_START_DATE=2025-07-05
LOCAL_WARMUP_START_DATE=2025-01-07
LOCAL_INITIAL_CASH=100000
LOCAL_PYTHON=/绝对路径/Personal_Strategy/.venv/bin/python
LOCAL_CONDA_ENV=strategy
```

`.feishu_webhook` 每行一个机器人地址：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/你的token
https://open.larkoffice.com/open-apis/bot/v2/hook/你的token
```

## 行情 Schema

默认行情文件：

```text
data/offline/a_share_history_tushare/prices_long.csv
```

字段：

```text
date
symbol
open
high
low
close
volume
amount
turnover
```

口径：

```text
历史段：来自离线快照
增量段：来自 Tushare daily + adj_factor + daily_basic
价格拼接：使用历史最后一天作为重叠日，按每只股票的重叠日 close 比例把后续 Tushare OHLC 映射到历史价格尺度
volume：手
amount：元
turnover：小数，例如 0.01 表示 1%
```

## 初始化和增量补齐行情

小样本验证：

```bash
. .venv/bin/activate

python update_offline_a_share_history_tushare_daily.py \
  --end-date "$(date +%F)" \
  --output-dir data/offline/a_share_history_smoke \
  --snapshot packages/a_share_history_snapshot_20260709.tar.gz \
  --env-file .env.local \
  --limit 20 \
  --log-level INFO
```

正式生成/更新：

```bash
python update_offline_a_share_history_tushare_daily.py \
  --end-date "$(date +%F)" \
  --output-dir data/offline/a_share_history_tushare \
  --snapshot packages/a_share_history_snapshot_20260709.tar.gz \
  --env-file .env.local \
  --log-level INFO
```

增量逻辑：

1. 如果输出目录不存在，先从历史快照初始化。
2. 读取本地最新日期。
3. 查询 Tushare 在 `end-date` 前最近的开市交易日。
4. 如果本地已到最新交易日，则跳过。
5. 如果本地落后，则拉取本地最新日作为重叠锚点，再拉取后续缺失交易日。
6. 使用重叠日逐股票计算价格比例，拼接后续 Tushare OHLC。
7. 合并后保持 9 列 schema，并按 `date + symbol` 去重。

## 运行本地策略

```bash
python local_strategy.py \
  --strategy-version v4 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date "$(python - <<'PY'
import pandas as pd
dates = pd.read_csv('data/offline/a_share_history_tushare/prices_long.csv', usecols=['date'])['date']
print(dates.max())
PY
)" \
  --initial-cash 100000 \
  --prices-file data/offline/a_share_history_tushare/prices_long.csv \
  --output-dir data/backtests/local_strategy_v4_latest
```

输出文件：

```text
local_weight_signals.csv
local_signal_debug.csv
local_raw_perf.csv
local_trades.csv
local_backtest_summary.json
```

## v4 策略逻辑

策略定位：

```text
小资金高置信度
最多持有 2 只股票
周频选股调仓
日频风控卖出
弱市场主动空仓
```

市场环境判断：

```text
breadth20 / breadth60 / breadth120
median_ret10 / median_ret20 / median_ret60
market_vol20
weak_drawdown_ratio
```

候选过滤：

```text
价格在 2.5 到 85 元
收盘价高于 MA20 / MA60 / MA120
20 日动量 > 0
60 日动量 > 0
120 日动量 > -3%
20 日动量 < 45%
5 日动量 < 45%
20 日波动率 < 5.5%
60 日回撤 > -22%
开盘跳空 < 6%
20 日平均成交值有效
```

v4 打分：

```text
24%: 60 日动量
18%: 120 日动量
10%: 20 日动量
18%: 收盘价 / MA60 - 1
16%: 低 20 日波动率
 8%: 低 60 日下行波动
 4%: 低 60 日回撤压力
 2%: 20 日平均成交值
```

买入规则：

```text
每周第一个交易日生成目标权重
候选数量少于 2 只则不开仓
选综合分最高的最多 2 只
按 20 日波动率倒数分配权重
单只股票最高 34%
市场中性总仓位 34%
市场强势总仓位 68%
```

卖出规则：

```text
每周重算后不再入选则卖出
市场环境转弱则清仓
收盘价跌破 20 日均线则卖出
亏损达到 6% 固定止损
从持仓最高价回撤 10% 移动止损
```

## 本地回测口径

```text
信号日收盘后生成目标权重
下一交易日开盘调仓
100 股整数手
先卖后买
现金不足时自动缩减买入手数
涨停不可买
跌停不可卖
停牌不可交易
买入成本 0.03%
卖出成本 0.13%
最低佣金 5 元
```

## 每日 daemon

```bash
mkdir -p logs run

nohup "$(pwd)/.venv/bin/python" -u tushare_daily_daemon.py \
  --python "$(pwd)/.venv/bin/python" \
  --env-file .env.local \
  --webhook-file .feishu_webhook \
  --data-time 21:10 \
  --strategy-time 21:30 \
  --interval-seconds 300 \
  --data-dir data/offline/a_share_history_tushare \
  --strategy-output-root data/backtests/daily_local_strategy_signals \
  > logs/tushare_daily_daemon.nohup.log 2>&1 &

echo $! > run/tushare_daily_daemon_launcher.pid
```

检查状态：

```bash
ps -ef | grep -E '[t]ushare_daily_daemon.py|[u]pdate_offline_a_share_history_tushare_daily.py|[l]ocal_strategy.py'
cat run/tushare_daily_daemon.pid
cat run/tushare_daily_daemon_state.json
tail -n 120 logs/tushare_daily_daemon.nohup.log
tail -n 120 logs/tushare_daily/$(date +%Y%m%d)_data_update.log
tail -n 120 logs/tushare_daily/$(date +%Y%m%d)_strategy.log
```

停止：

```bash
kill "$(cat run/tushare_daily_daemon.pid)"
```

## systemd 示例

```ini
[Unit]
Description=Personal Strategy Tushare Daily Daemon
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/personal-strategy/Personal_Strategy
ExecStart=/opt/personal-strategy/Personal_Strategy/.venv/bin/python -u tushare_daily_daemon.py --python /opt/personal-strategy/Personal_Strategy/.venv/bin/python --env-file .env.local --webhook-file .feishu_webhook --data-time 21:10 --strategy-time 21:30 --interval-seconds 300 --data-dir data/offline/a_share_history_tushare --strategy-output-root data/backtests/daily_local_strategy_signals
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

## 验收清单

```bash
python -m py_compile \
  tushare_provider.py \
  update_offline_a_share_history_tushare_daily.py \
  generate_offline_a_share_tushare.py \
  local_backtest.py \
  local_strategy.py \
  tushare_daily_daemon.py
```

重复键检查：

```bash
python - <<'PY'
import pandas as pd
p = 'data/offline/a_share_history_tushare/prices_long.csv'
df = pd.read_csv(p, dtype={'symbol': str})
dup = df.duplicated(['date', 'symbol']).sum()
print('rows=', len(df), 'symbols=', df['symbol'].nunique(), 'dates=', df['date'].nunique(), 'duplicates=', dup)
assert dup == 0
PY
```
