# AShare Quant

面向A股的点时（point-in-time）数据工程、因子研究、可信评测与部署自动化——
整个仓库围绕一个问题组织：**"这个策略，你敢上实盘吗？"**

[English](../README.md) | [架构说明](ARCHITECTURE.md) |
[Python API](API.md) | [健壮性分析SOP](SOP_ROBUSTNESS_ANALYSIS.md) |
[贡献指南](../CONTRIBUTING.md)

> 本项目仅用于研究与教育，不构成投资建议，不保证收益，也不会自动下单。

## 这个仓库有什么不一样

个人回测通常死于三个静默错误：未来函数、幸存者偏差和选择偏差
（ knobs 拧得足够多，总有一个看起来很好）。本项目把这三件事都当作
工程问题，用代码而非"评审自觉"来解决：

1. **数据主权，PIT 优先。** 每日宇宙、ST/停牌/退市状态、复权价与原始价
   双轨，全部按"当日有效"重建；财报在 `ann_date + 1` 之后才可见；
   复权价喂因子、原始价喂执行。重建原子化、可缓存、可审计。
2. **把怀疑量化成评测。** 指标全家桶（Sharpe/Sortino/Calmar/Omega/
   Ulcer/VaR/CVaR）之外，管线还跑 PSR、缩减夏普（按试验数校正）、
   PBO/CSCV、Haircut Sharpe、MinTRL，以及带 embargo 的 walk-forward
   样本外诊断。样本纪律显式化：train 2015–2021 / val 2022–2023 /
   **2024+ 锁盒只揭盲一次**。
3. **双通道因子准入，都有硬门。** 股票因子按严格 PIT 的 rank IC 与
   双半段稳定性准入；辅助/风险 overlay（波动目标、回撤阶梯、状态门）
   走并行协议——按对冻结基线的**边际组合级 delta** 判定，含 walk-forward
   方向一致性、bootstrap p 值、reject-only Sharpe 护栏、独立复杂度预算，
   且只进仓位构建、绝不进选股打分。
4. **真金白银之前先要两套独立实现。** 本地引擎与券商侧（Ptrade）回测
   结论一致，策略才被信任。部署适配器带分层执行防御——信号重试、
   停机漏信号恢复、停牌与延迟开盘处理、T+1 可卖数量——上实盘冒烟
   之前先过模拟撮合测试。
5. **AI coding agent 也能遵循的治理。** SOP 数值化、机器可校验；基线
   冻结（commit + CSV sha256）；每次准入试验——包括被拒绝的——都追加进
   台账并计入缩减夏普的试验数。研究战役可以委托给 coding agent 在
   护栏内执行；让结果诚实的，是护栏，不是 agent 的自觉。

## 功能特性

- 使用Tushare Pro构建严格point-in-time A股数据集。
- 因子复权价格与真实成交价格职责分离。
- 按历史日期恢复上市、退市、ST、停牌和涨跌停状态。
- 信号在下一交易日真实开盘价执行。
- 支持100股整数手、佣金、印花税、最低费用和双边滑点。
- 内置股票回测引擎与专用ETF轮动引擎（收盘成交、先卖后买、停牌感知盯市）。
- 内置防守型v4策略、分散化纯股票因子组合和ETF跨资产轮动策略。
- 评测框架：全套风险收益指标 + 统计显著性（PSR/DSR/PBO/CSCV/
  Haircut Sharpe/MinTRL）+ CAPM归因 + 压力/状态分析 + 成本容量 +
  带 embargo 的 walk-forward 稳定性诊断。
- 辅助/风险 overlay 按边际 delta 协议准入，内置六案例判别自检
  （真 overlay 通过、置乱零假设被拒）。
- 支持市值、行业、风格、调仓日期和滑点稳定性实验。
- 支持定时取数、策略检查及可选飞书/Lark通知。

仓库不提交行情数据、API Token、Webhook、日志、回测输出和私有策略资产。

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
│   ├── data/             # Tushare适配、PIT数据、行业历史和原始财报缓存
│   ├── evaluation/       # 指标、显著性检验(PSR/DSR/PBO)、walk-forward、
│   │                     # 辅助因子准入协议
│   ├── research/         # 因子面板、IC诊断和稳定性实验
│   ├── strategies/       # v4、复合因子、ETF轮动和前向验证资产
│   ├── visualization/    # matplotlib研究图表
│   ├── backtest.py       # 股票回测引擎（次开盘撮合）
│   ├── etf_backtest.py   # ETF轮动引擎（收盘撮合）
│   ├── splits.py         # train/val/锁盒切分的单一事实源
│   ├── benchmark.py      # 沪深300与相对绩效
│   └── paths.py          # 项目路径
├── deploy/ptrade/        # 券商侧适配器（分层执行防御）
├── scripts/              # 研究探针与部署打包
├── tests/                # 单元测试（含模拟撮合的适配器测试）
├── docs/                 # SOP、架构、API和研究记录
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
  --board-scope main \
  --output-dir data/offline/a_share_history_tushare
```

核心输出：

```text
data/offline/a_share_history_tushare/
├── prices_long.csv
├── daily_universe.csv
└── universe.csv
```

默认`main`只构建主板数据，保持现有研究数据集不变。若要补齐创业板、科创板和
北交所，必须写入独立目录，避免污染主板长期研究结果：

```bash
ashare-rebuild-data \
  --start-date 2010-01-01 \
  --end-date 2026-07-17 \
  --board-scope growth \
  --output-dir data/offline/a_share_growth_boards_tushare
