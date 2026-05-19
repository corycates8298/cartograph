"""scent_vocabulary.py — Olfactory vocabulary + marketing-fluff detector.

ChatGPT 2026-05-19 negative control: a scent note profile of
"viral luxury bestseller confidence energy" contains zero olfactory
content. Embedding that produces a vector that captures marketing tone,
not scent. If a customer onboarding pipeline accepts arbitrary product
descriptions and embeds them, taxonomy poisoning slips in.

This module gives the embedder a way to score each note profile's
olfactory content. Below threshold = low-confidence — route to manual
review, do not embed as a valid scent profile.

The vocabulary is intentionally narrow and grounded in real perfumery
terms. Adding marketing words here (e.g. "luxury", "elegant") would
defeat the purpose of the check.
"""

from __future__ import annotations

import re


# Core olfactory vocabulary. Grouped by family for readability — the
# detector flattens the whole set for membership checks.
SCENT_TERMS: frozenset[str] = frozenset({
    # Floral family
    "rose", "jasmine", "lavender", "magnolia", "lily", "peony", "honeysuckle",
    "violet", "iris", "tuberose", "neroli", "ylang", "gardenia", "lilac",
    "freesia", "carnation", "hibiscus", "orchid", "cherry blossom", "sakura",
    "blossom", "petal", "petals", "floral", "bouquet",
    # Citrus family
    "lemon", "lime", "orange", "grapefruit", "bergamot", "mandarin",
    "tangerine", "yuzu", "citron", "citrus", "zest", "rind",
    # Fresh / aquatic
    "aquatic", "ocean", "sea", "salt", "marine", "ozone", "ozonic",
    "aldehyde", "aldehydes", "cotton", "linen", "clean", "crisp",
    "dewy", "morning", "breeze", "air", "airy",
    # Woody / earthy
    "sandalwood", "cedar", "cedarwood", "oud", "patchouli", "vetiver",
    "amber", "musk", "leather", "smoke", "smoky", "wood", "woody",
    "moss", "oakmoss", "earthy", "balsam", "fir", "pine",
    # Sweet / gourmand
    "vanilla", "caramel", "sugar", "sugary", "honey", "honeyed",
    "chocolate", "cocoa", "coffee", "almond", "marzipan", "cinnamon",
    "spice", "spicy", "nutmeg", "clove", "tonka", "praline", "bakery",
    "cream", "creamy", "buttercream", "syrup",
    # Herbal / green
    "eucalyptus", "mint", "peppermint", "spearmint", "menthol", "rosemary",
    "sage", "thyme", "basil", "tea", "matcha", "chamomile", "verbena",
    "green", "grass", "leaf", "leaves", "fern", "botanical", "herbal",
    "aloe", "clover",
    # Fruity (non-citrus)
    "apple", "pear", "peach", "apricot", "plum", "berry", "strawberry",
    "raspberry", "blackberry", "blueberry", "currant", "cherry", "fig",
    "mango", "pineapple", "coconut", "guava", "passionfruit", "passion",
    "melon", "watermelon", "tropical", "juicy", "fruit", "fruity",
    # Texture / character notes that name a scent property
    "tart", "sweet", "bitter", "warm", "cool", "cooling", "fresh",
    "soft", "powdery", "rich", "deep", "light", "bright", "milky",
})


# Loose marketing-tone words that, if dominant, indicate poisoning.
_MARKETING_TONE: frozenset[str] = frozenset({
    "viral", "bestseller", "trending", "tiktok", "instagram", "luxury",
    "luxurious", "premium", "exclusive", "limited", "elite",
    "confidence", "energy", "unforgettable", "iconic", "signature",
    "celebrity", "influencer", "must-have", "obsession", "addictive",
    "elegant", "feminine", "masculine",      # OK as descriptors but not as the WHOLE profile
    "amazing", "incredible", "stunning", "perfect",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase + split on non-alphanumeric. Keep "cherry blossom" intact
    by also matching the raw text against multi-word terms in SCENT_TERMS."""
    return re.findall(r"[a-z]+(?:\s+[a-z]+)?", text.lower())


def scent_note_confidence(notes_text: str) -> float:
    """Score the olfactory content of a notes string in [0, 1].

    Counts matches against SCENT_TERMS / total non-stopword tokens. A
    profile of "lavender, herbal, calming, soothing" scores ~0.5; a
    profile of "viral luxury confidence" scores 0.0.

    The exact ratio is a heuristic — the caller compares against a
    threshold (default 0.25 in is_marketing_fluff)."""
    if not isinstance(notes_text, str) or not notes_text.strip():
        return 0.0

    lower = notes_text.lower()

    # First pass: count multi-word scent terms (e.g. "cherry blossom")
    multi_word_hits = 0
    multi_word_terms = [t for t in SCENT_TERMS if " " in t]
    for term in multi_word_terms:
        if term in lower:
            multi_word_hits += lower.count(term)

    # Second pass: single-word tokens
    tokens = re.findall(r"[a-z]+", lower)
    if not tokens:
        return 0.0
    single_word_terms = {t for t in SCENT_TERMS if " " not in t}
    single_word_hits = sum(1 for t in tokens if t in single_word_terms)

    total_hits = multi_word_hits + single_word_hits
    # Denominator: total tokens (gives the ratio of scent vocab to all words)
    return min(1.0, total_hits / max(len(tokens), 1))


def is_marketing_fluff(notes_text: str, threshold: float = 0.25) -> bool:
    """True if the note profile is dominated by marketing tone / has too
    little olfactory content to embed as a scent profile.

    Two-trigger logic:
      1. scent_note_confidence < threshold (sparse olfactory content), AND
      2. at least one marketing-tone word is present
    A profile of "Unscented" returns False (low scent vocab but no
    marketing tone — it's a legitimate noun). A profile of "viral
    luxury bestseller" returns True.
    """
    if not isinstance(notes_text, str) or not notes_text.strip():
        return True   # empty profile = fluff by default
    confidence = scent_note_confidence(notes_text)
    if confidence >= threshold:
        return False
    lower = notes_text.lower()
    has_marketing = any(m in lower for m in _MARKETING_TONE)
    return has_marketing
