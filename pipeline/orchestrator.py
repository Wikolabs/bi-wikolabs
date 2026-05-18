"""Orchestrator — coordinates all 6 pipeline steps with parallel execution."""

import asyncio
from typing import Optional

import pandas as pd
import plotly.graph_objects as go

from db import get_db
from pipeline.analyzer import analyze
from pipeline.collection_selector import select
from pipeline.entity_resolver import resolve
from pipeline.executor import execute_with_retry
from pipeline.mql_generator import generate
from pipeline.responder import respond

GREETING_RESPONSE = """👋 Hello! I'm **BI Wikolabs**, your enterprise analytics assistant.

I can answer business questions by querying your MongoDB database in real time. Here's what I can analyze:

| Domain | Examples |
|---|---|
| 💰 Sales & Revenue | Monthly trends, regional performance, order values |
| 📦 Products | Top sellers, margins, inventory, categories |
| 👥 Customers | Segments (Enterprise/SMB/Individual), lifetime value |
| 👔 HR & Employees | Headcount, payroll, performance by department |
| 💳 Finance | Cash flow, expense breakdown, transaction status |

**Try asking:**
- *"What are the top 5 products by revenue?"*
- *"Show monthly cash flow for the last year"*
- *"Which customer segment is most profitable?"*

What would you like to explore? 📊"""


async def run(question: str) -> tuple[str, Optional[go.Figure], Optional[pd.DataFrame]]:
    """Execute the full BI pipeline for a user question.

    Steps:
        1. Analyze the question (classifier + entity extraction)
        2. Parallel: resolve entities + select collections
        3. Generate MQL query
        4. Execute with retry
        5. Generate response (narrative + chart + dataframe)

    Returns:
        (narrative, figure, dataframe) — figure and dataframe may be None
    """
    db = get_db()

    # ── Step 1: Analyze ────────────────────────────────────────────────────────
    analysis = await analyze(question)

    # Short-circuit for small talk
    if analysis.get("query_type") == "smalltalk":
        return GREETING_RESPONSE, None, None

    entities = analysis.get("entities", [])
    intent = analysis.get("intent", question)
    query_type = analysis.get("query_type", "metric")

    # ── Steps 2 & 3: Parallel entity resolution + collection selection ─────────
    entity_map, collections = await asyncio.gather(
        resolve(entities, db),
        asyncio.to_thread(select, intent, query_type, entities),
    )

    # Fallback if no collections selected
    if not collections:
        collections = ["sales"]

    # ── Step 4: Generate MQL ───────────────────────────────────────────────────
    spec = await generate(
        question=question,
        analysis=analysis,
        collections=collections,
        entity_map=entity_map,
    )

    # ── Step 5: Execute with retry ─────────────────────────────────────────────
    results = await execute_with_retry(
        spec=spec,
        question=question,
        analysis=analysis,
        collections=collections,
        entity_map=entity_map,
        db=db,
    )

    # ── Optional drill-down for WHY questions ──────────────────────────────────
    if analysis.get("is_why_question") and results:
        drill_question = f"Why: {question} — context: {results[:5]}"
        drill_analysis = {**analysis, "is_why_question": False, "query_type": "comparison"}
        drill_spec = await generate(
            question=drill_question,
            analysis=drill_analysis,
            collections=collections,
            entity_map=entity_map,
        )
        try:
            drill_results = await execute_with_retry(
                spec=drill_spec,
                question=drill_question,
                analysis=drill_analysis,
                collections=collections,
                entity_map=entity_map,
                db=db,
            )
            results = results + drill_results
        except Exception:
            # Drill-down is best-effort; continue with primary results
            pass

    # ── Step 6: Respond ────────────────────────────────────────────────────────
    narrative, figure, df = await respond(question, analysis, results)

    return narrative, figure, df
