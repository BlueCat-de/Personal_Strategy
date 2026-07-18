"""Project-wide filesystem locations."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_MARKET_DATA_DIR = DATA_DIR / "offline/a_share_history_tushare"
DEFAULT_PRICES_FILE = DEFAULT_MARKET_DATA_DIR / "prices_long.csv"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.local"
