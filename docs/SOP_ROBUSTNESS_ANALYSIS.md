# SOP: 策略健壮性分析流程

> 本文档总结对本地因子策略进行**专业健壮性（Robustness）分析**的标准流程：
> 在不碰 test 集的前提下，用一系列扰动实验回答「这个策略是真的稳健，
> 还是参数过拟合出来的？」并以 `composite_alpha_no_industry` 的实测结果为模板。
>
> 适用范围：任意基于 `build_targets → run_local_backtest` 框架的本地策略。

---

## 0. 为什么要做健壮性分析

回测指标好（高夏普、高收益）不等于策略可靠。一个策略可能只是恰好踩在了一组
**孤立最优参数**上——换一个调仓日、换一个滑点假设、把某因子权重挪 20%，曲线
就可能崩掉。健壮性分析的目标是**绘制策略在参数空间邻域内的性能地形图**：

- 找出**脆弱点**（小幅扰动 → 大幅退化），实盘前就知道哪里不能碰；
- 找出**冗余因子**（去掉反而更好），简化策略、降低过拟合；
- 量化对**交易成本假设**的敏感度，判断实盘可行性；
- 给出「这个策略是否值得上实盘」的客观证据。

**核心纪律**：所有扰动实验只在 **train + val**（2007–2020）上做；**test 集
（2021–2026）全程保密**，最后只揭盲一次。绝不用 val 上的扰动结果去反向调权重
再回到 val 验证——那是过拟合。

---

## 1. 分析维度（量化行业通用模式）

本 SOP 覆盖五个维度，每个维度回答一个独立问题：

| 维度 | 实验 | 回答的问题 |
| --- | --- | --- |
| A. 基线复现 | 默认参数跑一次 | 后续扰动的参照系；验证脚本能忠实复现策略 |
| B. 因子权重微扰 | 每个因子 ±50% | 单因子权重是否在合理区间？哪个方向更敏感（非对称性）？ |
| C. 因子消融 | leave-one-out 去掉每个因子 | 每个因子的边际贡献排名；哪些是冗余/有害的？ |
| D. 滑点敏感度 | 5/10/20/50 bp | 实盘滑点假设若偏乐观，策略是否还成立？ |
| E. 持仓数 | 6/8/10/12 | 持仓数是任意选的还是局部最优？集中度风险？ |
| F. 调仓相位 | offset 0 vs 1 | 策略是否过拟合到某个特定的双月起始相位？ |

**为什么是这五个**：
- B + C 是**因子层**健壮性（模型本身）；
- D 是**成本层**健壮性（落地可行性）；
- E + F 是**执行层**健壮性（参数选择是否任意）。

可按需扩展的维度（本 SOP 未默认包含，但同框架可加）：
- **换手率/费用扫描**：`max_participation_rate`、佣金倍数；
- **中性化方式消融**：市值中性 vs 市值+行业中性（即 `composite_alpha` ↔ `composite_alpha_no_industry` 的对比，已单独完成）；
- **子样本稳定性**：val 段按年切片看逐年夏普方差；
- **bootstrapping / 蒙特卡洛**：对调仓日或标的随机扰动看分布。

---

## 2. 方法论要点

### 2.1 微扰实验的设计（维度 B）

对每个因子 $f$，将其权重 $w_f$ 扰动到 $w_f(1+\delta)$（$\delta \in \{+0.5, -0.5\}$），
**变化量按其余因子原权重比例分摊**，保持总权重归一：

```python
def perturb_weights(base, factor, delta):
    w = dict(base)
    old, new = w[factor], w[factor] * (1 + delta)
    w[factor] = new
    diff = old - new                      # 被挪走的权重
    others = {k: v for k, v in w.items() if k != factor and v > 0}
    tot = sum(others.values())
    for k in others:                      # 按比例分摊给其它因子
        w[k] += diff * (others[k] / tot)
    return w
```

**关注的不只是 |Δsharpe|，更是非对称性**：
- 两边都掉很多 → 权重在合理区间（峰顶）；
- +50% 掉、-50% 升 → 权重偏高，可考虑下调；
- -50% 掉、+50% 升 → 权重偏低，可考虑上调（**但不能据此在 val 上调参**，见 §5）。

### 2.2 消融实验的设计（维度 C）

去掉因子 $f$ 后**重新归一化**其余权重（等比放大），跑回测。
排名的是 $\Delta\text{sharpe} = \text{sharpe}_{\text{no-}f} - \text{sharpe}_{\text{base}}$：

