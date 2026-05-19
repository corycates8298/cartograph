"""cartograph_harness — Cartograph conformance certification runner.

Mirrors the FDI Practice OS harness pattern: evidence-derived readiness
labels, SHA-256 scorer manifest, certificate body self-fingerprint,
negative controls as the last gate, explicit NOT-claimed sections.

Entry point:
    python3 -m cartograph_harness.certify
or:
    python3 cartograph_harness/certify.py
"""
