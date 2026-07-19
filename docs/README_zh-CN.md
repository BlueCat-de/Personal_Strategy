# AShare Quant

面向A股的点时数据工程、因子研究、真实约束回测和每日信号自动化工具。

[English](../README.md) | [架构说明](ARCHITECTURE.md) |
[Python API](API.md) | [贡献指南](../CONTRIBUTING.md)

> 本项目仅用于研究与教育，不构成投资建议，不保证收益，也不会自动下单。

## 功能特性

- 使用Tushare Pro构建严格point-in-time A股数据集。
- 因子复权价格与真实成交价格职责分离。
- 按历史日期恢复上市、退市、ST、停牌和涨跌停状态。
- 信号在下一交易日真实开盘价执行。
- 支持100股整数手、佣金、印花税、最低费用和双边滑点。
- 内置防守型v4策略和分散化纯股票因子策略。
- 支持市值、行业、风格、调仓日期和滑点稳定性实验。
- 支持定时取数、策略检查及可选飞书/Lark通知。

仓库不提交行情数据、API Token、Webhook、日志和回测输出。

## 技术栈

- Python 3.11+
- pandas、NumPy
- Tushare Pro
- setuptools与标准`src/`包结构
- unittest/pytest兼容测试

## 目录结构

```text
.
├── src/ashare_quant/
│   ├── automation/       # 每日调度和飞书/Lark通知
│   ├── data/             # Tushare适配、PIT数据和行业历史
│   ├── research/         # 因子面板和稳定性实验
│   ├── strategies/       # v4和稳定纯股票策略
│   ├── backtest.py       # 下一交易日开盘撮合器
│   ├── benchmark.py      # 沪深300与相对绩效
│   └── paths.py          # 项目路径
├── tests/
├── docs/
├── pyproject.toml
└── requirements.txt
```

本地数据继续保存在`data/`，并由Git忽略。

## 安装

```bash
git clone https://github.com/BlueCat-de/Personal_Strategy.git
cd Personal_Strategy

python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

仅安装运行依赖：

```bash
python -m pip install -r requirements.txt
python -m pip install -e . --no-deps
```

## 配置

```bash
cp .env.example .env.local
cp .feishu_webhook.example .feishu_webhook
```

在`.env.local`中配置：

```dotenv
TUSHARE_TOKEN=替换为你的Token
```

飞书Webhook为可选配置。严禁提交`.env.local`和`.feishu_webhook`。

## 快速开始

### 1. 构建PIT行情

先运行小样本：

```bash
ashare-rebuild-data \
  --start-date 2024-01-01 \
  --end-date 2024-03-31 \
  --limit 30 \
  --output-dir data/offline/smoke
```

正式构建：

```bash
ashare-rebuild-data \
  --start-date 2020-01-01 \
  --end-date "$(date +%F)" \
  --output-dir data/offline/a_share_history_tushare
```

核心输出：

```text
data/offline/a_share_history_tushare/
├── prices_long.csv
├── daily_universe.csv
└── universe.csv
```

### 2. 获取申万历史行业

```bash
ashare-fetch-industries
```

该命令按调入和调出日期缓存申万一级行业历史成员，是行业中性策略的依赖。

### 3. 运行v4

```bash
ashare-v4 \
  --strategy-version v4 \
  --warmup-start-date 2024-01-01 \
  --start-date 2024-07-01 \
  --end-date 2026-07-16 \
  --initial-cash 100000 \
  --prices-file data/offline/a_share_history_tushare/prices_long.csv \
  --output-dir data/backtests/v4
```

### 4. 运行纯股票稳定策略

```bash
ashare-stable \
  --start-date 2021-07-01 \
  --warmup-start-date 2020-01-02 \
  --end-date 2026-07-16 \
  --initial-cash 45000 \
  --slippage 0.001 \
  --output-dir data/backtests/stable_stock_alpha