- $\Delta$ 很负 → 该因子边际贡献大，是策略支柱；
- $\Delta \approx 0$ → 冗余，去掉无影响（可简化）；
- $\Delta > 0$ → **该因子在当前权重下是有害的**，去掉反而好（重要信号！）。

**判定冗余/有害的硬标准**：消融改善必须在 **dev 和 val 两段同时为正**，才算稳健结论
（避免只在 val 上偶然成立）。

### 2.3 预计算优化（性能关键）

朴素做法每个扰动实验都重算一遍因子面板（$\approx 60\text{s}/\text{次}$），44 个实验要 45 分钟+
**纯重复计算**。本 SOP 的实现把可复用的部分预计算一次：

1. **因子面板只算一次**：对全部月频调仓日调用 `factor_snapshot` + `_add_behavioral_factors`，
   缓存为 `[(date, loc, panel), ...]`；
2. **权重扰动/消融只重做打分+选股**：`_composite_score_no_industry(panel, weights)` 是纯内存计算，毫秒级；
3. **滑点扫描只重做回测引擎**：targets 完全复用，只换 `BacktestConfig.slippage`。

实测：168 个月频面板预计算 62s（一次性），其后每个扰动实验仅 ~15s（回测引擎本身），
全部 44 个实验约 12 分钟。

---

## 3. 实现

脚本：[composite_robustness.py](../src/ashare_quant/research/composite_robustness.py)

核心结构：

```python
# 1. 一次性预计算月频因子面板（两个 offset 相位的并集）
panels = precompute_panels(prices, args)                 # 168 个月频面板

# 2. 标准测试用 offset=0 的双月子集（= 生产策略的真实节奏）
bimonthly_panels = filter_panels_by_offset(panels, prices, args, 0)   # 84 个

# 3. 各维度扫描：build_targets_from_panels → run_eval
targets = build_targets_from_panels(bimonthly_panels, weights, prices, positions=8)
row = run_eval(prices, targets, benchmark, bt_config, tag)   # 返回 dev/val 指标
```

