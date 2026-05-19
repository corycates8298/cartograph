#!/usr/bin/env python3
"""
Scout Analytics — Local SQL analytics engine for customer/purchase data.
Powered by DuckDB. Handles CSV, Excel, JSON, Parquet imports.

Usage:
    scout-analytics                          # interactive SQL shell
    scout-analytics import purchases.csv     # import data file
    scout-analytics import purchases.xlsx --table orders
    scout-analytics query "SELECT ..."       # one-shot query
    scout-analytics ask "how many guests bought body lotion and laundry soap?"
    scout-analytics schema                   # show all tables
    scout-analytics templates                # show query templates for common questions

The 'ask' command translates natural language to SQL using pattern matching
against common retail analytics questions.
"""

import argparse
import json
import os
import readline
import sys
from pathlib import Path

import duckdb

# pandas is lazy-imported inside the functions that use it. Module-level
# import broke test collection on machines without pandas (the SQL guards
# don't actually need pandas to function; only the result-printing paths
# and the Excel/sample-data branches do).
def _pd():
    import pandas as pd
    return pd

from .sql_guard import (
    safe_date, safe_category, safe_fragrance, safe_int, safe_path,
    execute_safe, SQLGuardError, ALLOWED_IMPORT_SUFFIXES,
)

DATA_DIR = Path.home() / "osint" / "data"
DB_PATH = DATA_DIR / "analytics.duckdb"

# ANSI colors
class C:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


# ── Query Templates for Retail Analytics ─────────────────────────
#
# Kimi + ChatGPT audit 2026-05-19: previously these templates used Python
# string-formatting placeholders consumed at run time on user-controlled
# params — exactly the same SQL-injection vector that was just closed in
# agent.py. The CLI attack surface is "operator only" today, but any
# script, web form, or cron wrapper around `scout-analytics template ...`
# re-opens it.
#
# NEW CONTRACT (every template MUST satisfy this):
#   - SQL uses DuckDB `?` placeholders only — never `{name}` / `%s` / `$N`
#   - `params` is the SQL-order list of named bindings
#   - `validators` maps each param name to a sql_guard validator key
#   - run_template() validates each value and passes them through
#     execute_safe() — which itself hard-refuses any SQL containing `{` or `}`
#
# Adding a template? Pick validators from VALIDATOR_TABLE below. Anything
# you can't validate to an allow-list or a strict format does NOT belong
# in a parameter-bound template — escalate the design instead.

# Map validator key → callable. The set is intentionally small. To add a
# new validator key, extend sql_guard.py first (the load-bearing module),
# then list the key here.
VALIDATOR_TABLE = {
    "category":  lambda v, name: safe_category(v),
    "date":      lambda v, name: safe_date(v, name),
    "fragrance": lambda v, name: safe_fragrance(v),
    "int_min0":  lambda v, name: safe_int(v, name, min_val=0),
    "int_min1":  lambda v, name: safe_int(v, name, min_val=1),
}


