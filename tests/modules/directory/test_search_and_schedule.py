"""Accent-insensitive search text and the next time of the daily sync."""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

from app.modules.directory.application.schedule import next_run_at
from app.modules.directory.domain.search import (
    MAX_TERMS,
    build_search_text,
    normalize_text,
    search_terms,
)

BOGOTA = ZoneInfo("America/Bogota")


def test_text_is_folded_to_ascii_words() -> None:
    assert normalize_text("Ana María Pérez-Gómez") == "ana maria perez gomez"
    assert normalize_text("  NÚÑEZ  ") == "nunez"
    assert normalize_text(None) == ""


def test_the_search_text_splits_emails_and_has_a_leading_space() -> None:
    text = build_search_text("Ana María", "Analista BI", None, "ana.perez@jdh.com.co")

    assert text == " ana maria analista bi perez jdh com co"
    assert build_search_text(None, "") == ""


def test_queries_match_word_prefixes_in_any_order() -> None:
    text = build_search_text("María Pérez", "Analista", None, "maria.perez@jdh.com.co")

    def matches(query: str) -> bool:
        return all(f" {term}" in text for term in search_terms(query))

    assert matches("per mar")
    assert matches("PÉREZ")
    assert not matches("erez")
    assert not matches("maria lopez")
    assert len(search_terms("a b c d e f g h i j")) == MAX_TERMS


def test_next_run_is_later_today_or_tomorrow_in_bogota() -> None:
    at = time(23, 59)
    # 15:00 UTC is 10:00 in Bogotá: today at 23:59 local, 04:59 UTC tomorrow.
    assert next_run_at(datetime(2026, 11, 4, 15, tzinfo=UTC), at, BOGOTA) == datetime(
        2026, 11, 5, 4, 59, tzinfo=UTC
    )
    # 05:00 UTC on the 5th is 00:00 local on the 5th: the run is that night.
    assert next_run_at(datetime(2026, 11, 5, 5, tzinfo=UTC), at, BOGOTA) == datetime(
        2026, 11, 6, 4, 59, tzinfo=UTC
    )
    # Exactly at the time, the next one is tomorrow.
    exactly = datetime(2026, 11, 5, 4, 59, tzinfo=UTC)
    assert next_run_at(exactly, at, BOGOTA) == datetime(2026, 11, 6, 4, 59, tzinfo=UTC)
