#!/usr/bin/env python3
"""Plot capital-sensitive backtest curves with matplotlib."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd

from ashare_quant.research.factors import atomic_write_csv

DEFAULT_INPUT_DIR = Path("data/backtests/offset_neutral_capital_sensitivity_20210701_20260716")
DEFAULT_OUTPUT_DIR = DEFAULT_INPUT_DIR / "charts"
DEFAULT_BENCHMARK_FILE = Path(
    "data/backtests/pit_hs300_core_v4_alpha_20210701_20260716/benchmark_000300.csv"
)
DEFAULT_CAPITALS = (50_000, 100_000, 500_000, 1_000_000)
COLORS = {"50k": "#f59f00", "100k": "#228be6", "500k": "#7950f2", "1m": "#12b886"}


@dataclass(frozen=True)
class Curve:
    label: str
    color: str
    values: pd.Series


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plot capital-sensitive backtest curves.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--benchmark-file", type=Path, default=DEFAULT_BENCHMARK_FILE)
    parser.add_argument("--start-date", default="2021-07-01")
    parser.add_argument("--capitals", type=int, nargs="+", default=list(DEFAULT_CAPITALS))
    return parser.parse_args()


def capital_label(capital: int) -> str:
    return f"{capital // 1_000_000}m" if capital >= 1_000_000 else f"{capital // 1_000}k"


def load_capital_curve(path: Path, capital: int, start_date: str) -> pd.Series:
    frame = pd.read_csv(path / f"cash_{capital}" / "local_raw_perf.csv")
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.loc[frame["date"] >= pd.Timestamp(start_date)].copy()
    values = pd.to_numeric(frame["portfolio_value"], errors="coerce")
    if frame.empty or values.isna().any() or values.iloc[0] <= 0:
        raise ValueError(f"Invalid portfolio values for cash_{capital}")
    return pd.Series(
        values.to_numpy() / values.iloc[0] * 100.0,
        index=frame["date"],
        name=capital_label(capital),
    )


def benchmark_curve(path: Path, start_date: str) -> pd.Series:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.loc[frame["date"] >= pd.Timestamp(start_date)].copy()
    close = pd.to_numeric(frame["benchmark_close"], errors="coerce")
    if frame.empty or close.isna().any() or close.iloc[0] <= 0:
        raise ValueError(f"Invalid benchmark values in {path}")
    return pd.Series(close.to_numpy() / close.iloc[0] * 100.0, index=frame["date"], name="hs300")


def merge_curves(curves: list[Curve]) -> pd.DataFrame:
    frame = pd.concat([curve.values.rename(curve.label) for curve in curves], axis=1, join="inner")
    if frame.empty:
        raise ValueError("Curves have no common trading days")
    return frame


def summary(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "series": frame.columns,
            "final_index": frame.iloc[-1].round(2).to_numpy(),
            "total_return": ((frame.iloc[-1] / 100.0 - 1.0) * 100.0).round(2).to_numpy(),
            "max_drawdown": ((frame / frame.cummax() - 1.0).min() * 100.0).round(2).to_numpy(),
        }
    )


def configure_matplotlib() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Arial", "DejaVu Sans"],
            "axes.titlesize": 15,
            "axes.labelsize": 10,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.color": "#adb5bd",
            "figure.facecolor": "#ffffff",
            "axes.facecolor": "#ffffff",
            "savefig.facecolor": "#ffffff",
        }
    )


def plot_curves(frame: pd.DataFrame, output_dir: Path) -> None:
    configure_matplotlib()
    fig, axes = plt.subplots(2, 1, figsize=(14, 10), sharex=True, constrained_layout=True)
    for column in frame.columns:
        color = "#495057" if column == "hs300" else COLORS[column]
        style = "--" if column == "hs300" else "-"
        width = 2.4 if column == "hs300" else 1.8
        axes[0].plot(
            frame.index, frame[column], label=column, color=color, linestyle=style, linewidth=width
        )
        axes[1].plot(
            frame.index,
            frame[column] - 100.0,
            label=column,
            color=color,
            linestyle=style,
            linewidth=width,
        )
    axes[0].axhline(100, color="#868e96", linewidth=0.8, linestyle=":")
    axes[1].axhline(0, color="#868e96", linewidth=0.8, linestyle=":")
    axes[0].set_title("Offset-neutral strategy: normalized NAV by capital")
    axes[1].set_title("Offset-neutral strategy: cumulative return by capital")
    axes[0].set_ylabel("NAV index (start = 100)")
    axes[1].set_ylabel("Cumulative return")
    axes[1].set_xlabel("Date")
    axes[1].yaxis.set_major_formatter(lambda value, _: f"{value:.0f}%")
    locator = mdates.MonthLocator(interval=6)
    axes[1].xaxis.set_major_locator(locator)
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    for axis in axes:
        axis.legend(ncol=5, frameon=False, loc="upper left")
        axis.spines[["top", "right"]].set_visible(False)
        axis.tick_params(axis="x", rotation=30)
    fig.savefig(output_dir / "capital_curves_matplotlib.png", dpi=160, bbox_inches="tight")
    fig.savefig(output_dir / "capital_curves_matplotlib.pdf", bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    curves = [
        Curve(
            capital_label(capital),
            COLORS[capital_label(capital)],
            load_capital_curve(args.input_dir, capital, args.start_date),
        )
        for capital in args.capitals
    ]
    curves.append(Curve("hs300", "#495057", benchmark_curve(args.benchmark_file, args.start_date)))
    frame = merge_curves(curves)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_csv(frame.reset_index(names="date"), args.output_dir / "matplotlib_curves.csv")
    atomic_write_csv(summary(frame), args.output_dir / "matplotlib_summary.csv")
    plot_curves(frame, args.output_dir)
    print(f"Output: {args.output_dir.resolve()}")


if __name__ == "__main__":
    main()