**复用基础设施（不改）**：
- [_composite_score_no_industry](../src/ashare_quant/strategies/composite_alpha_no_industry.py#L80)：市值中性化打分
- [factor_snapshot](../src/ashare_quant/research/style_frequency.py#L436)：PIT 因子面板
- [_add_behavioral_factors](../src/ashare_quant/strategies/composite_alpha.py#L120)：行为因子
- [run_local_backtest](../src/ashare_quant/backtest.py#L121)：T+1 撮合引擎
- [segment_metrics](../src/ashare_quant/research/long_horizon.py#L475)：分段指标
- [bimonthly_rebalance_dates](../src/ashare_quant/strategies/main_board_bimonthly_ic.py#L116) / [cadence_dates](../src/ashare_quant/research/style_frequency.py#L355)：调仓日历

---

## 4. 执行步骤

```bash
# 默认在 train+val（2007-01-01 ~ 2020-12-31）上跑全部 5 维度
.venv/Scripts/python.exe -m ashare_quant.research.composite_robustness

# 产物：
#   data/research/composite_robustness/robustness_results.csv
```

CSV 列：`tag, dev_sharpe, dev_return, val_sharpe, val_return, val_max_drawdown,
perturbed_factor, perturbation, delta_val_sharpe, ablated_factor, slippage_bp,
positions, rebalance_offset`（未涉及的维度为 NaN，按 tag 前缀区分实验类型）。

**复用到其它策略**：复制脚本，替换 `_composite_score_no_industry` 为目标策略的打分函数、
替换 `COMBINED_WEIGHTS` 为目标策略的权重表即可。预计算/扫描框架完全通用。

---

## 5. 结果解读规范（防止过拟合）

> ⚠️ **最重要的一条**：健壮性分析是**诊断**，不是**调参**。
> 不能把「+50% dividend_yield 在 val 上 sharpe 更高」当成「应该上调 dividend_yield 权重」——
> 那等于在 val 上做参数搜索，污染了 val 作为样本外验证的纯洁性。

**合法的结论类型**：
1. **脆弱性告警**：某维度小幅扰动 → 大幅退化。实盘前必须知道，但不一定要改（可能只是边界）。
2. **冗余简化**：消融在 **dev 和 val 同时改善** → 去掉该因子是稳健的简化（不是过拟合，因为两段独立样本都支持）。
3. **成本可行性**：滑点在合理范围内不崩 → 实盘可行。
4. **参数任意性**：某参数邻域平缓 → 该参数选择不敏感，实盘放心；某参数是孤立峰 → 实盘要严格还原。

**非法的结论**：基于 val 单段的最优去反向修改权重/参数，再声称「更好」。

---

## 6. 实测结果：composite_alpha_no_industry

**基线**（val 段，offset=0，n=8，滑点 10bp）：`dev_sharpe=0.930 / val_sharpe=0.942 / val_mdd=-45.7%`，
与 [performance.json](../data/backtests/composite_no_industry_full/performance.json) 完全一致——证明脚本忠实复现生产策略。

### 6.1 因子权重微扰（维度 B）

| 因子 | 当前权重 | +50% Δval_sharpe | −50% Δval_sharpe | 读数 |
| --- | --- | --- | --- | --- |
| dividend_yield | 0.125 | **+0.030** | **−0.410** | 减半即崩；上调略好。价值核心支柱 |
| AMIHUD20 | 0.150 | **−0.371** | −0.081 | 上调即崩；**权重已到上限边缘** |
| q_sales_yoy | 0.068 | −0.277 | −0.106 | 上调敏感；略偏高 |
| earnings_yield | 0.079 | −0.003 | **−0.287** | 减半即崩；偏低，价值支柱 |
| ocf_yoy | 0.076 | −0.234 | −0.096 | 上调敏感；略偏高 |
| low_turnover | 0.125 | −0.055 | **−0.213** | 减半明显退化；价值核心支柱 |
| roe | 0.037 | −0.118 | −0.028 | 上调敏感；当前权重合适 |
| low_residual_vol60 | 0.124 | −0.083 | −0.036 | 两端都稳；权重合适 |
| reversal20 | 0.066 | −0.052 | −0.093 | 两端都稳；贡献有限 |
| SKEW60 | 0.050 | −0.025 | −0.040 | 最不敏感；权重合适 |
| MAX20 | 0.100 | −0.048 | **+0.035** | 减半反而升；**疑似偏高** |

**关键读数**：
- 价值四件套（dividend_yield / earnings_yield / low_turnover / low_residual_vol60）减半全部大幅退化，是策略的承重墙；
- AMIHUD20 权重 0.15 已在悬崖边——再加 50% val_sharpe 从 0.94 跌到 0.57（流动性因子过权重会扎堆小盘 illiquid 名字）；
- MAX20 两项证据都指向「偏高」（−50% 升、消融去掉也升，见下）。

### 6.2 因子消融（维度 C，按重要性排序）

| 去掉的因子 | val_sharpe | Δval_sharpe | dev_sharpe | 读数 |
| --- | --- | --- | --- | --- |
| dividend_yield | 0.565 | **−0.377** | 0.852 | 第 1 重要 |
| earnings_yield | 0.572 | **−0.370** | 0.812 | 第 2 重要 |
| low_turnover | 0.586 | **−0.357** | 0.831 | 第 3 重要 |
| AMIHUD20 | 0.611 | **−0.332** | 0.726 | 第 4 重要（行为因子里唯一核心） |
| low_residual_vol60 | 0.688 | −0.254 | 0.773 | 第 5 重要 |
| ocf_yoy | 0.821 | −0.121 | 0.908 | 中等 |
| SKEW60 | 0.880 | −0.063 | 0.967 | 边际 |
| roe | 0.886 | −0.057 | 0.904 | 边际 |
| q_sales_yoy | 0.906 | −0.037 | 0.937 | 边际 |
| reversal20 | 0.947 | +0.004 | 0.970 | **冗余**（去掉无影响） |
| MAX20 | 0.970 | **+0.027** | 0.975 | **有害**（去掉反而两段都升） |

**关键读数**：
- 重要性排名清晰：价值核心 > AMIHUD20 > 低波 > 成长类 > 行为类尾部；
- **MAX20 在 dev（+0.045）和 val（+0.027）两段同时改善** → 满足 §5 的稳健简化标准，去掉它在两段独立样本上都更好。这是本次分析最强的可执行结论；
- **reversal20** 两段都近乎不变（dev +0.040 / val +0.004）→ 冗余，可考虑去掉以简化。

### 6.3 滑点敏感度（维度 D）

| 滑点(单边) | val_sharpe | val_mdd |
| --- | --- | --- |
| 5 bp | 0.950 | −45.6% |
| 10 bp（基线） | 0.942 | −45.7% |
| 20 bp | 0.894 | −45.9% |
| 50 bp | 0.807 | −46.3% |

**读数**：极其稳健。滑点放大 5 倍（10→50bp），夏普仅从 0.94 降到 0.81，回撤几乎不动
（−45.7% → −46.3%）。说明策略收益不是靠抠成本假设挤出来的，实盘滑点不确定性风险低。

### 6.4 持仓数（维度 E）

| 持仓数 | val_sharpe | val_mdd |
| --- | --- | --- |
| 6 | 0.625 | −48.3% |
| **8（基线）** | **0.942** | −45.7% |
| 10 | 0.884 | −46.3% |
| 12 | 0.810 | −49.2% |

**读数**：n=8 是**尖锐的局部最优**。n=6 集中度风险爆发（夏普掉到 0.62），n>8 稀释 alpha。
说明「8 只」不是随便选的，而是数据支持的最优点——但尖锐峰值也意味着实盘要严格保持 8 只，
不宜临时增减。

### 6.5 调仓相位（维度 F）

| offset | val_sharpe | val_return | val_mdd |
| --- | --- | --- | --- |
| 0（基线，双月起始） | 0.942 | 383% | −45.7% |
| 1（错开一个月） | 0.721 | 205% | −41.7% |

**读数**：⚠️ **这是策略最显著的脆弱点**。把双月调仓从「偶数月」错开到「奇数月」，
val_sharpe 从 0.94 掉到 0.72（Δ=−0.22），val 收益几乎减半。
说明策略对调仓时点的「月份相位」有过拟合成分——实盘必须严格按 offset=0 的节奏执行，
不能随意错开。

---

## 7. 结论与建议（针对 composite_alpha_no_industry）

**整体评价**：策略在因子层和成本层**健壮**，在执行层（持仓数、调仓相位）**有明确的脆弱点**。

| 维度 | 结论 | 风险等级 |
| --- | --- | --- |
| 价值核心因子 | 不可动（减半即崩），承重墙 | — |
| 滑点假设 | 极稳健，5x 滑点不崩 | 低 |
| 持仓数 n=8 | 数据支持的最优，但峰值尖锐 | 中（实盘严格保持 8） |
| 调仓相位 offset | ⚠️ 过拟合到 offset=0 | **高** |
| MAX20 因子 | 两段证据均显示当前权重有害 | 可执行简化 |
| reversal20 因子 | 冗余 | 可执行简化 |

**可执行建议（按是否触碰样本隔离分类）**：

1. **稳健简化（合法，dev+val 双段支持）**：考虑去掉 MAX20 与 reversal20，
   或至少把 MAX20 权重从 0.10 下调。两段独立样本都支持此改动。
   ⚠️ 改完后若要重新评估，必须用新参数跑一次 test 揭盲，且**揭盲后不再回头调**。
2. **实盘纪律（不涉及调参）**：
   - 严格保持 8 只持仓、offset=0 双月节奏——这两个是脆弱点，偏离即退化；
   - 滑点预算可放心按 10bp 设，即便实盘到 20–30bp 仍 Sharpe > 0.85；
   - AMIHUD20 权重不要再上调（已在悬崖边）。
3. **不建议（会污染样本）**：不要因「+50% dividend_yield 在 val 更好」就上调
   dividend_yield 权重——那是 val 上的参数搜索。

---

## 8. 局限性与注意事项

- **微扰是局部的**：±50% 单因子扰动只画了权重空间的一维十字，没扫联合分布。
  真正的权重最优可能在多维联合扰动里，但本 SOP 刻意不去做联合寻优——会滑向过拟合。
- **消融 ≠ 替代**：「去掉 MAX20 更好」只说明当前权重下它边际为负，不代表 MAX20 这个因子
  本身没用——换一个权重组合它可能重新有效。
- **val 段仍是样本内**：所有 Δ 都在 val 上测，val 本身有噪声。判定冗余/有害必须看 dev+val
  一致性（§5），单段证据不够。
- **不碰 test**：本流程全部在 2007–2020 完成。任何基于本分析的策略修改，其最终评判只能来自
  一次 test 揭盲，不能把 test 拉进来反复试。
- **行为因子的特殊性**：AMIHUD20 / MAX20 / SKEW60 与流动性/市值高度相关，权重敏感度
  普遍高于基本面因子，扰动幅度（±50%）可能偏大，解读时关注方向而非绝对数值。