```

该策略仅持有A股个股和现金，不使用ETF、指数权重、期货、期权或其他衍生品。

### 5. 运行实验性广度自适应策略

```bash
ashare-adaptive \
  --start-date 2021-07-01 \
  --warmup-start-date 2020-01-02 \
  --end-date 2026-07-16 \
  --initial-cash 45000 \
  --slippage 0.001 \
  --output-dir data/backtests/adaptive_stock_alpha
```

研究基线每两个月调仓一次。信号日全市场120日上涨广度不低于76%时，
选择12只大盘低波价值股票；否则选择8只行业中性的相对强弱股票，
且单一行业最多占持仓数量的20%。

该策略在完整PIT审计后仅达到5/6年度超额，当前不具备生产资格。此前6/6结果
已经作废，原因、修复内容和前向OOS协议见
[广度自适应策略验证状态](STRATEGY_VALIDATION_zh-CN.md)。

### 6. 运行调仓起点中性策略

```bash
ashare-offset-neutral \
  --initial-cash 100000 \
  --slippage 0.001 \
  --output-dir data/backtests/offset_neutral_stock_alpha
```

该纯股票候选使用月频大盘防守、小盘低换手和双起点自适应三个袖套。完整PIT
回测实现了6/6年度超额，但仍处于冻结后的前向验证阶段。`--initial-cash`支持5万元
及以上，20万元为建议资金。策略逻辑、成本压力和资金约束见
[调仓起点中性纯股票策略](OFFSET_NEUTRAL_STRATEGY_zh-CN.md)。版本控制边界、
策略状态和前向验证要求见[策略资产登记册](STRATEGY_ASSETS_zh-CN.md)。

### 7. 绘制不同本金回测曲线

```bash
ashare-plot-capital-curves \
  --input-dir data/backtests/offset_neutral_capital_sensitivity_20210701_20260716 \
  --output-dir data/backtests/offset_neutral_capital_sensitivity_20210701_20260716/charts
```

该命令使用`matplotlib`输出5万、10万、50万、100万策略资金的归一化净值曲线和
累计收益曲线，并与沪深300对比，生成PNG和PDF文件。

### 8. 运行稳定性实验

```bash
ashare-stability \
  --start-date 2021-07-01 \
  --validation-start 2024-01-01 \
  --end-date 2026-07-16 \
  --initial-cash 45000
```

实验覆盖：

- 因子权重邻域；
- 8/10/12只持仓；
- 月频和双月频；
- 市值和历史行业截面；
- 单边0.05%/0.1%/0.2%/0.5%滑点。

现有研究没有证明某个策略能在报告中的每一年都跑赢沪深300。

## 每日部署

```bash
ashare-daemon \
  --python "$PWD/.venv/bin/python" \
  --data-time 21:10 \
  --strategy-time 21:30
```

生产环境建议使用systemd、launchd或Supervisor托管。运行状态写入`run/`，
日志写入`logs/`，两者均不会提交到Git。

自动任务只生成信号和通知，不连接券商，也不发送订单。

## Python API

```python
from ashare_quant.backtest import BacktestConfig, run_local_backtest
from ashare_quant.strategies.v4 import build_targets, load_prices

prices = load_prices("data/offline/a_share_history_tushare/prices_long.csv", config)
targets, signals, debug = build_targets(prices, config)
result = run_local_backtest(
    prices,
    targets,
    BacktestConfig(initial_cash=100_000, slippage=0.001),
    strategy_name="example",
)
```

完整接口见[API.md](API.md)。

## 测试

```bash
python -m pytest
ruff check src tests
```

## 数据与复现

Tushare数据受其自身许可和账户权限约束，本仓库不重新分发行情数据。
只有在数据版本、PIT口径和配置一致时，回测才具有可比性。

`data/snapshots/`中的历史快照是本地资产，不属于Git仓库。

## 贡献

提交Issue或Pull Request前请阅读[CONTRIBUTING.md](../CONTRIBUTING.md)。
安全问题按[SECURITY.md](../SECURITY.md)私下报告。
社区行为遵循[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。

## 许可证

Copyright (C) BlueCat-de。

项目使用[GNU GPL-3.0](../LICENSE)许可证。

## 维护者

[BlueCat-de](https://github.com/BlueCat-de)
