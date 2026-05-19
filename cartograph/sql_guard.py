"""sql_guard.py — Cartograph SQL injection guard.

Kimi/ChatGPT audit 2026-05-19: the agent.py tools `cross_sell_analysis`,
`get_cohort_stats`, and (partially) `query_transactions` built SQL via
f-string interpolation on user-controlled parameters. Five injection points:
category_a, category_b, category, start_date, end_date.

The "Blocks DROP TABLE" check in test_red_team.py only covers the prefix
case (sql='DROP TABLE...'). A payload like `cat_a = "x' OR '1'='1"` flows
into agent.py:181 as:

    WHERE product_type = 'x' OR '1'='1'

and returns the entire transactions table — the destructive-prefix check at
agent.py:144 misses it because the rendered SQL still starts with SELECT.

This module is the load-bearing fix:
  - `safe_date(s)`     strict YYYY-MM-DD validator; raises SQLGuardError
  - `safe_category(s)` allow-list against the known product types; everything
                         else routes through parameter binding instead of
                         identifier interpolation
  - `safe_fragrance(s)` ditto for fragrance names — they come out of an
                         embedder result set, but we treat the boundary as
                         untrusted anyway
  - `execute_safe(conn, sql, params)` thin wrapper around DuckDB's parameter
                         binding so callers can't accidentally f-string

Use these everywhere user-controlled strings touch SQL. Callers that need
flexibility for non-allowlisted values must use parameter binding, not
string interpolation.
"""

from __future__ import annotations

import re
from typing import Iterable


class SQLGuardError(ValueError):
    """Raised when input fails the guard. Caller MUST surface this to the
    user — silently dropping it would mask injection attempts."""


# Strict ISO date. No times, no timezones — Cartograph queries operate on
# date columns only. If a tool ever needs timestamp filtering, add a
# separate validator rather than loosening this one.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def safe_date(s: str, field: str = "date") -> str:
    """Validate strict YYYY-MM-DD; raise on anything else.

    Returns the canonicalized string so callers can use the return value
    directly: `start = safe_date(start_date, 'start_date')`.
    """
    if not isinstance(s, str) or not _DATE_RE.match(s):
        raise SQLGuardError(
            f"invalid {field}: expected YYYY-MM-DD, got {s!r}"
        )
    # Validate as a real calendar date (catches 2026-13-99 etc.)
    import datetime
    try:
        datetime.date.fromisoformat(s)
    except ValueError as e:
        raise SQLGuardError(f"invalid {field}: {e}")
    return s


# Allow-list of product types the Cartograph agent knows about. Pulled from
# agent.py system prompt + Buff City Soap catalog. Anything outside this list
# is rejected — that's tighter than parameter-binding alone but catches the
# case where Claude hallucinates a category that doesn't exist AND blocks any
# attempt to inject through this surface.
KNOWN_PRODUCT_TYPES: frozenset[str] = frozenset({
    "Body Lotion", "Laundry Soap", "Bath Bomb", "Body Butter",
    "Hand Soap", "Shower Oil", "Body Wash", "Sugar Scrub",
    "Candle", "Dryer Ball",
})


def safe_category(s: str) -> str:
    """Allow-list product category names. Case-sensitive — agent prompt
    instructs Claude to use the canonical form."""
    if not isinstance(s, str):
        raise SQLGuardError(f"category must be a string, got {type(s).__name__}")
    if s not in KNOWN_PRODUCT_TYPES:
        raise SQLGuardError(
            f"unknown product category {s!r}. Known: {sorted(KNOWN_PRODUCT_TYPES)}"
        )
    return s


# Light syntactic check for fragrance names. Values are matched against the
# taxonomy downstream; this guard catches the obvious injection payloads
# (semicolons, quote-escape attempts, SQL comment markers) before they hit
# the embedder lookup. Real validation = membership in FRAGRANCE_TAXONOMY,
# done by the embedder.
_FRAGRANCE_BLOCKLIST = (
    "'", '"', ";", "--", "/*", "*/", "\\",
)


def safe_fragrance(s: str) -> str:
    """Block obvious SQL-injection metacharacters in fragrance names. The
    embedder will reject unknown names too — this is defense-in-depth."""
    if not isinstance(s, str):
        raise SQLGuardError(f"fragrance must be a string, got {type(s).__name__}")
    for tok in _FRAGRANCE_BLOCKLIST:
        if tok in s:
            raise SQLGuardError(
                f"fragrance name contains forbidden token {tok!r}: {s!r}"
            )
    if len(s) > 100:
        raise SQLGuardError(f"fragrance name too long ({len(s)} chars)")
    return s


