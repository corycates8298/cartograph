"""
Cartograph Agent — Conversational analytics via Claude tool-use.
Translates natural language questions into SQL + vector similarity queries.
"""

import json
import os
import sys
from pathlib import Path

import duckdb

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


# Tool definitions for Claude
TOOLS = [
    {
        "name": "query_transactions",
        "description": "Run a SQL query against the transactions database. Tables available: 'transactions' (guest_id, transaction_date, product_type, fragrance, price, discount_amount, discount_code, channel, transaction_id). Returns up to 50 rows.",
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute against DuckDB"
                }
            },
            "required": ["sql"]
        }
    },
    {
        "name": "find_similar_fragrances",
        "description": "Find fragrances similar to a given one using vector embeddings of scent profiles. Returns ranked list with similarity scores (0-1). Use this when the user asks about 'similar', 'like', 'related' scents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "fragrance_name": {
                    "type": "string",
                    "description": "The fragrance name to find neighbors for (e.g., 'Lavender', 'Fresh Cotton')"
                },
                "top_n": {
                    "type": "integer",
                    "description": "Number of similar fragrances to return (default: 5)",
                    "default": 5
                },
                "threshold": {
                    "type": "number",
                    "description": "Minimum similarity score (0-1, default: 0.4)",
                    "default": 0.4
                }
            },
            "required": ["fragrance_name"]
        }
    },
    {
        "name": "cross_sell_analysis",
        "description": "Find guests who purchased one category and also purchased another category in a similar fragrance. Uses vector similarity for scent matching.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category_a": {
                    "type": "string",
                    "description": "First product category (e.g., 'Body Lotion')"
                },
                "category_b": {
                    "type": "string",
                    "description": "Second product category to check cross-sell (e.g., 'Laundry Soap')"
                },
                "start_date": {
                    "type": "string",
                    "description": "Start date for cohort (YYYY-MM-DD)"
                },
                "end_date": {
                    "type": "string",
                    "description": "End date for cohort (YYYY-MM-DD)"
                },
                "similarity_threshold": {
                    "type": "number",
                    "description": "Minimum fragrance similarity (0-1, default: 0.6)",
                    "default": 0.6
                }
            },
            "required": ["category_a", "category_b", "start_date", "end_date"]
        }
    },
    {
        "name": "get_cohort_stats",
        "description": "Get statistics about a cohort of guests who purchased a specific category in a date range. Returns count, avg spend, top fragrances, channel mix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": "Product category"
                },
                "start_date": {"type": "string"},
                "end_date": {"type": "string"}
            },
            "required": ["category", "start_date", "end_date"]
        }
    },
    {
        "name": "export_segment",
        "description": "Export a list of guest IDs as a segment for marketing activation. Saves to CSV.",
        "input_schema": {
            "type": "object",
            "properties": {
                "guest_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of guest IDs to export"
                },
                "segment_name": {
                    "type": "string",
                    "description": "Name for the segment file"
                }
            },
            "required": ["guest_ids", "segment_name"]
        }
    },
]


