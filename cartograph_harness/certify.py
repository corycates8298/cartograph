"""certify.py — Cartograph Conformance Certification runner.

Mirrors harness/certify.py from the FDI Practice OS pattern:
  - SuiteResult per gate
  - CertificationReport aggregates + emits label
  - Evidence-derived readiness (not asserted, derived from suite results)
  - Negative controls run LAST so a regression always flips all_passed
  - Scorer manifest + certificate body self-fingerprint
  - Explicit NOT-claimed section in the markdown output

Run:
    python3 cartograph_harness/certify.py
or:
    python3 -m cartograph_harness.certify
"""

from __future__ import annotations

import datetime
import hashlib
import json
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cartograph_harness.readiness import (
    ReadinessLabel, READINESS_ORDER, EVIDENCE_GATES,
)
from cartograph_harness.scorer_manifest import (
    build_manifest, fingerprint_certificate_body,
)


@dataclass
class SuiteResult:
    suite: str
    passed: bool = False
    total: int = 0
    passed_count: int = 0
    failed_count: int = 0
    warning_count: int = 0
    skipped_count: int = 0
    failures: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    details: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    timestamp: str = ""


@dataclass
class CertificationReport:
    tool_version: str
    issued_at: str
    results: list[SuiteResult] = field(default_factory=list)
    readiness: str = "REPO_BASELINE_CAPTURED"
    readiness_reason: str = ""

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def total_warnings(self) -> int:
        return sum(r.warning_count for r in self.results)

    @property
    def total_skipped(self) -> int:
        return sum(r.skipped_count for r in self.results)

    @property
    def total_blockers(self) -> int:
        return sum(r.failed_count for r in self.results)

    def label(self) -> str:
        if not self.all_passed:
            return f"CARTOGRAPH-CONFORMANCE FAILED v{self.tool_version}"
        suffix_bits = []
        if self.total_warnings:
            suffix_bits.append(f"{self.total_warnings} WARNINGS")
        if self.total_skipped:
            suffix_bits.append(f"{self.total_skipped} ENV-SKIPPED")
        suffix = f" ({', '.join(suffix_bits)})" if suffix_bits else ""
        return f"CARTOGRAPH-CONFORMANCE CERTIFIED v{self.tool_version}{suffix}"

    def to_json(self) -> str:
        return json.dumps({
            "label": self.label(),
            "tool_version": self.tool_version,
            "issued_at": self.issued_at,
            "all_passed": self.all_passed,
            "readiness": self.readiness,
            "readiness_reason": self.readiness_reason,
            "total_warnings": self.total_warnings,
            "total_skipped": self.total_skipped,
            "total_blockers": self.total_blockers,
            "results": [asdict(r) for r in self.results],
        }, indent=2, default=str)

    def to_markdown(self) -> str:
        lines = [
            f"# {self.label()}",
            "",
            "## At-a-glance status",
            "",
            "| Field | Value |",
            "|-------|-------|",
            f"| Certification label | `{self.label()}` |",
            f"| Readiness rung | `{self.readiness}` |",
            f"| Issued | {self.issued_at} |",
            f"| Tool version | {self.tool_version} |",
            f"| Overall | {'PASS' if self.all_passed else 'FAIL'} |",
            f"| Warnings | {self.total_warnings} |",
            f"| Env-skipped | {self.total_skipped} |",
            f"| Blockers | {self.total_blockers} |",
            f"| Client-ready | **NO** — requires PILOT_READY rung (6/6) |",
            f"| Multi-tenant SaaS validated | **NO** — v3 code exists, not certified |",
            f"| Live Shopify integration | **NO** — no live API has been called |",
            f"| Live OAC equivalent | N/A — Cartograph is DuckDB-native |",
            "",
            "## Readiness reason",
            "",
            self.readiness_reason or "(none)",
            "",
            "## Suite results",
            "",
            "| Suite | Status | Catalogued | Pass | Warn | Skip | Blocker |",
            "|-------|:------:|-----------:|----:|-----:|-----:|--------:|",
        ]
        for r in self.results:
            flag = "✓" if r.passed else "✗"
            if r.warning_count > 0 and r.failed_count == 0:
                flag = "⚠️"
            lines.append(
                f"| {r.suite} | {flag} | {r.total} | {r.passed_count} | "
                f"{r.warning_count} | {r.skipped_count} | {r.failed_count} |"
            )
        if self.total_skipped:
            lines += ["", "## Env-skipped tests", ""]
            for r in self.results:
                for w in r.warnings:
                    if w.startswith("SKIP:"):
                        lines.append(f"- **{r.suite}**: {w[5:].strip()}")
        if self.total_blockers:
            lines += ["", "## Failures", ""]
            for r in self.results:
                for f in r.failures:
                    lines.append(f"- **{r.suite}**: {f}")
        return "\n".join(lines)


