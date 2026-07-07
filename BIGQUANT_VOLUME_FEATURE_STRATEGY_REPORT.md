# BigQuant 成交量特征增强策略报告

## 结论

本次实验不支持把成交量、成交额、换手率作为 v4 策略的主 alpha 因子；重权加入成交量排序会明显削弱原有价格动量与趋势信号。

相对有效的用法是：保留 v4 原始价格动量、趋势、低波和回撤修复框架，只把 BigQuant 新增的真实成交额与换手率用于风险过滤。当前最优候选为 `v4_volume_risk_filter`：

- 累计收益率：`21.23%`，略高于原 v4 的 `21.15%`
- 年化收益率：`22.20%`，略高于原 v4 的 `22.12%`
- Sharpe：`1.58`，略高于原 v4 的 `1.55`
- 胜率：`59.09%`，高于原 v4 的 `54.55%`
- 最大回撤：`8.02%`，弱于原 v4 的 `6.90%`

因此，`v4_volume_risk_filter` 可以作为研究候选，但不建议直接替换生产默认 v4。更稳妥的生产结论是：继续以原 v4 为主策略，把成交量增强版作为旁路观察版本。

## 数据差异

同样的 `prices_long.csv` schema 下，BigQuant 与原 Tencent/Sina 数据的主要差异如下：

| 字段 | BigQuant 数据 | Tencent/Sina 数据 | 策略价值 |
| --- | --- | --- | --- |
| `open/high/low/close` | 完整，已由适配层转为项目兼容复权口径 | 完整，但来源和复权口径依赖自建适配 | 两者都可用于 v4 价格动量 |
| `volume` | 完整，适配层转为“手” | 完整，单位同项目旧 schema | 两者都可粗略使用成交量 |
| `amount` | 完整，非空率 `100%` | 基本缺失，非空率约 `0.29%` | BigQuant 核心增量，可衡量真实成交额 |
| `turnover` | 完整，非空率 `100%` | 基本缺失，非空率约 `0.29%` | BigQuant 核心增量，可衡量换手活跃度 |
| 股票数 | 约 `3045` | 约 `3046` | 差异很小，主要来自数据源覆盖口径 |
| 日期 | `2025-01-07` 至 `2026-07-06` | `2025-01-06` 至 `2026-07-06` | BigQuant 少一天预热首日，不影响正式区间 |

## 策略设计

### 原 v4

原 v4 继续保留：

- 市场宽度过滤：MA20/60/120 宽度、市场波动、弱势回撤扩散。
- 个股过滤：价格区间、MA20/60/120、20/60/120 日动量、波动率、60 日回撤、开盘跳空。
- 横截面评分：动量、趋势强度、低波、下行波动、回撤修复、成交活跃度。
- 仓位：最多 2 只，单票最高 34%，强市场总仓位 68%，中性市场 34%。
- 风控：MA20 趋势退出、6% 止损、10% 跟踪止损。

### 成交量增强候选

本次加入以下 BigQuant 特征：

- `avg_value20`：20 日平均成交额。
- `avg_turnover20`：20 日平均换手率。
- `volume_ratio20`：当日成交量 / 20 日平均成交量。
- `amount_trend60`：20 日平均成交额 / 60 日平均成交额 - 1。

尝试过三类规则：

1. `v4_volume_enhanced`：成交量确认 + 成交量重权评分。
2. `v4_volume_light`：原 v4 框架 + 小权重成交量修正。
3. `v4_volume_risk_filter`：原 v4 评分不变，只过滤成交额趋势明显走弱、流动性不足或极端过热的候选。

实验证明，第 3 类最稳健。

## 回测设置

- 数据：`data/offline/a_share_12m_bigquant/prices_long.csv`
- 预热区间：`2025-01-07` 至 `2025-07-04`
- 正式区间：`2025-07-05` 至 `2026-07-06`
- 引擎：BigQuant BigTrader
- 初始资金：`100000`
- 买入/卖出价格：下一交易日开盘价
- T+1：`context.set_stock_t1(1)`
- 手续费：`buy_cost=0.0003, sell_cost=0.0013, min_cost=5.0`
- BigTrader 信号语义：事件日完整目标持仓快照，非调仓日 `instrument=None` 哨兵行。

## 绩效对比

| 版本 | 累计收益 | 年化收益 | Sharpe | 最大回撤 | 胜率 | 盈亏比 | 信号行 | 交易标的 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| v4_baseline | 21.15% | 22.12% | 1.55 | 6.90% | 54.55% | 2.79 | 270 | 17 |
| v4_volume_enhanced | 4.96% | 5.17% | 0.22 | 13.11% | 36.00% | 2.19 | 275 | 25 |
| v4_volume_score_only | 0.03% | 0.03% | -0.17 | 17.10% | 28.00% | 2.67 | 274 | 24 |
| v4_volume_guard_only | 11.86% | 12.38% | 0.75 | 11.80% | 36.00% | 2.76 | 276 | 24 |
| v4_volume_light | 12.67% | 13.23% | 0.88 | 9.87% | 51.85% | 1.82 | 272 | 20 |
| v4_volume_risk_filter | 21.23% | 22.20% | 1.58 | 8.02% | 59.09% | 2.34 | 270 | 18 |
| v4_volume_risk_filter_tight | 11.23% | 11.72% | 0.80 | 10.55% | 56.52% | 1.47 | 271 | 19 |
| v4_volume_risk_filter_defensive | 17.30% | 18.07% | 1.31 | 8.02% | 54.55% | 2.28 | 270 | 18 |

## 解释

成交量重权排序失效的原因：

- v4 的收益主要来自强价格动量、趋势延续和低波组合，而不是单纯成交活跃。
- 成交额、换手率高的股票往往已经进入拥挤交易阶段，追高风险更大。
- 一年样本下，成交量因子容易被短期主题行情和个股异动污染。

成交额趋势过滤有效的原因：

- 原 v4 的亏损交易中存在“价格趋势尚可，但成交额趋势已经转弱”的标的。
- `amount_trend60` 能识别资金关注度下降，适合作为风险过滤器。
- 该规则不改变主 alpha，只剔除一部分质量较差候选，因此过拟合风险低于重权排序。

## 推荐命令

原 v4 基准：

```bash
HOME=/Users/bytedance/cqm/Personal_Strategy conda run -n bigquant python -u bigquant_strategy.py \
  --strategy-version v4 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_v4_recheck/20260706
```

成交量风险过滤候选：

```bash
HOME=/Users/bytedance/cqm/Personal_Strategy conda run -n bigquant python -u bigquant_strategy.py \
  --strategy-version v4_volume_risk_filter \
  --min-amount20 30000000 \
  --max-turnover20 0.18 \
  --max-volume-ratio20 3.2 \
  --min-amount-trend60 -0.10 \
  --warmup-start-date 2025-01-07 \
  --start-date 2025-07-05 \
  --end-date 2026-07-06 \
  --prices-file data/offline/a_share_12m_bigquant/prices_long.csv \
  --output-dir data/backtests/bigquant_strategy_v4_volume_risk_filter/20260706
```

## 生产建议

当前不建议把默认生产策略直接切到成交量增强版。更合理的做法是：

1. 原 v4 继续作为主策略。
2. `v4_volume_risk_filter` 作为旁路策略每日生成信号。
3. 至少再积累 1-3 个月实盘模拟信号，对比调仓日、持仓、收益和回撤。
4. 如果成交量风险过滤在样本外继续提升胜率和 Sharpe，且回撤不继续扩大，再考虑替换默认版本。

