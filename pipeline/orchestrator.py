"""Orchestrator — coordinates all 6 pipeline steps with parallel execution."""

import asyncio
import json
import re
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from groq import AsyncGroq

from db import get_db
from pipeline.analyzer import analyze
from pipeline.collection_selector import select
from pipeline.entity_resolver import resolve
from pipeline.executor import execute_with_retry
from pipeline.mql_generator import generate
from pipeline.responder import respond

import os

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))

GREETING_RESPONSE = """Hello! I'm **BI Wikolabs**, your enterprise analytics assistant.

I can answer business questions by querying your MongoDB database in real time. Here's what I can analyze:

| Domain | Examples |
|---|---|
| Sales & Revenue | Monthly trends, regional performance, order values |
| Products | Top sellers, margins, inventory, categories |
| Customers | Segments (Enterprise/SMB/Individual), lifetime value |
| HR & Employees | Headcount, payroll, performance by department |
| Finance | Cash flow, expense breakdown, transaction status |
| Contacts | Named contacts at customer companies |
| Suppliers | Vendor info, lead times, product sourcing |

**Try asking:**
- *"What are the top 5 products by revenue?"*
- *"Show monthly cash flow for the last year"*
- *"Which customer segment is most profitable?"*
- *"What orders did Marcus Johnson manage?"*
- *"Show me all orders for Acme Global Corp"*

What would you like to explore?"""


async def _decompose(question: str) -> list[str]:
    """Split a compound question into independent sub-questions, or return [question].

    Only calls the LLM when obvious compound patterns are detected.
    """
    if not re.search(
        r'\b(and also|as well as|additionally|both .+ and)\b',
        question.lower()
    ):
        return [question]

    try:
        resp = await _groq.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Split this compound business question into independent sub-questions. "
                        "Return ONLY a JSON array of strings. "
                        "If it is not compound, return an array with the original question. "
                        'Example: ["Show monthly revenue trend", "Show top 5 products by margin"]'
                    ),
                },
                {"role": "user", "content": question},
            ],
            temperature=0,
            max_tokens=150,
        )
        raw = (resp.choices[0].message.content or "").strip()
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            parts = json.loads(match.group())
            if isinstance(parts, list) and len(parts) > 1:
                return parts
    except Exception:
        pass
    return [question]


async def _run_single(question: str, db) -> list:
    """Run steps 1-5 for a single question and return raw results."""
    analysis   = await analyze(question)

    if analysis.get("query_type") == "smalltalk":
        return []

    entities   = analysis.get("entities", [])
    intent     = analysis.get("intent", question)
    query_type = analysis.get("query_type", "metric")

    entity_map, collections = await asyncio.gather(
        resolve(entities, db),
        select(intent, query_type, entities, db=db),
    )

    if not collections:
        collections = ["orders"]

    spec = await generate(
        question=question,
        analysis=analysis,
        collections=collections,
        entity_map=entity_map,
    )

    results = await execute_with_retry(
        spec=spec,
        question=question,
        analysis=analysis,
        collections=collections,
        entity_map=entity_map,
        db=db,
    )

    # Optional drill-down for WHY questions
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
            pass  # Drill-down is best-effort

    return results


async def run(question: str) -> tuple[str, Optional[go.Figure], Optional[pd.DataFrame]]:
    """Execute the full BI pipeline for a user question.

    Steps:
        0. Decompose compound questions into sub-questions
        1. Analyze each sub-question (classifier + entity extraction)
        2. Parallel: resolve entities + select collections
        3. Generate MQL query
        4. Execute with retry
        5. Merge results
        6. Generate response (narrative + chart + dataframe)

    Returns:
        (narrative, figure, dataframe) — figure and dataframe may be None
    """
    db = get_db()

    # ── Step 0: Compound question decomposition ────────────────────────────────
    sub_questions = await _decompose(question)

    # Short-circuit: check if the single question is smalltalk
    if len(sub_questions) == 1:
        analysis = await analyze(sub_questions[0])
        if analysis.get("query_type") == "smalltalk":
            return GREETING_RESPONSE, None, None

        entities   = analysis.get("entities", [])
        intent     = analysis.get("intent", question)
        query_type = analysis.get("query_type", "metric")

        # ── Steps 2 & 3: Parallel entity resolution + collection selection ─────
        entity_map, collections = await asyncio.gather(
            resolve(entities, db),
            select(intent, query_type, entities, db=db),
        )

        if not collections:
            collections = ["orders"]

        # ── Step 4: Generate MQL ───────────────────────────────────────────────
        spec = await generate(
            question=question,
            analysis=analysis,
            collections=collections,
            entity_map=entity_map,
        )

        # ── Step 5: Execute with retry ─────────────────────────────────────────
        results = await execute_with_retry(
            spec=spec,
            question=question,
            analysis=analysis,
            collections=collections,
            entity_map=entity_map,
            db=db,
        )

        # Optional drill-down for WHY questions
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
                pass

    else:
        # ── Compound question: run sub-queries concurrently then merge ─────────
        all_results_lists = await asyncio.gather(
            *[_run_single(sq, db) for sq in sub_questions],
            return_exceptions=True,
        )
        results = []
        for r in all_results_lists:
            if isinstance(r, list):
                results.extend(r)
        # Re-use the original question's analysis for the responder
        analysis = await analyze(question)

    # ── Step 6: Respond ────────────────────────────────────────────────────────
    narrative, figure, df = await respond(question, analysis, results)
    return narrative, figure, df
