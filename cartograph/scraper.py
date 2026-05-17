#!/usr/bin/env python3
"""
Catalog Scraper — Scrapes product catalogs from Shopify stores into structured data.
Outputs JSON + loads into DuckDB for querying.

Usage:
    catalog-scraper https://buffcitysoap.com
    catalog-scraper https://buffcitysoap.com --output ~/osint/data/buffcity.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

DATA_DIR = Path.home() / "osint" / "data"


def scrape_shopify_catalog(base_url):
    """Scrape all products from a Shopify store via /products.json API."""
    products = []
    page = 1
    base_url = base_url.rstrip("/")

    print(f"  Scraping {base_url}/products.json ...")

    while True:
        url = f"{base_url}/products.json?limit=250&page={page}"
        try:
            resp = requests.get(url, timeout=30, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            })
            if resp.status_code != 200:
                print(f"  Page {page}: HTTP {resp.status_code}, stopping.")
                break

            data = resp.json()
            batch = data.get("products", [])
            if not batch:
                break

            products.extend(batch)
            print(f"  Page {page}: {len(batch)} products (total: {len(products)})")
            page += 1
            time.sleep(0.5)  # Be polite

        except Exception as e:
            print(f"  Error on page {page}: {e}")
            break

    return products


def normalize_products(raw_products, store_name=""):
    """Normalize Shopify product data into clean structured records."""
    normalized = []

    for p in raw_products:
        # Extract category from product_type or tags
        category = p.get("product_type", "").strip() or "Uncategorized"
        tags = [t.strip() for t in p.get("tags", "").split(",") if t.strip()] if isinstance(p.get("tags"), str) else p.get("tags", [])

        # Try to extract fragrance from title, tags, or options
        fragrance = ""
        for option in p.get("options", []):
            if option.get("name", "").lower() in ("scent", "fragrance", "smell", "aroma"):
                fragrance = ", ".join(option.get("values", []))
                break

        # If no fragrance option, check tags
        if not fragrance:
            frag_tags = [t for t in tags if any(kw in t.lower() for kw in ["scent", "fragrance"])]
            if frag_tags:
                fragrance = frag_tags[0]

        for variant in p.get("variants", []):
            record = {
                "store": store_name,
                "product_id": p.get("id"),
                "product_title": p.get("title", ""),
                "product_type": category,
                "vendor": p.get("vendor", ""),
                "tags": tags,
                "variant_id": variant.get("id"),
                "variant_title": variant.get("title", ""),
                "sku": variant.get("sku", ""),
                "price": float(variant.get("price", 0)),
                "compare_at_price": float(variant.get("compare_at_price", 0)) if variant.get("compare_at_price") else None,
                "available": variant.get("available", True),
                "fragrance": fragrance or variant.get("title", ""),
                "created_at": p.get("created_at", ""),
                "updated_at": p.get("updated_at", ""),
                "image_url": p.get("images", [{}])[0].get("src", "") if p.get("images") else "",
                "handle": p.get("handle", ""),
                "url": f"https://{store_name}/products/{p.get('handle', '')}",
            }
            normalized.append(record)

    return normalized


def load_to_duckdb(records, db_path, table_name="products"):
    """Load normalized products into DuckDB."""
    import duckdb

    conn = duckdb.connect(str(db_path))

    conn.execute(f"DROP TABLE IF EXISTS {table_name}")
    conn.execute(f"""
        CREATE TABLE {table_name} (
            store VARCHAR,
            product_id BIGINT,
            product_title VARCHAR,
            product_type VARCHAR,
            vendor VARCHAR,
            tags VARCHAR[],
            variant_id BIGINT,
            variant_title VARCHAR,
            sku VARCHAR,
            price DOUBLE,
            compare_at_price DOUBLE,
            available BOOLEAN,
            fragrance VARCHAR,
            created_at VARCHAR,
            updated_at VARCHAR,
            image_url VARCHAR,
            handle VARCHAR,
            url VARCHAR
        )
    """)

    for r in records:
        conn.execute(f"""
            INSERT INTO {table_name} VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, [
            r["store"], r["product_id"], r["product_title"], r["product_type"],
            r["vendor"], r["tags"], r["variant_id"], r["variant_title"],
            r["sku"], r["price"], r["compare_at_price"], r["available"],
            r["fragrance"], r["created_at"], r["updated_at"], r["image_url"],
            r["handle"], r["url"]
        ])

    count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    conn.close()
    return count


def main():
    parser = argparse.ArgumentParser(description="Scrape product catalogs from Shopify stores")
    parser.add_argument("url", help="Store URL (e.g. https://buffcitysoap.com)")
    parser.add_argument("--output", help="Output JSON path", default=None)
    parser.add_argument("--db", help="DuckDB path", default=str(DATA_DIR / "catalog.duckdb"))
    parser.add_argument("--table", help="Table name in DuckDB", default="products")

    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Derive store name from URL
    store_name = args.url.replace("https://", "").replace("http://", "").rstrip("/")

    # Scrape
    raw = scrape_shopify_catalog(args.url)
    if not raw:
        print("  No products found. Is this a Shopify store?")
        sys.exit(1)

    print(f"\n  Raw products: {len(raw)}")

    # Normalize
    records = normalize_products(raw, store_name=store_name)
    print(f"  Normalized variants: {len(records)}")

    # Save JSON
    json_path = args.output or str(DATA_DIR / f"{store_name.replace('.', '_')}.json")
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    print(f"  JSON saved: {json_path}")

    # Load to DuckDB
    count = load_to_duckdb(records, args.db, args.table)
    print(f"  DuckDB loaded: {count} rows → {args.db}:{args.table}")

    # Print summary stats
    import duckdb
    conn = duckdb.connect(args.db)
    print(f"\n  === Catalog Summary ===")
    print(f"  Categories:")
    for row in conn.execute(f"SELECT product_type, COUNT(*) as cnt FROM {args.table} GROUP BY product_type ORDER BY cnt DESC LIMIT 15").fetchall():
        print(f"    {row[0]}: {row[1]} variants")
    print(f"\n  Price range: ${conn.execute(f'SELECT MIN(price), MAX(price) FROM {args.table}').fetchone()}")
    print(f"  Unique fragrances: {conn.execute(f'SELECT COUNT(DISTINCT fragrance) FROM {args.table}').fetchone()[0]}")
    conn.close()


if __name__ == "__main__":
    main()
