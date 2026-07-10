# 本地 A 股因子库

本项目的本地因子库基于已经稳定跑通的 BigQuant 离线日频行情缓存生成，不依赖 BigQuant 付费预计算因子表，也不依赖 `D.features` 旧接口。

当前版本除了基础量价因子、时间序列分位和横截面排名外，还补充了一组可由本地 OHLCV 数据近似实现的 `WorldQuant 101` 风格 alpha 子集，便于后续做 IC 检验和策略筛选。

## 数据来源

输入文件：

```text
data/offline/a_share_12m_bigquant/prices_long.csv
```

输入字段：

```text
date, symbol, open, high, low, close, volume, amount, turnover
```

这些字段来自当前项目的 BigQuant DAI 取数链路，和策略回测使用同一份行情口径。

## 生成命令

完整生成最近一年因子：

```bash
.venv/bin/python build_local_factor_library.py \
  --output-dir data/factors/local_a_share \
  --start-date 2025-07-08 \
  --end-date 2026-07-08
```

小样本 smoke test：

```bash
.venv/bin/python build_local_factor_library.py \
  --limit-symbols 20 \
  --output-dir data/factors/local_a_share_smoke \
  --start-date 2025-07-08 \
  --end-date 2026-07-08
```

如果只想快速验证包含 `WQ101` 子集的结果，也可以输出到单独目录：

```bash
.venv/bin/python build_local_factor_library.py \
  --limit-symbols 20 \
  --output-dir data/factors/local_a_share_wq_smoke \
  --start-date 2025-07-08 \
  --end-date 2026-07-08
```

默认会为每只股票保留 120 个自然日左右的 warmup，再输出正式区间的因子，避免滚动窗口早期值不足。

## 输出文件

```text
data/factors/local_a_share/factors_long.csv
  长表因子数据。每行是一个 date + symbol。

data/factors/local_a_share/feature_catalog.csv
  因子目录，记录每个因子的分类。

data/factors/local_a_share/manifest.json
  生成配置、日期范围、行数、股票数、因子列清单。
```

当前已生成版本：

```text
generated_at=2026-07-09T15:14:00+08:00
start_date=2025-07-08
end_date=2026-07-08
rows=731,682
symbols=3,034
feature_count=130
label_count=4
```

## 因子分类

当前版本是“透明、可解释、无额外权限依赖”的本地量价因子库，主要包含以下类别：

| 分类 | 数量 | 说明 |
| --- | ---: | --- |
| price_momentum_reversal | 28 | 动量、反转、均线偏离、区间位置、回撤等价格类因子 |
| risk_volatility | 17 | 波动率、下行波动率、收益偏度/峰度、日内振幅等风险类因子 |
| liquidity_volume | 37 | 成交额、成交量、换手率、量价相关等流动性因子 |
| time_series_rank | 5 | 个股自身时间序列分位数因子 |
| cross_sectional_rank | 20 | 每个交易日横截面百分比排名因子 |
| worldquant_101 | 14 | 基于本地 OHLCV 近似实现的 `WQ101` alpha 子集 |
| other | 9 | 均线、收盘位置、单位成交金额等基础派生字段 |

## 主要因子例子

价格/动量：

```text
momentum_5 / 10 / 20 / 60 / 120
reversal_5 / 10 / 20 / 60 / 120
ma_5 / 10 / 20 / 60 / 120
price_to_ma_5 / 10 / 20 / 60 / 120
drawdown_from_high_5 / 10 / 20 / 60 / 120
range_position_5 / 10 / 20 / 60 / 120
```

风险/波动：

```text
volatility_5 / 10 / 20 / 60 / 120
downside_volatility_5 / 10 / 20 / 60 / 120
return_skew_5 / 20 / 60
return_kurt_5 / 20 / 60
high_low_range
```

流动性/量价：

```text
amount_ma_5 / 10 / 20 / 60 / 120
volume_ma_5 / 10 / 20 / 60 / 120
turnover_ma_5 / 10 / 20 / 60 / 120
amount_ratio_5 / 10 / 20 / 60 / 120
volume_ratio_5 / 10 / 20 / 60 / 120
turnover_ratio_5 / 10 / 20 / 60 / 120
price_volume_corr_5 / 20 / 60
amount_return_corr_5 / 20 / 60
```

横截面排名：

```text
cs_rank_momentum_20
cs_rank_volatility_20
cs_rank_amount_ratio_20
cs_rank_turnover_ma_20
cs_rank_price_to_ma_60
...
```

WorldQuant 101 子集：

```text
wq101_alpha_001
wq101_alpha_002
wq101_alpha_003
wq101_alpha_004
wq101_alpha_005
wq101_alpha_006
wq101_alpha_007
wq101_alpha_008
wq101_alpha_012
wq101_alpha_013
wq101_alpha_014
wq101_alpha_015
wq101_alpha_016
wq101_alpha_018
```

其中部分公开公式原本依赖 `VWAP`，当前实现使用 `(open + high + low + close) / 4` 的本地代理口径，以保证在离线行情 schema 下可稳定复现。

## 标签列

脚本同时生成以下 forward return，方便后续做 IC / RankIC / 分组收益分析：

```text
fwd_return_1
fwd_return_5
fwd_return_10
fwd_return_20
```

注意：这些列是研究标签，只能用于因子检验，不能作为策略特征或模型输入，否则会引入未来函数。

## 读取示例

```python
import pandas as pd

factors = pd.read_csv(
    "data/factors/local_a_share/factors_long.csv",
    dtype={"symbol": str},
)

cols = [
    "date",
    "symbol",
    "momentum_20",
    "volatility_20",
    "amount_ratio_20",
    "cs_rank_momentum_20",
    "wq101_alpha_002",
    "fwd_return_5",
]
print(factors[cols].head())
```

## 设计原则

1. **不依赖外部因子权限**：只使用项目已有 BigQuant 日频行情缓存。
2. **口径一致**：因子生成、策略回测和每日自动任务使用同一份离线行情。
3. **避免未来函数**：特征只使用当日及历史数据；forward return 仅作为研究标签单独标注。
4. **先量价后扩展**：先做稳定的 OHLCV 基础因子，再叠加本地可复现的 `WQ101` 风格 alpha 子集。
5. **便于检验**：保留横截面排名和 forward return，方便下一步做 IC / RankIC / 分组收益分析。

## 下一步建议

1. 对 130 个因子批量做 IC / RankIC 检验，并单独评估 `WQ101` 子集的稳定性。
2. 做五分组或十分组收益检验，剔除高换手、低稳定性因子。
3. 计算因子相关性矩阵，合并高度相关因子，压缩冗余特征。
4. 将低相关、稳定有效的基础因子和 `WQ101` 因子纳入当前 `v4` 策略候选评分逻辑。