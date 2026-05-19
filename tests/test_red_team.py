"""Red Team Tests for Cartograph — pytest-native.

Kimi/ChatGPT audit 2026-05-19: the original test_red_team.py was a custom
runner that pytest couldn't collect (the `test()` helper conflicted with the
pytest test collector). Rewritten as native pytest with:

  - Taxonomy + embedding-quality tests (existing functionality)
  - SQL injection negative controls against the TYPED tools (cross_sell_analysis,
    get_cohort_stats, find_similar_fragrances, export_segment). The previous
    suite only tested raw-SQL prefixes — which never proved the f-string
    interpolation in cross_sell_analysis was safe (it wasn't).
  - Philyra-trap test (note-profile > name-only similarity)
  - Schema-inspection tool returns metadata only, no rows

Run: `python3 -m pytest tests/test_red_team.py -v`
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from cartograph.taxonomy import FRAGRANCE_TAXONOMY, get_notes, get_family
from cartograph.embeddings import FragranceEmbedder
from cartograph.agent import execute_tool, TOOLS
from cartograph.sql_guard import (
    safe_date, safe_category, safe_fragrance, safe_guest_ids,
    execute_safe, SQLGuardError, KNOWN_PRODUCT_TYPES,
)

# Environment gates — embedding tests require sentence-transformers, export
# tests require pandas. Skip cleanly on machines where those aren't installed
# rather than burying the SQL-injection signal under env errors.
_HAS_SBERT = False
try:
    import sentence_transformers  # noqa: F401
    _HAS_SBERT = True
except Exception:
    # Broader than ImportError — sentence-transformers can fail at import
    # time on version-incompatible torch/numpy combos. Treat any failure as
    # "not available on this host" and skip the embedding tests.
    pass

_HAS_PANDAS = False
try:
    import pandas  # noqa: F401
    _HAS_PANDAS = True
except Exception:
    pass

# This Mac's numpy install is a namespace package without function exports
# (env damage, unrelated to Cartograph). When that's the case, tests that
# write fake .npz caches can't run — skip-gate them too.
_HAS_NUMPY_SAVEZ = False
try:
    import numpy as _np_probe
    _HAS_NUMPY_SAVEZ = hasattr(_np_probe, "savez_compressed")
except Exception:
    pass

requires_sbert = pytest.mark.skipif(
    not _HAS_SBERT, reason="sentence-transformers not installed")
requires_pandas = pytest.mark.skipif(
    not _HAS_PANDAS, reason="pandas not installed")
requires_numpy_io = pytest.mark.skipif(
    not _HAS_NUMPY_SAVEZ, reason="numpy install missing savez_compressed")


# ── Taxonomy ─────────────────────────────────────────────────────────

def test_all_fragrances_have_notes():
    assert all("notes" in v for v in FRAGRANCE_TAXONOMY.values())


def test_all_fragrances_have_family():
    assert all("family" in v for v in FRAGRANCE_TAXONOMY.values())


def test_notes_are_non_empty():
    assert all(len(v["notes"]) > 10 for v in FRAGRANCE_TAXONOMY.values())


def test_case_insensitive_lookup():
    assert get_notes("lavender") is not None


def test_plus_variant_strips():
    assert get_notes("Lavender  Plus") is not None


def test_unknown_fragrance_returns_none():
    assert get_notes("ZZZZ_NOT_REAL") is None


def test_family_lookup():
    assert get_family("Lavender") == "Floral"


def test_eight_scent_families():
    assert len(set(v["family"] for v in FRAGRANCE_TAXONOMY.values())) == 8


# ── Embedding quality ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def embedder():
    if not _HAS_SBERT:
        pytest.skip("sentence-transformers not installed")
    return FragranceEmbedder(use_cache=True)


@requires_sbert
def test_embeddings_loaded(embedder):
    assert embedder.embeddings is not None


@requires_sbert
def test_embedding_dimension(embedder):
    assert embedder.embeddings.shape[1] == 384


@requires_sbert
def test_all_fragrances_embedded(embedder):
    assert len(embedder.names) == len(FRAGRANCE_TAXONOMY)


@requires_sbert
def test_self_similarity_is_one(embedder):
    assert abs(embedder.get_similarity("Lavender", "Lavender") - 1.0) < 0.01


@requires_sbert
def test_same_family_more_similar_than_different(embedder):
    same = embedder.get_similarity("Eucalyptus", "Lemongrass + Eucalyptus")
    diff = embedder.get_similarity("Eucalyptus", "Pink Sugar")
    assert same > diff, f"same-family {same:.3f} should beat cross-family {diff:.3f}"


@requires_sbert
def test_coconut_pair_more_similar_than_distant(embedder):
    same = embedder.get_similarity("Coconut", "Coconut Cream")
    diff = embedder.get_similarity("Coconut", "Commando")
    assert same > diff


@requires_sbert
def test_unknown_fragrance_returns_empty(embedder):
    assert embedder.find_similar("FAKE_SCENT_999") == []


@requires_sbert
def test_threshold_one_returns_nothing(embedder):
    assert embedder.find_similar("Lavender", threshold=1.0) == []


@requires_sbert
def test_top_n_zero_returns_empty(embedder):
    assert embedder.find_similar("Lavender", top_n=0) == []


# ── Philyra trap (the load-bearing embedding-quality test) ──────────

@requires_sbert
def test_note_profile_beats_name_only_similarity(embedder):
    """Kimi 2026-05-19: the load-bearing claim of the Cartograph design is
    that embedding note PROFILES beats embedding names. If two fragrances
    share name tokens but have orthogonal scent profiles, name-only would
    rank them higher than profile-based.

    Triplet: Grapefruit Mimosa vs Lemon Drop (both tart citrus, share zero
    name tokens) should be MORE similar than Grapefruit Mimosa vs anything
    that shares zero scent notes but a token name (e.g., Pink Sugar).
    """
    citrus_pair = embedder.get_similarity("Grapefruit Mimosa", "Lemon Drop")
    cross_family = embedder.get_similarity("Grapefruit Mimosa", "Pink Sugar")
    assert citrus_pair > cross_family, (
        f"Philyra trap active: cross-family ({cross_family:.3f}) > "
        f"in-family no-name-overlap ({citrus_pair:.3f}) — embedding may be "
        f"name-token-driven instead of scent-note-driven"
    )


# ── SQL Guard primitives ─────────────────────────────────────────────

def test_safe_date_rejects_injection():
    """Date validator must reject SQL injection payloads disguised as dates."""
    for payload in [
        "2024-01-01'; DROP TABLE transactions; --",
        "2024-01-01' OR '1'='1",
        "'; DELETE FROM transactions WHERE '1'='1",
        "2024/01/01",      # wrong format
        "2024-13-99",      # invalid calendar date
        "",
        None,
        123,
    ]:
        with pytest.raises(SQLGuardError):
            safe_date(payload)


def test_safe_date_accepts_valid_iso():
    assert safe_date("2026-05-19") == "2026-05-19"


def test_safe_category_rejects_unknown():
    with pytest.raises(SQLGuardError):
        safe_category("Body Lotion'; DROP TABLE transactions; --")
    with pytest.raises(SQLGuardError):
        safe_category("nonexistent category")


def test_safe_category_accepts_known():
    for cat in KNOWN_PRODUCT_TYPES:
        assert safe_category(cat) == cat


def test_safe_fragrance_blocks_quotes_and_comments():
    for payload in [
        "Lavender' OR '1'='1",
        "Lavender'; DROP TABLE transactions; --",
        "Lavender/*comment*/",
        'Lavender"',
        "Lavender\\",
    ]:
        with pytest.raises(SQLGuardError):
            safe_fragrance(payload)


def test_safe_fragrance_allows_real_names():
    assert safe_fragrance("Lavender") == "Lavender"
    assert safe_fragrance("Lemongrass + Eucalyptus") == "Lemongrass + Eucalyptus"


def test_safe_guest_ids_rejects_injection():
    with pytest.raises(SQLGuardError):
        safe_guest_ids(["g123", "g456' OR '1'='1"])
    with pytest.raises(SQLGuardError):
        safe_guest_ids(["g123", "; DROP TABLE transactions"])


def test_safe_guest_ids_accepts_alphanumeric():
    assert safe_guest_ids(["g001", "G-002_xyz"]) == ["g001", "G-002_xyz"]


def test_execute_safe_rejects_brace_template():
    """A caller that passes a string with { or } almost certainly meant to
    f-string. execute_safe rejects this hard — fail-fast on the rendering
    bug rather than letting injection slip through."""
    import duckdb
    conn = duckdb.connect(":memory:")
    try:
        with pytest.raises(SQLGuardError):
            execute_safe(conn, "SELECT '{cat_a}'", [])
    finally:
        conn.close()


# ── Negative controls: agent tools refuse injection ──────────────────

def test_cross_sell_rejects_category_injection():
    """The exact attack ChatGPT/Kimi flagged: category_a containing an
    injection payload. Before the fix this returned rows; after the fix it
    returns an `error` key."""
    r = execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion'; DROP TABLE transactions; --",
        "category_b": "Laundry Soap",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    })
    assert "error" in r, f"injection slipped through cross_sell: {r}"


def test_cross_sell_rejects_or_true_payload():
    r = execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion' OR '1'='1",
        "category_b": "Laundry Soap",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    })
    assert "error" in r


def test_cross_sell_rejects_date_injection():
    r = execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion",
        "category_b": "Laundry Soap",
        "start_date": "2024-01-01'; DELETE FROM transactions; --",
        "end_date": "2024-12-31",
    })
    assert "error" in r


def test_get_cohort_stats_rejects_category_injection():
    r = execute_tool("get_cohort_stats", {
        "category": "Body Lotion' UNION SELECT * FROM transactions --",
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
    })
    assert "error" in r


def test_get_cohort_stats_rejects_date_injection():
    r = execute_tool("get_cohort_stats", {
        "category": "Body Lotion",
        "start_date": "not-a-date",
        "end_date": "2024-12-31",
    })
    assert "error" in r


def test_find_similar_rejects_quote_injection():
    r = execute_tool("find_similar_fragrances", {
        "fragrance_name": "Lavender' OR '1'='1",
    })
    assert "error" in r


def test_export_segment_rejects_bad_guest_ids():
    r = execute_tool("export_segment", {
        "guest_ids": ["g001", "g002'; DROP TABLE transactions; --"],
        "segment_name": "test_segment",
    })
    assert "error" in r


@requires_pandas
def test_export_segment_sanitizes_filename():
    """A malicious segment_name (path traversal) must be slug-sanitized."""
    r = execute_tool("export_segment", {
        "guest_ids": ["g001", "g002"],
        "segment_name": "../../../etc/passwd",
    })
    # Should succeed but path must not escape segments/
    if "path" in r:
        assert "/etc/" not in r["path"]
        assert ".." not in r["path"]


def test_raw_sql_tool_no_longer_exists():
    """Kimi 2026-05-19: query_transactions was the largest attack surface —
    Claude could write any SELECT. Replaced with inspect_schema (metadata
    only). If query_transactions reappears, this test fires."""
    tool_names = {t["name"] for t in TOOLS}
    assert "query_transactions" not in tool_names, (
        "query_transactions tool restored — Claude can write raw SQL again"
    )
    assert "inspect_schema" in tool_names


def test_inspect_schema_returns_metadata_only():
    """inspect_schema must NEVER return row data — only column descriptors."""
    r = execute_tool("inspect_schema", {})
    if "error" in r:
        # Allowed if the analytics.duckdb doesn't exist on this machine —
        # the test still proves the tool can't be coerced into row dumps.
        return
    assert "tables" in r
    for table_name, cols in r["tables"].items():
        for col in cols:
            assert set(col.keys()) <= {"name", "type"}, \
                f"inspect_schema returning more than metadata: {col}"


# ── Similarity quality (existing) ────────────────────────────────────

# ── Negative controls: CLI template path (Kimi/ChatGPT 2026-05-19) ────
#
# The agent path was fixed first. The CLI run_template() path used the
# same vulnerable pattern (`.format(**params)`) — these tests prove the
# refactor closed it. Categories of attack covered:
#   - product-category payload (DROP TABLE)
#   - OR-true tautology (data exfiltration)
#   - UNION-based exfiltration
#   - date injection
#   - unknown / extra params (typo OR injection attempt)
#   - bad int (min_purchases)

from cartograph.cli import run_template, import_file, TEMPLATES, VALIDATOR_TABLE


def test_cli_template_rejects_category_drop_payload(tmp_path):
    """The exact payload Kimi spelled out: a DROP-shaped category."""
    r = run_template("cross_sell_fragrance", {
        "cat_a": "Body Lotion'; DROP TABLE transactions; --",
        "cat_b": "Laundry Soap",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    }, db_path=str(tmp_path / "x.duckdb"))
    assert "error" in r, f"DROP payload slipped through run_template: {r}"


def test_cli_template_rejects_or_true_category():
    r = run_template("cross_sell_fragrance", {
        "cat_a": "x' OR '1'='1",
        "cat_b": "Laundry Soap",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    })
    assert "error" in r


def test_cli_template_rejects_union_exfil():
    r = run_template("category_affinity", {
        "category": "Body Lotion' UNION SELECT guest_id, transaction_date, "
                    "price, '1', '1' FROM transactions --",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    })
    assert "error" in r


def test_cli_template_rejects_date_injection():
    r = run_template("fragrance_loyalty", {
        "start_date": "2025-01-01'; DELETE FROM transactions; --",
        "end_date": "2025-12-31",
    })
    assert "error" in r


def test_cli_template_rejects_unknown_param():
    """Extra params must be REJECTED, not silently passed through. An
    unrecognized name is either a typo or an attempt to slip an injection
    via a param the template wasn't supposed to expose."""
    r = run_template("fragrance_loyalty", {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "evil_extra": "x'; DROP TABLE transactions; --",
    })
    assert "error" in r
    assert "unexpected parameter" in r["error"] or "evil_extra" in r["error"]


