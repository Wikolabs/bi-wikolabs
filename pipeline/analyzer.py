"""Step 1: Query Analysis — classify the user question and extract entities."""

import json
import os
import re

from groq import AsyncGroq

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"

_SYSTEM = """You are a query analyzer for a business intelligence system.
Given a user question, return ONLY a JSON object (no markdown, no explanation) with this structure:

{
  "query_type": "<one of: smalltalk | lookup | list | metric | ranking | comparison>",
  "is_why_question": <true|false>,
  "intent": "<brief description of what the user wants>",
  "entities": [
    {"type": "<customer|product|employee|region>", "value": "<extracted name or value>"}
  ]
}

Definitions:
- smalltalk: greetings, thanks, who are you, what can you do, general chat
- lookup: fetching details about a specific named entity
- list: listing multiple records (without ranking by numeric value)
- metric: a single aggregated KPI (total revenue, average salary, count)
- ranking: top-N or bottom-N by a numeric measure
- comparison: comparing two or more groups side-by-side
- is_why_question: true if the question asks "why" or requests an explanation of a result
- entities: named business objects mentioned (customer names, product names, employees, regions)

Return only the JSON object."""


async def analyze(question: str) -> dict:
    """Classify the question and extract entities. Returns analysis dict."""
    resp = await _groq.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": question},
        ],
        temperature=0,
        max_tokens=256,
    )
    raw = resp.choices[0].message.content.strip()

    # Extract JSON from response (strip markdown fences if present)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        # Fallback to safe defaults
        result = {
            "query_type": "metric",
            "is_why_question": False,
            "intent": question,
            "entities": [],
        }

    # Ensure required keys exist
    result.setdefault("query_type", "metric")
    result.setdefault("is_why_question", False)
    result.setdefault("intent", question)
    result.setdefault("entities", [])

    return result
