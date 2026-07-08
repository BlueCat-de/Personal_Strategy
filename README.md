# Personal Strategy

Personal Strategy 是一个 BigQuant SDK-only 的 A 股量化研究、离线取数、BigTrader 回测和每日策略提醒项目。当前分支遵循“奥卡姆剃刀”原则：数据源统一使用 BigQuant DAI，回测统一使用 BigTrader，本地只保留运行所必需的脚本、报告和部署工具。

本项目仅用于个人量化研究和交易辅助，不构成投资建议，也不会自动下单。每日飞书消息给出的是基于 BigQuant 数据和 BigTrader 回测口径生成的次日操作参考，实盘下单前必须核对账户资金、持仓、价格、涨跌停、停牌和可交易性。

## 目录

- [项目目标](#项目目标)
- [整体架构](#整体架构)
- [代码结构](#代码结构)
- [Git 忽略策略](#git-忽略策略)
- [环境要求](#环境要求)
- [首次安装](#首次安装)
- [本地配置文件](#本地配置文件)
- [数据口径](#数据口径)
- [生成离线数据](#生成离线数据)
- [每日增量取数](#每日增量取数)
- [运行策略回测](#运行策略回测)
- [策略逻辑](#策略逻辑)
- [每日自动化部署](#每日自动化部署)
- [运行状态检查](#运行状态检查)
- [从零部署流程](#从零部署流程)
- [常见问题](#常见问题)

## 项目目标

这个项目解决三个核心问题：

1. 使用 BigQuant SDK 稳定获取 A 股日频行情，避免维护多套 AkShare、Tencent、Sina、JQData 等碎片化链路。
2. 将行情保存为本地离线缓存，降低 BigQuant 每周 cell 配额消耗，并保证回测和每日策略运行使用同一份数据口径。
3. 每日夜间自动更新数据，在数据更新到当天交易日后运行策略，并通过飞书或 Lark 机器人推送持仓调整建议。

当前生产默认策略是 `v4`，即小资金高置信度 A 股策略。它最多持有 2 只股票，周频调仓，日线级别交易，强调 A 股真实约束：T+1、手续费、印花税、最低佣金、主板可交易范围和风险退出。

## 整体架构

```text
BigQuant DAI: cn_stock_bar1d
    |
    |  仅读取必要字段，支持 BIGQUANT_API_KEY / BIGQUANT_API_KEY2 配额切换
    v
bigquant_provider.py
    |
    |  代码转换、股票池过滤、复权转换、成交量单位转换
    v
data/offline/a_share_12m_bigquant/          # 本地行情缓存，不提交 Git
    |
    |  每日增量补齐本地缺失日期
    v
update_offline_a_share_bigquant_daily.py
    |
    |  生成目标权重信号
    v
bigquant_strategy.py
    |
    |  BigTrader 撮合回测，T+1，开盘价成交，手续费近似
    v
data/backtests/daily_bigquant_strategy_signals/
    |
    |  每日飞书 / Lark 推送
    v
bigquant_daily_daemon.py + launchd
```

每日生产链路是串行状态机：先取数，后策略；取数未完成或数据未更新到当天时，策略不会运行。

## 代码结构

```text
.env.example
  BigQuant API Key 和运行参数模板。复制为 .env.local 后填写真实值。

.feishu_webhook.example
  飞书 / Lark 机器人 webhook 模板。复制为 .feishu_webhook 后填写真实 URL。

bigquant_provider.py
  BigQuant DAI 数据适配层。负责 API Key 初始化、多 Key 配额切换、股票代码转换、字段查询、复权和单位转换。

generate_offline_a_share_bigquant.py
  全量离线行情生成脚本。用于首次构建本地 12 个月行情缓存，也支持 resume 续跑。

update_offline_a_share_bigquant_daily.py
  每日增量取数脚本。会比较本地最新日期和 BigQuant 最新日期，自动补齐中间缺失日期。

bigquant_strategy.py
  策略与 BigTrader 回测入口。支持 v4、成交量增强、成交量风险过滤和 v5 研究版本。

bigquant_daily_daemon.py
  每日生产调度进程。负责定时取数、失败重试、策略运行、飞书推送、PID 文件和状态文件。

install_bigquant_launchd.py
  macOS launchd 安装脚本。用于登录后自动启动 daemon，并在异常退出后拉起。

compare_bigtrader_runtime_diff.py
  BigTrader 与旧本地 runtime 差异分析工具。

BIGQUANT_SDK_USAGE.md
  BigQuant SDK 使用笔记。

BIGTRADER_RUNTIME_DIFF.md
  BigTrader 信号语义和旧 runtime 差异说明。

BIGQUANT_VOLUME_FEATURE_STRATEGY_REPORT.md
BIGQUANT_V5_REGIME_ADAPTIVE_REPORT.md
PRICE_VOLATILITY_ASYMMETRY_STRATEGY_REPORT.md
  策略研究报告。

requirements.txt
  Python 基础依赖。BigQuant SDK 需要使用官方私有源单独安装。
```

## Git 忽略策略

当前 `.gitignore` 保留以下原则：

```text
提交到 Git:
  源码、README、研究报告、requirements.txt、.env.example、.feishu_webhook.example

不提交到 Git:
  .env.local            # 真实 BigQuant API Key
  .feishu_webhook       # 真实飞书 / Lark 机器人地址
  data/                 # 本地行情缓存和回测结果，体量大且可再生成
  logs/                 # 运行日志
  run/                  # PID、状态文件、本机运行状态
  .bigquant/            # BigQuant SDK 本地日志和 telemetry
  .trae/ .vscode/       # 本机 IDE 配置
  strategies/           # 本地私有策略资产
```

真实密钥、机器人地址和本地数据都是运行必要依赖，但不应进入 Git。新环境通过模板文件和 README 指令生成这些文件，这样既能复现运行，又不会泄露本地隐私资产。

## 环境要求

推荐环境：

```text
操作系统：macOS
Python：3.11.x
环境管理：conda / miniforge / miniconda
BigQuant SDK：bigquant[bigtrader]
调度托管：launchd
网络：可以访问 BigQuant SDK 服务和飞书 / Lark webhook
```

Python 依赖：

```text
pandas>=2.0.0
numpy>=1.24.0
bigquant[bigtrader]  # 从 BigQuant 官方 PyPI 源安装
```

BigQuant SDK 安装源：

```text
https://pypi.bigquant.com/simple/
```

## 首次安装

克隆仓库：

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy
git checkout feature/bigquant-sdk-adapter
```

创建 Python 3.11 环境：

```bash
conda create -n bigquant python=3.11 pip -y
conda activate bigquant
```

安装依赖：

```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install 'bigquant[bigtrader]' -i https://pypi.bigquant.com/simple/
python -m pip install -r requirements.txt
```

验证安装：

```bash
python - <<'PY'
import pandas as pd
import numpy as np
import bigquant
from bigquant import dai, bigtrader
print("environment ok")
PY
```

## 本地配置文件

复制 BigQuant 环境变量模板：

```bash
cp .env.example .env.local
```

编辑 `.env.local`：

```bash
BIGQUANT_API_KEY=你的主 BigQuant API Key
BIGQUANT_API_KEY2=你的备用 BigQuant API Key，可为空
BIGQUANT_STRATEGY_VERSION=v4
BIGQUANT_STRATEGY_START_DATE=2025-07-05
BIGQUANT_WARMUP_START_DATE=2025-01-07
BIGQUANT_INITIAL_CASH=100000
```

`BIGQUANT_API_KEY2` 是可选项。当主 Key 返回配额不足错误时，`bigquant_provider.py` 会自动切换到备用 Key。日志只打印 key slot，不打印真实 Key。

复制飞书 webhook 模板：

```bash
cp .feishu_webhook.example .feishu_webhook
```

编辑 `.feishu_webhook`，每行放一个机器人地址：

```text
https://open.feishu.cn/open-apis/bot/v2/hook/你的token
https://open.larkoffice.com/open-apis/bot/v2/hook/你的token
```

支持 `open.feishu.cn` 和 `open.larkoffice.com`。没有配置 webhook 时，程序仍可运行，但不会收到消息推送。

## 数据口径

当前数据源：

```text
BigQuant DAI datasource: cn_stock_bar1d
文档：https://bigquant.com/data/datasources/cn_stock_bar1d
```

读取字段：

```text
date
instrument
open
high
low
close
volume
amount
turn
adjust_factor
name
```

导出到本地后的标准字段：

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

口径转换：

```text
BigQuant 后复权价 / 最新 adjust_factor => 本地 qfq 近似价格
BigQuant volume 单位“股” / 100 => 本地 volume 单位“手”
BigQuant turn => 本地 turnover
```

股票池过滤默认开启：

```text
排除创业板：300、301
排除科创板：688、689
排除北交所：4、8、920
排除 ST / *ST
```

这些过滤规则符合当前个人账户限制和项目约束。

## 生成离线数据

### 小样本 smoke test

首次在新环境中建议先跑 5 只股票的小样本，验证 BigQuant Key、SDK 和文件写入是否正常：

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 1 \
  --warmup-days 5 \
  --limit 5 \
  --output-dir data/offline/a_share_1m_bigquant_smoke \
  --env-file .env.local \
  --batch-size 5 \
  --overwrite
```

检查输出：

```bash
ls -lh data/offline/a_share_1m_bigquant_smoke
head data/offline/a_share_1m_bigquant_smoke/prices_long.csv
```

### 生成完整 12 个月数据

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 12 \
  --warmup-days 180 \
  --output-dir data/offline/a_share_12m_bigquant \
  --env-file .env.local \
  --batch-size 100 \
  --overwrite
```

参数说明：

```text
--end-date       数据结束日期，支持 YYYY-MM-DD 或 YYYYMMDD
--months         正式回测窗口长度，默认 12
--warmup-days    指标预热天数，默认 180
--output-dir     本地离线数据目录
--env-file       BigQuant Key 配置文件
--batch-size     每批查询股票数，过大可能消耗更多 cell 或触发失败
--overwrite      覆盖已有 symbol CSV
--resume         复用已完成的 symbol CSV，适合 quota 中断后续跑
--limit          限制股票数量，仅用于 smoke test
```

如果中途因为 BigQuant cell 配额不足失败，下次可以用 `--resume` 续跑：

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 12 \
  --warmup-days 180 \
  --output-dir data/offline/a_share_12m_bigquant \
  --env-file .env.local \
  --batch-size 100 \
  --resume
```

## 每日增量取数

每日取数脚本用于生产环境更新本地缓存：

```bash
HOME="$(pwd)" conda run -n bigquant python update_offline_a_share_bigquant_daily.py \
  --end-date "$(date +%F)" \
  --output-dir data/offline/a_share_12m_bigquant \
  --env-file .env.local \
  --batch-size 100
```

增量逻辑：

1. 读取 `data/offline/a_share_12m_bigquant/prices_long.csv` 的本地最新日期。
2. 查询 BigQuant 在 `end-date` 前最近 14 天内的最新可用交易日。
3. 如果 BigQuant 最新日期小于或等于本地最新日期，则跳过。
4. 如果本地缺失日期，则从 `local_latest + 1 day` 拉到 `latest_bigquant_date`，自动补齐跨日期缺口。
5. 合并写回 `prices_long.csv` 和 `symbols/*.csv`。
6. 写出 `daily_update_summary.json`。

这个机制可以处理“昨日取数失败，今日 BigQuant 已经更新到今日”的场景：程序会补拉昨日到今日的完整区间，而不是只拉今日。

## 运行策略回测

### 小样本验证

```bash
HOME="$(pwd)" conda run -n bigquant python bigquant_strategy.py \
  --strategy-version v4 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --initial-cash 100000 \
  --limit 500 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_smoke_500
```

### 完整股票池回测

```bash
HOME="$(pwd)" conda run -n bigquant python bigquant_strategy.py \
  --strategy-version v4 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --initial-cash 100000 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_v4/20260706
```

常用参数：

```text
--strategy-version       策略版本，默认 v4
--warmup-start-date      指标预热开始日期
--start-date             正式收益统计开始日期
--end-date               回测结束日期
--initial-cash           初始资金
--max-positions          最大持仓数量，默认 2
--max-position-weight    单票最大权重，默认 0.34
--prices-file            本地 BigQuant prices_long.csv
--output-dir             回测输出目录
--limit                  限制股票数量，仅用于测试
```

输出文件：

```text
bigquant_weight_signals.csv
  传给 BigTrader 的目标权重信号。

bigtrader_raw_perf.csv
  BigTrader 原生逐日绩效，包含持仓、交易、现金、净值和收益。

bigtrader_summary.json
  策略参数、绩效摘要和运行配置。

universe.csv
  本次回测使用的股票池。
```

## 策略逻辑

当前默认生产策略是 `v4`。

策略定位：

```text
小资金账户
最多持有 2 只股票
周频调仓
日线级交易
趋势 + 动量 + 风险过滤
弱市场主动空仓
```

市场状态判断：

```text
计算全市场 MA20、MA60、MA120 宽度
强市场：市场宽度和波动满足要求，总仓位 68%
中性市场：总仓位 34%
弱市场：空仓
```

候选股票过滤：

```text
价格区间：默认 2.5 到 85
趋势：收盘价位于关键均线之上
动量：20/60/120 日动量满足阈值
不过热：过滤短期涨幅过大的股票
波动：过滤高波动标的
回撤：过滤 60 日回撤过深标的
跳空：过滤信号日异常跳空
流动性：使用 amount / turnover / volume ratio 做约束
```

评分逻辑：

```text
20 日动量
60 日动量
120 日动量
相对 MA60 趋势强度
低波动奖励
低下行波动奖励
低回撤压力奖励
成交活跃度奖励
```

仓位管理：

```text
最多 2 只
单票上限 34%
总仓位根据市场状态在 0%、34%、68% 之间切换
入选股票按 20 日波动率倒数分配权重
```

退出规则：

```text
跌破趋势退出线，默认 MA20
相对信号价亏损超过 6%
相对持仓后高点回撤超过 10%
周频调仓时不再满足选股条件
```

BigTrader 撮合设置：

```text
成交价：下一交易日 open
T+1：context.set_stock_t1(1)
手续费：买入 0.03%，卖出 0.13%，最低 5 元
调仓方式：context.order_target_percent
信号语义：调仓日完整目标持仓快照，非调仓日 instrument=None 哨兵行
```

非调仓日哨兵行非常重要。BigTrader 的 `handle_data_weight_based` 可能把缺失信号解释为空目标，导致错误清仓。本项目已修正为：事件日输出完整目标快照，非事件日输出 `instrument=None`，确保持仓延续。

## 已验证绩效

历史完整股票池 BigTrader 回测曾得到如下结果，具体结果会随数据截止日期和参数变化：

```text
strategy=small_account_high_conviction_policy_v4_bigquant
universe_count=3,045
signal_rows=270
traded_instruments=17
return_ratio=21.15%
annual_return_ratio=22.12%
benchmark_ratio=22.11%
max_drawdown=6.90%
win_ratio=54.55%
```

该结果使用 BigQuant 数据和 BigTrader 撮合口径，不等同于真实账户收益。

## 每日自动化部署

### 手动启动 daemon

```bash
mkdir -p logs run

nohup /opt/homebrew/Caskroom/miniforge/base/envs/bigquant/bin/python -u bigquant_daily_daemon.py \
  --data-time 21:10 \
  --strategy-time 21:30 \
  --interval-seconds 300 \
  --retry-seconds 1800 \
  --data-dir data/offline/a_share_12m_bigquant \
  --env-file .env.local \
  --webhook-file .feishu_webhook \
  > logs/bigquant_daily_daemon.nohup.log 2>&1 &

echo $! > run/bigquant_daily_daemon_launcher.pid
```

### 使用 launchd 托管

安装：

```bash
python3 install_bigquant_launchd.py \
  --python /opt/homebrew/Caskroom/miniforge/base/envs/bigquant/bin/python \
  --data-time 21:10 \
  --strategy-time 21:30 \
  --interval-seconds 300
```

查看状态：

```bash
launchctl print gui/$(id -u)/com.personal-strategy.bigquant-daily
```

停止并移除：

```bash
python3 install_bigquant_launchd.py --uninstall
```

重启：

```bash
python3 install_bigquant_launchd.py --uninstall
python3 install_bigquant_launchd.py \
  --python /opt/homebrew/Caskroom/miniforge/base/envs/bigquant/bin/python \
  --data-time 21:10 \
  --strategy-time 21:30 \
  --interval-seconds 300
```

默认生产调度：

```text
21:10 取数
21:30 策略检查
每 300 秒轮询一次
失败后 1800 秒重试
BigQuant 配额不足时，当天不再重复重试，次日重新尝试
```

选择 21:10 / 21:30 的原因是 BigQuant 日频数据通常在每日 20:00-21:00 完成更新。

## 运行状态检查

查看 daemon 进程：

```bash
ps -ef | rg "bigquant_daily_daemon.py|update_offline_a_share_bigquant_daily.py|bigquant_strategy.py"
```

查看 launchd 状态：

```bash
launchctl print gui/$(id -u)/com.personal-strategy.bigquant-daily
```

查看状态文件：

```bash
cat run/bigquant_daily_daemon_state.json
```

查看最新取数摘要：

```bash
cat data/offline/a_share_12m_bigquant/daily_update_summary.json
```

查看日志：

```bash
tail -n 120 logs/bigquant_daily_daemon.launchd.log
tail -n 120 logs/bigquant_daily_daemon.launchd.err.log
tail -n 120 logs/bigquant_daily/$(date +%Y%m%d)_data_update.log
tail -n 120 logs/bigquant_daily/$(date +%Y%m%d)_strategy.log
```

查看本地数据截止日期：

```bash
python - <<'PY'
import pandas as pd
path = "data/offline/a_share_12m_bigquant/prices_long.csv"
dates = pd.read_csv(path, usecols=["date"])["date"]
print(dates.min(), dates.max(), len(dates))
PY
```

## 从零部署流程

新机器完整流程如下：

1. 拉取代码并进入 BigQuant 分支。

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy
git checkout feature/bigquant-sdk-adapter
```

2. 创建 Python 3.11 环境并安装依赖。

```bash
conda create -n bigquant python=3.11 pip -y
conda activate bigquant
python -m pip install --upgrade pip setuptools wheel
python -m pip install 'bigquant[bigtrader]' -i https://pypi.bigquant.com/simple/
python -m pip install -r requirements.txt
```

3. 配置本地密钥和飞书机器人。

```bash
cp .env.example .env.local
cp .feishu_webhook.example .feishu_webhook
```

编辑 `.env.local` 和 `.feishu_webhook`，填入真实值。

4. 跑小样本验证。

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date "$(date +%F)" \
  --months 1 \
  --warmup-days 5 \
  --limit 5 \
  --output-dir data/offline/a_share_smoke \
  --env-file .env.local \
  --batch-size 5 \
  --overwrite
```

5. 生成正式离线数据。

```bash
HOME="$(pwd)" conda run -n bigquant python generate_offline_a_share_bigquant.py \
  --end-date "$(date +%F)" \
  --months 12 \
  --warmup-days 180 \
  --output-dir data/offline/a_share_12m_bigquant \
  --env-file .env.local \
  --batch-size 100 \
  --overwrite
```

6. 跑一次手动策略检查。

```bash
LATEST_DATE="$(python - <<'PY'
import pandas as pd
dates = pd.read_csv("data/offline/a_share_12m_bigquant/prices_long.csv", usecols=["date"])["date"]
print(dates.max())
PY
)"

HOME="$(pwd)" conda run -n bigquant python bigquant_strategy.py \
  --strategy-version v4 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date "$LATEST_DATE" \
  --initial-cash 100000 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/manual_check
```

7. 安装 launchd 后台任务。

```bash
python3 install_bigquant_launchd.py \
  --python /opt/homebrew/Caskroom/miniforge/base/envs/bigquant/bin/python \
  --data-time 21:10 \
  --strategy-time 21:30 \
  --interval-seconds 300
```

8. 检查启动状态和飞书启动通知。

```bash
launchctl print gui/$(id -u)/com.personal-strategy.bigquant-daily
cat run/bigquant_daily_daemon.pid
tail -n 80 logs/bigquant_daily_daemon.launchd.log
```

## 常见问题

### 为什么不提交 `.env.local` 和 `.feishu_webhook`？

它们包含真实 BigQuant API Key 和机器人 token。即使远端仓库是私有仓库，也不建议提交密钥。项目提供 `.env.example` 和 `.feishu_webhook.example`，新环境按模板创建即可。

### 为什么不提交 `data/`？

`data/` 包含本地行情缓存和回测结果，体量大、可再生成，而且可能包含个人研究过程资产。README 已给出完整生成命令。

### BigQuant 配额不足怎么办？

优先使用 `.env.local` 中的 `BIGQUANT_API_KEY2`。如果两个 Key 都不足，等待 BigQuant 每周配额刷新。全量生成时可使用 `--resume` 续跑，避免重复下载已完成股票。

### 昨天取数失败，今天会补昨天的数据吗？

会。每日更新脚本会读取本地最新日期，并从 `local_latest + 1 day` 拉到 BigQuant 最新日期。如果本地停在 `2026-07-06`，BigQuant 最新到 `2026-07-08`，会自动拉取 `2026-07-07` 到 `2026-07-08`。

### 为什么策略没有在 21:30 运行？

策略运行前提是本地数据已经更新到当天交易日。如果 BigQuant 最新数据仍停在昨天，或取数失败、配额不足，策略不会运行，避免用旧数据给出今日操作建议。

### Mac 盒盖或睡眠会怎样？

如果机器睡眠导致任务中断，daemon 重启后会识别 `in_progress` 状态并在下一轮调度补跑。若机器在 21:10 到 21:30 期间长期睡眠，任务会等机器恢复后继续按状态机处理。

### 如何确认当前远端仓库没有提交隐私资产？

可以运行：

```bash
git status --ignored --short
git ls-files | rg "env|webhook|data/|logs/|run/|strategies/|\\.trae|\\.vscode"
```

正常情况下，真实 `.env.local`、`.feishu_webhook`、`data/`、`logs/`、`run/`、`strategies/` 不应出现在 `git ls-files` 中。