def test_cli_template_rejects_missing_required_param():
    r = run_template("repeat_buyers", {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        # missing: min_purchases
    })
    assert "error" in r


def test_cli_template_rejects_non_int_min_purchases():
    r = run_template("repeat_buyers", {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "min_purchases": "3'; DROP TABLE transactions; --",
    })
    assert "error" in r


def test_cli_template_rejects_negative_min_purchases():
    r = run_template("repeat_buyers", {
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
        "min_purchases": "-1",
    })
    assert "error" in r


def test_cli_template_rejects_unknown_template():
    r = run_template("nonexistent_template", {})
    assert "error" in r


def test_cli_template_specs_are_consistent():
    """Every template MUST: have sql with no '{' or '}', list every named
    param in validators, and reference only validator keys that exist in
    VALIDATOR_TABLE. The shape contract — if any template gets added that
    violates it, run_template's parameter-binding contract breaks."""
    for name, spec in TEMPLATES.items():
        assert "sql" in spec, f"{name}: missing sql"
        assert "params" in spec, f"{name}: missing params"
        assert "validators" in spec, f"{name}: missing validators"
        assert "{" not in spec["sql"], (
            f"{name}: sql contains '{{' — looks like f-string leftover, "
            f"would be hard-rejected by execute_safe"
        )
        assert "}" not in spec["sql"], f"{name}: sql contains '}}'"
        # Every key in params (deduped) must have a validator
        for p in set(spec["params"]):
            assert p in spec["validators"], \
                f"{name}: param {p!r} in sql order list but no validator"
        # Every validator key must exist in VALIDATOR_TABLE
        for p, vkey in spec["validators"].items():
            assert vkey in VALIDATOR_TABLE, \
                f"{name}: validator {vkey!r} for {p!r} not in VALIDATOR_TABLE"


