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
import pandas as pd

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

TEMPLATES = {
    "cross_sell_fragrance": {
        "description": "Guests who bought [Category A] AND [Category B] in similar fragrance",
        "sql": """
-- Cross-sell: Guests who bought {cat_a} AND {cat_b} in similar fragrance
-- Between {start_date} and {end_date}
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
    AND b.product_type = '{cat_b}'
    AND (
        b.fragrance = a.fragrance
        OR b.fragrance ILIKE '%' || SPLIT_PART(a.fragrance, ' ', 1) || '%'
    )
WHERE a.product_type = '{cat_a}'
    AND a.transaction_date BETWEEN '{start_date}' AND '{end_date}'
ORDER BY a.guest_id, a.transaction_date;
""",
        "example": "cross_sell_fragrance cat_a='Body Lotion' cat_b='Laundry Soap' start_date='2025-01-03' end_date='2026-01-02'"
    },

    "category_affinity": {
        "description": "What categories do buyers of [Category] also purchase?",
        "sql": """
-- Category affinity: What else do {category} buyers purchase?
WITH target_guests AS (
    SELECT DISTINCT guest_id
    FROM transactions
    WHERE product_type ILIKE '%{category}%'
        AND transaction_date BETWEEN '{start_date}' AND '{end_date}'
)
SELECT
    t.product_type,
    COUNT(DISTINCT t.guest_id) AS unique_guests,
    COUNT(*) AS total_purchases,
    ROUND(COUNT(DISTINCT t.guest_id) * 100.0 / (SELECT COUNT(*) FROM target_guests), 1) AS pct_of_target
FROM transactions t
JOIN target_guests tg ON t.guest_id = tg.guest_id
WHERE t.product_type NOT ILIKE '%{category}%'
GROUP BY t.product_type
ORDER BY unique_guests DESC;
""",
        "example": "category_affinity category='Body Lotion' start_date='2025-01-01' end_date='2026-01-01'"
    },

    "fragrance_loyalty": {
        "description": "Do guests stick to the same fragrance across categories?",
        "sql": """
-- Fragrance loyalty: Do guests repeat the same fragrance across categories?
SELECT
    guest_id,
    fragrance,
    COUNT(DISTINCT product_type) AS categories_purchased,
    ARRAY_AGG(DISTINCT product_type) AS category_list,
    COUNT(*) AS total_purchases,
    SUM(price) AS total_spend
FROM transactions
WHERE fragrance IS NOT NULL AND fragrance != ''
    AND transaction_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY guest_id, fragrance
HAVING COUNT(DISTINCT product_type) >= 2
ORDER BY categories_purchased DESC, total_spend DESC
LIMIT 100;
""",
        "example": "fragrance_loyalty start_date='2024-01-01' end_date='2026-01-01'"
    },

    "discount_impact": {
        "description": "How do discounts affect purchase behavior?",
        "sql": """
-- Discount impact on purchase behavior
SELECT
    CASE WHEN discount_amount > 0 THEN 'Discounted' ELSE 'Full Price' END AS purchase_type,
    product_type,
    COUNT(*) AS transactions,
    COUNT(DISTINCT guest_id) AS unique_guests,
    ROUND(AVG(price), 2) AS avg_price,
    ROUND(AVG(discount_amount), 2) AS avg_discount,
    ROUND(SUM(price - COALESCE(discount_amount, 0)), 2) AS net_revenue
FROM transactions
WHERE transaction_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY 1, product_type
ORDER BY product_type, purchase_type;
""",
        "example": "discount_impact start_date='2025-01-01' end_date='2026-01-01'"
    },

    "channel_performance": {
        "description": "Revenue and guest count by purchase channel",
        "sql": """
-- Channel performance
SELECT
    channel,
    COUNT(DISTINCT guest_id) AS unique_guests,
    COUNT(*) AS transactions,
    ROUND(SUM(price), 2) AS gross_revenue,
    ROUND(AVG(price), 2) AS avg_order_value,
    ROUND(SUM(discount_amount), 2) AS total_discounts,
    COUNT(DISTINCT product_type) AS categories_sold
FROM transactions
WHERE transaction_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY channel
ORDER BY gross_revenue DESC;
""",
        "example": "channel_performance start_date='2025-01-01' end_date='2026-01-01'"
    },

    "repeat_buyers": {
        "description": "Guests with multiple purchases — frequency and recency",
        "sql": """
-- Repeat buyer analysis
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
WHERE transaction_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY guest_id
HAVING COUNT(*) >= {min_purchases}
ORDER BY lifetime_value DESC
LIMIT 100;
""",
        "example": "repeat_buyers start_date='2022-01-01' end_date='2026-01-01' min_purchases=3"
    },

    "fragrance_popularity": {
        "description": "Most popular fragrances by category",
        "sql": """
-- Fragrance popularity by category
SELECT
    product_type,
    fragrance,
    COUNT(*) AS purchases,
    COUNT(DISTINCT guest_id) AS unique_guests,
    ROUND(SUM(price), 2) AS revenue
FROM transactions
WHERE fragrance IS NOT NULL AND fragrance != ''
    AND transaction_date BETWEEN '{start_date}' AND '{end_date}'
GROUP BY product_type, fragrance
ORDER BY product_type, purchases DESC;
""",
        "example": "fragrance_popularity start_date='2025-01-01' end_date='2026-01-01'"
    },

    "market_basket": {
        "description": "Products frequently purchased together in same transaction",
        "sql": """
-- Market basket: products bought together
WITH order_pairs AS (
    SELECT
        a.transaction_id,
        a.product_type AS product_a,
        b.product_type AS product_b
    FROM transactions a
    JOIN transactions b
        ON a.transaction_id = b.transaction_id
        AND a.product_type < b.product_type
    WHERE a.transaction_date BETWEEN '{start_date}' AND '{end_date}'
)
SELECT
    product_a,
    product_b,
    COUNT(*) AS co_occurrences,
    ROUND(COUNT(*) * 100.0 / (SELECT COUNT(DISTINCT transaction_id) FROM transactions WHERE transaction_date BETWEEN '{start_date}' AND '{end_date}'), 2) AS pct_of_orders
FROM order_pairs
GROUP BY product_a, product_b
ORDER BY co_occurrences DESC
LIMIT 20;
""",
        "example": "market_basket start_date='2025-01-01' end_date='2026-01-01'"
    },
}


