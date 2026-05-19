"""Tests for cartograph_harness — the formal certification runner.

Mirrors harness/tests/test_certify.py from the FDI side. Pins:
  - 6 suites collected
  - negative_controls is LAST
  - readiness is derived from evidence, not asserted
  - scorer manifest is deterministic
  - certificate body self-fingerprint round-trips
  - the runner refuses to claim AGENT_SAFETY_CERTIFIED when env skips exist
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from cartograph_harness.certify import (
    CartographConformanceCertification, CertificationReport, _derive_readiness,
)
from cartograph_harness.readiness import (
    ReadinessLabel, READINESS_ORDER, EVIDENCE_GATES,
)
from cartograph_harness.scorer_manifest import (
    build_manifest, fingerprint_certificate_body,
)


def test_readiness_ladder_has_seven_rungs():
    """7-rung ladder (vs FDI's 12). REPO_BASELINE → PILOT_READY."""
    assert len(READINESS_ORDER) == 7
    assert READINESS_ORDER[0] == ReadinessLabel.REPO_BASELINE_CAPTURED
    assert READINESS_ORDER[-1] == ReadinessLabel.PILOT_READY


def test_every_rung_has_evidence_gate():
    """A rung without an evidence gate would be unprovable. Pin the
    contract so the ladder can't drift out of sync with the runner."""
    for rung in READINESS_ORDER:
        assert rung.name in EVIDENCE_GATES, f"no evidence gate for {rung.name}"
        assert EVIDENCE_GATES[rung.name], f"empty evidence gate for {rung.name}"


@pytest.fixture(scope="module")
def report():
    """Run the full suite once — expensive (kicks off pytest subprocesses).
    Reused across all assertions in this file."""
    cert = CartographConformanceCertification(tool_version="test")
    return cert.run_suite()


def test_six_suites_run(report):
    suite_names = [r.suite for r in report.results]
    assert len(suite_names) == 6
    # Order matters — negative_controls LAST
    assert suite_names[-1] == "negative_controls"


def test_data_model_suite_passes(report):
    dm = next(r for r in report.results if r.suite == "data_model")
    assert dm.passed
    assert dm.evidence.get("eight_scent_families_present") is True
    assert dm.evidence.get("fragrance_count") > 0


def test_query_safety_evidence_present(report):
    qs = next(r for r in report.results if r.suite == "query_safety")
    assert qs.evidence.get("agent_no_raw_sql_tool") is True
    assert qs.evidence.get("all_templates_use_placeholders") is True
    assert qs.evidence.get("template_count") == 8


def test_negative_controls_is_truly_last(report):
    """If negative_controls is moved earlier, an upstream suite failure
    might mask a negative-control regression. Pin the order."""
    assert report.results[-1].suite == "negative_controls"


def test_readiness_is_derived_from_evidence_not_asserted(report):
    """The runner must NOT just set readiness to a constant — it must
    derive it from suite evidence. We exercise this by checking that
    the derived readiness reflects what the suites actually proved."""
    qs = next(r for r in report.results if r.suite == "query_safety")
    if qs.passed and qs.evidence.get("agent_no_raw_sql_tool") and \
            qs.evidence.get("all_templates_use_placeholders"):
        # Must reach AT LEAST QUERY_SAFETY_CERTIFIED on this host
        readiness_idx = next(i for i, r in enumerate(READINESS_ORDER)
                             if r.name == report.readiness)
        qs_idx = next(i for i, r in enumerate(READINESS_ORDER)
                      if r.name == "QUERY_SAFETY_CERTIFIED")
        assert readiness_idx >= qs_idx, (
            f"readiness {report.readiness} < QUERY_SAFETY_CERTIFIED "
            f"despite all evidence present"
        )


def test_readiness_does_not_overclaim_with_env_skips(report):
    """Even if every test passed, we must NOT claim AGENT_SAFETY_CERTIFIED
    when env skips exist — those tests didn't actually run."""
    agent = next(r for r in report.results if r.suite == "agent_safety")
    if agent.skipped_count > 0:
        assert report.readiness != "AGENT_SAFETY_CERTIFIED", (
            f"runner claimed AGENT_SAFETY_CERTIFIED despite "
            f"{agent.skipped_count} env-skipped agent_safety tests"
        )


def test_manifest_is_deterministic():
    a = build_manifest()
    b = build_manifest()
    assert a == b


def test_manifest_covers_tool_harness_tests():
    m = build_manifest()
    assert "tool" in m["components"]
    assert "harness" in m["components"]
    assert "tests" in m["components"]
    for c in m["components"].values():
        assert c["file_count"] > 0
        assert len(c["sha256"]) == 64


def test_certificate_body_self_fingerprint_round_trips(report):
    """The certificate body hash must be stable when the inputs are
    unchanged — same drift detector that caught a real bug in the FDI
    harness."""
    body = {"readiness": report.readiness, "tests": 72, "issued_at": "test"}
    h1 = fingerprint_certificate_body(body)
    body["certificate_body_sha256"] = h1
    h2 = fingerprint_certificate_body(body)
    assert h1 == h2, "self-fingerprint contaminated by its own field"


def test_label_says_failed_when_a_suite_fails(report, monkeypatch):
    """If any suite fails, the label MUST contain FAILED. Synthesize a
    failure without re-running the whole suite by mutating a passed
    suite to failed."""
    # Build a fake report with a forced-failed suite
    from dataclasses import replace
    from cartograph_harness.certify import CertificationReport, SuiteResult
    fake = CertificationReport(
        tool_version="test",
        issued_at="test",
        results=[SuiteResult(suite="data_model", passed=False, failed_count=1,
                              failures=["synthetic"]),
                 SuiteResult(suite="negative_controls", passed=True,
                              passed_count=10)],
    )
    assert "FAILED" in fake.label()
    assert "CERTIFIED" not in fake.label()


def test_not_claimed_rungs_remain_unclaimable_on_this_host(report):
    """BUFF_CITY_DEMO_READY and PILOT_READY require evidence the runner
    cannot collect on this host (live Shopify, multi-tenant audit logs,
    healthy demo env). The runner MUST NOT assign them."""
    assert report.readiness not in ("BUFF_CITY_DEMO_READY", "PILOT_READY")