TEMPLATES = {
    "cross_sell_fragrance": {
        "description": "Guests who bought [Category A] AND [Category B] in similar fragrance",
        "sql": """
SELECT
    a.guest_id,
    a.product_type AS first_purchase_category,
    a.fragrance AS first_fragrance,
    a.transaction_date AS first_purchase_date,
    b.product_type AS cross_sell_category,
    b.fragrance AS cross_sell_fragrance,
    b.transaction_date AS cross_sell_date
FROM transactions a
JOIN transactions b
    ON a.guest_id = b.guest_id
    AND b.product_type = ?
    AND (
        b.fragrance = a.fragrance
        OR b.fragrance ILIKE '%' || SPLIT_PART(a.fragrance, ' ', 1) || '%'
    )
WHERE a.product_type = ?
    AND a.transaction_date BETWEEN ? AND ?
ORDER BY a.guest_id, a.transaction_date
""",
        # SQL-order — first ? = cat_b (in JOIN), second = cat_a (WHERE), then dates
        "params": ["cat_b", "cat_a", "start_date", "end_date"],
        "validators": {
            "cat_a": "category", "cat_b": "category",
            "start_date": "date", "end_date": "date",
        },
        "example": "cross_sell_fragrance cat_a='Body Lotion' cat_b='Laundry Soap' start_date='2025-01-03' end_date='2026-01-02'",
    },

    "category_affinity": {
        "description": "What categories do buyers of [Category] also purchase?",
        "sql": """
WITH target_guests AS (
    SELECT DISTINCT guest_id
    FROM transactions
    WHERE product_type ILIKE '%' || ? || '%'
        AND transaction_date BETWEEN ? AND ?
)
SELECT
    t.product_type,
    COUNT(DISTINCT t.guest_id) AS unique_guests,
    COUNT(*) AS total_purchases,
    ROUND(COUNT(DISTINCT t.guest_id) * 100.0 / (SELECT COUNT(*) FROM target_guests), 1) AS pct_of_target
FROM transactions t
JOIN target_guests tg ON t.guest_id = tg.guest_id
WHERE t.product_type NOT ILIKE '%' || ? || '%'
GROUP BY t.product_type
ORDER BY unique_guests DESC
""",
        "params": ["category", "start_date", "end_date", "category"],
        "validators": {
            "category": "category",
            "start_date": "date", "end_date": "date",
        },
        "example": "category_affinity category='Body Lotion' start_date='2025-01-01' end_date='2026-01-01'",
    },

    "fragrance_loyalty": {
        "description": "Do guests stick to the same fragrance across categories?",
        "sql": """
SELECT
    guest_id,
    fragrance,
    COUNT(DISTINCT product_type) AS categories_purchased,
    ARRAY_AGG(DISTINCT product_type) AS category_list,
    COUNT(*) AS total_purchases,
    SUM(price) AS total_spend
FROM transactions
WHERE fragrance IS NOT NULL AND fragrance != ''
    AND transaction_date BETWEEN ? AND ?
GROUP BY guest_id, fragrance
HAVING COUNT(DISTINCT product_type) >= 2
ORDER BY categories_purchased DESC, total_spend DESC
LIMIT 100
""",
        "params": ["start_date", "end_date"],
        "validators": {"start_date": "date", "end_date": "date"},
        "example": "fragrance_loyalty start_date='2024-01-01' end_date='2026-01-01'",
    },

    "discount_impact": {
        "description": "How do discounts affect purchase behavior?",
        "sql": """
SELECT
    CASE WHEN discount_amount > 0 THEN 'Discounted' ELSE 'Full Price' END AS purchase_type,
    product_type,
    COUNT(*) AS transactions,
    COUNT(DISTINCT guest_id) AS unique_guests,
    ROUND(AVG(price), 2) AS avg_price,
    ROUND(AVG(discount_amount), 2) AS avg_discount,
    ROUND(SUM(price - COALESCE(discount_amount, 0)), 2) AS net_revenue
FROM transactions
WHERE transaction_date BETWEEN ? AND ?
GROUP BY 1, product_type
ORDER BY product_type, purchase_type
""",
        "params": ["start_date", "end_date"],
        "validators": {"start_date": "date", "end_date": "date"},
        "example": "discount_impact start_date='2025-01-01' end_date='2026-01-01'",
    },

    "channel_performance": {
        "description": "Revenue and guest count by purchase channel",
        "sql": """
SELECT
    channel,
    COUNT(DISTINCT guest_id) AS unique_guests,
    COUNT(*) AS transactions,
    ROUND(SUM(price), 2) AS gross_revenue,
    ROUND(AVG(price), 2) AS avg_order_value,
    ROUND(SUM(discount_amount), 2) AS total_discounts,
    COUNT(DISTINCT product_type) AS categories_sold
FROM transactions
WHERE transaction_date BETWEEN ? AND ?
GROUP BY channel
ORDER BY gross_revenue DESC
""",
        "params": ["start_date", "end_date"],
        "validators": {"start_date": "date", "end_date": "date"},
        "example": "channel_performance start_date='2025-01-01' end_date='2026-01-01'",
    },

    "repeat_buyers": {
        "description": "Guests with multiple purchases — frequency and recency",
        "sql": """
SELECT
    guest_id,
    COUNT(*) AS purchase_count,
    COUNT(DISTINCT product_type) AS categories,
    MIN(transaction_date) AS first_purchase,
    MAX(transaction_date) AS last_purchase,
    DATEDIFF('day', MIN(transaction_date), MAX(transaction_date)) AS days_span,
    ROUND(SUM(price), 2) AS lifetime_value,
    ARRAY_AGG(DISTINCT product_type) AS category_mix
FROM transactions
WHERE transaction_date BETWEEN ? AND ?
GROUP BY guest_id
HAVING COUNT(*) >= ?
ORDER BY lifetime_value DESC
LIMIT 100
""",
        "params": ["start_date", "end_date", "min_purchases"],
        "validators": {
            "start_date": "date", "end_date": "date",
            "min_purchases": "int_min1",
        },
        "example": "repeat_buyers start_date='2022-01-01' end_date='2026-01-01' min_purchases=3",
    },

    "fragrance_popularity": {
        "description": "Most popular fragrances by category",
        "sql": """
SELECT
    product_type,
    fragrance,
    COUNT(*) AS purchases,
    COUNT(DISTINCT guest_id) AS unique_guests,
    ROUND(SUM(price), 2) AS revenue
FROM transactions
WHERE fragrance IS NOT NULL AND fragrance != ''
    AND transaction_date BETWEEN ? AND ?
GROUP BY product_type, fragrance
ORDER BY product_type, purchases DESC
""",
        "params": ["start_date", "end_date"],
        "validators": {"start_date": "date", "end_date": "date"},
        "example": "fragrance_popularity start_date='2025-01-01' end_date='2026-01-01'",
    },

    "market_basket": {
        "description": "Products frequently purchased together in same transaction",
        "sql": """
WITH order_pairs AS (
    SELECT
        a.transaction_id,
        a.product_type AS product_a,
        b.product_type AS product_b
    FROM transactions a
    JOIN transactions b
        ON a.transaction_id = b.transaction_id
        AND a.product_type < b.product_type
    WHERE a.transaction_date BETWEEN ? AND ?
)
SELECT
    product_a,
    product_b,
    COUNT(*) AS co_occurrences,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(DISTINCT transaction_id) FROM transactions WHERE transaction_date BETWEEN ? AND ?), 2) AS pct_of_orders
FROM order_pairs
GROUP BY product_a, product_b
ORDER BY co_occurrences DESC
LIMIT 20
""",
        "params": ["start_date", "end_date", "start_date", "end_date"],
        "validators": {"start_date": "date", "end_date": "date"},
        "example": "market_basket start_date='2025-01-01' end_date='2026-01-01'",
    },
}