# ── Import Functions ─────────────────────────────────────────────

def import_file(filepath, table_name=None, db_path=DB_PATH):
    """Import CSV, Excel, JSON, or Parquet into DuckDB."""
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"  {C.RED}File not found: {filepath}{C.RESET}")
        return

    if not table_name:
        table_name = filepath.stem.lower().replace(" ", "_").replace("-", "_")

    conn = duckdb.connect(str(db_path))

    ext = filepath.suffix.lower()
    if ext == ".csv":
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_csv_auto('{filepath}')")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(filepath)
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
    elif ext == ".json":
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_json_auto('{filepath}')")
    elif ext == ".parquet":
        conn.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM read_parquet('{filepath}')")
    else:
        print(f"  {C.RED}Unsupported format: {ext}{C.RESET}")
        conn.close()
        return

    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    cols = conn.execute(f"DESCRIBE {table_name}").fetchall()
    conn.close()

    print(f"  {C.GREEN}Imported: {filepath.name} → {table_name}{C.RESET}")
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
    """Run a template with parameters."""
    if template_name not in TEMPLATES:
        print(f"  {C.RED}Unknown template: {template_name}{C.RESET}")
        print(f"  Available: {', '.join(TEMPLATES.keys())}")
        return

    sql = TEMPLATES[template_name]["sql"]
    try:
        sql = sql.format(**params)
    except KeyError as e:
        print(f"  {C.RED}Missing parameter: {e}{C.RESET}")
        print(f"  Example: {TEMPLATES[template_name]['example']}")
        return

    print(f"  {C.DIM}Running:{C.RESET}")
    print(f"  {C.DIM}{sql.strip()[:200]}...{C.RESET}\n")

    conn = duckdb.connect(str(db_path))
    try:
        result = conn.execute(sql).fetchdf()
        if result.empty:
            print(f"  {C.YELLOW}No results.{C.RESET}")
        else:
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 120)
            print(result.to_string(index=False))
            print(f"\n  {C.DIM}({len(result)} rows){C.RESET}")
    except Exception as e:
        print(f"  {C.RED}Error: {e}{C.RESET}")
    finally:
        conn.close()


def interactive_shell(db_path=DB_PATH):
    """Interactive SQL shell with DuckDB."""
    print(f"""
  {C.CYAN}{C.BOLD}Scout Analytics — Interactive SQL Shell{C.RESET}
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
                pd.set_option('display.max_columns', None)
                pd.set_option('display.width', 120)
                pd.set_option('display.max_rows', 50)
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
    df = pd.DataFrame(records)
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

    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.command == "import":
        import_file(args.file, table_name=args.table, db_path=args.db)
    elif args.command == "query":
        conn = duckdb.connect(args.db)
        try:
            result = conn.execute(args.sql).fetchdf()
            pd.set_option('display.max_columns', None)
            pd.set_option('display.width', 120)
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
    elif args.command == "generate-sample":
        generate_sample()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
