#!/usr/bin/env python3
"""
Red Team Tests for Cartograph v2.0
Tests edge cases, adversarial inputs, and correctness.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cartograph.taxonomy import FRAGRANCE_TAXONOMY, get_notes, get_family
from cartograph.embeddings import FragranceEmbedder
from cartograph.agent import execute_tool

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
results = []


def test(name, condition):
    status = PASS if condition else FAIL
    results.append(condition)
    print(f"  {status} {name}")
    return condition


def run_tests():
    print("\n  ═══════════════════════════════════════════")
    print("  RED TEAM TESTS — Cartograph v2.0")
    print("  ═══════════════════════════════════════════\n")

    # ── Phase 1: Taxonomy Tests ────────────────────────────
    print("  ── Taxonomy ──")

    test("All fragrances have notes", all(
        "notes" in v for v in FRAGRANCE_TAXONOMY.values()
    ))
    test("All fragrances have family", all(
        "family" in v for v in FRAGRANCE_TAXONOMY.values()
    ))
    test("All notes are non-empty strings", all(
        len(v["notes"]) > 10 for v in FRAGRANCE_TAXONOMY.values()
    ))
    test("Case-insensitive lookup works", get_notes("lavender") is not None)
    test("Plus variant strips correctly", get_notes("Lavender  Plus") is not None)
    test("Unknown fragrance returns None", get_notes("ZZZZ_NOT_REAL") is None)
    test("Family lookup works", get_family("Lavender") == "Floral")
    test("8 scent families defined", len(set(v["family"] for v in FRAGRANCE_TAXONOMY.values())) == 8)

    # ── Phase 2: Embedding Tests ───────────────────────────
    print("\n  ── Embeddings ──")

    embedder = FragranceEmbedder(use_cache=True)

    test("Embeddings loaded", embedder.embeddings is not None)
    test("Correct dimension (384)", embedder.embeddings.shape[1] == 384)
    test("All fragrances embedded", len(embedder.names) == len(FRAGRANCE_TAXONOMY))
    test("Self-similarity is ~1.0", abs(embedder.get_similarity("Lavender", "Lavender") - 1.0) < 0.01)

    # Sanity: same family should be more similar than distant families
    same_family = embedder.get_similarity("Eucalyptus", "Lemongrass + Eucalyptus")
    diff_family = embedder.get_similarity("Eucalyptus", "Pink Sugar")
    test(f"Same family > diff family ({same_family:.3f} > {diff_family:.3f})", same_family > diff_family)

    same_family2 = embedder.get_similarity("Coconut", "Coconut Cream")
    diff_family2 = embedder.get_similarity("Coconut", "Commando")
    test(f"Coconut ~ Coconut Cream ({same_family2:.3f} > {diff_family2:.3f})", same_family2 > diff_family2)

    # Edge cases
    test("Unknown fragrance returns empty list", embedder.find_similar("FAKE_SCENT_999") == [])
    test("Threshold=1.0 returns nothing", len(embedder.find_similar("Lavender", threshold=1.0)) == 0)
    test("Top_n=0 returns empty", len(embedder.find_similar("Lavender", top_n=0)) == 0)
    test("get_similar_set returns list", isinstance(embedder.get_similar_set("Lavender", threshold=0.5), list))

    # ── Phase 3: Agent Tool Safety ─────────────────────────
    print("\n  ── Agent Safety ──")

    # SQL injection attempts
    result = execute_tool("query_transactions", {"sql": "DROP TABLE transactions"})
    test("Blocks DROP TABLE", "error" in result)

    result = execute_tool("query_transactions", {"sql": "DELETE FROM transactions WHERE 1=1"})
    test("Blocks DELETE", "error" in result)

    result = execute_tool("query_transactions", {"sql": "INSERT INTO transactions VALUES ('x','x','x','x','x',0,0,'x','x')"})
    test("Blocks INSERT", "error" in result)

    result = execute_tool("query_transactions", {"sql": "UPDATE transactions SET price=0"})
    test("Blocks UPDATE", "error" in result)

    # Valid query works
    result = execute_tool("query_transactions", {"sql": "SELECT COUNT(*) as cnt FROM transactions LIMIT 1"})
    test("Allows SELECT", "data" in result and result["data"][0]["cnt"] > 0)

    # Tool with missing embedder
    result = execute_tool("find_similar_fragrances", {"fragrance_name": "Lavender"}, embedder=None)
    test("Handles missing embedder gracefully", "error" in result)

    # Unknown tool
    result = execute_tool("nonexistent_tool", {})
    test("Handles unknown tool", "error" in result)

    # ── Phase 4: Similarity Quality ───────────────────────
    print("\n  ── Similarity Quality ──")

    # These should intuitively be similar
    intuitive_pairs = [
        ("Lavender", "Hey Headache", 0.5),     # Both calming/herbal
        ("Coconut", "Coconut Cream", 0.6),      # Obviously related
        ("Fresh Cotton", "Aloe + Clover", 0.4), # Both fresh/clean
        ("Eucalyptus", "Eucalyptus Mint", 0.6), # Same base ingredient
        ("Pink Sugar", "Cotton Candy", 0.5),    # Both sweet/candy
    ]

    for frag_a, frag_b, min_score in intuitive_pairs:
        score = embedder.get_similarity(frag_a, frag_b)
        test(f"{frag_a} ~ {frag_b} ({score:.3f} >= {min_score})", score >= min_score)

    # These should NOT be very similar
    dissimilar_pairs = [
        ("Lavender", "Commando", 0.5),          # Floral vs. rugged masculine
        ("Pink Sugar", "Patchouli", 0.5),        # Candy vs. earthy
        ("Fresh Cotton", "Brown Sugar Fig", 0.5), # Clean vs. gourmand
    ]

    for frag_a, frag_b, max_score in dissimilar_pairs:
        score = embedder.get_similarity(frag_a, frag_b)
        test(f"{frag_a} ≠ {frag_b} ({score:.3f} < {max_score})", score < max_score)

    # ── Summary ────────────────────────────────────────────
    passed = sum(results)
    total = len(results)
    print(f"\n  ═══════════════════════════════════════════")
    pct = passed / total * 100
    color = "\033[92m" if pct >= 90 else "\033[93m" if pct >= 70 else "\033[91m"
    print(f"  {color}Results: {passed}/{total} passed ({pct:.0f}%)\033[0m")
    print(f"  ═══════════════════════════════════════════\n")

    return passed == total


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