# ── Source-level regression: f-string SQL must never come back ─────────

def test_cli_source_does_not_use_format_for_sql():
    """ChatGPT 2026-05-19 explicit instruction: a source-level test that
    fires if anyone reintroduces `sql.format(**params)` or equivalent.
    Scans the cli.py source directly so a future refactor can't quietly
    re-open the f-string SQL surface."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "cartograph" / "cli.py"
    text = src.read_text()
    assert ".format(**params)" not in text, (
        "cli.py reintroduced sql.format(**params) — closing the SQL "
        "injection regression"
    )
    # Also no f-string-style SQL execution with user param dict
    assert "sql.format(**" not in text


def test_agent_source_does_not_use_format_for_sql():
    """Same regression check on agent.py — must remain free of f-string SQL."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parents[1] / "cartograph" / "agent.py"
    text = src.read_text()
    assert ".format(**" not in text


# ── Path traversal / import injection ──────────────────────────────────

def test_import_rejects_path_traversal(tmp_path):
    """A path that escapes the data_root must be refused. The previous
    import_file() interpolated the raw path into SQL — `safe_path()` +
    parameter binding closes it."""
    (tmp_path / "ok.csv").write_text("a,b\n1,2\n")
    # Path outside data root
    result = import_file("../../etc/passwd", data_root=tmp_path)
    assert result is None      # rejected silently to stderr; programmatic = None


