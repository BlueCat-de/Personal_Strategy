#!/usr/bin/env python3
"""Package the local inputs required to reproduce the frozen main-board research."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MARKET_DATA_DIR = PROJECT_ROOT / "data/offline/a_share_history_tushare"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data/packages"
REQUIRED_PATHS = (
    MARKET_DATA_DIR / "prices_long.csv",
    MARKET_DATA_DIR / "daily_universe.csv",
    MARKET_DATA_DIR / "universe.csv",
    MARKET_DATA_DIR / "manifest.json",
    MARKET_DATA_DIR / "benchmark_000300.csv",
    MARKET_DATA_DIR / "sw_l1_membership_history.csv",
    MARKET_DATA_DIR / ".daily_basic_monthly_cache",
    MARKET_DATA_DIR / ".long_horizon_daily_basic_cache",
    MARKET_DATA_DIR / ".long_horizon_fina_indicator_cache",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(paths: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"required reproduction input is missing: {path}")
        if path.is_dir():
            files.extend(item for item in sorted(path.rglob("*")) if item.is_file())
        else:
            files.append(path)
    return files


def git_revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--name", default=f"ashare_quant_main_reproduction_{datetime.now():%Y%m%d}")
    args = parser.parse_args()

    files = iter_files(REQUIRED_PATHS)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    archive = args.output_dir / f"{args.name}.tar.gz"
    checksum = archive.with_suffix(archive.suffix + ".sha256")
    manifest_path = args.output_dir / f"{args.name}.manifest.json"

    entries = []
    for path in files:
        relative = path.relative_to(PROJECT_ROOT)
        entries.append(
            {
                "path": relative.as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256(path),
            }
        )
    manifest = {
        "schema": "ashare_quant_main_reproduction_v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "git_revision": git_revision(),
        "scope": "frozen main-board strategy and committed strict-PIT research",
        "excludes": [
            "API tokens and webhook secrets",
            "backtest outputs",
            "historical backup directories",
            "growth-board extension data",
            "Ptrade transaction exports",
        ],
        "files": entries,
        "total_uncompressed_bytes": sum(entry["bytes"] for entry in entries),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with tempfile.TemporaryDirectory() as temporary:
        embedded_manifest = Path(temporary) / "REPRODUCTION_DATA_MANIFEST.json"
        embedded_manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with tarfile.open(archive, "w:gz", compresslevel=6) as bundle:
            bundle.add(embedded_manifest, arcname="REPRODUCTION_DATA_MANIFEST.json")
            for path in files:
                bundle.add(path, arcname=path.relative_to(PROJECT_ROOT))

    archive_digest = sha256(archive)
    checksum.write_text(f"{archive_digest}  {archive.name}\n", encoding="ascii")
    print(f"archive: {archive}")
    print(f"manifest: {manifest_path}")
    print(f"checksum: {checksum}")
    print(f"files: {len(files):,}")
    print(f"uncompressed bytes: {manifest['total_uncompressed_bytes']:,}")
    print(f"archive bytes: {archive.stat().st_size:,}")


if __name__ == "__main__":
    main()