def execute_tool(tool_name, tool_input, embedder=None):
    """Execute a tool call and return the result."""
    conn = duckdb.connect(str(DB_PATH))

    try:
        if tool_name == "query_transactions":
            sql = tool_input["sql"]
            # Safety: block destructive operations
            dangerous = ["DROP", "DELETE", "INSERT", "UPDATE", "ALTER", "CREATE"]
            if any(sql.upper().strip().startswith(d) for d in dangerous):
                return {"error": "Only SELECT queries are allowed."}
            result = conn.execute(sql).fetchdf()
            if len(result) > 50:
                result = result.head(50)
            return {"rows": len(result), "data": result.to_dict(orient="records")}

        elif tool_name == "find_similar_fragrances":
            if embedder is None:
                return {"error": "Embedding engine not initialized"}
            name = tool_input["fragrance_name"]
            top_n = tool_input.get("top_n", 5)
            threshold = tool_input.get("threshold", 0.4)
            results = embedder.find_similar(name, top_n=top_n, threshold=threshold)
            return {
                "query_fragrance": name,
                "similar": [
                    {"name": n, "similarity": s, "family": f}
                    for n, s, f in results
                ]
            }

        elif tool_name == "cross_sell_analysis":
            if embedder is None:
                return {"error": "Embedding engine not initialized"}

            cat_a = tool_input["category_a"]
            cat_b = tool_input["category_b"]
            start = tool_input["start_date"]
            end = tool_input["end_date"]
            threshold = tool_input.get("similarity_threshold", 0.6)

            # Get cohort A guests and their fragrances
            cohort = conn.execute(f"""
                SELECT DISTINCT guest_id, fragrance
                FROM transactions
                WHERE product_type = '{cat_a}'
                AND transaction_date BETWEEN '{start}' AND '{end}'
            """).fetchall()

            # For each guest in cohort, check if they bought cat_b in a similar fragrance
            cross_sell_guests = set()
            matches = []

            for guest_id, frag_a in cohort:
                if not frag_a:
                    continue
                # Get similar fragrances
                similar_set = embedder.get_similar_set(frag_a, threshold=threshold)
                similar_set.append(frag_a)  # Include exact match

                # Check if guest bought cat_b in any similar fragrance
                placeholders = ",".join(f"'{f}'" for f in similar_set)
                hits = conn.execute(f"""
                    SELECT fragrance, transaction_date
                    FROM transactions
                    WHERE guest_id = '{guest_id}'
                    AND product_type = '{cat_b}'
                    AND fragrance IN ({placeholders})
                    LIMIT 1
                """).fetchall()

                if hits:
                    cross_sell_guests.add(guest_id)
                    if len(matches) < 20:
                        matches.append({
                            "guest_id": guest_id,
                            "bought_a": f"{cat_a} ({frag_a})",
                            "bought_b": f"{cat_b} ({hits[0][0]})",
                            "similarity": embedder.get_similarity(frag_a, hits[0][0]) if hits[0][0] != frag_a else 1.0,
                        })

            total_cohort = len(set(g for g, _ in cohort))
            return {
                "cohort_size": total_cohort,
                "cross_sell_count": len(cross_sell_guests),
                "conversion_rate": round(len(cross_sell_guests) / max(total_cohort, 1) * 100, 1),
                "sample_matches": matches,
            }

        elif tool_name == "get_cohort_stats":
            cat = tool_input["category"]
            start = tool_input["start_date"]
            end = tool_input["end_date"]

            stats = conn.execute(f"""
                SELECT
                    COUNT(DISTINCT guest_id) as unique_guests,
                    COUNT(*) as total_purchases,
                    ROUND(AVG(price), 2) as avg_price,
                    ROUND(SUM(price), 2) as total_revenue
                FROM transactions
                WHERE product_type = '{cat}'
                AND transaction_date BETWEEN '{start}' AND '{end}'
            """).fetchdf().to_dict(orient="records")[0]

            top_frags = conn.execute(f"""
                SELECT fragrance, COUNT(*) as cnt
                FROM transactions
                WHERE product_type = '{cat}'
                AND transaction_date BETWEEN '{start}' AND '{end}'
                GROUP BY fragrance ORDER BY cnt DESC LIMIT 5
            """).fetchdf().to_dict(orient="records")

            channels = conn.execute(f"""
                SELECT channel, COUNT(*) as cnt
                FROM transactions
                WHERE product_type = '{cat}'
                AND transaction_date BETWEEN '{start}' AND '{end}'
                GROUP BY channel ORDER BY cnt DESC
            """).fetchdf().to_dict(orient="records")

            return {**stats, "top_fragrances": top_frags, "channels": channels}

        elif tool_name == "export_segment":
            guest_ids = tool_input["guest_ids"]
            name = tool_input["segment_name"]
            export_path = DATA_DIR / f"segments/{name}.csv"
            export_path.parent.mkdir(parents=True, exist_ok=True)

            import pandas as pd
            df = pd.DataFrame({"guest_id": guest_ids})
            df.to_csv(export_path, index=False)
            return {"exported": len(guest_ids), "path": str(export_path)}

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    finally:
        conn.close()


def run_agent(question, embedder=None, verbose=True):
    """
    Run the conversational agent on a natural language question.
    Uses Claude API with tool-use to answer.
    """
    try:
        import anthropic
    except ImportError:
        print(f"  {C.RED}Error: pip install anthropic{C.RESET}")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(f"  {C.RED}Error: ANTHROPIC_API_KEY not set{C.RESET}")
        return None

    client = anthropic.Anthropic(api_key=api_key)

    system_prompt = """You are Cartograph, a customer purchase analytics agent for retail businesses.
You have access to a transaction database and a fragrance similarity engine.

When answering questions:
1. Use query_transactions for SQL-based analysis
2. Use find_similar_fragrances when the user asks about scent similarity
3. Use cross_sell_analysis for "who bought X also bought Y" questions with fragrance matching
4. Use get_cohort_stats to understand a population before diving deeper
5. Use export_segment when the user wants an actionable list

Always provide:
- The quantitative answer (numbers, percentages)
- Business interpretation (what this means)
- Recommended next action (what to do with this insight)

The database has: transactions (transaction_id, guest_id, transaction_date, product_type, fragrance, price, discount_amount, discount_code, channel).
Product types include: Body Lotion, Laundry Soap, Bath Bomb, Body Butter, Hand Soap, Shower Oil, Body Wash, Sugar Scrub, Candle, Dryer Ball.
Fragrances include: Lavender, Eucalyptus Mint, Beach, Coconut Cream, Warm Vanilla, Japanese Cherry Blossom, Fresh Linen, Peppermint, Lemon Drop, Brown Sugar Fig, Grapefruit Mimosa, Honeysuckle, Rosemary Sage, Unscented, Mango Tango, Cotton Candy.
Channels: In-Store, Online, Mobile App, Subscription.
Date range: 2022-01-01 to 2026-05-01."""

    messages = [{"role": "user", "content": question}]

    if verbose:
        print(f"\n  {C.CYAN}{C.BOLD}Cartograph Agent{C.RESET}")
        print(f"  {C.DIM}Question: {question}{C.RESET}\n")

    # Agentic loop — keep going until Claude gives a final answer
    max_turns = 10
    for turn in range(max_turns):
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=system_prompt,
            tools=TOOLS,
            messages=messages,
        )

        # Process response
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Check if done (no tool use)
        if response.stop_reason == "end_turn":
            # Extract text response
            for block in assistant_content:
                if hasattr(block, "text"):
                    if verbose:
                        print(f"  {block.text}")
                    return block.text
            break

        # Handle tool use
        tool_results = []
        for block in assistant_content:
            if block.type == "tool_use":
                if verbose:
                    print(f"  {C.DIM}→ Calling {block.name}...{C.RESET}")

                result = execute_tool(block.name, block.input, embedder=embedder)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result, default=str),
                })

        if tool_results:
            messages.append({"role": "user", "content": tool_results})
        else:
            break

    return None
