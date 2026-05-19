"""readiness.py — Cartograph readiness ladder.

Lean 7-rung ladder (vs FDI's 12) because Cartograph's blast radius is
"embarrassment in front of a customer demo," not "client tenant
destruction." Each rung's evidence gate is enforceable in code; rungs
above the current proof must remain unclaimable.

Promotion is one rung at a time. Demotion is always allowed.
"""

from __future__ import annotations

from enum import IntEnum


class ReadinessLabel(IntEnum):
    """Ordered enum — higher value = more validated."""
    REPO_BASELINE_CAPTURED = 0
    DATA_MODEL_VALIDATED = 1
    EMBEDDING_QUALITY_CERTIFIED = 2
    QUERY_SAFETY_CERTIFIED = 3
    AGENT_SAFETY_CERTIFIED = 4
    BUFF_CITY_DEMO_READY = 5
    PILOT_READY = 6

    @classmethod
    def from_str(cls, s: str) -> "ReadinessLabel":
        s = s.strip().upper().replace("-", "_")
        try:
            return cls[s]
        except KeyError:
            raise ValueError(
                f"unknown readiness label: {s!r}. "
                f"Allowed: {[m.name for m in cls]}"
            )

    def __str__(self) -> str:
        return self.name


READINESS_ORDER = [
    ReadinessLabel.REPO_BASELINE_CAPTURED,
    ReadinessLabel.DATA_MODEL_VALIDATED,
    ReadinessLabel.EMBEDDING_QUALITY_CERTIFIED,
    ReadinessLabel.QUERY_SAFETY_CERTIFIED,
    ReadinessLabel.AGENT_SAFETY_CERTIFIED,
    ReadinessLabel.BUFF_CITY_DEMO_READY,
    ReadinessLabel.PILOT_READY,
]


# Per-rung evidence — what must be true for the cert runner to assign each label.
# A rung is assignable iff every key in its `requires` list maps to True in
# the live evidence dict the runner builds at certify time.
EVIDENCE_GATES: dict[str, list[str]] = {
    "REPO_BASELINE_CAPTURED": [
        "repo_tree_captured",
        "tests_collectable",
    ],
    "DATA_MODEL_VALIDATED": [
        "taxonomy_loads",
        "fragrance_count_positive",
        "eight_scent_families_present",
    ],
    "EMBEDDING_QUALITY_CERTIFIED": [
        "philyra_trap_passes",          # note-profile > name-only similarity
        "taxonomy_hash_deterministic",
        "embeddings_built_from_note_profiles_not_names",
    ],
    "QUERY_SAFETY_CERTIFIED": [
        "agent_no_raw_sql_tool",         # query_transactions removed
        "agent_uses_parameter_binding",  # execute_safe everywhere
        "cli_templates_use_placeholders",
        "cli_import_uses_safe_path",
        "interactive_shell_marked_dev_only",
        "source_regression_test_passes",
    ],
    "AGENT_SAFETY_CERTIFIED": [
        "zero_overlap_blocks_recommendation",
        "weak_top_match_blocked",
        "stale_cache_detected",
        "marketing_copy_blocked",
        "tenant_data_root_isolation_verified",
        "recommendation_contract_enforces_evidence",
    ],
    "BUFF_CITY_DEMO_READY": [
        "all_lower_rungs_passed",
        "demo_pipeline_end_to_end_runs",
        "campaign_export_csv_validated",
        "demo_env_healthy",              # numpy + pandas + sbert all importable
    ],
    "PILOT_READY": [
        "multi_tenant_data_root_isolation",
        "import_validation_report",
        "audit_log_present",
        "token_cost_cap",
        "client_data_handling_documented",
    ],
}