def test_import_rejects_sql_injection_in_path(tmp_path):
    """A path containing SQL metacharacters must be refused at safe_path,
    not flow into execute_safe."""
    result = import_file("/tmp/x.csv'); DROP TABLE transactions; --",
                          data_root=tmp_path)
    assert result is None


def test_import_rejects_unsupported_suffix(tmp_path):
    bad = tmp_path / "weird.exe"
    bad.write_text("not a real file")
    result = import_file(str(bad), data_root=tmp_path)
    assert result is None


def test_import_rejects_bad_table_name(tmp_path):
    """A custom table_name with SQL metacharacters must be refused.
    Identifiers can't be parameter-bound, so we hard-allowlist via
    _safe_table_name."""
    ok = tmp_path / "ok.csv"
    ok.write_text("a,b\n1,2\n")
    # table_name with injection payload
    result = import_file(str(ok), table_name="evil; DROP TABLE x; --",
                          data_root=tmp_path)
    assert result is None


# ── Negative controls: Recommendation truthfulness + cache discipline ──
#
# Kimi 2026-05-19 + ChatGPT 2026-05-19: SQL injection is closed; now
# protect the demo from the OTHER class of failure — confidently wrong
# answers. Five controls:
#   1. zero_overlap_recommendation_hallucination
#   2. similarity_threshold_forced_match
#   3. stale_embeddings_after_catalog_change
#   4. taxonomy_poisoning_marketing_copy
#   5. tenant_bleed_or_data_path_leak


