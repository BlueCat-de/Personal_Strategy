"""Prepare data assets required by the standalone Ptrade strategy."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT_ROOT / "data/offline/a_share_history_tushare/sw_l1_membership_history.csv"
DEFAULT_DESTINATION = PROJECT_ROOT / "deploy/ptrade/sw_l1_membership_history.csv"
REQUIRED_COLUMNS = [
    "symbol",
    "l1_name",
    "in_date",
    "out_date",
    "classification_version",
    "classification_effective_date",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    frame = pd.read_csv(args.source, dtype=str)
    missing = sorted(set(REQUIRED_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"industry history missing columns: {missing}")
    selected = frame[REQUIRED_COLUMNS].copy()
    selected = selected[selected["symbol"].str.fullmatch(r"\d{6}", na=False)]
    output = (
        selected.dropna(
            subset=[
                "symbol",
                "l1_name",
                "in_date",
                "classification_effective_date",
            ]
        )
        .drop_duplicates()
        .sort_values(
            [
                "classification_effective_date",
                "symbol",
                "in_date",
                "out_date",
            ],
            na_position="last",
        )
    )
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.destination, index=False)
    print(
        f"wrote {len(output):,} rows ({args.destination.stat().st_size:,} bytes) "
        f"to {args.destination}"
    )


if __name__ == "__main__":
    main()
