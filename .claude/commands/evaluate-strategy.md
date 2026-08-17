---
description: "对指定策略运行全面评测 pipeline"
---

# 策略评测：$ARGUMENTS

对 `$ARGUMENTS` 策略运行完整评测系统。

## 执行

```bash
.venv/Scripts/python.exe -m ashare_quant.evaluation.pipeline \
  --strategy-name "$ARGUMENTS" \
  --raw-perf "data/backtests/$ARGUMENTS/local_raw_perf.csv" \
  --output-dir "data/evaluation/$ARGUMENTS" \
  --n-trials 50
```

如果 raw_perf 路径不同，需要先确认正确路径。

## 评测覆盖的维度

| 模块 | 内容 |
|---|---|
| 核心指标 | Sharpe / Sortino / Calmar / Omega / Ulcer / VaR / CVaR / 尾部比率 |
| 统计显著性 | PSR / DSR / Haircut Sharpe / PBO(CSCV) / MinTRL |
| 风险分析 | 回撤分析(深度+持续期) / 5场景压力测试 / 4区制分析 / 滚动夏普 |
| 归因 | CAPM回归 / 捕获比率 / 逐年分解 / 月度热力图 |
| 成本 | 换手率 / 成本拖累 / 持有期 / 容量估算 |

## 产出

```
data/evaluation/<strategy>/
├── evaluation_report.md       # Markdown 报告（含 A-K 清单核验）
├── evaluation_summary.json    # 所有指标结构化
├── equity_curve.png           # 净值 + 回撤
├── rolling_sharpe.png         # 滚动夏普
├── yearly_heatmap.png         # 月度热力图
├── return_distribution.png    # 收益分布
└── regime_analysis.png        # 区制表现
```

## 注意

- `--n-trials` 是估计试过的策略变体数（影响 DSR 严格度），默认 50
- **预算利用率（SOP §0.3/§6.2 闭环）**：评测时应对照策略冻结的复杂度预算报 K/K_max、N/N_max、Effective N/上限。可用 `python -m ashare_quant.research.factor_library composite <策略的因子...> --effective-n <值>` 算组合复杂度；N 接近 N_max 或 Effective N 超上限时在报告里标红——这是把"事后算 DSR 的 N"和"事前冻结的预算"对上的反馈环。
- 评测系统位于 `src/ashare_quant/evaluation/`
- 框架对照标准：`docs/EVALUATION_FRAMEWORK_INDUSTRY_zh-CN.md`
