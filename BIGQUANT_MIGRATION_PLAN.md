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

## 当前阻塞

本地 `bigquant` conda 环境安装成功：

```text
Python 3.11.15
bigquant==0.1.11
bigquant-core==0.1.14
bigtradercpp==0.1.22
```

探针已能进入 BigQuant SDK 查询路径，但服务端返回：

```text
请先申请SDK使用权限
```

这说明：

- Python 版本正确；
- SDK 安装正确；
- 本地代码已调用到 DAI；
- 当前账号仍缺少 BigQuant SDK 数据权限。

申请权限后可重新运行探针。

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

新增脚本：

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