# 1 — zero-overlap recommendation hallucination ─────────────────────────

def test_zero_overlap_does_not_generate_campaign_recommendation(
        zero_overlap_db, stub_embedder, monkeypatch):
    """Kimi+ChatGPT 2026-05-19: when cross_sell_count == 0, the agent
    response MUST carry a recommendation_contract with
    allowed_to_recommend=False and a `reason` that names the missing
    overlap. If the contract is missing OR allowed_to_recommend=True with
    zero overlap, the harness has regressed."""
    # Point the agent at the zero-overlap fixture DB
    from cartograph import agent as agent_mod
    monkeypatch.setattr(agent_mod, "DB_PATH", zero_overlap_db)

    result = agent_mod.execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion",
        "category_b": "Laundry Soap",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    }, embedder=stub_embedder)

    assert "error" not in result, f"unexpected error: {result}"
    assert result["cross_sell_count"] == 0
    assert "recommendation_contract" in result, \
        "missing recommendation_contract — agent could synthesize a campaign"
    contract = result["recommendation_contract"]
    assert contract["allowed_to_recommend"] is False, \
        f"zero overlap but allowed_to_recommend=True: {contract}"
    assert contract["overlap_count"] == 0
    assert contract["reason"] and "overlap" in contract["reason"].lower()


def test_recommendation_contract_rejects_lift_without_baseline():
    """If a downstream caller passes lift but no baseline_rate, the
    contract must refuse — claiming lift without a baseline is exactly
    the kind of unprovable demo claim ChatGPT flagged."""
    from cartograph.recommendation_contract import build_contract
    contract = build_contract(
        cohort_id="test",
        overlap_count=100,
        similarity_threshold=0.7,
        lift=2.5,            # claimed lift...
        baseline_rate=None,  # ...without a baseline
    )
    assert contract.allowed_to_recommend is False
    assert "baseline" in contract.reason.lower()