# ── Suite runners ─────────────────────────────────────────────────────


def _ts() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


def _run_pytest_subset(suite_name: str, test_filter: str) -> SuiteResult:
    """Helper: run pytest with -k filter and parse the summary line.

    Cartograph's tests already enforce every gate at the test level. The
    cert runner just aggregates them by purpose. Returns a SuiteResult
    with passed_count / warning_count (skipped) / failed_count from the
    pytest exit summary.
    """
    import subprocess
    res = SuiteResult(suite=suite_name, timestamp=_ts())
    cmd = ["python3", "-m", "pytest", "tests/", "-q", "-k", test_filter,
           "--tb=no", "--no-header"]
    try:
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True,
                               text=True, timeout=120)
    except subprocess.TimeoutExpired:
        res.failed_count = 1
        res.failures.append(f"pytest timeout on filter={test_filter!r}")
        return res

    out = proc.stdout + proc.stderr
    # Parse last summary line: "N passed, M skipped, K failed, ..."
    import re
    m = re.search(r"(\d+) passed", out)
    if m:
        res.passed_count = int(m.group(1))
    m = re.search(r"(\d+) skipped", out)
    if m:
        res.skipped_count = int(m.group(1))
        # Surface skip reasons as warnings — they're env-only, not failures
        for skip_line in re.findall(r"SKIPPED.*", out):
            res.warnings.append(f"SKIP: {skip_line.strip()}")
    m = re.search(r"(\d+) failed", out)
    if m:
        res.failed_count = int(m.group(1))
        for fail_line in re.findall(r"FAILED.*", out):
            res.failures.append(fail_line.strip())
    m = re.search(r"(\d+) error", out)
    if m:
        res.failed_count += int(m.group(1))
        for err_line in re.findall(r"ERROR.*", out):
            res.failures.append(err_line.strip())

    res.total = res.passed_count + res.skipped_count + res.failed_count
    res.passed = (res.failed_count == 0)
    return res


def _run_data_model() -> SuiteResult:
    """Rung 1 evidence: taxonomy loads, fragrance count > 0, 8 families."""
    res = SuiteResult(suite="data_model", timestamp=_ts())
    try:
        from cartograph.taxonomy import FRAGRANCE_TAXONOMY
        count = len(FRAGRANCE_TAXONOMY)
        families = {v["family"] for v in FRAGRANCE_TAXONOMY.values()}
        res.evidence["taxonomy_loads"] = True
        res.evidence["fragrance_count"] = count
        res.evidence["fragrance_count_positive"] = count > 0
        res.evidence["family_count"] = len(families)
        res.evidence["eight_scent_families_present"] = (len(families) == 8)
        if not res.evidence["eight_scent_families_present"]:
            res.failed_count += 1
            res.failures.append(
                f"expected 8 scent families, got {len(families)}: {sorted(families)}"
            )
        else:
            res.passed_count += 1
        res.total = 1
    except Exception as e:
        res.failed_count += 1
        res.failures.append(f"taxonomy load failed: {type(e).__name__}: {e}")
    res.passed = (res.failed_count == 0)
    return res


def _run_query_safety() -> SuiteResult:
    """Rung 3 evidence: SQL injection vectors closed. Cross-validates
    against the test_red_team.py SQL-guard tests AND the source-level
    regression tests."""
    res = _run_pytest_subset(
        "query_safety",
        "guard or injection or path_traversal or import_rejects or "
        "source_does_not_use or specs_are_consistent or "
        "raw_sql_tool_no_longer_exists or inspect_schema_returns_metadata",
    )
    # Evidence assertions
    from cartograph.agent import TOOLS as agent_tools
    from cartograph.cli import TEMPLATES, VALIDATOR_TABLE
    tool_names = {t["name"] for t in agent_tools}
    res.evidence["agent_no_raw_sql_tool"] = "query_transactions" not in tool_names
    res.evidence["template_count"] = len(TEMPLATES)
    res.evidence["all_templates_use_placeholders"] = all(
        "{" not in s["sql"] and "}" not in s["sql"] for s in TEMPLATES.values()
    )
    res.evidence["validator_table_size"] = len(VALIDATOR_TABLE)
    # If any evidence is False, fail the suite
    for key in ("agent_no_raw_sql_tool", "all_templates_use_placeholders"):
        if not res.evidence[key]:
            res.failed_count += 1
            res.failures.append(f"evidence {key} = False")
    res.passed = (res.failed_count == 0)
    return res