# ── Import Functions ─────────────────────────────────────────────

# Identifier safety for table names: alphanumeric + underscore only, 1..63 chars,
# must start with a letter. This is the ONLY identifier we let through — DuckDB
# parameter binding does not cover identifiers, so we allow-list instead.
import re as _re_id
_TABLE_NAME_RE = _re_id.compile(r"^[A-Za-z][A-Za-z0-9_]{0,62}$")


def _safe_table_name(name: str) -> str:
    """Validate a DuckDB table identifier. Raises SQLGuardError on anything
    outside the alphanumeric+underscore pattern. Identifiers cannot be
    parameter-bound, so we hard-allowlist."""
    if not isinstance(name, str) or not _TABLE_NAME_RE.match(name):
        raise SQLGuardError(
            f"invalid table name {name!r} — must match [A-Za-z][A-Za-z0-9_]{{0,62}}"
        )
    return name


def import_file(filepath, table_name=None, db_path=DB_PATH, data_root=None):
    """Import CSV, Excel, JSON, or Parquet into DuckDB.

    Kimi/ChatGPT 2026-05-19: previously this used f-string interpolation
    `read_csv_auto('{filepath}')` which made a path like
    `'/tmp/x.csv'); DROP TABLE transactions; --` an injection vector.

    Fix:
      - safe_path() validates the path resolves under data_root + has an
        allowed suffix + exists
      - _safe_table_name() allow-lists the table identifier (DuckDB doesn't
        parameter-bind identifiers, so we restrict to [A-Za-z0-9_])
      - File path is parameter-bound into read_csv_auto/read_json_auto/
        read_parquet via execute_safe; no string interpolation of paths
    """
    if data_root is None:
        data_root = Path.home()    # broad fallback for general CLI use

    try:
        p = safe_path(filepath, data_root, ALLOWED_IMPORT_SUFFIXES)
    except SQLGuardError as e:
        print(f"  {C.RED}Import rejected: {e}{C.RESET}")
        return None

    if not table_name:
        table_name = p.stem.lower().replace(" ", "_").replace("-", "_")
    try:
        table_name = _safe_table_name(table_name)
    except SQLGuardError as e:
        print(f"  {C.RED}{e}{C.RESET}")
        return None

    conn = duckdb.connect(str(db_path))
    ext = p.suffix.lower()
    try:
        if ext == ".csv":
            execute_safe(
                conn,
                f"CREATE OR REPLACE TABLE {table_name} AS "
                f"SELECT * FROM read_csv_auto(?)",
                [str(p)],
            )
        elif ext == ".tsv":
            execute_safe(
                conn,
                f"CREATE OR REPLACE TABLE {table_name} AS "
                f"SELECT * FROM read_csv_auto(?, delim='\\t')",
                [str(p)],
            )
        elif ext in (".xlsx", ".xls"):
            df = _pd().read_excel(p)
            # `df` is a Python-side dataframe — DuckDB scans it directly via
            # the implicit `df` reference. Table name already validated above.
            conn.execute(
                f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df"
            )
        elif ext == ".json":
            execute_safe(
                conn,
                f"CREATE OR REPLACE TABLE {table_name} AS "
                f"SELECT * FROM read_json_auto(?)",
                [str(p)],
            )
        elif ext == ".parquet":
            execute_safe(
                conn,
                f"CREATE OR REPLACE TABLE {table_name} AS "
                f"SELECT * FROM read_parquet(?)",
                [str(p)],
            )
        else:
            # safe_path already enforces this, but defense-in-depth
            print(f"  {C.RED}Unsupported format: {ext}{C.RESET}")
            return None
    except Exception as e:
        print(f"  {C.RED}Import failed: {e}{C.RESET}")
        conn.close()
        return None

    # Table name was validated above — safe to splice into DDL-shape queries
    count = execute_safe(
        conn, f"SELECT COUNT(*) FROM {table_name}",
    ).fetchone()[0]
    cols = execute_safe(conn, f"DESCRIBE {table_name}").fetchall()
    conn.close()

    print(f"  {C.GREEN}Imported: {p.name} → {table_name}{C.RESET}")
    print(f"  Rows: {count} | Columns: {len(cols)}")
    print(f"  Schema:")
    for col in cols:
        print(f"    {col[0]:30s} {col[1]}")

    return table_name


