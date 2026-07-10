# 本地因子组合策略研究报告

## 结论摘要
- 本地取数链路已更新到 `2026-07-08`；daemon 状态显示取数 `skipped`、策略 `success`。
- 本地因子库覆盖 `2025-07-08` ~ `2026-07-08`，`3034` 只股票、`130` 个特征、`731682` 行。
- 当前生产 v4 仍是更成熟的线上候选：过去一年 BigTrader 摘要收益和回撤优于本次本地近似研究策略。
- 本次探索中表现最好的本地因子组合是 `local_wq_smooth_midband`：累计收益 12.56%，年化 13.05%，Sharpe 1.45，最大回撤 -7.52%。
- 该结果是样本内探索；建议先作为 v4 的候选股二级排序/风险过滤，而不是直接替换生产策略。

## 本地数据与任务状态
- 离线行情：`data/offline/a_share_12m_bigquant`，BigQuant 最新日期 `2026-07-08`，本地最新日期 `2026-07-08`，最近一次更新状态 `skipped`。
- 每日状态机：取数最近一次 `skipped`，策略最近一次 `success`，策略成功数据日 `2026-07-08`。
- v4 最近完整一年 BigTrader：累计收益 27.20%，Sharpe 1.66，最大回撤 -8.79%。

## 当前策略使用的因子/变量
- v4 候选约束：价格区间、站上 MA20/MA60/MA120、20/60/120 日动量、20 日波动率、60 日回撤、开盘跳空、20 日成交额，以及市场宽度/市场波动状态。
- v4 打分：`momentum_60`、`momentum_120`、`momentum_20`、`price_to_ma_60`、低 `volatility_20`、低 `downside_volatility_60`、`drawdown_from_high_60`、`amount_ma_20`。
- v4_volume/v5 研究版本额外引入 `turnover_ma_20`、`volume_ratio_20`、`amount_trend60`、正收益天数比例和换手平衡项。

## 因子筛选方法
- 股票池：价格 2.5~85 元、20 日成交额不低于 3000 万、20 日换手率 0.3%~16%，排除极端放量和深回撤样本。
- 检验口径：逐日横截面 Spearman RankIC，并按方向调整后计算 Top/Bottom 十分位未来收益差。
- 组合逻辑：优先使用低相关的 WQ101 量价结构因子，再叠加低波动、低拥挤、短期回撤/平滑趋势约束。

### 20 日 RankIC Top 因子（方向已调整）
| feature | direction | mean_rank_ic | median_rank_ic | ic_positive_rate | mean_top_bottom_spread |
| --- | --- | --- | --- | --- | --- |
| wq101_alpha_016 | 1.0 | 0.05 | 0.04 | 84.75% | 0.54% |
| volatility_20 | -1.0 | 0.05 | 0.04 | 60.54% | -1.97% |
| wq101_alpha_013 | 1.0 | 0.05 | 0.05 | 82.51% | 0.43% |
| downside_volatility_20 | -1.0 | 0.04 | 0.03 | 60.54% | -0.87% |
| price_to_ma_60 | -1.0 | 0.04 | 0.04 | 65.92% | -2.59% |
| ts_rank_turnover_60 | -1.0 | 0.04 | 0.04 | 66.37% | -0.15% |
| ts_rank_amount_60 | -1.0 | 0.04 | 0.04 | 64.13% | -0.54% |
| price_volume_corr_20 | -1.0 | 0.04 | 0.04 | 69.96% | 0.04% |
| amount_return_corr_20 | -1.0 | 0.04 | 0.04 | 68.16% | -0.06% |
| volatility_60 | -1.0 | 0.04 | 0.04 | 60.54% | -2.34% |

## 策略设计
- `local_wq_lowvol_pullback`：WQ101 alpha_016/013/006 为核心，叠加低波动、低成交拥挤和短期回撤，不追高。
- `local_wq_smooth_trend`：WQ101 核心 + 60/120 日趋势质量 + 低波动，要求中期趋势为正。
- `local_defensive_reversal`：更强调短期反转、低波动和低拥挤，只要求中期趋势不明显破坏。
- `_midband/_lowband` 版本来自十分位检验：当极端高分并非最优时，改选历史分组收益更好的中间分位或低分位。
- 交易规则：周频调仓，信号日收盘生成目标，下一交易日开盘按 100 股整数手成交；买入成本 0.03%，卖出成本 0.13%，最低佣金 5 元。

## 过去一年本地近似回测结果
| strategy | total_return | annual_return | sharpe | max_drawdown | win_rate |
| --- | --- | --- | --- | --- | --- |
| BigTrader v4 baseline | 27.20% | 28.33% | 1.66 | -8.79% | 65.22% |
| local_wq_smooth_midband | 12.56% | 13.05% | 1.45 | -7.52% | 23.87% |
| liquid universe equal-weight | 7.70% | 7.99% | 0.53 | -12.70% | 54.32% |
| local_wq_smooth_trend | -2.21% | -2.30% | -0.00 | -19.75% | 23.46% |
| local_wq_lowvol_midband | -2.50% | -2.59% | -0.28 | -10.05% | 25.10% |
| local_wq_lowvol_pullback | -4.14% | -4.29% | -0.69 | -7.80% | 22.22% |
| local_defensive_reversal_lowband | -15.55% | -16.08% | -0.95 | -22.45% | 25.51% |
| local_defensive_reversal | -8.42% | -8.72% | -1.54 | -11.20% | 20.58% |

### 本次最佳组合十分位检验
| recipe | decile | fwd_return_5 | fwd_return_10 | fwd_return_20 |
| --- | --- | --- | --- | --- |
| local_wq_smooth_midband | 1 | -0.10% | 0.14% | 0.68% |
| local_wq_smooth_midband | 2 | 0.22% | 0.54% | 1.29% |
| local_wq_smooth_midband | 3 | 0.43% | 0.86% | 1.79% |
| local_wq_smooth_midband | 4 | 0.35% | 0.95% | 2.22% |
| local_wq_smooth_midband | 5 | 0.58% | 1.05% | 2.02% |
| local_wq_smooth_midband | 6 | 0.63% | 1.21% | 2.42% |
| local_wq_smooth_midband | 7 | 0.68% | 1.28% | 2.36% |
| local_wq_smooth_midband | 8 | 0.65% | 1.34% | 2.25% |
| local_wq_smooth_midband | 9 | 0.51% | 1.26% | 2.29% |
| local_wq_smooth_midband | 10 | 0.70% | 1.17% | 1.91% |

## 研究判断
- WQ101 alpha_016/013/006 在本地样本中 RankIC 稳定性最好，适合作为现有 v4 候选池内的二级排序增强。
- 单纯追逐价格动量在这一年横截面上并不稳定；更稳健的用法是保留趋势约束，但把拥挤放量、高波动和短期过热作为扣分项。
- 本地回测撮合是近似版，不等同 BigTrader；若要进入生产，应先把最佳组合接入 `bigquant_strategy.py` 的研究版本，再用 BigTrader 复核。
- 不建议用本次样本内最佳策略替换 v4；更合理的下一步是把 `wq101_alpha_016/013/006 + low_vol + low_crowding` 做成 v4 的候选股二级排序或风险过滤开关。

## 输出文件
- 研究目录：`/mnt/bn/ecom-ai-platform-1/chenqimao/Personal_Strategy/data/backtests/local_factor_composite/20260708_full_year`
- `factor_ic.csv`、`strategy_summaries.csv`、`equity_curves.csv`、`trades.csv`、`signals.csv`、`signal_debug.csv`、`composite_deciles.csv`、`selected_factor_correlation.csv`。
