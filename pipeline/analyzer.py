"""Step 1: Query Analysis — classify the user question and extract entities.

Uses instructor to guarantee a valid AnalysisResult Pydantic model is returned.
Falls back to safe defaults if the LLM repeatedly fails validation.
"""

import os
import logging

import instructor
from groq import AsyncGroq

from pipeline.models import AnalysisResult

logger = logging.getLogger(__name__)

_groq = instructor.from_groq(
    AsyncGroq(api_key=os.getenv("GROQ_API_KEY")),
    mode=instructor.Mode.TOOLS,
)
MODEL = "llama-3.1-8b-instant"

_SYSTEM = """You are a query analyzer for a business intelligence system.
Classify the user question and extract named entities.

Query types:
- smalltalk  : greetings, thanks, "who are you", "what can you do", general chat
- lookup     : fetch details about a specific named entity
- list       : list multiple records (no ranking by numeric value)
- metric     : a single aggregated KPI (total, average, count)
- ranking    : top-N or bottom-N by a numeric measure
- comparison : compare two or more groups side-by-side

Entity types:
- customer  : a company/business that is a buyer
- contact   : a named person who works at a customer company
- employee  : an internal staff member
- product   : a product or SKU name
- supplier  : a product supplier / vendor
- region    : a geographic region
- person    : a named person (unclear if contact or employee)

Set is_why_question=true if the question asks "why" or requests a root-cause explanation."""


async def analyze(question: str) -> dict:
    """Classify the question and extract entities.

    Returns a plain dict (keys: query_type, is_why_question, intent, entities)
    for backwards compatibility with the rest of the pipeline.
    """
    try:
        result: AnalysisResult = await _groq.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user",   "content": question},
            ],
            response_model=AnalysisResult,
            temperature=0,
            max_tokens=256,
            max_retries=2,
        )
        return result.to_dict()
    except Exception as exc:
        logger.warning("Analyzer fallback triggered: %s", exc)
        return {
            "query_type":      "metric",
            "is_why_question": False,
            "intent":          question,
            "entities":        [],
        }