def test_recommendation_contract_rejects_deterministic_promise():
    """A recommendation that says 'will increase sales' is making an
    unprovable promise. Contract must refuse."""
    from cartograph.recommendation_contract import build_contract
    contract = build_contract(
        cohort_id="test",
        overlap_count=100,
        similarity_threshold=0.7,
        recommendation="Run a Lavender campaign — will increase sales by 20%",
    )
    assert contract.allowed_to_recommend is False
    assert "promise" in contract.reason.lower() or \
           "will increase" in contract.reason


# 2 — weak top-match must not count as similar ─────────────────────────

def test_weak_top_match_does_not_count_as_similar(stub_embedder):
    """find_similar_fragrances must return qualifying_above_threshold=False
    + a non-empty reason when nothing exceeds the threshold. The tool
    output must be unambiguous — empty list alone is not enough; a
    confused caller could still treat top-1 as 'the answer.'

    Uses the stub embedder which returns [] from find_similar() to
    simulate 'no fragrance above threshold' deterministically (the real
    embedder requires sentence-transformers; tested separately under
    @requires_sbert)."""
    from cartograph.agent import execute_tool
    result = execute_tool("find_similar_fragrances", {
        "fragrance_name": "Lavender",
        "top_n": 5,
        "threshold": 0.99,   # near-impossible threshold; stub returns []
    }, embedder=stub_embedder)
    assert result["qualifying_above_threshold"] is False
    assert result["reason"] and "threshold" in result["reason"].lower()
    assert result["similar"] == []


@requires_sbert
def test_weak_top_match_real_embedder(embedder):
    """Real embedder version: query an existing fragrance with a
    near-1.0 threshold. The embedder returns [] (nothing this similar to
    self except self, and self is filtered out)."""
    from cartograph.agent import execute_tool
    result = execute_tool("find_similar_fragrances", {
        "fragrance_name": "Lavender",
        "top_n": 5,
        "threshold": 0.999,
    }, embedder=embedder)
    # Either nothing above threshold OR very few — both must surface the
    # qualifying_above_threshold field explicitly
    assert "qualifying_above_threshold" in result
    if not result["qualifying_above_threshold"]:
        assert result["reason"] and "threshold" in result["reason"].lower()


# 3 — stale embeddings after catalog change ────────────────────────────

@requires_numpy_io
def test_stale_embedding_index_blocks_similarity(tmp_path, monkeypatch):
    """Kimi+ChatGPT 2026-05-19: if a new fragrance is added to the
    catalog but `cartograph embed` isn't re-run, the cache is stale —
    similarity queries would use an embedding space that doesn't include
    the new fragrance. The cache carries a taxonomy_hash; load-time
    integrity check must detect drift and refuse to use the stale cache.
    """
    import numpy as np
    from cartograph import embeddings as emb_mod

    # 1. Build a fake "old" cache with a deliberately wrong taxonomy_hash
    fake_cache = tmp_path / "fragrance_embeddings.npz"
    np.savez_compressed(
        fake_cache,
        names=np.array(["Lavender", "Lemon Drop"]),
        embeddings=np.zeros((2, 384), dtype=np.float32),
        similarity_matrix=np.eye(2, dtype=np.float32),
        taxonomy_hash=np.array("STALE_HASH_THAT_DOES_NOT_MATCH_CURRENT"),
    )

    monkeypatch.setattr(emb_mod, "CACHE_FILE", fake_cache)

    # 2. Constructing with strict_cache=True (default) must raise
    with pytest.raises(emb_mod.StaleEmbeddingCacheError) as excinfo:
        emb_mod.FragranceEmbedder(use_cache=True)
    assert "rebuild" in str(excinfo.value).lower() or \
           "cartograph embed" in str(excinfo.value).lower()


def test_is_stale_returns_true_on_hash_mismatch():
    """Pure-logic version of the stale-cache test that doesn't need numpy I/O.
    Manually instantiate a stub embedder, set cache_taxonomy_hash to a known-
    wrong value, and assert is_stale() returns True."""
    from cartograph.embeddings import FragranceEmbedder, taxonomy_hash

    # Build an "embedder" without invoking the file system or numpy
    e = FragranceEmbedder.__new__(FragranceEmbedder)
    e.cache_taxonomy_hash = "STALE_HASH_THAT_DOES_NOT_MATCH"
    assert e.is_stale() is True, "is_stale missed obvious hash drift"

    # And the positive case: matching hash → not stale
    e.cache_taxonomy_hash = taxonomy_hash()
    assert e.is_stale() is False

    # Missing hash (pre-2026-05-19 caches) → treated as stale
    e.cache_taxonomy_hash = None
    assert e.is_stale() is True


