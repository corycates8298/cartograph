# Cartograph

**Customer purchase analytics engine** — map the terrain of buyer behavior using vector embeddings and conversational AI.

Answers questions no standard BI tool can:

> "If a guest purchased body lotion between January 3, 2025 and January 2, 2026, how many also purchased laundry soap in a **similar** fragrance?"

The word "similar" is what kills ThoughtSpot, Fabric, and Tableau. Cartograph solves it with scent-profile vector embeddings — Lavender matches Hey Headache (both calming/herbal), Grapefruit Mimosa matches Lemon Drop (both tart citrus), not because of SQL JOINs but because of cosine similarity in embedding space.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    cartograph ask                             │
│         "how many body lotion buyers also bought             │
│          laundry soap in a similar fragrance?"               │
└─────────────────────┬───────────────────────────────────────┘
                      │
         ┌────────────┼────────────┐
         ▼            ▼            ▼
┌─────────────┐ ┌──────────┐ ┌──────────────┐
│ Claude Agent│ │ DuckDB   │ │ Embeddings   │
│ (tool-use)  │ │ (SQL)    │ │ (similarity) │
│             │ │          │ │              │
│ NL → tools  │ │ Cohorts  │ │ 51 scents    │
│ + actions   │ │ Joins    │ │ 384-dim vecs │
│             │ │ Agg      │ │ cosine sim   │
└─────────────┘ └──────────┘ └──────────────┘
         │            │            │
         └────────────┼────────────┘
                      ▼
              ┌──────────────┐
              │   Answer +   │
              │   Next Step  │
              │   Action     │
              └──────────────┘
```

## Three Layers

| Layer | What it solves | Tech |
|-------|---------------|------|
| **SQL Engine** | Cohort filtering, cross-category joins, date ranges, aggregation | DuckDB |
| **Embedding Layer** | "Similar fragrance" = cosine similarity on scent note profiles | sentence-transformers (all-MiniLM-L6-v2) |
| **Conversational Agent** | Natural language → SQL + vector query → answer + recommendations | Claude API (tool-use) |

## Installation

```bash
python3 -m venv ~/cartograph-env
source ~/cartograph-env/bin/activate
pip install -e .

# Build fragrance embeddings (one-time, ~5 seconds)
cartograph embed
```

## Usage

### Natural Language Queries (Agent Mode)

```bash
# Ask anything about the data
cartograph ask "how many guests who bought body lotion also bought laundry soap in a similar fragrance?"
cartograph ask "what are the top cross-sell opportunities for lavender buyers?"
cartograph ask "which channel drives the most repeat purchases?"
cartograph ask "show me fragrance loyalty across categories for the last year"
```

### Fragrance Similarity

```bash
# Find similar scents
cartograph similar "Lavender"
cartograph similar "Fresh Cotton" --top 10 --threshold 0.4

# Output:
#   0.736 Hey Headache (Herbal)
#   0.644 Rosemary Sage (Herbal)
#   0.601 Oatmeal + Honey (Gourmand)
```

### SQL Templates

```bash
# Pre-built analytics queries
cartograph template cross_sell_fragrance \
    cat_a='Body Lotion' cat_b='Laundry Soap' \
    start_date='2025-01-03' end_date='2026-01-02'

cartograph template category_affinity \
    category='Bath Bomb' start_date='2025-01-01' end_date='2026-01-01'

cartograph template fragrance_loyalty \
    start_date='2024-01-01' end_date='2026-01-01'

cartograph templates  # list all 8 templates
```

### Data Operations

```bash
# Import real client data
cartograph import transactions.csv
cartograph import purchases.xlsx --table orders

# Scrape product catalog
catalog-scraper https://buffcitysoap.com

# Generate demo data
cartograph generate-sample

# Interactive SQL
cartograph shell

# View schema
cartograph schema
```

## Query Templates

| Template | Question it answers |
|----------|-------------------|
| `cross_sell_fragrance` | Who bought A AND B in same/similar scent? |
| `category_affinity` | What else do X buyers purchase? |
| `fragrance_loyalty` | Do guests repeat scents across categories? |
| `market_basket` | Products bought together in same transaction |
| `discount_impact` | How do discounts affect behavior? |
| `channel_performance` | Revenue/guests by channel |
| `repeat_buyers` | Frequency, recency, lifetime value |
| `fragrance_popularity` | Top scents by category |

## Fragrance Taxonomy

51 fragrances mapped to scent note profiles across 8 families:

| Family | Examples | Notes Embedded |
|--------|----------|---------------|
| Floral | Lavender, Magnolia, White Jasmine | "french lavender fields, calming purple floral, herbal relaxation" |
| Citrus | 99 PomLems, Good Morning Sunshine, Lemon Drop | "bright morning citrus, orange juice, sunny grapefruit" |
| Fresh | Fresh Cotton, Aqua Spa, Cobalt Blue | "clean laundry, fresh linen, soft cotton, airy white musk" |
| Woody | Commando, Patchouli, Sandalwood | "rugged masculine cologne, cedar, leather, fresh bergamot" |
| Sweet | Pink Sugar, Warm Vanilla, Narcissist | "cotton candy, pink spun sugar, sweet carnival, girly vanilla" |
| Gourmand | Cherry Almond, Coconut Cream, Fruity Loopy | "sweet cherry, toasted almond, marzipan, bakery warmth" |
| Herbal | Eucalyptus, Breathe, Rosemary Sage | "pure eucalyptus leaf, camphor, green medicinal, spa steam" |
| Fruity | Island Nectar, Mango Tango, Peach Mimosa | "tropical island fruits, passionfruit, guava, sweet nectar" |

The embedding engine uses these note profiles (not names) to compute similarity — so "Grapefruit Mimosa" correctly matches "Lemon Drop" (0.715 similarity) even though they share zero words.

## Red Team Results

```
33/33 tests passed (100%)
- Taxonomy: 8/8
- Embeddings: 10/10
- Agent Safety: 7/7 (blocks SQL injection, handles missing deps)
- Similarity Quality: 8/8 (intuitive pairs match, dissimilar pairs don't)
```

## Why This Exists

Standard BI tools (ThoughtSpot, Tableau, Fabric) can handle:
- Cohort filtering (date-bounded populations)
- Cross-category joins (did cohort also buy X?)

But they **cannot** handle:
- **Semantic matching** — "similar fragrance" isn't a JOIN key

Cartograph solves this by embedding scent profiles into vector space, making "similarity" a computable function rather than a manual taxonomy. This is the same approach used by Symrise/IBM's Philyra system for commercial fragrance development, applied here to retail cross-sell analytics.

## License

MIT