```

输出目录为：

```text
data/offline/a_share_growth_boards_tushare/
├── prices_long.csv
├── daily_universe.csv
└── universe.csv
```

回测默认仍为`--board-scope main`。如果要在运行时把三板文件纳入候选池，不需要
把两个CSV合并到同一个文件，可使用：

> **审计状态：** `financial_quality_alpha`已完成严格PIT诊断回测，但研究流程须
> 重置，所有2026-07-21以前发布的历史绩效均失效，不得用于投资决策。详见
> [未来函数审计报告](FUTURE_FUNCTION_AUDIT_zh-CN.md)和
> [严格PIT风格探索](STRICT_PIT_STYLE_FREQUENCY_EXPLORATION_zh-CN.md)。

```bash
ashare-financial-quality \
  --prices-file data/offline/a_share_history_tushare/prices_long.csv \
  --extra-prices-file data/offline/a_share_growth_boards_tushare/prices_long.csv \
  --board-scope all \
  --start-date 2011-01-01 \
  --warmup-start-date 2010-01-04 \
  --end-date 2026-07-17 \
  --initial-cash 100000 \
  --slippage 0.001 \
  --output-dir data/backtests/financial_quality_all_boards
```

### 2. 获取申万历史行业

```bash
ashare-fetch-industries
```

该命令缓存带分类版本生效日、调入日和调出日的申万一级行业历史成员。
SW2021分类不会用于其生效日以前的历史截面。

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

Git仓库只包含代码和文档。拉取仓库后，需要自行在本地组装：

1. `.env.local`：填写自己的`TUSHARE_TOKEN`；
2. `deploy/ptrade/tushare_token.csv`：仅运行Ptrade版本时需要；
3. `data/offline/a_share_history_tushare/`下的严格PIT数据。

严禁提交上述Token。Ptrade成交导出、回测结果和运行日志同样不会进入Git。

### 使用冻结数据包

由于Tushare许可约束和GitHub文件大小限制，数据包通过项目私有制品渠道单独传输。
取得`ashare_quant_main_reproduction_YYYYMMDD.tar.gz`及对应`.sha256`文件后，
将它们放在仓库根目录执行：

```bash
shasum -a 256 -c ashare_quant_main_reproduction_YYYYMMDD.tar.gz.sha256
tar -xzf ashare_quant_main_reproduction_YYYYMMDD.tar.gz
```

压缩包会直接恢复以下结构：

```text
data/offline/a_share_history_tushare/
├── prices_long.csv
├── daily_universe.csv
├── universe.csv
├── manifest.json
├── benchmark_000300.csv
├── sw_l1_membership_history.csv
├── .daily_basic_monthly_cache/
├── .long_horizon_daily_basic_cache/
└── .long_horizon_fina_indicator_cache/
```

该数据包覆盖当前冻结主板策略及已提交的严格PIT研究，截止日期为2026-07-17。
它不包含API密钥、回测输出、历史备份、Ptrade成交导出及单独的成长板数据。

### 从Tushare重新组装

先配置`.env.local`，然后构建主板PIT行情和行业历史：

```bash
ashare-rebuild-data \
  --start-date 2006-01-04 \
  --end-date 2026-07-17 \
  --board-scope main \
  --output-dir data/offline/a_share_history_tushare

ashare-fetch-industries
```

随后需要为每个月首个交易日缓存`daily_basic`，并对`universe.csv`中的每只股票
缓存`fina_indicator`历史。财务缓存必须保留`ann_date`、`end_date`和
`update_flag`；研究代码使用`available_date = ann_date + 1个自然日`，并在缺少
独立修订时间时优先原始披露。禁止用当前截面回填这些历史缓存。

各目录用途如下：

- `.daily_basic_monthly_cache`：短周期策略基础面快照；
- `.long_horizon_daily_basic_cache`：2007—2026风格和因子研究快照；
- `.long_horizon_fina_indicator_cache`：按公告日保存的历史财务指标；
- `sw_l1_membership_history.csv`：带分类版本生效日的PIT申万行业；
- `benchmark_000300.csv`：离线沪深300价格指数。

如果Tushare事后修订历史数据，重新拉取的结果可能与原研究不同。因此精确复现必须使用
冻结数据包，仅执行相同API命令不能保证字节级一致。

### 复现当前冻结策略

```bash
ashare-main-board-bimonthly-ic \
  --warmup-start-date 2006-01-04 \
  --start-date 2007-01-01 \
  --end-date 2026-07-17 \
  --initial-cash 100000 \
  --positions 8 \
  --rebalance-offset 0 \
  --slippage 0.001 \
  --output-dir data/backtests/main_board_bimonthly_ic
```

本轮研究可按顺序运行：

```bash
python -m ashare_quant.research.incremental_behavioral_factors
python -m ashare_quant.research.incremental_behavioral_robustness
python -m ashare_quant.research.core_satellite_blend
python -m ashare_quant.research.dual_regime_strategy
python -m ashare_quant.research.dual_regime_meta_strategy
python -m ashare_quant.research.dual_regime_frozen_model
python -m ashare_quant.research.dual_regime_dual_offset_model
python -m ashare_quant.research.score_layer_blend --positions 6 8 10 12
```

已组装好本地数据后，可生成新的复现包：

```bash
python scripts/package_reproduction_data.py
```

命令会在`data/packages/`下生成压缩包、逐文件清单和压缩包SHA-256文件。

## 贡献

提交Issue或Pull Request前请阅读[CONTRIBUTING.md](../CONTRIBUTING.md)。
安全问题按[SECURITY.md](../SECURITY.md)私下报告。
社区行为遵循[CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md)。

## 许可证

Copyright (C) BlueCat-de。

项目使用[GNU GPL-3.0](../LICENSE)许可证。

## 维护者

[BlueCat-de](https://github.com/BlueCat-de)
