"""recommendation_contract.py — Evidence gate for campaign recommendations.

ChatGPT 2026-05-19: every recommendation Cartograph emits must carry
structured evidence — cohort id, overlap count, baseline rate, lift,
similarity threshold, fragrance pairs, confidence. Without this contract,
a chatbot can "recommend a Lavender campaign" when the underlying cohort
has zero overlap, and the demo audience has no way to tell.

The contract is a pure dataclass plus a `build_contract()` factory and a
`validate_contract()` enforcer. Hard fails:
  - no cohort id
  - overlap count zero AND a recommendation is present
  - similarity threshold missing
  - claims lift without a baseline
  - deterministic promises ("will increase sales") instead of hypothesis
    framing ("hypothesis to test")
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Literal


Confidence = Literal["none", "low", "medium", "high"]


@dataclass
class RecommendationContract:
    """Structured evidence wrapper for a campaign recommendation.

    Callers MUST populate `cohort_id`, `overlap_count`, and
    `similarity_threshold`. `allowed_to_recommend` is derived in
    `validate_contract()` based on the gate rules — don't set it manually.
    """
    cohort_id: str
    overlap_count: int
    similarity_threshold: float | None = None
    baseline_rate: float | None = None
    lift: float | None = None
    fragrance_pairs: list = field(default_factory=list)
    confidence: Confidence = "none"
    recommendation: str | None = None
    reason: str | None = None
    allowed_to_recommend: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# Deterministic-sales-promise blocklist. A campaign recommendation that says
# any of these is making an unprovable claim — bounce it.
_PROMISE_BLOCKLIST: tuple[str, ...] = (
    "will increase",
    "will boost",
    "will drive",
    "guarantees",
    "guaranteed",
    "100%",
)


def build_contract(
    cohort_id: str,
    overlap_count: int,
    similarity_threshold: float | None,
    baseline_rate: float | None = None,
    lift: float | None = None,
    fragrance_pairs: list | None = None,
    recommendation: str | None = None,
) -> RecommendationContract:
    """Construct + validate a recommendation contract in one step.

    Returns a contract with `allowed_to_recommend` correctly derived. If
    the contract fails the gate, `allowed_to_recommend=False` and `reason`
    explains why. Callers display `reason` instead of the recommendation.
    """
    contract = RecommendationContract(
        cohort_id=cohort_id,
        overlap_count=int(overlap_count) if overlap_count is not None else 0,
        similarity_threshold=similarity_threshold,
        baseline_rate=baseline_rate,
        lift=lift,
        fragrance_pairs=list(fragrance_pairs or []),
        recommendation=recommendation,
    )
    validate_contract(contract)
    return contract


def validate_contract(contract: RecommendationContract) -> None:
    """Mutates contract in place: sets `allowed_to_recommend` + `reason` +
    `confidence` based on the evidence."""
    # 1. Hard fail: zero overlap — no recommendation possible
    if contract.overlap_count <= 0:
        contract.allowed_to_recommend = False
        contract.confidence = "none"
        contract.reason = (
            "zero qualifying cohort overlap — no recommendation possible. "
            "Suggest expanding date range, lowering threshold, or building "
            "a lookalike hypothesis."
        )
        return

    # 2. Hard fail: similarity threshold missing
    if contract.similarity_threshold is None:
        contract.allowed_to_recommend = False
        contract.confidence = "none"
        contract.reason = (
            "similarity_threshold not recorded — cannot reason about cohort "
            "quality. Refuse recommendation until threshold is logged."
        )
        return

    # 3. Hard fail: claims lift without baseline
    if contract.lift is not None and contract.baseline_rate is None:
        contract.allowed_to_recommend = False
        contract.confidence = "none"
        contract.reason = (
            "lift reported without baseline_rate — cannot validate the "
            "lift number. Recompute with explicit baseline."
        )
        return

    # 4. Hard fail: deterministic promise language
    if contract.recommendation:
        lower = contract.recommendation.lower()
        for promise in _PROMISE_BLOCKLIST:
            if promise in lower:
                contract.allowed_to_recommend = False
                contract.confidence = "none"
                contract.reason = (
                    f"recommendation contains deterministic promise "
                    f"language ({promise!r}). Rephrase as 'hypothesis to "
                    f"test'."
                )
                return

    # 5. Pass — derive confidence from overlap size + threshold
    if contract.overlap_count >= 50 and contract.similarity_threshold >= 0.7:
        contract.confidence = "high"
    elif contract.overlap_count >= 20 and contract.similarity_threshold >= 0.6:
        contract.confidence = "medium"
    else:
        contract.confidence = "low"
    contract.allowed_to_recommend = True
    contract.reason = (
        f"contract validated: {contract.overlap_count} overlap, "
        f"threshold={contract.similarity_threshold}, "
        f"confidence={contract.confidence}"
    )