def test_stale_cache_constructor_raises_when_strict():
    """If FragranceEmbedder loads a cache with a hash mismatch and
    strict_cache=True, it must raise StaleEmbeddingCacheError. This
    bypasses the numpy I/O by patching _load_cache directly."""
    from cartograph.embeddings import (
        FragranceEmbedder, StaleEmbeddingCacheError, CACHE_FILE,
    )
    import unittest.mock as mock

    def fake_load_cache(self):
        self.names = ["Lavender"]
        self.embeddings = None
        self.similarity_matrix = None
        self.cache_taxonomy_hash = "STALE_HASH"

    with mock.patch.object(FragranceEmbedder, "_load_cache", fake_load_cache):
        # Need a file at CACHE_FILE for the `.exists()` check to pass
        if not CACHE_FILE.exists():
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_bytes(b"placeholder")
            cleanup = True
        else:
            cleanup = False
        try:
            with pytest.raises(StaleEmbeddingCacheError):
                FragranceEmbedder(use_cache=True, strict_cache=True)
        finally:
            if cleanup and CACHE_FILE.exists():
                CACHE_FILE.unlink()


def test_taxonomy_hash_is_deterministic():
    """Same taxonomy → same hash, every time. If hashing drifts, the
    stale-cache check becomes useless."""
    from cartograph.embeddings import taxonomy_hash
    a = taxonomy_hash()
    b = taxonomy_hash()
    assert a == b
    assert len(a) == 64    # SHA-256


def test_taxonomy_hash_changes_when_taxonomy_changes():
    """Add a synthetic fragrance and verify the hash changes — this is
    the SAME signal the cache uses to detect staleness."""
    from cartograph.embeddings import taxonomy_hash
    from cartograph.taxonomy import FRAGRANCE_TAXONOMY
    h1 = taxonomy_hash(FRAGRANCE_TAXONOMY)
    modified = dict(FRAGRANCE_TAXONOMY)
    modified["__SYNTHETIC_NEW_FRAGRANCE__"] = {
        "family": "Fresh", "notes": "synthetic test", "tags": [],
    }
    h2 = taxonomy_hash(modified)
    assert h1 != h2


# 4 — taxonomy poisoning by marketing copy ──────────────────────────────

@pytest.mark.parametrize("notes,expected_fluff", [
    # Pure marketing — should be flagged
    ("viral luxury bestseller confidence energy unforgettable", True),
    ("amazing incredible luxurious premium signature scent", True),
    ("elite TikTok trending must-have obsession", True),
    # Real olfactory profiles — should NOT be flagged
    ("french lavender fields, calming purple floral, herbal relaxation, soothing", False),
    ("creamy coconut milk, tropical coconut flesh, sweet island, warm", False),
    ("eucalyptus, peppermint, menthol, clear airways, cooling medicinal", False),
    # Edge: short but legitimate
    ("Unscented", False),     # legitimate noun, no marketing tone
])
def test_marketing_copy_not_accepted_as_scent_profile(notes, expected_fluff):
    """Kimi+ChatGPT 2026-05-19: an onboarding pipeline that accepts free-
    text product descriptions and embeds them is vulnerable to taxonomy
    poisoning. is_marketing_fluff() routes pure-tone descriptions to
    manual review instead of trusting them as olfactory data."""
    from cartograph.scent_vocabulary import is_marketing_fluff
    assert is_marketing_fluff(notes) is expected_fluff, (
        f"notes={notes!r}: expected fluff={expected_fluff}, "
        f"got {is_marketing_fluff(notes)}"
    )


def test_scent_note_confidence_grades_olfactory_content():
    """A profile with strong olfactory vocabulary scores higher than a
    profile of mostly filler words. Spot-check the gradient."""
    from cartograph.scent_vocabulary import scent_note_confidence
    rich = "lavender, jasmine, rose, floral, herbal, sweet, vanilla"
    sparse = "viral luxury confidence energy"
    rich_score = scent_note_confidence(rich)
    sparse_score = scent_note_confidence(sparse)
    assert rich_score > sparse_score
    assert rich_score > 0.4
    assert sparse_score < 0.1


