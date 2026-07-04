#!/usr/bin/env python3
"""
Configurable crawler for public quantitative trading strategy source code.

The crawler favors public APIs and polite crawling:
    - GitHub code search API for open-source repositories.
    - Optional HTML index pages configured by the user.
    - Rate limiting, retries, robots.txt checks, stateful resume, logging.

It does not bypass login walls, captchas, paywalls, or access controls.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


LOGGER = logging.getLogger("strategy_crawler")


STRATEGY_KEYWORDS = {
    "strategy",
    "alpha",
    "signal",
    "backtest",
    "portfolio",
    "rebalance",
    "position",
    "order",
    "buy",
    "sell",
    "risk",
    "stop_loss",
    "take_profit",
    "drawdown",
    "sharpe",
    "factor",
    "momentum",
    "mean_reversion",
    "pair",
    "arbitrage",
}

FRAMEWORK_KEYWORDS = {
    "backtrader",
    "zipline",
    "vnpy",
    "rqalpha",
    "quantconnect",
    "lean",
    "jqdata",
    "talib",
    "pyfolio",
    "alphalens",
    "qstrader",
    "vectorbt",
}

LOW_QUALITY_PATTERNS = (
    "hello world",
    "todo: implement",
    "your code here",
    "pass  #",
    "print('hello",
    'print("hello',
)

TYPE_RULES = {
    "momentum": ("momentum", "breakout", "trend", "moving_average", "ma_cross", "rsi"),
    "mean_reversion": ("mean_reversion", "boll", "zscore", "reversion", "oversold", "overbought"),
    "factor": ("factor", "alpha", "rank", "neutralize", "ic", "cross_section"),
    "pairs_trading": ("pair", "cointegration", "spread", "hedge_ratio"),
    "arbitrage": ("arbitrage", "market_making", "stat_arb"),
    "machine_learning": ("sklearn", "xgboost", "lightgbm", "lstm", "randomforest", "predict"),
    "grid": ("grid", "martingale"),
}


@dataclass(frozen=True)
class CrawlCandidate:
    source_name: str
    source_type: str
    url: str
    name: str
    content_url: str | None = None
    repo_full_name: str | None = None
    repo_stars: int = 0
    repo_forks: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredStrategy:
    candidate: CrawlCandidate
    code: str
    content_hash: str
    score: float
    strategy_type: str
    reasons: list[str]


class StateStore:
    """Small JSON state store used for resumable crawling and deduplication."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.state = {
            "processed_urls": {},
            "content_hashes": {},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as file:
            self.state = json.load(file)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as file:
            json.dump(self.state, file, ensure_ascii=False, indent=2)
        tmp_path.replace(self.path)

    def has_url(self, url: str) -> bool:
        return url in self.state["processed_urls"]

    def has_hash(self, content_hash: str) -> bool:
        return content_hash in self.state["content_hashes"]

    def mark_url(self, url: str, status: str, detail: str = "") -> None:
        self.state["processed_urls"][url] = {
            "status": status,
            "detail": detail,
            "updated_at": int(time.time()),
        }

    def mark_hash(self, content_hash: str, save_path: str) -> None:
        self.state["content_hashes"][content_hash] = save_path


class RobotsCache:
    """Caches robots.txt parsers per host."""

    def __init__(self, user_agent: str, timeout: int = 10) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.parsers: dict[str, RobotFileParser] = {}

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        if base_url not in self.parsers:
            parser = RobotFileParser()
            parser.set_url(urljoin(base_url, "/robots.txt"))
            try:
                parser.read()
            except Exception as exc:  # noqa: BLE001 - robots failures should not crash the job.
                LOGGER.warning("Failed to read robots.txt for %s: %s", base_url, exc)
                return False
            self.parsers[base_url] = parser
        return self.parsers[base_url].can_fetch(self.user_agent, url)


