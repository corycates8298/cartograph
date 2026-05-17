# Cartograph

**Customer purchase analytics engine** — map the terrain of buyer behavior.

Import transaction data (CSV, Excel, JSON, Parquet), scrape product catalogs from Shopify stores, and run pre-built analytics queries against DuckDB. Answers questions like:

> "If a guest purchased body lotion between January 3, 2025 and January 2, 2026, how many also purchased laundry soap in a similar fragrance?"

## Features

- **Shopify catalog scraper** — pulls full product catalog (categories, prices, fragrances, variants)
- **Multi-format import** — CSV, Excel (.xlsx), JSON, Parquet → DuckDB
- **8 pre-built query templates** — cross-sell, affinity, market basket, fragrance loyalty, and more
- **Interactive SQL shell** — DuckDB-powered, with `.schema`, `.import`, `.templates` commands
- **Sample data generator** — realistic 50K transactions from 5K guests for demos
- **Local-first** — all data stays on your machine, no cloud dependency

## Installation

```bash
python3 -m venv ~/cartograph-env
source ~/cartograph-env/bin/activate
pip install -e .
```

## Quick Start

```bash
# Scrape a Shopify store's product catalog
catalog-scraper https://buffcitysoap.com

# Generate sample transaction data for demos
cartograph generate-sample

# Answer: who bought body lotion AND laundry soap in same fragrance?
cartograph template cross_sell_fragrance \
    cat_a='Body Lotion' \
    cat_b='Laundry Soap' \
    start_date='2025-01-03' \
    end_date='2026-01-02'

# Import real client data
cartograph import customer_transactions.csv
cartograph import purchases.xlsx --table orders

# Interactive SQL shell
cartograph shell

# One-shot query
cartograph query "SELECT product_type, COUNT(*) FROM transactions GROUP BY 1"
```

## Query Templates

| Template | What it answers |
|----------|----------------|
| `cross_sell_fragrance` | Guests who bought Category A AND Category B in same/similar fragrance |
| `category_affinity` | What categories do buyers of X also purchase? |
| `fragrance_loyalty` | Do guests stick to the same fragrance across categories? |
| `market_basket` | Products frequently purchased together in same transaction |
| `discount_impact` | How do discounts affect purchase behavior? |
| `channel_performance` | Revenue and guest count by purchase channel |
| `repeat_buyers` | Frequency, recency, lifetime value analysis |
| `fragrance_popularity` | Most popular fragrances by category |

### Template Usage

```bash
cartograph template cross_sell_fragrance \
    cat_a='Body Lotion' \
    cat_b='Laundry Soap' \
    start_date='2025-01-03' \
    end_date='2026-01-02'

cartograph template category_affinity \
    category='Bath Bomb' \
    start_date='2025-01-01' \
    end_date='2026-01-01'

cartograph template repeat_buyers \
    start_date='2022-01-01' \
    end_date='2026-01-01' \
    min_purchases=5
```

## Data Schema

### Expected Transaction Fields

When importing real data, Cartograph works best with these columns:

| Field | Type | Description |
|-------|------|-------------|
| `transaction_id` | string | Unique order/transaction ID |
| `guest_id` | string | Customer/guest identifier |
| `transaction_date` | date | Purchase date (YYYY-MM-DD) |
| `product_type` | string | Category (Body Lotion, Laundry Soap, etc.) |
| `fragrance` | string | Scent/fragrance name |
| `price` | decimal | Purchase price |
| `discount_amount` | decimal | Discount applied |
| `discount_code` | string | Promo code used |
| `channel` | string | Purchase channel (In-Store, Online, Mobile App) |

Column names are flexible — DuckDB reads whatever columns exist in your file. Templates assume the field names above but you can customize queries in the shell.

## Catalog Scraper

Pulls the full product catalog from any Shopify store:

```bash
catalog-scraper https://buffcitysoap.com
catalog-scraper https://store.example.com --output ~/data/products.json
```

Extracts:
- Product titles, types (categories), vendors
- All variants with SKUs and prices
- Fragrances (from options, tags, or variant names)
- Images, URLs, availability
- Compare-at prices (discount detection)

## Architecture

```
~/osint/data/
├── catalog.duckdb         # Scraped product catalogs
├── analytics.duckdb       # Transaction data (imported or generated)
├── buffcitysoap_com.json  # Raw catalog JSON
└── reports/               # OSINT reports (from scout-osint)
```

All powered by [DuckDB](https://duckdb.org/) — fast columnar analytics, zero dependencies, runs locally.

## License

MIT
