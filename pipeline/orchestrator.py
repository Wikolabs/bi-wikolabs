"""Orchestrator — coordinates all pipeline steps.

run() now returns a 5-tuple:
  (narrative, figure, dataframe, last_spec, last_collections)

last_spec and last_collections are used by app.py to save golden records
when the user marks a result as correct.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from groq import AsyncGroq

from db import get_client_db
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

What would you like to explore?"""


async def _decompose(question: str) -> list[str]:
    """Split a compound question into sub-questions, or return [question]."""
    if not re.search(r'\b(and also|as well as|additionally|both .+ and)\b', question.lower()):
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
                        "If not compound, return an array with the original question. "
                        'Example: ["Show monthly revenue", "Show top 5 products by margin"]'
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


async def _run_single(question: str, db) -> tuple[list, dict, list[str]]:
    """Run steps 1-5 for a single question.

    Returns (results, last_spec, collections).
    """
    analysis = await analyze(question)

    if analysis.get("query_type") == "smalltalk":
        return [], {}, []

    entities   = analysis.get("entities", [])
    intent     = analysis.get("intent", question)
    query_type = analysis.get("query_type", "metric")

    entity_map, collections = await asyncio.gather(
        resolve(entities, db),
        select(intent, query_type, entities, db=db),
    )
    if not collections:
        collections = ["orders"]

    spec = await generate(question=question, analysis=analysis,
                          collections=collections, entity_map=entity_map)

    results = await execute_with_retry(spec=spec, question=question, analysis=analysis,
                                       collections=collections, entity_map=entity_map, db=db)

    if analysis.get("is_why_question") and results:
        drill_q = f"Why: {question} — context: {results[:5]}"
        drill_a = {**analysis, "is_why_question": False, "query_type": "comparison"}
        drill_spec = await generate(question=drill_q, analysis=drill_a,
                                    collections=collections, entity_map=entity_map)
        try:
            drill_results = await execute_with_retry(spec=drill_spec, question=drill_q,
                                                     analysis=drill_a, collections=collections,
                                                     entity_map=entity_map, db=db)
            results = results + drill_results
        except Exception:
            pass

    return results, spec, collections


async def run(
    question: str,
) -> tuple[str, Optional[go.Figure], Optional[pd.DataFrame], dict, list[str]]:
    """Execute the full BI pipeline.

    Returns:
        (narrative, figure, dataframe, last_spec, last_collections)

    last_spec / last_collections are passed back to app.py so the user
    can save a golden record when the result is confirmed correct.
    """
    db = get_client_db()

    sub_questions = await _decompose(question)

    last_spec: dict = {}
    last_collections: list[str] = []

    if len(sub_questions) == 1:
        analysis = await analyze(sub_questions[0])

        if analysis.get("query_type") == "smalltalk":
            return GREETING_RESPONSE, None, None, {}, []

        entities   = analysis.get("entities", [])
        intent     = analysis.get("intent", question)
        query_type = analysis.get("query_type", "metric")

        entity_map, collections = await asyncio.gather(
            resolve(entities, db),
            select(intent, query_type, entities, db=db),
        )
        if not collections:
            collections = ["orders"]

        spec = await generate(question=question, analysis=analysis,
                              collections=collections, entity_map=entity_map)
        last_spec, last_collections = spec, collections

        results = await execute_with_retry(spec=spec, question=question, analysis=analysis,
                                           collections=collections, entity_map=entity_map, db=db)

        if analysis.get("is_why_question") and results:
            drill_q = f"Why: {question} — context: {results[:5]}"
            drill_a = {**analysis, "is_why_question": False, "query_type": "comparison"}
            drill_spec = await generate(question=drill_q, analysis=drill_a,
                                        collections=collections, entity_map=entity_map)
            try:
                drill_results = await execute_with_retry(spec=drill_spec, question=drill_q,
                                                         analysis=drill_a, collections=collections,
                                                         entity_map=entity_map, db=db)
                results = results + drill_results
            except Exception:
                pass

    else:
        all_results = await asyncio.gather(
            *[_run_single(sq, db) for sq in sub_questions],
            return_exceptions=True,
        )
        results = []
        for r in all_results:
            if isinstance(r, tuple):
                results.extend(r[0])
                if r[1]:
                    last_spec, last_collections = r[1], r[2]
        analysis = await analyze(question)

    narrative, figure, df = await respond(question, analysis, results)
    return narrative, figure, df, last_spec, last_collections
