# BigQuant SDK 适配计划

本文档记录在 `feature/bigquant-sdk-adapter` 分支中进行 BigQuant SDK 适配的工程方案。

## 目标

在不影响当前生产链路的前提下，引入 BigQuant SDK：

1. 使用 `dai` 查询 BigQuant 云端行情数据。
2. 将 BigQuant 数据转换为当前项目既有离线 schema。
3. 用当前本地策略 runtime 跑同一策略，对比 Tencent/Sina 与 BigQuant 数据口径差异。
4. 仅当数据口径稳定后，再评估是否迁移到 `bigtrader` 回测。

## 不直接改生产链路的原因

当前生产链路已经包含：

- 每日 16:30 取数；
- 每日 16:50 策略检查；
- 取数文件锁；
- CSV/JSON 原子替换；
- daemon 调度状态持久化；
- 飞书多机器人推送；
- live state 收益口径；
- A 股 T+1、100 股整数手、最低佣金、印花税、过户费、涨跌停近似等本地撮合约束。

BigQuant SDK 引入后，最大风险不是代码能否运行，而是：

- 复权口径差异；
- 成交额/成交量/换手率单位差异；
- 停牌和非交易日处理差异；
- ST、退市、上市不足样本处理差异；
- BigTrader 默认撮合和当前本地 realistic runtime 不一致。

因此本分支先做旁路验证。

## 当前新增文件

### `BIGQUANT_SDK_USAGE.md`

保存 BigQuant SDK 使用教程、本机 conda 环境和安装记录。

### `bigquant_provider.py`

BigQuant 数据适配层：

- 从 `.env.local` 中读取 `BIGQUANT_API_KEY`，不打印密钥；
- 初始化 BigQuant SDK；
- 转换股票代码：
  - `000001` -> `000001.SZ`
  - `600519` -> `600519.SH`
  - `920xxx` -> `920xxx.BJ`
- 查询 `cn_stock_bar1d`；
- 将 BigQuant DAI 输出标准化为当前项目内部行情格式：
  `trade_date,symbol,open,high,low,close,volume,amount,turnover_rate`

### `bigquant_data_probe.py`

小样本数据探针：

- 拉取 BigQuant 指定股票和日期区间；
- 读取本地 `data/offline/a_share_12m_tencent_sina/prices_long.csv`；
- 对比 OHLCV、成交额、换手率；
- 输出：
  - `bigquant_prices.csv`
  - `local_prices.csv`
  - `comparison.csv`

默认输出目录：

```text
data/bigquant_probe/
```

该目录在 `data/` 下，默认不会提交。

## 权限和口径验证结果

本地 `bigquant` conda 环境安装成功：

```text
Python 3.11.15
bigquant==0.1.11
bigquant-core==0.1.14
bigtradercpp==0.1.22
```

SDK 权限已开通后，探针验证通过：

```text
python bigquant_data_probe.py \
  --symbols 000001,600519 \
  --start-date 2026-07-01 \
  --end-date 2026-07-06 \
  --output-dir data/bigquant_probe/smoke
```

结果：

```text
BigQuant rows: 8
Local rows: 8
Matched rows: 8
open_median_abs_diff_pct: 0
high_median_abs_diff_pct: 0
low_median_abs_diff_pct: 0
close_median_abs_diff_pct: 0
volume_median_abs_diff_pct: 3.82851e-07
```

关键结论：

- `cn_stock_bar1d` 官方文档标注为后复权日行情。
- BigQuant 价格需要用 `后复权价 / 最新 adjust_factor` 转为当前项目使用的前复权口径。
- BigQuant `volume` 单位是股，当前项目数据是手，需要除以 100。
- 转换后，`000001` 和 `600519` 在 2026-07-01 至 2026-07-06 的 OHLC 与当前 Tencent/Sina 离线数据完全匹配，成交量仅有四舍五入级别误差。

## 验证命令

```bash
conda activate bigquant

python bigquant_data_probe.py \
  --symbols 000001,600519 \
  --start-date 2026-07-01 \
  --end-date 2026-07-06 \
  --output-dir data/bigquant_probe/smoke
```

如果权限已开通，预期输出：

```text
BigQuant rows: <n>
Local rows: <n>
Matched rows: <n>
close_median_abs_diff_pct: ...
Output: ...
```

## 后续实施步骤

### 阶段 1：权限验证和字段口径确认

1. 申请 BigQuant SDK 权限。
2. 运行 `bigquant_data_probe.py`。
3. 检查 `data/bigquant_probe/smoke/comparison.csv`。
4. 确认字段：
   - `open`
   - `high`
   - `low`
   - `close`
   - `volume`
   - `amount`
   - `turnover_rate`
5. 确认复权口径是否等价于当前 `qfq` 数据。

### 阶段 2：生成 BigQuant 离线数据

已新增脚本：

```text
generate_offline_a_share_bigquant.py
```

要求：

- 输出目录独立：
  `data/offline/a_share_12m_bigquant`