def show_schema(db_path=DB_PATH):
    """Show all tables and their schemas."""
    if not Path(db_path).exists():
        print(f"  {C.DIM}No database yet. Import data first.{C.RESET}")
        return

    conn = duckdb.connect(str(db_path))
    tables = conn.execute("SHOW TABLES").fetchall()

    if not tables:
        print(f"  {C.DIM}No tables found.{C.RESET}")
        conn.close()
        return

    for (table,) in tables:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"\n  {C.BOLD}{C.CYAN}{table}{C.RESET} ({count:,} rows)")
        cols = conn.execute(f"DESCRIBE {table}").fetchall()
        for col in cols:
            print(f"    {col[0]:30s} {C.DIM}{col[1]}{C.RESET}")

    conn.close()


def show_templates():
    """Display available query templates."""
    print(f"\n  {C.BOLD}{C.CYAN}Available Query Templates{C.RESET}")
    print(f"  {'─' * 50}")
    for name, tmpl in TEMPLATES.items():
        print(f"\n  {C.GREEN}{name}{C.RESET}")
        print(f"    {tmpl['description']}")
        print(f"    {C.DIM}Example: scout-analytics template {tmpl['example']}{C.RESET}")


def run_template(template_name, params, db_path=DB_PATH):
    """Run a template with parameters.

    Kimi/ChatGPT 2026-05-19 refactor: previously this used Python string
    formatting on template SQL with user-controlled params — the same SQL
    injection vector that was closed in agent.py. NOW: every value flows
    through sql_guard validators, and the SQL is parameter-bound via
    execute_safe — no string interpolation of user values into SQL ever.
    (Source-level regression test test_cli_source_does_not_use_format_for_sql
    fires if the old call pattern ever returns.)

    Returns a dict for programmatic callers (tests use this) AND prints to
    stdout for CLI users. The CLI return value is the dict; the visual
    output is a side effect.
    """
    if template_name not in TEMPLATES:
        msg = f"Unknown template: {template_name}"
        print(f"  {C.RED}{msg}{C.RESET}")
        print(f"  Available: {', '.join(TEMPLATES.keys())}")
        return {"error": msg, "available": sorted(TEMPLATES.keys())}

    spec = TEMPLATES[template_name]
    sql = spec["sql"]
    sql_param_order = spec["params"]
    validators = spec["validators"]

    # 1. Validate every value through sql_guard. Any failure aborts BEFORE
    #    any SQL touches DuckDB.
    try:
        validated_by_name = {}
        for pname, vkey in validators.items():
            if pname not in params:
                raise SQLGuardError(
                    f"missing required parameter {pname!r} for template "
                    f"{template_name!r}. Example: {spec['example']}"
                )
            try:
                vfn = VALIDATOR_TABLE[vkey]
            except KeyError:
                raise SQLGuardError(
                    f"unknown validator {vkey!r} for param {pname!r} — "
                    f"template definition is malformed"
                )
            validated_by_name[pname] = vfn(params[pname], pname)

        # Reject unknown params — refuse "pass through" since an unrecognized
        # name is either a typo (caller bug) or an injection attempt (param
        # the template doesn't actually use).
        for k in params:
            if k not in validators:
                raise SQLGuardError(
                    f"unexpected parameter {k!r} for template "
                    f"{template_name!r}; allowed: {sorted(validators)}"
                )

        # 2. Bind in the EXACT SQL order the template specifies (allows
        #    the same param to appear multiple times — market_basket etc.)
        bound_values = [validated_by_name[name] for name in sql_param_order]

    except SQLGuardError as e:
        print(f"  {C.RED}Input rejected: {e}{C.RESET}")
        return {"error": str(e)}

    print(f"  {C.DIM}Running template {template_name} with "
          f"{len(bound_values)} bound param(s){C.RESET}\n")

    conn = duckdb.connect(str(db_path))
    try:
        # 3. execute_safe is the load-bearing call — it refuses any SQL
        #    containing literal `{` or `}` (the f-string regression
        #    tripwire), and binds values via DuckDB's prepared-statement
        #    parameter binding. No string interpolation of user values.
        result = execute_safe(conn, sql, bound_values).fetchdf()
        if result.empty:
            print(f"  {C.YELLOW}No results.{C.RESET}")
            return {"rows": 0, "data": []}
        _pd().set_option('display.max_columns', None)
        _pd().set_option('display.width', 120)
        print(result.to_string(index=False))
        print(f"\n  {C.DIM}({len(result)} rows){C.RESET}")
        return {"rows": len(result), "data": result.to_dict(orient="records")}
    except Exception as e:
        msg = f"{type(e).__name__}: {e}"
        print(f"  {C.RED}Error: {msg}{C.RESET}")
        return {"error": msg}
    finally:
        conn.close()