def _run_embedding_quality() -> SuiteResult:
    """Rung 2 evidence: Philyra trap + taxonomy hash + note-profile usage."""
    res = SuiteResult(suite="embedding_quality", timestamp=_ts())
    try:
        from cartograph.embeddings import taxonomy_hash
        # taxonomy_hash determinism
        a = taxonomy_hash()
        b = taxonomy_hash()
        res.evidence["taxonomy_hash_deterministic"] = (a == b)
        res.evidence["taxonomy_hash"] = a
        if not res.evidence["taxonomy_hash_deterministic"]:
            res.failed_count += 1
            res.failures.append("taxonomy_hash is not deterministic")
        else:
            res.passed_count += 1

        # Verify embedder uses note profiles, not names (source check)
        from cartograph import embeddings as emb_mod
        src = Path(emb_mod.__file__).read_text()
        # The _build method should iterate get_taxonomy_for_embedding which
        # returns (name, notes) tuples and feed notes to encode
        uses_notes = (
            "get_taxonomy_for_embedding" in src
            and "texts = [notes for _, notes in taxonomy]" in src
        )
        res.evidence["embeddings_built_from_note_profiles_not_names"] = uses_notes
        if not uses_notes:
            res.failed_count += 1
            res.failures.append(
                "embeddings.py does not appear to embed note profiles — "
                "may have regressed to name-only embedding (Philyra trap)"
            )
        else:
            res.passed_count += 1
        res.total = 2
    except Exception as e:
        res.failed_count += 1
        res.failures.append(f"{type(e).__name__}: {e}")

    # Add the actual sbert/Philyra pytest results — skipped on this Mac
    pytest_res = _run_pytest_subset(
        "embedding_quality_pytest",
        "philyra or note_profile or taxonomy_hash or scent_note or marketing_copy",
    )
    res.passed_count += pytest_res.passed_count
    res.failed_count += pytest_res.failed_count
    res.skipped_count += pytest_res.skipped_count
    res.warning_count += pytest_res.warning_count
    res.warnings.extend(pytest_res.warnings)
    res.failures.extend(pytest_res.failures)
    res.total += pytest_res.total

    res.passed = (res.failed_count == 0)
    return res


def _run_agent_safety() -> SuiteResult:
    """Rung 4 evidence: recommendation truthfulness + cache discipline +
    tenant isolation. The 5 negative controls Kimi/ChatGPT specified."""
    res = _run_pytest_subset(
        "agent_safety",
        "zero_overlap or weak_top or stale or marketing or tenant_ or "
        "recommendation_contract or qualifying or is_stale",
    )
    return res


def _run_recommendation_grounding() -> SuiteResult:
    """Rung 4 evidence: recommendation contract refuses unprovable claims."""
    res = _run_pytest_subset(
        "recommendation_grounding",
        "recommendation_contract or zero_overlap_does_not",
    )
    return res


def _run_negative_controls() -> SuiteResult:
    """LAST suite. Per Kimi+ChatGPT: a certification that doesn't have
    negative controls as the final gate is a demo, not a harness. Run
    every red_team trap and require all to pass."""
    res = _run_pytest_subset(
        "negative_controls",
        "rejects or no_longer_exists or returns_metadata or "
        "does_not_use_format or specs_are_consistent or "
        "marketing_copy or zero_overlap or weak_top or stale or "
        "tenant_ or contract_rejects",
    )
    return res


# ── Orchestrator ──────────────────────────────────────────────────────