- 保持当前 schema：
  `date,symbol,open,high,low,close,volume,amount,turnover`
- 保持 `universe.csv`、`symbols/{symbol}.csv`、`prices_long.csv`、`manifest.json` 结构。
- 不覆盖当前 `a_share_12m_tencent_sina` 数据。

小样本 smoke test 已通过：

```bash
conda activate bigquant

python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 1 \
  --warmup-days 5 \
  --limit 5 \
  --output-dir data/offline/a_share_1m_bigquant_smoke \
  --overwrite
```

输出：

```text
rows=110
symbols=5
columns=date,symbol,open,high,low,close,volume,amount,turnover
```

完整 12 个月数据生成命令：

```bash
conda activate bigquant

python generate_offline_a_share_bigquant.py \
  --end-date 2026-07-06 \
  --months 12 \
  --warmup-days 180 \
  --output-dir data/offline/a_share_12m_bigquant \
  --overwrite
```

按当前股票数量估算，完整生成大约消耗 700 万至 900 万 cell，低于每周 3000 万 cell 限额。生成后应优先复用本地 CSV，避免重复消耗额度。

实际完整生成结果：

```text
output=data/offline/a_share_12m_bigquant
source=bigquant
datasource=cn_stock_bar1d
rows=1,084,914
symbols=3,045
start=2025-01-07
end=2026-07-06
saved_count=3,045
empty_or_failed_count=0
```

与当前 Tencent/Sina 数据差异：

```text
Tencent/Sina: symbols=3,046 rows=1,087,885 start=2025-01-06 end=2026-07-06
BigQuant:    symbols=3,045 rows=1,084,914 start=2025-01-07 end=2026-07-06
local_only_symbol=600193
```

`600193` 是“退市创兴”，BigQuant 当前交易日股票池不包含它。该差异合理，后续生产股票池应进一步明确排除退市标的。

生成过程中 BigQuant SDK 尝试写入：

```text
~/.bigquant/logs/telemetry/audit.jsonl
```

该路径被 Trae 沙盒拦截，导致命令退出码为 1，但数据文件已经完整生成并通过校验。后续如果需要在 Trae 内长期运行 BigQuant SDK，应考虑调整沙盒允许路径，或确认 SDK 是否支持关闭 telemetry。

### 阶段 3：现有策略无感运行

不改策略逻辑，只切换数据目录：

```bash
AKSHARE_OFFLINE_DATA_DIR=data/offline/a_share_12m_bigquant \
python strategies/ai_native/small_account_high_conviction_policy.py \
  --warmup-start-date 20250106 \
  --start-date 20250705 \
  --end-date <latest_data_date> \
  --output-dir data/backtests/small_account_bigquant_probe \
  --limit 3046
```

对比：

- 收益；
- 回撤；
- 持仓；
- 调仓日期；
- 交易股票；
- 空仓日期。

BigQuant 离线数据已完成一次策略兼容性验证：

```bash
AKSHARE_OFFLINE_DATA_DIR=/Users/bytedance/cqm/Personal_Strategy/data/offline/a_share_12m_bigquant \
AKSHARE_INITIAL_CASH=100000 \
python3 strategies/ai_native/small_account_high_conviction_policy.py \
  --warmup-start-date 20250107 \
  --start-date 20250705 \
  --end-date 20260706 \
  --output-dir data/backtests/small_account_bigquant_probe/20260706 \
  --limit 3045
```

结果：

```text
final_equity=120016.40
total_return=20.02%
trades=82
last_date=2026-07-06
latest_target_holding=空仓
```

当前 Tencent/Sina 生产回测口径：

```text
backtest_final_equity=118160.30
backtest_total_return=18.16%
latest_target_holding=空仓
```

结论：BigQuant 数据可以被现有策略 runtime 无感读取，但收益和交易记录有差异，不能直接切生产。下一步应做逐日持仓、调仓和因子排名差异分析。

### 阶段 4：每日增量更新旁路

新增脚本：

```text
update_offline_a_share_daily_bigquant.py
```

要求保留生产保护：

- 文件锁；
- 原子写；
- scheduler state；
- 飞书通知；
- 数据未就绪告警；
- 不影响 Tencent/Sina 生产链路。

### 阶段 5：评估 BigTrader

只有数据口径稳定后才评估回测迁移。

重点验证 BigTrader 是否能完整复刻：

- A 股 T+1；
- 100 股整数手；
- 最低 5 元佣金；
- 卖出印花税；
- 过户费；
- 滑点；
- 涨停禁买、跌停禁卖；
- 周频调仓；
- 日频风控退出；
- 最多 2 只实际持仓；
- 单票最大权重 34%。

如果不能完全复刻，继续使用当前本地 runtime，只把 BigQuant 作为数据源。

## 密钥约束

- 真实 API Key 只允许保存在 `.env.local` 或 `~/.bigquant/config.json`。
- 不得写入 Git、README、迁移文档、日志或命令输出。
- `.env.local` 已被 `.gitignore` 忽略，不要强制提交。
