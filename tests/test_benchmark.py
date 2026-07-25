from pathlib import Path

import pandas as pd

from ashare_quant.benchmark import load_benchmark_file


def test_load_benchmark_file_returns_complete_local_slice(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.csv"
    pd.DataFrame(
        {
            "date": ["2024-01-02", "2024-01-03", "2024-01-04"],
            "benchmark_close": [100.0, 101.0, 102.0],
        }
    ).to_csv(path, index=False)

    result = load_benchmark_file(path, "2024-01-02", "2024-01-04")

    assert result["benchmark_close"].tolist() == [100.0, 101.0, 102.0]


def test_load_benchmark_file_rejects_incomplete_range(tmp_path: Path) -> None:
    path = tmp_path / "benchmark.csv"
    pd.DataFrame(
        {
            "date": ["2024-01-03", "2024-01-04"],
            "benchmark_close": [101.0, 102.0],
        }
    ).to_csv(path, index=False)

    assert load_benchmark_file(path, "2024-01-02", "2024-01-04").empty
