#!/usr/bin/env python3
"""
Scrape BigQuant community strategy pages listed in strategies/BigQuant/webs.

The script uses public BigQuant endpoints where available:
    - /square/{uuid}: article title and strategy description.
    - /community/strategyshares/{uuid}/new/code: notebook-style strategy code.
    - /community/strategyshares/performance: cumulative return and metrics.

It does not bypass login walls or paid access controls. If BigQuant changes an
endpoint or requires authentication for a resource, the failure is recorded in
metadata.json and the rest of the strategy artifacts are still saved.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger("bigquant_strategy_scraper")

BIGQUANT_BASE_URL = "https://bigquant.com"
UUID_RE = re.compile(
    r"(?:/playground/|/square/)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

PERFORMANCE_COLUMNS = [
    "strategy_id",
    "date",
    "cumulative_return",
    "relative_return",
    "total_asset",
    "metrics",
    "market_value",
    "cash",
    "position_value",
    "gross_leverage",
    "benchmark_return",
]

STYLE_RULES = {
    "machine_learning": (
        "ai",
        "人工智能",
        "机器学习",
        "模型",
        "训练",
        "预测",
        "stockranker",
        "randomforest",
        "xgboost",
        "lightgbm",
        "sklearn",
    ),
    "multi_factor": (
        "多因子",
        "因子",
        "factor",
        "rank",
        "评分",
        "排序",
        "基本面",
        "成长",
        "价值",
        "估值",
    ),
    "etf_allocation": (
        "etf",
        "基金",
        "风险平价",
        "资产配置",
        "纳斯达克",
        "指数",
        "轮动",
    ),
    "trend_momentum": (
        "趋势",
        "动量",
        "momentum",
        "breakout",
        "均线",
        "择时",
        "量价",
        "上涨",
    ),
    "convertible_bond": (
        "可转债",
        "转债",
        "convertible",
    ),
    "intraday": (
        "日内",
        "高频",
        "intraday",
        "分钟",
        "tick",
    ),
    "value_quality": (
        "净利润",
        "经营",
        "盈利",
        "股东收益",
        "质量",
        "质优",
        "value",
        "quality",
    ),
}


@dataclass(frozen=True)
class BigQuantPage:
    url: str
    strategy_id: str
    article_url: str


@dataclass
class ScrapeResult:
    strategy_id: str
    title: str
    style: str
    output_dir: Path
    code_saved: bool
    performance_saved: bool
    article_saved: bool
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
            "User-Agent": "PersonalStrategyBigQuantScraper/0.1",
            "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        }
    )
    cookie = os.getenv("BIGQUANT_COOKIE")
    if cookie:
        session.headers["Cookie"] = cookie
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def request_get(session: requests.Session, url: str, **kwargs: Any) -> requests.Response:
    timeout = getattr(session, "request_timeout", 30)
    return session.get(url, timeout=timeout, **kwargs)


def load_pages(path: Path) -> list[BigQuantPage]:
    pages: list[BigQuantPage] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        url = raw_line.strip()
        if not url or url.startswith("#"):
            continue
        match = UUID_RE.search(url)
        if not match:
            LOGGER.warning("Skip URL without BigQuant UUID: %s", url)
            continue
        strategy_id = match.group(1)
        pages.append(
            BigQuantPage(
                url=url,
                strategy_id=strategy_id,
                article_url=f"{BIGQUANT_BASE_URL}/square/{strategy_id}",
            )
        )
    seen: set[str] = set()
    unique_pages: list[BigQuantPage] = []
    for page in pages:
        if page.strategy_id in seen:
            continue
        seen.add(page.strategy_id)
        unique_pages.append(page)
    return unique_pages


def safe_name(value: str, max_length: int = 80) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    value = value or "untitled_strategy"
    return value[:max_length].strip("._")


def extract_article(session: requests.Session, page: BigQuantPage) -> tuple[str, str, str]:
    response = request_get(session, page.article_url)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    title = ""
    heading = soup.select_one("h1")
    if heading:
        title = heading.get_text(" ", strip=True)
    if not title and soup.title and soup.title.string:
        title = soup.title.string.replace("- BigQuant策略社区", "").strip()
    content_node = soup.select_one("#markdown-content") or soup.select_one("main")
    article_text = content_node.get_text("\n", strip=True) if content_node else ""
    article_html = str(content_node) if content_node else ""
    return title or page.strategy_id, article_text, article_html


def extract_code_from_notebook_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    data = payload.get("data", {})
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    code_payload = data.get("code") if isinstance(data, dict) else None
    if not code_payload:
        return "", {}
    notebook = json.loads(code_payload) if isinstance(code_payload, str) else code_payload
    cells = notebook.get("cells", []) if isinstance(notebook, dict) else []
    code_parts: list[str] = []
    for index, cell in enumerate(cells):
        if cell.get("cell_type") != "code":
            continue
        source = cell.get("source", [])
        if isinstance(source, list):
            source_text = "".join(source)
        else:
            source_text = str(source)
        code_parts.append(f"# %% BigQuant cell {index}\n{source_text.rstrip()}\n")
    return "\n".join(code_parts).rstrip() + "\n", notebook if isinstance(notebook, dict) else {}


def fetch_strategy_code(session: requests.Session, page: BigQuantPage) -> tuple[str, dict[str, Any]]:
    url = f"{BIGQUANT_BASE_URL}/bigapis/trading/v1/community/strategyshares/{page.strategy_id}/new/code"
    response = request_get(
        session,
        url,
        headers={"Referer": page.url},
    )
    response.raise_for_status()
    payload = response.json()
    return extract_code_from_notebook_payload(payload)


def fetch_performance(session: requests.Session, page: BigQuantPage) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = f"{BIGQUANT_BASE_URL}/bigapis/trading/v1/community/strategyshares/performance"
    response = request_get(
        session,
        url,
        params=[("strategy_ids", page.strategy_id), ("extra_fields", "true")],
        headers={"Referer": page.url},
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("data", {}).get("items", [])
    rows: list[dict[str, Any]] = []
    for item in items:
        row = {column: item[idx] if idx < len(item) else None for idx, column in enumerate(PERFORMANCE_COLUMNS)}
        rows.append(row)
    rows.sort(key=lambda row: row.get("date") or "")
    return rows, payload


def summarize_performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    valid_rows = [row for row in rows if isinstance(row.get("cumulative_return"), (int, float))]
    if not valid_rows:
        return {}
    first = valid_rows[0]
    latest = valid_rows[-1]
    cumulative_values = [float(row["cumulative_return"]) for row in valid_rows]
    equity_values = [1.0 + value for value in cumulative_values]
    running_peak = -math.inf
    max_drawdown = 0.0
    for equity in equity_values:
        running_peak = max(running_peak, equity)
        if running_peak > 0:
            max_drawdown = min(max_drawdown, equity / running_peak - 1.0)
    days = max(1, len(valid_rows) - 1)
    total_return = float(latest["cumulative_return"])
    annual_return = None
    if total_return > -1:
        annual_return = (1 + total_return) ** (252 / days) - 1
    latest_metrics = latest.get("metrics") if isinstance(latest.get("metrics"), dict) else {}
    return {
        "start_date": first.get("date"),
        "end_date": latest.get("date"),
        "trading_days": len(valid_rows),
        "total_return": total_return,
        "annual_return_estimated": annual_return,
        "max_drawdown_estimated": max_drawdown,
        "latest_total_asset": latest.get("total_asset"),
        "latest_relative_return": latest.get("relative_return"),
        "latest_metrics": latest_metrics,
    }


def detect_style(title: str, article_text: str, _code: str) -> str:
    del _code
    text = f"{title}\n{article_text}".lower()
    priority_rules = (
        ("convertible_bond", ("可转债", "转债", "convertible")),
        ("etf_allocation", ("etf", "风险平价", "纳斯达克")),
        ("intraday", ("日内", "高频", "intraday")),
        ("machine_learning", ("ai", "人工智能", "机器学习", "stockranker", "xgboost", "lightgbm")),
    )
    for style, keywords in priority_rules:
        if any(keyword.lower() in text for keyword in keywords):
            return style
    best_style = "general"
    best_score = 0
    for style, keywords in STYLE_RULES.items():
        score = sum(1 for keyword in keywords if keyword.lower() in text)
        if score > best_score:
            best_style = style
            best_score = score
    return best_style


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_performance_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PERFORMANCE_COLUMNS)
        writer.writeheader()
        for row in rows:
            serialized = dict(row)
            if isinstance(serialized.get("metrics"), dict):
                serialized["metrics"] = json.dumps(serialized["metrics"], ensure_ascii=False)
            writer.writerow(serialized)


def format_percent(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "N/A"
    return f"{value * 100:.2f}%"


def save_strategy(
    output_root: Path,
    page: BigQuantPage,
    title: str,
    article_text: str,
    article_html: str,
    code: str,
    notebook: dict[str, Any],
    performance_rows: list[dict[str, Any]],
    raw_performance: dict[str, Any],
    errors: list[str],
) -> ScrapeResult:
    style = detect_style(title, article_text, code)
    strategy_dir = output_root / style / f"{safe_name(title)}_{page.strategy_id[:8]}"
    strategy_dir.mkdir(parents=True, exist_ok=True)

    summary = summarize_performance(performance_rows)
    metadata = {
        "source": "BigQuant",
        "source_url": page.url,
        "article_url": page.article_url,
        "strategy_id": page.strategy_id,
        "title": title,
        "style": style,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "code_saved": bool(code.strip()),
        "performance_rows": len(performance_rows),
        "performance_summary": summary,
        "errors": errors,
    }

    if code.strip():
        (strategy_dir / "strategy.py").write_text(code, encoding="utf-8")
    if notebook:
        write_json(strategy_dir / "notebook.json", notebook)
    if article_text:
        (strategy_dir / "article.md").write_text(f"# {title}\n\n{article_text}\n", encoding="utf-8")
    if article_html:
        (strategy_dir / "article.html").write_text(article_html, encoding="utf-8")
    if performance_rows:
        write_json(strategy_dir / "performance.json", {"summary": summary, "rows": performance_rows})
        write_performance_csv(strategy_dir / "performance.csv", performance_rows)
        write_json(strategy_dir / "performance_raw.json", raw_performance)

    summary_md = [
        f"# {title}",
        "",
        f"- Source URL: {page.url}",
        f"- Article URL: {page.article_url}",
        f"- Strategy ID: `{page.strategy_id}`",
        f"- Trading style: `{style}`",
        f"- Code saved: `{bool(code.strip())}`",
        f"- Performance rows: `{len(performance_rows)}`",
        f"- Total return: `{format_percent(summary.get('total_return'))}`",
        f"- Estimated annual return: `{format_percent(summary.get('annual_return_estimated'))}`",
        f"- Estimated max drawdown: `{format_percent(summary.get('max_drawdown_estimated'))}`",
        f"- Latest Sharpe: `{summary.get('latest_metrics', {}).get('sharpe', 'N/A')}`",
    ]
    if errors:
        summary_md.extend(["", "## Errors", *[f"- {error}" for error in errors]])
    (strategy_dir / "summary.md").write_text("\n".join(summary_md) + "\n", encoding="utf-8")
    write_json(strategy_dir / "metadata.json", metadata)

    return ScrapeResult(
        strategy_id=page.strategy_id,
        title=title,
        style=style,
        output_dir=strategy_dir,
        code_saved=bool(code.strip()),
        performance_saved=bool(performance_rows),
        article_saved=bool(article_text.strip()),
        errors=errors,
    )


def scrape_page(session: requests.Session, output_root: Path, page: BigQuantPage) -> ScrapeResult:
    title = page.strategy_id
    article_text = ""
    article_html = ""
    code = ""
    notebook: dict[str, Any] = {}
    performance_rows: list[dict[str, Any]] = []
    raw_performance: dict[str, Any] = {}
    errors: list[str] = []

    try:
        title, article_text, article_html = extract_article(session, page)
    except Exception as exc:  # noqa: BLE001 - persist partial results.
        errors.append(f"article: {exc}")

    try:
        code, notebook = fetch_strategy_code(session, page)
        if not code.strip():
            errors.append("code: empty code payload")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"code: {exc}")

    try:
        performance_rows, raw_performance = fetch_performance(session, page)
        if not performance_rows:
            errors.append("performance: empty performance payload")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"performance: {exc}")

    return save_strategy(
        output_root=output_root,
        page=page,
        title=title,
        article_text=article_text,
        article_html=article_html,
        code=code,
        notebook=notebook,
        performance_rows=performance_rows,
        raw_performance=raw_performance,
        errors=errors,
    )


def configure_logging(log_file: Path, level: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape BigQuant strategy pages from a URL list.")
    parser.add_argument("--input-file", default="strategies/BigQuant/webs", help="File containing BigQuant URLs.")
    parser.add_argument("--output-dir", default="strategies/BigQuant", help="Output root directory.")
    parser.add_argument("--log-file", default="logs/bigquant_strategy_scraper.log", help="Log file path.")
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
    LOGGER.info("Loaded %d BigQuant URLs from %s", len(pages), input_file)

    session = build_session(args.timeout)
    results: list[ScrapeResult] = []
    for index, page in enumerate(pages, start=1):
        LOGGER.info("Scraping %d/%d id=%s", index, len(pages), page.strategy_id)
        result = scrape_page(session, output_root, page)
        results.append(result)
        LOGGER.info(
            "Saved style=%s code=%s performance=%s path=%s errors=%d",
            result.style,
            result.code_saved,
            result.performance_saved,
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
            "code_saved": result.code_saved,
            "performance_saved": result.performance_saved,
            "article_saved": result.article_saved,
            "errors": result.errors,
        }
        for result in results
    ]
    write_json(output_root / "index.json", index_rows)
    LOGGER.info(
        "Finished. total=%d code=%d performance=%d errors=%d",
        len(results),
        sum(result.code_saved for result in results),
        sum(result.performance_saved for result in results),
        sum(bool(result.errors) for result in results),
    )


if __name__ == "__main__":
    main()