def interactive_shell(db_path=DB_PATH):
    """Interactive SQL shell with DuckDB.

    **DEVELOPER-ONLY.** This shell intentionally accepts arbitrary SQL —
    it is the only Cartograph surface that does. ChatGPT 2026-05-19
    explicit ruling: this is excluded from QUERY_SAFETY_CERTIFIED and MUST
    NOT be reachable from:
      - Claude tool-use (the agent has its own typed-tool surface)
      - Web UI / demo chatbot (route those through run_template() instead)
      - Any production caller

    Anyone wrapping `cartograph shell` behind a network listener is
    explicitly violating the certification contract.
    """
    print(f"""
  {C.CYAN}{C.BOLD}Scout Analytics — Interactive SQL Shell{C.RESET}
  {C.YELLOW}⚠ DEVELOPER-ONLY — accepts arbitrary SQL. Not certified for production use.{C.RESET}
  {C.DIM}Database: {db_path}{C.RESET}
  {C.DIM}Commands: .schema, .tables, .templates, .import <file>, .quit{C.RESET}
  {C.DIM}Type SQL directly or use templates.{C.RESET}
""")

    conn = duckdb.connect(str(db_path))

    while True:
        try:
            query = input(f"  {C.GREEN}sql>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye.")
            break

        if not query:
            continue
        if query in (".quit", ".exit", "quit", "exit"):
            break
        if query == ".schema" or query == ".tables":
            show_schema(db_path)
            continue
        if query == ".templates":
            show_templates()
            continue
        if query.startswith(".import "):
            filepath = query[8:].strip()
            import_file(filepath, db_path=db_path)
            # Reconnect to pick up new table
            conn.close()
            conn = duckdb.connect(str(db_path))
            continue

        try:
            result = conn.execute(query).fetchdf()
            if result.empty:
                print(f"  {C.DIM}(empty result){C.RESET}")
            else:
                _pd().set_option('display.max_columns', None)
                _pd().set_option('display.width', 120)
                _pd().set_option('display.max_rows', 50)
                print(result.to_string(index=False))
                print(f"  {C.DIM}({len(result)} rows){C.RESET}")
        except Exception as e:
            print(f"  {C.RED}{e}{C.RESET}")

    conn.close()


