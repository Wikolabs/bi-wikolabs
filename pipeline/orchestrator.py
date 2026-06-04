"""Orchestrator · coordinates all pipeline steps.

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

_GREETING_FALLBACK = """Hello! I'm your **BI Intelligence Agent**.

I can answer business questions by querying your MongoDB database in real time.

**Try asking:**
- *"What are the top entries by value?"*
- *"Show trends over the last 12 months"*
- *"Compare totals by category"*
- *"List all records matching a condition"*

What would you like to explore?"""


async def _greeting() -> str:
    """Build a greeting that lists the client's actual collections."""
    try:
        from schema_registry import get_all_summaries
        summaries = await get_all_summaries()
        if summaries:
            cols = sorted(summaries.keys())
            table = "\n".join(
                f"| `{c}` | {summaries[c][:65]}{'…' if len(summaries[c]) > 65 else ''} |"
                for c in cols
            )
            return f"""Hello! I'm your **BI Intelligence Agent**.

I can answer questions about your data in plain English.

**Your collections:**

| Collection | About |
|---|---|
{table}

**Try asking:**
- *"What are the top items by value?"*
- *"Show me a monthly trend"*
- *"Compare totals across categories"*

What would you like to explore?"""
    except Exception:
        pass
    return _GREETING_FALLBACK


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
        from schema_registry import get_all_summaries
        summaries = await get_all_summaries()
        collections = list(summaries.keys())[:1] if summaries else []

    spec = await generate(question=question, analysis=analysis,
                          collections=collections, entity_map=entity_map)

    results = await execute_with_retry(spec=spec, question=question, analysis=analysis,
                                       collections=collections, entity_map=entity_map, db=db)

    if analysis.get("is_why_question") and results:
        drill_q = f"Why: {question} · context: {results[:5]}"
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


_META_PATTERNS = re.compile(
    r'\b(what (questions?|can i ask|should i ask)|'
    r'give me (some |example |sample )?(questions?|queries|ideas)|'
    r'suggest (some |a )?(questions?|queries)|'
    r'(how|what).{0,30}(pie chart|bar chart|line chart|chart|graph|visual)|'
    r'what (data|info|information) (do you have|can you show)|'
    r'help me (ask|query|understand)|'
    r'(example|sample) (questions?|queries?))\b',
    re.IGNORECASE,
)


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

    # Short-circuit meta-questions before they reach the MQL generator
    if _META_PATTERNS.search(question):
        return await _greeting(), None, None, {}, []

    sub_questions = await _decompose(question)

    last_spec: dict = {}
    last_collections: list[str] = []

    if len(sub_questions) == 1:
        analysis = await analyze(sub_questions[0])

        if analysis.get("query_type") == "smalltalk":
            return await _greeting(), None, None, {}, []

        entities   = analysis.get("entities", [])
        intent     = analysis.get("intent", question)
        query_type = analysis.get("query_type", "metric")

        entity_map, collections = await asyncio.gather(
            resolve(entities, db),
            select(intent, query_type, entities, db=db),
        )
        if not collections:
            from schema_registry import get_all_summaries
            summaries = await get_all_summaries()
            collections = list(summaries.keys())[:1] if summaries else []

        spec = await generate(question=question, analysis=analysis,
                              collections=collections, entity_map=entity_map)
        last_spec, last_collections = spec, collections

        results = await execute_with_retry(spec=spec, question=question, analysis=analysis,
                                           collections=collections, entity_map=entity_map, db=db)

        if analysis.get("is_why_question") and results:
            drill_q = f"Why: {question} · context: {results[:5]}"
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
