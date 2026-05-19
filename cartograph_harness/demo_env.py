"""demo_env.py — DEMO_ENV_HEALTHY gate for Cartograph readiness.

ChatGPT 2026-05-19: BUFF_CITY_DEMO_READY (rung 5) cannot be claimed on a
host where sentence-transformers / pandas / numpy fail to import. The 21
env-skipped tests on this Mac are a real signal — those tests need the
embedder to actually run. Until that's true on the host, the demo can't
be considered ready.

This module is the cheap, deterministic check that distinguishes "demo
machine ready" from "demo machine has env damage." Two functions:

  - probe_demo_env()      -> dict of (module → ok|reason)
  - is_demo_env_healthy() -> bool (all three importable cleanly)

Called by certify.py at certification time. Result becomes part of the
evidence ladder for promotion to BUFF_CITY_DEMO_READY.
"""

from __future__ import annotations


# The minimum modules a healthy Cartograph demo machine must import. Each
# entry: (probe name shown in evidence, callable that imports). Callable
# raises if the module is unavailable / broken.
REQUIRED_DEMO_MODULES = [
    ("numpy",
     lambda: __import__("numpy").__version__),     # also exercises namespace
    ("pandas",
     lambda: __import__("pandas").__version__),
    ("sentence_transformers",
     lambda: __import__("sentence_transformers").__version__),
    ("duckdb",
     lambda: __import__("duckdb").__version__),
    ("anthropic",
     lambda: __import__("anthropic").__version__),
]


def probe_demo_env() -> dict[str, dict]:
    """Probe each required module. Returns a dict per module with `ok`
    (bool), `version` (str, when ok), and `reason` (str, when not ok)."""
    out: dict[str, dict] = {}
    for name, probe in REQUIRED_DEMO_MODULES:
        try:
            version = probe()
            out[name] = {"ok": True, "version": str(version)}
        except Exception as e:
            # Broad except — sentence_transformers can fail at import time
            # on torch/numpy version mismatches (not just ModuleNotFoundError).
            out[name] = {
                "ok": False,
                "reason": f"{type(e).__name__}: {e}",
            }
    return out


def is_demo_env_healthy() -> bool:
    """True iff every required demo module imports cleanly."""
    return all(r["ok"] for r in probe_demo_env().values())


def env_health_summary() -> str:
    """Human-readable one-line summary for the certificate body."""
    probe = probe_demo_env()
    failed = [name for name, r in probe.items() if not r["ok"]]
    if not failed:
        return f"DEMO_ENV_HEALTHY: all {len(probe)} required modules importable"
    return (
        f"DEMO_ENV_UNHEALTHY: {len(failed)}/{len(probe)} modules broken: "
        + ", ".join(failed)
    )


if __name__ == "__main__":
    import json
    print(json.dumps(probe_demo_env(), indent=2))
    print()
    print(env_health_summary())