class RateLimiter:
    """Host-aware sleep-based rate limiter."""

    def __init__(self, default_delay: float) -> None:
        self.default_delay = default_delay
        self.last_request_at: dict[str, float] = {}

    def wait(self, url: str, delay: float | None = None) -> None:
        host = urlparse(url).netloc
        interval = self.default_delay if delay is None else delay
        elapsed = time.time() - self.last_request_at.get(host, 0)
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self.last_request_at[host] = time.time()


class HttpClient:
    """HTTP client with retries, timeout, rate limit, and optional robots checks."""

    def __init__(
        self,
        user_agent: str,
        timeout: int,
        rate_limiter: RateLimiter,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.rate_limiter = rate_limiter
        self.respect_robots = respect_robots
        self.robots = RobotsCache(user_agent=user_agent, timeout=timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        if self.respect_robots and not self._is_api_url(url) and not self.robots.can_fetch(url):
            raise PermissionError(f"robots.txt disallows fetching {url}")
        self.rate_limiter.wait(url)
        response = self.session.get(url, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response

    @staticmethod
    def _is_api_url(url: str) -> bool:
        return urlparse(url).netloc == "api.github.com"


class QualityScorer:
    """Explainable heuristic quality filter for strategy source code."""

    def __init__(self, min_score: float, min_lines: int, allowed_extensions: set[str]) -> None:
        self.min_score = min_score
        self.min_lines = min_lines
        self.allowed_extensions = allowed_extensions

    def score(self, candidate: CrawlCandidate, code: str) -> ScoredStrategy | None:
        suffix = Path(candidate.name).suffix.lower()
        if suffix not in self.allowed_extensions:
            return None

        content_hash = hashlib.sha256(code.encode("utf-8", errors="ignore")).hexdigest()
        normalized = code.lower()
        lines = [line for line in code.splitlines() if line.strip()]
        reasons: list[str] = []
        score = 0.0

        if len(lines) < self.min_lines:
            return None
        score += min(len(lines) / 20, 20)
        reasons.append(f"non_empty_lines={len(lines)}")

        keyword_hits = sum(1 for keyword in STRATEGY_KEYWORDS if keyword in normalized)
        framework_hits = sum(1 for keyword in FRAMEWORK_KEYWORDS if keyword in normalized)
        score += min(keyword_hits * 3, 24)
        score += min(framework_hits * 8, 24)
        if keyword_hits:
            reasons.append(f"strategy_keywords={keyword_hits}")
        if framework_hits:
            reasons.append(f"framework_keywords={framework_hits}")

        if re.search(r"class\s+\w+.*strategy", code, re.IGNORECASE | re.DOTALL):
            score += 10
            reasons.append("strategy_class")
        if re.search(r"def\s+(initialize|handle_data|next|on_bar|before_trading|rebalance)\s*\(", code):
            score += 10
            reasons.append("strategy_lifecycle_function")
        if any(term in normalized for term in ("stop_loss", "risk", "position_size", "max_drawdown")):
            score += 8
            reasons.append("risk_management_terms")
        if any(term in normalized for term in ("sharpe", "drawdown", "annual_return", "backtest")):
            score += 6
            reasons.append("performance_or_backtest_terms")

        stars_score = min(candidate.repo_stars / 50, 16)
        forks_score = min(candidate.repo_forks / 20, 8)
        score += stars_score + forks_score
        if candidate.repo_stars:
            reasons.append(f"repo_stars={candidate.repo_stars}")
        if candidate.repo_forks:
            reasons.append(f"repo_forks={candidate.repo_forks}")

        if any(pattern in normalized for pattern in LOW_QUALITY_PATTERNS):
            score -= 20
            reasons.append("low_quality_pattern_penalty")
        if normalized.count("pass\n") > 5:
            score -= 10
            reasons.append("too_many_pass_statements")

        strategy_type = self.detect_strategy_type(normalized)
        if score < self.min_score:
            return None

        return ScoredStrategy(
            candidate=candidate,
            code=code,
            content_hash=content_hash,
            score=round(score, 2),
            strategy_type=strategy_type,
            reasons=reasons,
        )

    @staticmethod
    def detect_strategy_type(normalized_code: str) -> str:
        for strategy_type, keywords in TYPE_RULES.items():
            if any(keyword in normalized_code for keyword in keywords):
                return strategy_type
        return "general"


class StrategySaver:
    """Persists accepted strategies and metadata under source/type folders."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def save(self, strategy: ScoredStrategy) -> Path:
        candidate = strategy.candidate
        source = safe_name(candidate.source_name)
        strategy_type = safe_name(strategy.strategy_type)
        filename = safe_name(Path(candidate.name).name)
        if not Path(filename).suffix:
            filename = f"{filename}.py"

        directory = self.output_dir / source / strategy_type
        directory.mkdir(parents=True, exist_ok=True)
        save_path = unique_path(directory / filename)
        save_path.write_text(strategy.code, encoding="utf-8")

        metadata = {
            "source_name": candidate.source_name,
            "source_type": candidate.source_type,
            "url": candidate.url,
            "content_url": candidate.content_url,
            "repo_full_name": candidate.repo_full_name,
            "repo_stars": candidate.repo_stars,
            "repo_forks": candidate.repo_forks,
            "score": strategy.score,
            "strategy_type": strategy.strategy_type,
            "content_hash": strategy.content_hash,
            "reasons": strategy.reasons,
            "extra": candidate.metadata,
        }
        save_path.with_suffix(save_path.suffix + ".meta.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return save_path


class StrategyCrawler:
    def __init__(
        self,
        config: dict[str, Any],
        output_dir: Path,
        state_file: Path,
        dry_run: bool = False,
        github_token: str | None = None,
    ) -> None:
        crawler_config = config.get("crawler", {})
        self.config = config
        self.dry_run = dry_run
        self.max_items = int(crawler_config.get("max_items", 50))
        self.max_items_per_source = int(crawler_config.get("max_items_per_source", 20))
        self.state = StateStore(state_file)
        self.rate_limiter = RateLimiter(float(crawler_config.get("request_delay_seconds", 2.0)))
        self.http = HttpClient(
            user_agent=crawler_config.get("user_agent", "PersonalStrategyCrawler/0.1"),
            timeout=int(crawler_config.get("timeout_seconds", 20)),
            rate_limiter=self.rate_limiter,
            respect_robots=bool(crawler_config.get("respect_robots_txt", True)),
        )
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        if self.github_token:
            self.http.session.headers.update({"Authorization": f"Bearer {self.github_token}"})

        quality = config.get("quality", {})
        allowed_extensions = set(quality.get("allowed_extensions", [".py"]))
        self.scorer = QualityScorer(
            min_score=float(quality.get("min_score", 45)),
            min_lines=int(quality.get("min_lines", 60)),
            allowed_extensions=allowed_extensions,
        )
        self.saver = StrategySaver(output_dir)

    def run(self) -> None:
        saved = 0
        seen_in_run: set[str] = set()
        for source in self.config.get("sources", []):
            if not source.get("enabled", True):
                LOGGER.info("Skip disabled source: %s", source.get("name"))
                continue

            for candidate in self.iter_candidates(source):
                if saved >= self.max_items:
                    LOGGER.info("Reached global max_items=%s", self.max_items)
                    self.state.save()
                    return
                if candidate.url in seen_in_run or self.state.has_url(candidate.url):
                    LOGGER.debug("Skip already processed url: %s", candidate.url)
                    continue
                seen_in_run.add(candidate.url)

                try:
                    code = self.fetch_candidate_code(candidate)
                    strategy = self.scorer.score(candidate, code)
                    if strategy is None:
                        self.state.mark_url(candidate.url, "rejected", "quality_filter")
                        LOGGER.info("Rejected by quality filter: %s", candidate.url)
                        continue
                    if self.state.has_hash(strategy.content_hash):
                        self.state.mark_url(candidate.url, "duplicate", strategy.content_hash)
                        LOGGER.info("Duplicate content skipped: %s", candidate.url)
                        continue
                    if self.dry_run:
                        self.state.mark_url(candidate.url, "dry_run_accepted", str(strategy.score))
                        LOGGER.info(
                            "Dry-run accepted score=%s type=%s url=%s",
                            strategy.score,
                            strategy.strategy_type,
                            candidate.url,
                        )
                    else:
                        save_path = self.saver.save(strategy)
                        self.state.mark_url(candidate.url, "saved", str(save_path))
                        self.state.mark_hash(strategy.content_hash, str(save_path))
                        saved += 1
                        LOGGER.info(
                            "Saved score=%s type=%s path=%s source=%s",
                            strategy.score,
                            strategy.strategy_type,
                            save_path,
                            candidate.url,
                        )
                except Exception as exc:  # noqa: BLE001 - batch crawling should continue.
                    self.state.mark_url(candidate.url, "failed", str(exc))
                    LOGGER.exception("Failed candidate %s: %s", candidate.url, exc)
                finally:
                    self.state.save()

        LOGGER.info("Crawler finished. saved=%s", saved)

    def iter_candidates(self, source: dict[str, Any]) -> list[CrawlCandidate]:
        source_type = source.get("type")
        if source_type == "github_code_search":
            return self.iter_github_code_candidates(source)
        if source_type == "html_index":
            return self.iter_html_index_candidates(source)
        if source_type == "url_list":
            return self.iter_url_list_candidates(source)
        LOGGER.warning("Unsupported source type: %s", source_type)
        return []

    def iter_github_code_candidates(self, source: dict[str, Any]) -> list[CrawlCandidate]:
        name = source.get("name", "github")
        queries = source.get("queries", [])
        max_results = min(int(source.get("max_results_per_query", 20)), 100)
        candidates: list[CrawlCandidate] = []
        repo_cache: dict[str, dict[str, Any]] = {}

        for query in queries:
            LOGGER.info("GitHub search source=%s query=%s", name, query)
            response = self.http.get(
                "https://api.github.com/search/code",
                params={"q": query, "per_page": max_results, "page": 1},
                headers={"Accept": "application/vnd.github+json"},
            )
            payload = response.json()
            for item in payload.get("items", [])[:max_results]:
                repo = item.get("repository", {})
                repo_full_name = repo.get("full_name")
                repo_detail = {}
                if repo_full_name:
                    repo_detail = repo_cache.get(repo_full_name) or self.fetch_github_repo_detail(
                        repo_full_name
                    )
                    repo_cache[repo_full_name] = repo_detail

                candidates.append(
                    CrawlCandidate(
                        source_name=name,
                        source_type="github_code_search",
                        url=item.get("html_url", item.get("url")),
                        name=item.get("name", "strategy.py"),
                        content_url=item.get("url"),
                        repo_full_name=repo_full_name,
                        repo_stars=int(repo_detail.get("stargazers_count", 0) or 0),
                        repo_forks=int(repo_detail.get("forks_count", 0) or 0),
                        metadata={
                            "query": query,
                            "path": item.get("path"),
                            "repository_url": repo.get("html_url"),
                        },
                    )
                )
                if len(candidates) >= self.max_items_per_source:
                    return candidates
        return candidates

    def fetch_github_repo_detail(self, repo_full_name: str) -> dict[str, Any]:
        url = f"https://api.github.com/repos/{repo_full_name}"
        try:
            response = self.http.get(url, headers={"Accept": "application/vnd.github+json"})
            return response.json()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to fetch repo detail for %s: %s", repo_full_name, exc)
            return {}

    def iter_html_index_candidates(self, source: dict[str, Any]) -> list[CrawlCandidate]:
        name = source.get("name", "html_index")
        url = source["url"]
        allow_patterns = [re.compile(pattern) for pattern in source.get("allow_url_patterns", [])]
        max_results = int(source.get("max_results", self.max_items_per_source))

        response = self.http.get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[CrawlCandidate] = []
        for anchor in soup.find_all("a", href=True):
            href = urljoin(url, anchor["href"])
            if allow_patterns and not any(pattern.search(href) for pattern in allow_patterns):
                continue
            candidates.append(
                CrawlCandidate(
                    source_name=name,
                    source_type="html_index",
                    url=href,
                    name=Path(urlparse(href).path).name or safe_name(anchor.get_text(strip=True)),
                    content_url=href,
                    metadata={"index_url": url},
                )
            )
            if len(candidates) >= max_results:
                break
        return candidates

    def iter_url_list_candidates(self, source: dict[str, Any]) -> list[CrawlCandidate]:
        name = source.get("name", "url_list")
        candidates: list[CrawlCandidate] = []
        for item in source.get("urls", []):
            if isinstance(item, str):
                url = item
                filename = Path(urlparse(url).path).name or "strategy.py"
                metadata: dict[str, Any] = {}
            else:
                url = item["url"]
                filename = item.get("name") or Path(urlparse(url).path).name or "strategy.py"
                metadata = {key: value for key, value in item.items() if key not in {"url", "name"}}

            candidates.append(
                CrawlCandidate(
                    source_name=name,
                    source_type="url_list",
                    url=url,
                    name=filename,
                    content_url=url,
                    metadata=metadata,
                )
            )
        return candidates[: self.max_items_per_source]

    def fetch_candidate_code(self, candidate: CrawlCandidate) -> str:
        if candidate.source_type == "github_code_search":
            return self.fetch_github_code(candidate)
        response = self.http.get(candidate.content_url or candidate.url)
        return response.text

    def fetch_github_code(self, candidate: CrawlCandidate) -> str:
        if not candidate.content_url:
            raise ValueError("GitHub candidate missing content_url")

        response = self.http.get(
            candidate.content_url,
            headers={"Accept": "application/vnd.github+json"},
        )
        payload = response.json()
        if payload.get("encoding") == "base64" and payload.get("content"):
            return base64.b64decode(payload["content"]).decode("utf-8", errors="replace")
        download_url = payload.get("download_url")
        if download_url:
            return self.http.get(download_url).text
        raise ValueError(f"Cannot extract source code from {candidate.content_url}")


def safe_name(value: str) -> str:
    value = value.strip() or "unknown"
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value[:120].strip("._-") or "unknown"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot create unique path for {path}")


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def setup_logging(log_file: Path, level: str) -> None:
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
    parser = argparse.ArgumentParser(
        description="Crawl public quantitative strategy source code with quality filtering."
    )
    parser.add_argument("--config", default="crawler_config.json", help="Path to crawler JSON config.")
    parser.add_argument("--output-dir", default="strategies", help="Directory to save accepted strategies.")
    parser.add_argument(
        "--state-file",
        default=".crawler_state/state.json",
        help="Path to resume/deduplication state JSON.",
    )
    parser.add_argument("--log-file", default="logs/crawler.log", help="Crawler log file.")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--github-token", help="GitHub token. Defaults to GITHUB_TOKEN env var.")
    parser.add_argument("--max-items", type=int, help="Override crawler.max_items in config.")
    parser.add_argument("--dry-run", action="store_true", help="Run filters without saving strategy files.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(Path(args.log_file), args.log_level)
    config = load_config(Path(args.config))
    if args.max_items is not None:
        config.setdefault("crawler", {})["max_items"] = args.max_items

    crawler = StrategyCrawler(
        config=config,
        output_dir=Path(args.output_dir),
        state_file=Path(args.state_file),
        dry_run=args.dry_run,
        github_token=args.github_token,
    )
    crawler.run()


if __name__ == "__main__":
    main()
