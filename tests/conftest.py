"""Pytest fixtures for Cartograph negative controls.

The zero-overlap and tenant-bleed tests need real DuckDB databases with
controlled data. We build them in pytest tmp_path so they're isolated
from `~/osint/data/analytics.duckdb` (which may not exist on the test
host anyway, and absolutely must not be read by accident).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _make_transactions_table(db_path: Path):
    """Create an empty transactions table with the canonical Cartograph
    schema. Returns the connection (caller closes)."""
    conn = duckdb.connect(str(db_path))
    conn.execute("""
        CREATE TABLE transactions (
            transaction_id    VARCHAR,
            guest_id          VARCHAR,
            transaction_date  DATE,
            product_type      VARCHAR,
            fragrance         VARCHAR,
            price             DOUBLE,
            discount_amount   DOUBLE,
            discount_code     VARCHAR,
            channel           VARCHAR
        )
    """)
    return conn


@pytest.fixture
def zero_overlap_db(tmp_path):
    """A DuckDB instance where Body Lotion buyers AND Laundry Soap buyers
    both exist but NEVER overlap (no guest bought both). The expected
    cross_sell_count = 0; contract.allowed_to_recommend must be False."""
    db = tmp_path / "zero_overlap.duckdb"
    conn = _make_transactions_table(db)
    rows = [
        # 5 guests bought Body Lotion only
        ("T1", "G001", "2025-03-01", "Body Lotion",  "Lavender",   14.0, 0, None, "Online"),
        ("T2", "G002", "2025-03-02", "Body Lotion",  "Lemon Drop", 14.0, 0, None, "Online"),
        ("T3", "G003", "2025-03-03", "Body Lotion",  "Lavender",   14.0, 0, None, "In-Store"),
        ("T4", "G004", "2025-03-04", "Body Lotion",  "Coconut",    14.0, 0, None, "Online"),
        ("T5", "G005", "2025-03-05", "Body Lotion",  "Lavender",   14.0, 0, None, "Online"),
        # 5 DIFFERENT guests bought Laundry Soap — zero overlap
        ("T6",  "G101", "2025-03-06", "Laundry Soap", "Fresh Cotton", 16.0, 0, None, "Online"),
        ("T7",  "G102", "2025-03-07", "Laundry Soap", "Lavender",     16.0, 0, None, "Online"),
        ("T8",  "G103", "2025-03-08", "Laundry Soap", "Eucalyptus",   16.0, 0, None, "In-Store"),
        ("T9",  "G104", "2025-03-09", "Laundry Soap", "Lemon Drop",   16.0, 0, None, "Online"),
        ("T10", "G105", "2025-03-10", "Laundry Soap", "Fresh Cotton", 16.0, 0, None, "Online"),
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.close()
    return db


@pytest.fixture
def tenant_a_db(tmp_path):
    """Tenant A: a real cross-sell exists (G201 bought both Body Lotion AND
    Laundry Soap in similar Lavender fragrance)."""
    db = tmp_path / "tenant_a.duckdb"
    conn = _make_transactions_table(db)
    rows = [
        ("TA1", "G201", "2025-03-01", "Body Lotion",  "Lavender", 14.0, 0, None, "Online"),
        ("TA2", "G201", "2025-03-15", "Laundry Soap", "Lavender", 16.0, 0, None, "Online"),
        ("TA3", "G202", "2025-03-05", "Body Lotion",  "Coconut",  14.0, 0, None, "Online"),
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.close()
    return db


@pytest.fixture
def tenant_b_db(tmp_path):
    """Tenant B: a STRONGER cross-sell exists than Tenant A. If a query
    intended for Tenant A accidentally reads Tenant B, the answer changes
    visibly — which is what tenant_bleed_or_data_path_leak catches."""
    db = tmp_path / "tenant_b.duckdb"
    conn = _make_transactions_table(db)
    rows = [
        # 3 distinct guests with Body Lotion + Laundry Soap pairing — much
        # higher overlap than Tenant A's single pair
        ("TB1", "G301", "2025-03-01", "Body Lotion",  "Lavender", 14.0, 0, None, "Online"),
        ("TB2", "G301", "2025-03-02", "Laundry Soap", "Lavender", 16.0, 0, None, "Online"),
        ("TB3", "G302", "2025-03-03", "Body Lotion",  "Coconut",  14.0, 0, None, "Online"),
        ("TB4", "G302", "2025-03-04", "Laundry Soap", "Coconut",  16.0, 0, None, "Online"),
        ("TB5", "G303", "2025-03-05", "Body Lotion",  "Lavender", 14.0, 0, None, "Online"),
        ("TB6", "G303", "2025-03-06", "Laundry Soap", "Lavender", 16.0, 0, None, "Online"),
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.close()
    return db


class _StubEmbedder:
    """Tiny embedder stub for tests that exercise cross_sell_analysis
    without sentence-transformers. Just returns fragrances as-is and
    reports a fixed similarity. Real embedder behavior is exercised in
    separate sbert-gated tests."""

    def get_similar_set(self, fragrance, threshold=0.6):
        # Return a small set keyed off the input — enough to make
        # cross_sell_analysis's IN clause non-empty
        return [fragrance]

    def get_similarity(self, a, b):
        return 1.0 if a == b else 0.5

    def find_similar(self, name, top_n=5, threshold=0.4):
        return []


@pytest.fixture
def stub_embedder():
    return _StubEmbedder()
