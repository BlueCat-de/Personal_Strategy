"""A-share board scope helpers."""

from __future__ import annotations

from collections.abc import Iterable


MAINBOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
CHINEXT_PREFIXES = ("300", "301")
STAR_PREFIXES = ("688", "689")
BSE_PREFIXES = ("4", "8", "920")
BOARD_SCOPES = ("main", "chinext", "star", "bse", "growth", "all")


def normalize_board_scope(scope: str | None) -> str:
    value = (scope or "main").strip().lower()
    aliases = {
        "mainboard": "main",
        "chuangye": "chinext",
        "cyb": "chinext",
        "kcb": "star",
        "sse-star": "star",
        "beijing": "bse",
        "bj": "bse",
        "non-main": "growth",
        "non_main": "growth",
        "extended": "growth",
        "full": "all",
    }
    value = aliases.get(value, value)
    if value not in BOARD_SCOPES:
        raise ValueError(f"Unsupported board scope: {scope!r}. Choose from {BOARD_SCOPES}")
    return value


def prefixes_for_scope(scope: str | None) -> tuple[str, ...]:
    value = normalize_board_scope(scope)
    if value == "main":
        return MAINBOARD_PREFIXES
    if value == "chinext":
        return CHINEXT_PREFIXES
    if value == "star":
        return STAR_PREFIXES
    if value == "bse":
        return BSE_PREFIXES
    if value == "growth":
        return CHINEXT_PREFIXES + STAR_PREFIXES + BSE_PREFIXES
    return MAINBOARD_PREFIXES + CHINEXT_PREFIXES + STAR_PREFIXES + BSE_PREFIXES


def symbol_in_board_scope(symbol: object, scope: str | None) -> bool:
    value = str(symbol).strip()
    if not value:
        return False
    if "." in value:
        value = value.split(".", 1)[0]
    value = value.replace(".0", "").zfill(6)
    return value.startswith(prefixes_for_scope(scope))


def filter_symbols_by_board_scope(symbols: Iterable[object], scope: str | None) -> list[bool]:
    return [symbol_in_board_scope(symbol, scope) for symbol in symbols]
