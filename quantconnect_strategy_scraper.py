#!/usr/bin/env python3
"""
Scrape QuantConnect strategy pages listed in strategies/quantconnect/webs.

Public QuantConnect strategy endpoints expose leaderboard metadata, strategy
descriptions, tags, authors, statistics, and equity curves. Source code belongs
to the linked clone project and may require a logged-in QuantConnect session. If
`QUANTCONNECT_COOKIE` is set, the scraper attempts project/file endpoints and
saves any accessible source files. Without that cookie, code access failures are
recorded in metadata.json and the public artifacts are still saved.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger("quantconnect_strategy_scraper")

QUANTCONNECT_BASE_URL = "https://www.quantconnect.com"
STRATEGY_URL_RE = re.compile(r"/strategies/(\d+)/([^/?#]+)", re.IGNORECASE)

STYLE_RULES = {
    "momentum": ("momentum", "rotation", "winner", "trend", "sma", "crossover"),
    "mean_reversion": ("mean reversion", "overbought", "oversold", "short volatility"),
    "factor": ("factor", "fundamental", "roe", "quality", "large cap", "selection"),
    "risk_optimized": ("tail risk", "risk optimized", "kelly", "inverse volatility", "volatility allocation"),
    "seasonality": ("calendar", "seasonality", "month"),
    "long_short": ("long-short", "long short", "shorts"),
    "equity": ("equity", "stock", "qqq", "spy"),
}


@dataclass(frozen=True)
class QuantConnectPage:
    url: str
    strategy_id: int
    slug: str


@dataclass
class ScrapeResult:
    strategy_id: int
    title: str
    style: str
    output_dir: Path
    metadata_saved: bool
    code_files_saved: int
    errors: list[str]


def build_session(timeout: int) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET", "POST"),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": "PersonalStrategyQuantConnectScraper/0.1",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        }
    )
    cookie = os.getenv("QUANTCONNECT_COOKIE")
    if cookie:
        session.headers["Cookie"] = cookie
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def request_get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    return session.get(url, timeout=getattr(session, "request_timeout", 30), **kwargs)


def request_post(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    return session.post(url, timeout=getattr(session, "request_timeout", 30), **kwargs)


def load_pages(path: Path) -> list[QuantConnectPage]:
    pages: list[QuantConnectPage] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        url = raw_line.strip()
        if not url or url.startswith("#"):
            continue
        match = STRATEGY_URL_RE.search(url)
        if not match:
            LOGGER.warning("Skip URL without QuantConnect strategy id: %s", url)
            continue
        pages.append(QuantConnectPage(url=url, strategy_id=int(match.group(1)), slug=match.group(2)))
    seen: set[int] = set()
    unique_pages: list[QuantConnectPage] = []
    for page in pages:
        if page.strategy_id in seen:
            continue
        seen.add(page.strategy_id)
        unique_pages.append(page)
    return unique_pages


def safe_name(value: str, max_length: int = 90) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    value = value or "untitled_strategy"
    return value[:max_length].strip("._")


def fetch_leaderboard(session: requests.Session, limit: int = 10) -> dict[int, dict[str, Any]]:
    response = request_get(
        session,
        f"{QUANTCONNECT_BASE_URL}/api/v2/strategies/list/",
        params={"start": 0, "end": limit, "sort": "oos 3m sharpe"},
    )
    response.raise_for_status()
    payload = response.json()
    return {int(item["id"]): item for item in payload.get("strategies", []) if "id" in item}


def fetch_strategy(session: requests.Session, strategy_id: int) -> dict[str, Any]:
    response = request_get(
        session,
        f"{QUANTCONNECT_BASE_URL}/api/v2/strategies/read/",
        params={"strategyId": strategy_id},
    )
    response.raise_for_status()
    payload = response.json()
    strategy = payload.get("strategy")
    if not isinstance(strategy, dict):
        raise RuntimeError(f"strategy read failed: {payload}")
    return strategy


def try_fetch_code_files(session: requests.Session, strategy: dict[str, Any]) -> tuple[list[dict[str, str]], list[str]]:
    project_id = strategy.get("cloneProjectId")
    if not project_id:
        return [], ["code: missing cloneProjectId"]

    errors: list[str] = []
    endpoints = [
        (f"{QUANTCONNECT_BASE_URL}/api/v2/projects/read", {"projectId": project_id}),
        (f"{QUANTCONNECT_BASE_URL}/api/v2/files/read", {"projectId": project_id}),
    ]
    for url, data in endpoints:
        try:
            response = request_post(session, url, data=data, headers={"Referer": strategy_url(strategy)})
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"code endpoint {url}: {exc}")
            continue
        if payload.get("success") is not True:
            message = payload.get("errors") or payload.get("messages") or payload.get("message") or payload
            errors.append(f"code endpoint {url}: {message}")
            continue
        files = extract_files_from_payload(payload)
        if files:
            return files, errors
        errors.append(f"code endpoint {url}: no files in successful payload")
    if not os.getenv("QUANTCONNECT_COOKIE"):
        errors.append("code: QuantConnect project files require login; set QUANTCONNECT_COOKIE to retry")
    return [], errors


def extract_files_from_payload(payload: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[Any] = []
    for key in ("files", "projectFiles"):
        if isinstance(payload.get(key), list):
            candidates.extend(payload[key])
    for project in payload.get("projects", []) if isinstance(payload.get("projects"), list) else []:
        if isinstance(project, dict):
            for key in ("files", "projectFiles"):
                if isinstance(project.get(key), list):
                    candidates.extend(project[key])

    files: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("fileName") or item.get("path")
        content = item.get("content") or item.get("code") or item.get("source")
        if isinstance(name, str) and isinstance(content, str):
            files.append({"name": name, "content": content})
    return files


def strategy_url(strategy: dict[str, Any]) -> str:
    strategy_id = strategy.get("id", "")
    name = safe_name(str(strategy.get("name", "strategy"))).replace("_", "-")
    return f"{QUANTCONNECT_BASE_URL}/strategies/{strategy_id}/{name}"


def detect_style(strategy: dict[str, Any]) -> str:
    title_text = str(strategy.get("name", "")).lower()
    full_text = " ".join(
        [
            str(strategy.get("name", "")),
            str(strategy.get("description", "")),
            " ".join(str(tag) for tag in strategy.get("tags", []) or []),
            " ".join(str(asset) for asset in strategy.get("assetClasses", []) or []),
        ]
    ).lower()

    title_matches: dict[str, int] = {}
    full_matches: dict[str, int] = {}
    for style, keywords in STYLE_RULES.items():
        title_matches[style] = sum(1 for keyword in keywords if keyword in title_text)
        full_matches[style] = sum(1 for keyword in keywords if keyword in full_text)

    # Strategy descriptions often discuss benchmark or alternative methods.
    # Prefer explicit title signals, then use the full public metadata as a tie-breaker.
    if max(title_matches.values(), default=0) > 0:
        return max(STYLE_RULES, key=lambda style: (title_matches[style], full_matches[style]))

    best_style = "general"
    best_score = 0
    for style, keywords in STYLE_RULES.items():
        score = full_matches[style]
        if score > best_score:
            best_style = style
            best_score = score
    return best_style


def summarize_statistics(strategy: dict[str, Any]) -> dict[str, Any]:
    statistics = strategy.get("statistics") if isinstance(strategy.get("statistics"), dict) else {}
    chart = statistics.get("chart") if isinstance(statistics.get("chart"), list) else []
    latest_chart_value = chart[-1][1] if chart and isinstance(chart[-1], list) and len(chart[-1]) >= 2 else None
    return {
        "score": strategy.get("score"),
        "rank": strategy.get("rank"),
        "leaderboard": strategy.get("leaderboard"),
        "followers_count": strategy.get("followersCount"),
        "watchers": strategy.get("watchers"),
        "clones": strategy.get("clones"),
        "comments": strategy.get("comments"),
        "published": strategy.get("published"),
        "latest_chart_value": latest_chart_value,
        "statistics": statistics,
    }


def chart_rows(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    statistics = strategy.get("statistics") if isinstance(strategy.get("statistics"), dict) else {}
    chart = statistics.get("chart") if isinstance(statistics.get("chart"), list) else []
    rows: list[dict[str, Any]] = []
    for point in chart:
        if not isinstance(point, list) or len(point) < 2:
            continue
        timestamp, value = point[0], point[1]
        date = ""
        if isinstance(timestamp, (int, float)):
            date = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        rows.append({"timestamp": timestamp, "date": date, "equity_curve_value": value})
    return rows


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_chart_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["timestamp", "date", "equity_curve_value"])
        writer.writeheader()
        writer.writerows(rows)


def save_strategy(
    output_root: Path,
    page: QuantConnectPage,
    strategy: dict[str, Any],
    leaderboard_item: dict[str, Any] | None,
    code_files: list[dict[str, str]],
    errors: list[str],
) -> ScrapeResult:
    title = str(strategy.get("name") or page.slug)
    style = detect_style(strategy)
    strategy_dir = output_root / style / f"{safe_name(title)}_{page.strategy_id}"
    strategy_dir.mkdir(parents=True, exist_ok=True)

    for code_file in code_files:
        name = safe_name(code_file["name"], max_length=120)
        if "." not in Path(name).name:
            name = f"{name}.py"
        (strategy_dir / name).write_text(code_file["content"], encoding="utf-8")

    rows = chart_rows(strategy)
    if rows:
        write_chart_csv(strategy_dir / "equity_curve.csv", rows)

    performance = summarize_statistics(strategy)
    write_json(strategy_dir / "performance.json", {"summary": performance, "equity_curve": rows})

    description = str(strategy.get("description") or "")
    description_md = [
        f"# {title}",
        "",
        f"- Source URL: {page.url}",
        f"- Strategy ID: `{page.strategy_id}`",
        f"- Clone project ID: `{strategy.get('cloneProjectId')}`",
        f"- Backtest ID: `{strategy.get('backtestId')}`",
        f"- Trading style: `{style}`",
        f"- Author: `{strategy.get('authorName') or strategy.get('organizationName')}`",
        f"- Tags: `{', '.join(str(tag) for tag in strategy.get('tags', []) or [])}`",
        f"- Asset classes: `{', '.join(str(asset) for asset in strategy.get('assetClasses', []) or [])}`",
        "",
        "## Description",
        "",
        description,
    ]
    if errors:
        description_md.extend(["", "## Scrape Notes", *[f"- {error}" for error in errors]])
    (strategy_dir / "summary.md").write_text("\n".join(description_md).rstrip() + "\n", encoding="utf-8")

    metadata = {
        "source": "QuantConnect",
        "source_url": page.url,
        "strategy_id": page.strategy_id,
        "slug": page.slug,
        "title": title,
        "style": style,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "code_files_saved": len(code_files),
        "code_status": "saved" if code_files else "not_available",
        "errors": errors,
        "strategy": strategy,
        "leaderboard_item": leaderboard_item,
    }
    write_json(strategy_dir / "metadata.json", metadata)

    return ScrapeResult(
        strategy_id=page.strategy_id,
        title=title,
        style=style,
        output_dir=strategy_dir,
        metadata_saved=True,
        code_files_saved=len(code_files),
        errors=errors,
    )


def scrape_page(
    session: requests.Session,
    output_root: Path,
    page: QuantConnectPage,
    leaderboard: dict[int, dict[str, Any]],
) -> ScrapeResult:
    errors: list[str] = []
    strategy = fetch_strategy(session, page.strategy_id)
    code_files, code_errors = try_fetch_code_files(session, strategy)
    errors.extend(code_errors)
    return save_strategy(
        output_root=output_root,
        page=page,
        strategy=strategy,
        leaderboard_item=leaderboard.get(page.strategy_id),
        code_files=code_files,
        errors=errors,
    )


def configure_logging(log_file: Path, level: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_file, encoding="utf-8")],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape QuantConnect leaderboard strategy pages.")
    parser.add_argument("--input-file", default="strategies/quantconnect/webs", help="File containing strategy URLs.")
    parser.add_argument("--output-dir", default="strategies/quantconnect", help="Output root directory.")
    parser.add_argument("--log-file", default="logs/quantconnect_strategy_scraper.log", help="Log file path.")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between strategies in seconds.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of URLs to process.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(Path(args.log_file), args.log_level)
    input_file = Path(args.input_file)
    output_root = Path(args.output_dir)
    output_root.mkdir(parents=True, exist_ok=True)

    pages = load_pages(input_file)
    if args.limit:
        pages = pages[: args.limit]
    LOGGER.info("Loaded %d QuantConnect URLs from %s", len(pages), input_file)

    session = build_session(args.timeout)
    try:
        leaderboard = fetch_leaderboard(session, limit=max(10, len(pages)))
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to fetch leaderboard list: %s", exc)
        leaderboard = {}

    results: list[ScrapeResult] = []
    for index, page in enumerate(pages, start=1):
        LOGGER.info("Scraping %d/%d id=%s", index, len(pages), page.strategy_id)
        try:
            result = scrape_page(session, output_root, page, leaderboard)
        except Exception as exc:  # noqa: BLE001
            error_dir = output_root / "errors"
            error_dir.mkdir(parents=True, exist_ok=True)
            write_json(
                error_dir / f"{page.strategy_id}.json",
                {"url": page.url, "strategy_id": page.strategy_id, "error": str(exc)},
            )
            LOGGER.exception("Failed id=%s", page.strategy_id)
            continue
        results.append(result)
        LOGGER.info(
            "Saved style=%s code_files=%d path=%s errors=%d",
            result.style,
            result.code_files_saved,
            result.output_dir,
            len(result.errors),
        )
        if index < len(pages):
            time.sleep(args.delay)

    index_rows = [
        {
            "strategy_id": result.strategy_id,
            "title": result.title,
            "style": result.style,
            "output_dir": str(result.output_dir),
            "metadata_saved": result.metadata_saved,
            "code_files_saved": result.code_files_saved,
            "errors": result.errors,
        }
        for result in results
    ]
    write_json(output_root / "index.json", index_rows)
    LOGGER.info(
        "Finished. total=%d metadata=%d code_files=%d errors=%d",
        len(results),
        sum(result.metadata_saved for result in results),
        sum(result.code_files_saved for result in results),
        sum(bool(result.errors) for result in results),
    )


if __name__ == "__main__":
    main()