def safe_guest_ids(ids: Iterable[str]) -> list[str]:
    """Validate a list of guest IDs before they go into a SQL IN clause.

    Guest IDs in Cartograph fixtures are alphanumeric + dashes — anything
    else is rejected. NOTE: even after validation, the calling code MUST use
    parameter binding (`?, ?, ?, ...`) — never f-string interpolation. This
    function exists to fail fast on shape violations, not to substitute for
    parameter binding.
    """
    out: list[str] = []
    pattern = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
    for gid in ids:
        if not isinstance(gid, str) or not pattern.match(gid):
            raise SQLGuardError(f"invalid guest_id: {gid!r}")
        out.append(gid)
    return out


def safe_int(s, field: str = "value", min_val: int | None = None,
             max_val: int | None = None) -> int:
    """Coerce to int and validate range. Rejects strings with anything other
    than an optional sign + digits — including SQL-payload-shaped values.

    Used for CLI template params like `min_purchases` that are user-supplied
    but must be integers."""
    if isinstance(s, bool):
        # bool is a subclass of int; reject explicitly to catch True/False
        raise SQLGuardError(f"{field} must be an integer, got bool")
    if isinstance(s, int):
        n = s
    elif isinstance(s, str):
        if not re.fullmatch(r"-?\d+", s.strip()):
            raise SQLGuardError(
                f"{field} must be an integer, got {s!r}"
            )
        n = int(s)
    else:
        raise SQLGuardError(
            f"{field} must be an integer or numeric string, got {type(s).__name__}"
        )
    if min_val is not None and n < min_val:
        raise SQLGuardError(f"{field}={n} below minimum {min_val}")
    if max_val is not None and n > max_val:
        raise SQLGuardError(f"{field}={n} above maximum {max_val}")
    return n


# File suffixes the CLI importer is allowed to touch. Extend deliberately —
# adding .sql or .exe here would re-open a code-execution surface.
ALLOWED_IMPORT_SUFFIXES: frozenset[str] = frozenset({
    ".csv", ".tsv", ".json", ".parquet", ".xlsx", ".xls",
})


def safe_path(path, data_root, allowed_suffixes: frozenset[str] | None = None):
    """Validate an import path: must resolve INSIDE the allowed data root and
    carry an allow-listed suffix.

    ChatGPT 2026-05-19: import_file() previously interpolated raw user paths
    into SQL as `read_csv_auto('{filepath}')` — a payload like
    `'/tmp/x.csv'); DROP TABLE transactions; --` injected directly. The fix
    is two-layered: (1) validate the path here, (2) parameter-bind via
    execute_safe at the call site.

    Returns a resolved `pathlib.Path` the caller can pass to parameter
    binding. Raises SQLGuardError on path-traversal, suffix violations, or
    non-string input.
    """
    from pathlib import Path
    if allowed_suffixes is None:
        allowed_suffixes = ALLOWED_IMPORT_SUFFIXES
    if not isinstance(path, (str, Path)):
        raise SQLGuardError(
            f"import path must be a string or Path, got {type(path).__name__}"
        )
    p = Path(path).expanduser().resolve()
    root = Path(data_root).expanduser().resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise SQLGuardError(
            f"import path {p} escapes the allowed data root {root}"
        )
    suffix = p.suffix.lower()
    if suffix not in allowed_suffixes:
        raise SQLGuardError(
            f"unsupported import suffix {suffix!r}; allowed: {sorted(allowed_suffixes)}"
        )
    if not p.exists():
        raise SQLGuardError(f"import path does not exist: {p}")
    return p


def execute_safe(conn, sql: str, params: list | tuple | None = None):
    """DuckDB parameter binding wrapper.

    Refuse callers that pass a string with literal Python `{}` braces — that
    almost always means they were about to f-string something into the SQL.
    """
    if "{" in sql or "}" in sql:
        raise SQLGuardError(
            "execute_safe sql contains '{' or '}' — looks like an f-string "
            "template. Use ? placeholders + params list instead."
        )
    if params is None:
        params = []
    return conn.execute(sql, params)