# ── Sample Data Generator ────────────────────────────────────────

def generate_sample(db_path=DB_PATH):
    """Generate sample transaction data matching Buff City Soap's structure."""
    import random
    from datetime import datetime, timedelta

    categories = ["Body Lotion", "Laundry Soap", "Bath Bomb", "Body Butter",
                  "Hand Soap", "Shower Oil", "Body Wash", "Sugar Scrub",
                  "Candle", "Dryer Ball"]

    fragrances = ["Lavender", "Eucalyptus Mint", "Beach", "Coconut Cream",
                  "Warm Vanilla", "Japanese Cherry Blossom", "Fresh Linen",
                  "Peppermint", "Lemon Drop", "Brown Sugar Fig",
                  "Grapefruit Mimosa", "Honeysuckle", "Rosemary Sage",
                  "Unscented", "Mango Tango", "Cotton Candy"]

    channels = ["In-Store", "Online", "Mobile App", "Subscription"]

    # Prices by category
    prices = {
        "Body Lotion": (12.0, 16.0), "Laundry Soap": (14.0, 18.0),
        "Bath Bomb": (6.0, 9.0), "Body Butter": (14.0, 18.0),
        "Hand Soap": (8.0, 12.0), "Shower Oil": (12.0, 15.0),
        "Body Wash": (10.0, 14.0), "Sugar Scrub": (12.0, 16.0),
        "Candle": (16.0, 24.0), "Dryer Ball": (4.0, 6.0),
    }

    num_guests = 5000
    num_transactions = 50000

    print(f"  Generating {num_transactions} transactions from {num_guests} guests...")

    # Give guests fragrance preferences (realistic behavior)
    guest_prefs = {}
    for g in range(1, num_guests + 1):
        # Each guest prefers 1-3 fragrances
        preferred = random.sample(fragrances, random.randint(1, 3))
        # And shops mostly via one channel
        primary_channel = random.choice(channels)
        guest_prefs[g] = {"fragrances": preferred, "channel": primary_channel}

    records = []
    start_date = datetime(2022, 1, 1)
    end_date = datetime(2026, 5, 1)
    days_range = (end_date - start_date).days

    for i in range(num_transactions):
        guest_id = random.randint(1, num_guests)
        prefs = guest_prefs[guest_id]

        # 70% chance they pick their preferred fragrance
        if random.random() < 0.7:
            fragrance = random.choice(prefs["fragrances"])
        else:
            fragrance = random.choice(fragrances)

        # 80% chance they use their primary channel
        if random.random() < 0.8:
            channel = prefs["channel"]
        else:
            channel = random.choice(channels)

        category = random.choice(categories)
        price_range = prices[category]
        price = round(random.uniform(*price_range), 2)

        # 20% chance of discount
        discount = 0.0
        discount_code = ""
        if random.random() < 0.2:
            discount = round(price * random.choice([0.1, 0.15, 0.2, 0.25]), 2)
            discount_code = random.choice(["WELCOME10", "LOYALTY15", "BOGO20", "SEASONAL25", "VIP20"])

        tx_date = start_date + timedelta(days=random.randint(0, days_range))
        tx_id = f"TX-{tx_date.strftime('%Y%m%d')}-{i:06d}"

        records.append({
            "transaction_id": tx_id,
            "guest_id": f"GUEST-{guest_id:05d}",
            "transaction_date": tx_date.strftime("%Y-%m-%d"),
            "product_type": category,
            "fragrance": fragrance,
            "price": price,
            "discount_amount": discount,
            "discount_code": discount_code,
            "channel": channel,
        })

    # Load into DuckDB
    conn = duckdb.connect(str(db_path))
    df = _pd().DataFrame(records)
    conn.execute("CREATE OR REPLACE TABLE transactions AS SELECT * FROM df")
    count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    conn.close()

    print(f"  {C.GREEN}Generated: {count:,} transactions → analytics.duckdb:transactions{C.RESET}")
    print(f"  Guests: {num_guests:,} | Date range: 2022-01-01 to 2026-05-01")
    print(f"  Categories: {len(categories)} | Fragrances: {len(fragrances)} | Channels: {len(channels)}")
    print(f"\n  {C.DIM}Now run: scout-analytics query \"SELECT ...\" or scout-analytics shell{C.RESET}")
    print(f"  {C.DIM}Or: scout-analytics template cross_sell_fragrance cat_a='Body Lotion' cat_b='Laundry Soap' start_date='2025-01-03' end_date='2026-01-02'{C.RESET}")


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Scout Analytics — Local SQL analytics for customer/purchase data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command")

    # import
    imp = sub.add_parser("import", help="Import a data file (CSV, Excel, JSON, Parquet)")
    imp.add_argument("file", help="Path to data file")
    imp.add_argument("--table", help="Table name (default: derived from filename)")
    imp.add_argument("--db", default=str(DB_PATH))

    # query
    q = sub.add_parser("query", help="Run a SQL query")
    q.add_argument("sql", help="SQL query string")
    q.add_argument("--db", default=str(DB_PATH))

    # template
    t = sub.add_parser("template", help="Run a query template with parameters")
    t.add_argument("name", help="Template name")
    t.add_argument("params", nargs="*", help="key=value parameters")
    t.add_argument("--db", default=str(DB_PATH))

    # schema
    sub.add_parser("schema", help="Show all tables and columns")

    # templates
    sub.add_parser("templates", help="List available query templates")

    # shell
    sub.add_parser("shell", help="Interactive SQL shell")

    # generate-sample
    sub.add_parser("generate-sample", help="Generate sample Buff City Soap-style data")

    # ask (conversational agent)
    ask_parser = sub.add_parser("ask", help="Ask a question in natural language (uses Claude)")
    ask_parser.add_argument("question", help="Your question about the data")

    # similar (fragrance similarity)
    sim_parser = sub.add_parser("similar", help="Find similar fragrances using embeddings")
    sim_parser.add_argument("fragrance", help="Fragrance name to find neighbors for")
    sim_parser.add_argument("--top", type=int, default=10, help="Number of results")
    sim_parser.add_argument("--threshold", type=float, default=0.3, help="Min similarity (0-1)")

    # embed (build/rebuild embedding cache)
    sub.add_parser("embed", help="Build/rebuild fragrance embedding cache")

    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "import":
        import_file(args.file, table_name=args.table, db_path=args.db)
    elif args.command == "query":
        conn = duckdb.connect(args.db)
        try:
            result = conn.execute(args.sql).fetchdf()
            _pd().set_option('display.max_columns', None)
            _pd().set_option('display.width', 120)
            print(result.to_string(index=False))
            print(f"\n  ({len(result)} rows)")
        except Exception as e:
            print(f"  Error: {e}")
        conn.close()
    elif args.command == "template":
        params = {}
        for p in (args.params or []):
            k, v = p.split("=", 1)
            params[k] = v.strip("'\"")
        run_template(args.name, params, db_path=args.db)
    elif args.command == "schema":
        show_schema()
    elif args.command == "templates":
        show_templates()
    elif args.command == "shell":
        interactive_shell()
    elif args.command == "ask":
        from .embeddings import FragranceEmbedder
        from .agent import run_agent
        embedder = FragranceEmbedder()
        run_agent(args.question, embedder=embedder)
    elif args.command == "similar":
        from .embeddings import FragranceEmbedder
        embedder = FragranceEmbedder()
        results = embedder.find_similar(args.fragrance, top_n=args.top, threshold=args.threshold)
        if not results:
            print(f"  Fragrance '{args.fragrance}' not found in taxonomy.")
        else:
            print(f"\n  Fragrances similar to '{args.fragrance}':")
            print(f"  {'─' * 50}")
            for name, score, family in results:
                bar = '█' * int(score * 20)
                print(f"  {score:.3f} {bar:20s} {name} ({family})")
    elif args.command == "embed":
        from .embeddings import FragranceEmbedder
        embedder = FragranceEmbedder(use_cache=False)
        stats = embedder.stats()
        print(f"  Embedded {stats['total_fragrances']} fragrances ({stats['embedding_dim']}d)")
        print(f"  Families: {', '.join(stats['families'])}")
        print(f"  Cache: {stats['cache_file']}")
    elif args.command == "generate-sample":
        generate_sample()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
