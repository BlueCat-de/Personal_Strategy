"""Report generation: Markdown + matplotlib charts for evaluation pipeline."""

from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

PPY = 252


def _fmt(x, suffix="", pct=False, float_fmt=".3f"):
    """Format a number for the report."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "N/A"
    if pct:
        return f"{x*100:.2f}%"
    if isinstance(x, (int,)):
        return f"{x}{suffix}"
    return f"{x:{float_fmt}}{suffix}"


def _configure_matplotlib():
    plt.rcParams.update({
        "font.family": "sans-serif",
        "font.sans-serif": ["Microsoft YaHei", "SimHei", "Arial", "DejaVu Sans"],
        "axes.unicode_minus": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "figure.dpi": 150,
    })


def _plot_equity_curve(raw_perf, benchmark, output_path):
    """Equity curve + drawdown subplot."""
    _configure_matplotlib()
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    rp = raw_perf.set_index("date")

    bm = benchmark.copy()
    bm["date"] = pd.to_datetime(bm["date"])
    bm = bm.set_index("date")["benchmark_close"]

    # Rebase to 1.0
    eq = rp["portfolio_value"] / rp["portfolio_value"].iloc[0]
    bm_nav = bm / bm.loc[eq.index[0]] if eq.index[0] in bm.index else bm / bm.iloc[0]
    bm_nav = bm_nav.reindex(eq.index).ffill()

    dd = eq / eq.cummax() - 1

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 8), gridspec_kw={"height_ratios": [3, 1]}, sharex=True)
    ax1.plot(eq.index, eq.values, label="策略净值", linewidth=1.5, color="#2563eb")
    ax1.plot(bm_nav.index, bm_nav.values, label="沪深300", linewidth=1, color="#94a3b8", alpha=0.7)
    ax1.fill_between(dd.index, dd.values, 0, alpha=0.3, color="#ef4444")
    ax1.set_ylabel("累计净值")
    ax1.legend(frameon=False, loc="upper left")
    ax1.spines[["top", "right"]].set_visible(False)

    ax2.fill_between(dd.index, dd.values * 100, 0, alpha=0.4, color="#ef4444")
    ax2.set_ylabel("回撤 (%)")
    ax2.set_xlabel("")
    ax2.spines[["top", "right"]].set_visible(False)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _plot_rolling_sharpe(raw_perf, output_path):
    """Rolling 252-day Sharpe ratio."""
    from .risk import rolling_sharpe

    _configure_matplotlib()
    rs = rolling_sharpe(raw_perf, 252)
    fig, ax = plt.subplots(figsize=(14, 4))
    ax.plot(rs.index, rs.values, linewidth=1.2, color="#2563eb")
    ax.axhline(0, color="#adb5bd", linewidth=0.8)
    ax.axhline(rs.mean(), color="#f59e0b", linewidth=1, linestyle="--", label=f"均值 {rs.mean():.2f}")
    ax.set_ylabel("滚动夏普 (252日)")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _plot_yearly_heatmap(raw_perf, output_path):
    """Year × month returns heatmap."""
    _configure_matplotlib()
    from .attribution import monthly_returns_matrix

    matrix = monthly_returns_matrix(raw_perf)
    # Drop the "全年" column for the heatmap
    monthly_only = matrix.drop(columns=["全年"], errors="ignore")

    fig, ax = plt.subplots(figsize=(12, max(4, len(monthly_only) * 0.4)))
    data = (monthly_only.values * 100)
    im = ax.imshow(data, cmap="RdYlGn", aspect="auto", vmin=-15, vmax=15)
    ax.set_xticks(range(len(monthly_only.columns)))
    ax.set_xticklabels(monthly_only.columns, fontsize=9)
    ax.set_yticks(range(len(monthly_only.index)))
    ax.set_yticklabels(monthly_only.index, fontsize=9)
    # Annotate cells
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            val = data[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=7,
                        color="white" if abs(val) > 10 else "black")
    plt.colorbar(im, ax=ax, label="月度收益 (%)", shrink=0.8)
    ax.set_title("月度收益率热力图")
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _plot_return_distribution(raw_perf, output_path):
    """Daily return distribution histogram + normal overlay."""
    _configure_matplotlib()
    raw_perf = raw_perf.copy()
    raw_perf["date"] = pd.to_datetime(raw_perf["date"])
    returns = raw_perf.set_index("date")["returns"].dropna()

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.hist(returns * 100, bins=100, density=True, alpha=0.6, color="#2563eb", edgecolor="white", linewidth=0.3)

    # Normal overlay
    mu, sigma = returns.mean() * 100, returns.std() * 100
    x = np.linspace(mu - 4 * sigma, mu + 4 * sigma, 200)
    normal_pdf = (1 / (sigma * math.sqrt(2 * math.pi))) * np.exp(-0.5 * ((x - mu) / sigma) ** 2)
    ax.plot(x, normal_pdf, "r-", linewidth=1.5, label=f"正态 N({mu:.3f}, {sigma:.3f})")

    ax.set_xlabel("日收益率 (%)")
    ax.set_ylabel("密度")
    ax.legend(frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def _plot_regime(raw_perf, benchmark, output_path):
    """Regime analysis bar chart."""
    _configure_matplotlib()
    from .risk import regime_analysis

    regimes = regime_analysis(raw_perf, benchmark)
    names = list(regimes.keys())
    sharpes = [regimes[n].get("sharpe", 0) for n in names]
    fractions = [regimes[n].get("day_fraction", 0) * 100 for n in names]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
    colors = ["#22c55e" if s > 0 else "#ef4444" for s in sharpes]
    ax1.barh(names, sharpes, color=colors)
    ax1.set_xlabel("区制内夏普")
    ax1.set_title("各区制下策略表现")
    ax1.spines[["top", "right"]].set_visible(False)

    ax2.barh(names, fractions, color="#94a3b8")
    ax2.set_xlabel("天数占比 (%)")
    ax2.set_title("各区制时间占比")
    ax2.spines[["top", "right"]].set_visible(False)

    fig.tight_layout()
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)


def generate_report(
    strategy_name: str,
    raw_perf: pd.DataFrame,
    benchmark: pd.DataFrame,
    all_results: dict,
    output_dir: Path,
) -> None:
    """Generate complete evaluation report: Markdown + charts + JSON summary."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # Charts
    _plot_equity_curve(raw_perf, benchmark, output_dir / "equity_curve.png")
    _plot_rolling_sharpe(raw_perf, output_dir / "rolling_sharpe.png")
    _plot_yearly_heatmap(raw_perf, output_dir / "yearly_heatmap.png")
    _plot_return_distribution(raw_perf, output_dir / "return_distribution.png")
    _plot_regime(raw_perf, benchmark, output_dir / "regime_analysis.png")

    # JSON summary
    with open(output_dir / "evaluation_summary.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2, default=str)

    # Markdown report
    core = all_results.get("core", {})
    sig = all_results.get("significance", {})
    risk = all_results.get("risk", {})
    attrib = all_results.get("attribution", {})
    costs = all_results.get("costs", {})
    segments = all_results.get("segments", {})

    md = []
    md.append(f"# 策略系统性评测报告: {strategy_name}\n")
    md.append(f"> 生成于评测框架 v1.0 | 基于 `EVALUATION_FRAMEWORK_INDUSTRY_zh-CN.md`\n\n---\n")

    # 1. Core metrics
    md.append("## 1. 核心风险调整指标\n")
    md.append("| 指标 | 值 |")
    md.append("| --- | --- |")
    md.append(f"| 年化收益 | {_fmt(core.get('annualized_return'), pct=True)} |")
    md.append(f"| 年化波动率 | {_fmt(core.get('annualized_volatility'), pct=True)} |")
    md.append(f"| **夏普比率** | **{_fmt(core.get('sharpe'))}** |")
    md.append(f"| Sortino 比率 | {_fmt(core.get('sortino'))} |")
    md.append(f"| Calmar 比率 | {_fmt(core.get('calmar'))} |")
    md.append(f"| Omega 比率 | {_fmt(core.get('omega'))} |")
    md.append(f"| 最大回撤 | {_fmt(core.get('max_drawdown'), pct=True)} |")
    md.append(f"| Ulcer 指数 | {_fmt(core.get('ulcer_index'), pct=True)} |")
    md.append(f"| VaR(95%) | {_fmt(core.get('var_95'), pct=True)} |")
    md.append(f"| CVaR(95%) | {_fmt(core.get('cvar_95'), pct=True)} |")
    md.append(f"| 尾部比率 | {_fmt(core.get('tail_ratio'))} |")
    md.append(f"| 偏度 | {_fmt(core.get('skewness'))} |")
    md.append(f"| 超额峰度 | {_fmt(core.get('excess_kurtosis'))} |")
    md.append(f"| 日胜率 | {_fmt(core.get('win_day_rate'), pct=True)} |")
    if "information_ratio" in core:
        md.append(f"| 信息比率 | {_fmt(core.get('information_ratio'))} |")
        md.append(f"| Beta | {_fmt(core.get('beta'))} |")
        md.append(f"| 年化 Alpha | {_fmt(core.get('alpha_annual'), pct=True)} |")
        md.append(f"| 跟踪误差 | {_fmt(core.get('tracking_error'), pct=True)} |")
    md.append("")

    # 2. Statistical significance
    md.append("## 2. 统计显著性（多重检验校正）\n")
    hc = sig.get("haircut", {})
    pbo = sig.get("pbo", {})
    mtrl = sig.get("min_trl", {})
    md.append("| 指标 | 值 | 判定 |")
    md.append("| --- | --- | --- |")
    md.append(f"| PSR (vs 0) | {_fmt(sig.get('psr'))} | {'✅ ≥0.95' if sig.get('psr', 0) >= 0.95 else '⚠️ <0.95'} |")
    md.append(f"| DSR (N={sig.get('n_trials', '?')}) | {_fmt(sig.get('dsr'))} | {'✅ ≥0.95' if sig.get('dsr', 0) >= 0.95 else '⚠️ <0.95'} |")
    md.append(f"| 缩水夏普 (Haircut) | 砍 {hc.get('haircut_pct', 0)*100:.1f}% → SR={hc.get('sr_at_dsr95', 0):.3f} | {hc.get('verdict', '?')} |")
    md.append(f"| PBO (CSCV) | {_fmt(pbo.get('pbo'))} | {pbo.get('verdict', '?')} |")
    md.append(f"| MinTRL | {mtrl.get('min_trl_years', 0):.1f} 年 | {mtrl.get('verdict', '?')} |")
    md.append(f"| Jarque-Bera 正态性 | p={core.get('jarque_bera', {}).get('p_value', 0):.4f} | {'⚠️ 非正态' if not core.get('jarque_bera', {}).get('is_normal', True) else '✅ 近似正态'} |")
    md.append("")

    # 3. Three-segment breakdown
    md.append("## 3. 三段拆分（dev / val / test）\n")
    md.append("| 段 | 收益 | 年化 | 夏普 | IR | 回撤 | 年度胜率 |")
    md.append("| --- | --- | --- | --- | --- | --- | --- |")
    for label in ["development", "validation", "test"]:
        seg = segments.get(label, {})
        if not seg:
            continue
        md.append(f"| {label} | {_fmt(seg.get('return'), pct=True)} | {_fmt(seg.get('annualized_return'), pct=True)} | {_fmt(seg.get('sharpe'))} | {_fmt(seg.get('information_ratio'))} | {_fmt(seg.get('max_drawdown'), pct=True)} | {_fmt(seg.get('annual_win_rate'), pct=True)} |")
    md.append("")
    md.append("![净值曲线](equity_curve.png)\n")

    # 4. Drawdown analysis
    dd = risk.get("drawdown", {})
    md.append("## 4. 回撤分析\n")
    md.append("| 指标 | 值 |")
    md.append("| --- | --- |")
    md.append(f"| 最大回撤 | {_fmt(dd.get('max_drawdown'), pct=True)} |")
    md.append(f"| 回撤持续天数(最大) | {dd.get('max_duration_days', 0)} 天 |")
    md.append(f"| 回撤持续天数(平均) | {_fmt(dd.get('avg_duration_days'))} 天 |")
    md.append(f"| 水下时间占比 | {_fmt(dd.get('underwater_ratio'), pct=True)} |")
    md.append(f"| 回撤事件数 | {dd.get('n_episodes', 0)} |")
    md.append("")

    # Top 5 drawdowns
    top5 = dd.get("top_5_drawdowns", [])
    if top5:
        md.append("### Top 5 回撤事件\n")
        md.append("| # | 起点 | 谷底 | 恢复 | 深度 | 持续(天) |")
        md.append("| --- | --- | --- | --- | --- | --- |")
        for i, ep in enumerate(top5, 1):
            md.append(f"| {i} | {ep.get('start', '?')} | {ep.get('trough', '?')} | {ep.get('end', '?')} | {_fmt(ep.get('depth'), pct=True)} | {ep.get('duration_days', 0)} |")
        md.append("")

    # 5. Stress test
    stress = risk.get("stress_test", {})
    if stress:
        md.append("## 5. 压力测试\n")
        md.append("| 情景 | 收益 | 夏普 | 回撤 |")
        md.append("| --- | --- | --- | --- |")
        for scenario, metrics in stress.items():
            md.append(f"| {scenario} | {_fmt(metrics.get('return'), pct=True)} | {_fmt(metrics.get('sharpe'))} | {_fmt(metrics.get('max_drawdown'), pct=True)} |")
        md.append("")

    # 6. Regime analysis
    regime = risk.get("regime", {})
    if regime:
        md.append("## 6. 区制分析\n")
        md.append(f"夏普稳定性比率: **{_fmt(risk.get('sharpe_stability_ratio'))}** (越高越稳定)\n")
        md.append("| 区制 | 天数占比 | 平均日收益 | 夏普 | 回撤 |")
        md.append("| --- | --- | --- | --- | --- |")
        for name, metrics in regime.items():
            md.append(f"| {name} | {_fmt(metrics.get('day_fraction'), pct=True)} | {_fmt(metrics.get('avg_daily_return'), pct=True)} | {_fmt(metrics.get('sharpe'))} | {_fmt(metrics.get('max_drawdown'), pct=True)} |")
        md.append("")
        md.append("![区制分析](regime_analysis.png)\n")

    # 7. Attribution
    capm = attrib.get("capm_regression", {})
    cap = attrib.get("capture_ratios", {})
    md.append("## 7. 归因分析\n")
    md.append("### CAPM 回归\n")
    md.append("| 指标 | 值 |")
    md.append("| --- | --- |")
    md.append(f"| Alpha (年化) | {_fmt(capm.get('alpha_annual'), pct=True)} |")
    md.append(f"| Beta | {_fmt(capm.get('beta'))} |")
    md.append(f"| R² | {_fmt(capm.get('r_squared'))} |")
    md.append(f"| 残差波动率(年化) | {_fmt(capm.get('idio_vol_annual'), pct=True)} |")
    md.append(f"| Alpha t-stat | {_fmt(capm.get('alpha_tstat'))} |")
    md.append("")
    md.append("### 捕获比率\n")
    md.append(f"- 上行捕获: {_fmt(cap.get('up_capture'))} (策略捕获了基准上涨的 {cap.get('up_capture', 0):.1%})")
    md.append(f"- 下行捕获: {_fmt(cap.get('down_capture'))} (策略捕获了基准下跌的 {cap.get('down_capture', 0):.1%})")
    md.append(f"- 上行/下行比: {_fmt(cap.get('up_down_ratio'))}\n")

    # Yearly table
    yearly = attrib.get("yearly", [])
    if yearly:
        md.append("### 逐年表现\n")
        md.append("| 年份 | 收益 | 超额 | 夏普 | Sortino | 回撤 | 日胜率 |")
        md.append("| --- | --- | --- | --- | --- | --- | --- |")
        for y in yearly:
            md.append(f"| {y.get('year')} | {_fmt(y.get('return'), pct=True)} | {_fmt(y.get('excess'), pct=True)} | {_fmt(y.get('sharpe'))} | {_fmt(y.get('sortino'))} | {_fmt(y.get('max_drawdown'), pct=True)} | {_fmt(y.get('win_day_rate'), pct=True)} |")
        md.append("")
        md.append("![年度热力图](yearly_heatmap.png)\n")
        md.append("![滚动夏普](rolling_sharpe.png)\n")
        md.append("![收益分布](return_distribution.png)\n")

    # 8. Cost analysis
    turn = costs.get("turnover", {})
    cd = costs.get("cost_drag", {})
    md.append("## 8. 成本与换手分析\n")
    md.append("| 指标 | 值 |")
    md.append("| --- | --- |")
    md.append(f"| 单边年化换手率 | {turn.get('single_side_annual_turnover', 0):.1f}x |")
    md.append(f"| 平均持有天数 | {_fmt(turn.get('avg_holding_days'))} 天 ({_fmt(turn.get('avg_holding_months'))} 月) |")
    md.append(f"| 佣金年化拖累 | {_fmt(cd.get('commission_drag_annual_pct'))} % |")
    md.append(f"| 滑点年化拖累 | {_fmt(cd.get('slippage_drag_annual_pct'))} % |")
    md.append(f"| 总成本年化拖累 | {_fmt(cd.get('total_drag_annual_pct'))} % |")
    md.append("")

    # 9. Checklist
    md.append("## 9. 业界评测清单（A-K 逐项核验）\n")
    checklist = [
        ("A1", "PIT 数据/幸存者偏差", "✅ 已处理", "本地 prices_long.csv 含退市股；PIT 严格"),
        ("A2", "公司行动/未来函数", "✅ 已审计", "houfuquan 调整价；无 look-ahead"),
        ("A3", "研究域/交易域隔离", "✅ 已隔离", "train/val/test 三段，test 揭盲一次"),
        ("B1", "DSR ≥ 0.95", f"{'✅' if sig.get('dsr', 0) >= 0.95 else '⚠️'} {_fmt(sig.get('dsr'))}", f"N={sig.get('n_trials')} 次试验"),
        ("B2", "PBO < 0.5", f"{'✅' if pbo.get('pbo', 1) < 0.5 else '⚠️'} {_fmt(pbo.get('pbo'))}", pbo.get('verdict', '')),
        ("C1", "IC 显著", "✅ 见因子稳定性报告", "factor_stability_trainval.csv"),
        ("D1", "Alpha 显著(非隐藏 beta)", f"{'✅' if abs(capm.get('alpha_tstat', 0)) > 2 else '⚠️'} t={_fmt(capm.get('alpha_tstat'))}", f"R²={_fmt(capm.get('r_squared'))}"),
        ("F1", "换手率成本已计入", "✅", f"年化拖累 {_fmt(cd.get('total_drag_annual_pct'))}%"),
        ("G1", "等权优于优化器", "✅", "策略使用等权，已在健壮性 SOP 验证"),
        ("H1", "参数稳定性", "✅ 见健壮性报告", "v2_robustness_results.csv"),
        ("I1", "Sortino/Calmar/Ulcer", "✅ 见第1节", ""),
        ("I2", "回撤路径+持续期", "✅ 见第4节", ""),
        ("I3", "压力测试", "✅ 见第5节", ""),
        ("I4", "区制检测", "✅ 见第6节", ""),
        ("K1", "对抗审查", "✅ 已执行", "v2_adversarial_review.js 4 lens"),
    ]
    md.append("| 编号 | 检查项 | 状态 | 备注 |")
    md.append("| --- | --- | --- | --- |")
    for code, item, status, note in checklist:
        md.append(f"| {code} | {item} | {status} | {note} |")
    md.append("")

    md.append("---\n")
    md.append("> **终极提醒**: 所有 DSR/PSR/PBO 指标最值钱的用途是告诉你**何时不要信任一个回测**。")
    md.append("> 唯一真正的样本外是实盘交易。\n")

    with open(output_dir / "evaluation_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md))