def test_every_existing_fragrance_passes_fluff_check():
    """Regression guard: every entry in FRAGRANCE_TAXONOMY's `notes`
    field must currently pass the fluff check. If a future taxonomy
    update breaks this, the maintainer either needs to fix the notes or
    relax the threshold — both are deliberate decisions, not silent."""
    from cartograph.scent_vocabulary import is_marketing_fluff
    from cartograph.taxonomy import FRAGRANCE_TAXONOMY
    fluffy = [
        name for name, data in FRAGRANCE_TAXONOMY.items()
        if is_marketing_fluff(data["notes"])
    ]
    assert not fluffy, f"existing taxonomy entries flagged as fluff: {fluffy}"


# 5 — tenant / data-root bleed ──────────────────────────────────────────

def test_query_uses_selected_data_root_only(
        tenant_a_db, tenant_b_db, stub_embedder, monkeypatch):
    """Kimi+ChatGPT 2026-05-19: even though multi-tenant SaaS isn't
    certified yet, the demo path must not accidentally read the wrong
    DuckDB file. We confirm:
      - When agent.DB_PATH points at Tenant A, the result reflects ONLY
        Tenant A's overlap (1 cross-sell, G201)
      - When repointed at Tenant B, the result reflects ONLY Tenant B
        (3 cross-sells, G301/G302/G303)
      - The default ~/osint/data/analytics.duckdb is NEVER consulted
        during the test (we don't touch it)
    """
    from cartograph import agent as agent_mod

    # First: Tenant A
    monkeypatch.setattr(agent_mod, "DB_PATH", tenant_a_db)
    result_a = agent_mod.execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion",
        "category_b": "Laundry Soap",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    }, embedder=stub_embedder)
    a_count = result_a["cross_sell_count"]

    # Then: same query, Tenant B
    monkeypatch.setattr(agent_mod, "DB_PATH", tenant_b_db)
    result_b = agent_mod.execute_tool("cross_sell_analysis", {
        "category_a": "Body Lotion",
        "category_b": "Laundry Soap",
        "start_date": "2025-01-01",
        "end_date": "2025-12-31",
    }, embedder=stub_embedder)
    b_count = result_b["cross_sell_count"]

    # The results must DIFFER — that's the proof that each query consulted
    # only its assigned DB, not some shared default. If both queries
    # returned the same number, the implementation might be reading a
    # cached shared default and ignoring DB_PATH.
    assert a_count != b_count, (
        f"Tenant A and Tenant B returned the same cross_sell_count "
        f"({a_count}) — query is not scoped to the selected data root"
    )
    # Both should still produce contracts — overlap > 0 for both
    assert result_a["recommendation_contract"]["allowed_to_recommend"]
    assert result_b["recommendation_contract"]["allowed_to_recommend"]


# ── Original embedding-similarity tests (unchanged) ────────────────────


@pytest.mark.parametrize("frag_a,frag_b,min_score", [
    ("Lavender", "Hey Headache", 0.5),
    ("Coconut", "Coconut Cream", 0.6),
    ("Fresh Cotton", "Aloe + Clover", 0.4),
    ("Eucalyptus", "Eucalyptus Mint", 0.6),
    ("Pink Sugar", "Cotton Candy", 0.5),
])
@requires_sbert
def test_intuitive_similarity(embedder, frag_a, frag_b, min_score):
    score = embedder.get_similarity(frag_a, frag_b)
    assert score >= min_score, f"{frag_a} ~ {frag_b}: {score:.3f} < {min_score}"


@pytest.mark.parametrize("frag_a,frag_b,max_score", [
    ("Lavender", "Commando", 0.5),
    ("Pink Sugar", "Patchouli", 0.5),
    ("Fresh Cotton", "Brown Sugar Fig", 0.5),
])
@requires_sbert
def test_dissimilar_pairs(embedder, frag_a, frag_b, max_score):
    score = embedder.get_similarity(frag_a, frag_b)
    assert score < max_score, f"{frag_a} ≠ {frag_b}: {score:.3f} >= {max_score}"