def _derive_readiness(report: CertificationReport) -> tuple[str, str]:
    """Walk the suites bottom-up. The highest rung whose evidence is
    fully satisfied becomes the assigned readiness. Higher rungs require
    proof that doesn't exist on this host (env-skipped tests, no live
    customer tenant, etc.)."""
    suites_by_name = {r.suite: r for r in report.results}

    # Always pass: REPO_BASELINE_CAPTURED is implicit if anything ran
    label = "REPO_BASELINE_CAPTURED"
    reason = "harness collected suites successfully"

    # DATA_MODEL_VALIDATED
    dm = suites_by_name.get("data_model")
    if dm and dm.passed and dm.evidence.get("eight_scent_families_present"):
        label = "DATA_MODEL_VALIDATED"
        reason = (
            f"taxonomy loads with {dm.evidence.get('fragrance_count')} "
            f"fragrances across 8 scent families"
        )

    # EMBEDDING_QUALITY_CERTIFIED (only if Philyra-trap actually ran, not skipped)
    eq = suites_by_name.get("embedding_quality")
    if eq and eq.passed and eq.evidence.get("taxonomy_hash_deterministic") \
            and eq.evidence.get("embeddings_built_from_note_profiles_not_names"):
        if eq.skipped_count == 0 and eq.passed_count >= 4:
            label = "EMBEDDING_QUALITY_CERTIFIED"
            reason = (
                "Philyra trap passes on live embedder + taxonomy hash is "
                "deterministic + embeddings built from note profiles"
            )
        else:
            # Can't claim full EQ_CERTIFIED if Philyra is env-skipped, but
            # we can still proceed to QUERY_SAFETY since they're independent.
            pass

    # QUERY_SAFETY_CERTIFIED
    qs = suites_by_name.get("query_safety")
    if qs and qs.passed and qs.evidence.get("agent_no_raw_sql_tool") \
            and qs.evidence.get("all_templates_use_placeholders"):
        # QUERY_SAFETY can stand on its own even if EMBEDDING_QUALITY is skipped
        if label in ("REPO_BASELINE_CAPTURED", "DATA_MODEL_VALIDATED",
                     "EMBEDDING_QUALITY_CERTIFIED"):
            label = "QUERY_SAFETY_CERTIFIED"
            reason = (
                f"agent has no raw-SQL tool, {qs.evidence['template_count']} "
                f"CLI templates use ? placeholders + validators, source-level "
                f"regression test passes"
            )

    # AGENT_SAFETY_CERTIFIED — only if zero env skips on agent-safety tests
    asuite = suites_by_name.get("agent_safety")
    neg = suites_by_name.get("negative_controls")
    if (asuite and asuite.passed and asuite.skipped_count == 0
            and neg and neg.passed and neg.skipped_count == 0
            and label == "QUERY_SAFETY_CERTIFIED"):
        label = "AGENT_SAFETY_CERTIFIED"
        reason = (
            "all agent-safety + negative-control tests pass with zero env skips"
        )

    # BUFF_CITY_DEMO_READY / PILOT_READY require evidence not yet built —
    # never reached by this runner. Keep the ladder honest.

    return label, reason


class CartographConformanceCertification:
    def __init__(self, tool_version: str = "0.9.0"):
        self.tool_version = tool_version

    def run_suite(self) -> CertificationReport:
        report = CertificationReport(
            tool_version=self.tool_version,
            issued_at=datetime.datetime.now(datetime.UTC)
                .isoformat(timespec="seconds"),
        )
        # Run suites in readiness-rung order; negative_controls LAST.
        report.results.append(_run_data_model())             # rung 1
        report.results.append(_run_embedding_quality())      # rung 2
        report.results.append(_run_query_safety())           # rung 3
        report.results.append(_run_agent_safety())           # rung 4
        report.results.append(_run_recommendation_grounding())
        report.results.append(_run_negative_controls())      # LAST gate
        # Derive readiness from evidence — not from assertion
        report.readiness, report.readiness_reason = _derive_readiness(report)
        return report


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> int:
    cert = CartographConformanceCertification(tool_version="0.9.0")
    report = cert.run_suite()

    # Build SHA-256 manifest + self-fingerprint the certificate body
    manifest = build_manifest(include_files=False)

    out_dir = REPO_ROOT / "cartograph_harness" / "certificates"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    md_path = out_dir / f"certificate_{ts}.md"
    json_path = out_dir / f"certificate_{ts}.json"

    md_body = report.to_markdown()
    # Append manifest table
    md_body += "\n\n## Scorer manifest (SHA-256 freeze)\n\n"
    md_body += "| Component | Files | SHA-256 |\n"
    md_body += "|-----------|------:|---------|\n"
    for label, comp in sorted(manifest["components"].items()):
        md_body += f"| `{label}` | {comp['file_count']} | `{comp['sha256']}` |\n"
    md_body += f"\n**Overall fingerprint**: `{manifest['fingerprint_sha256']}`\n"
    md_body += (
        "\n## NOT claimed\n\n"
        "- Live Shopify API integration — no live HTTP call has been made\n"
        "- Multi-tenant SaaS production readiness — v3 code exists in "
        "cartograph/multitenant/ but is NOT in the certified surface\n"
        "- `interactive_shell()` developer SQL — explicitly excluded; "
        "accepts arbitrary SQL by design and is not safe behind any "
        "network listener\n"
        "- BUFF_CITY_DEMO_READY — requires healthy demo env (sbert + "
        "pandas + numpy importable) and end-to-end pipeline verification "
        "with campaign export\n"
        "- PILOT_READY — requires multi-tenant data-root isolation, "
        "import validation report, audit log, token cost cap, and "
        "documented client data handling\n"
    )

    md_path.write_text(md_body)

    # JSON metadata with self-fingerprint
    metadata = json.loads(report.to_json())
    metadata["scorer_manifest"] = manifest
    metadata["certificate_body_sha256"] = fingerprint_certificate_body(metadata)
    json_path.write_text(json.dumps(metadata, indent=2, default=str))

    print(md_body)
    print(f"\nCertificate saved: {md_path}")
    print(f"Metadata saved:   {json_path}")
    return 0 if report.all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
