"""Step 3: Collection Selection · find relevant MongoDB collections.

Priority order:
  1. pgvector semantic search on schema embeddings  (best · uses real schema)
  2. LLM call with live schema summaries             (fallback if vectors missing)
  3. Keyword matching                                (final fallback, always works)
"""

import json
import logging
import os
import re

from groq import AsyncGroq

logger = logging.getLogger(__name__)

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"

_KEYWORD_MAP = {
    "orders":       ["order", "sale", "revenue", "purchase", "invoice", "deal", "sold"],
    "products":     ["product", "item", "sku", "catalog", "inventory", "stock", "category", "margin", "price"],
    "customers":    ["customer", "client", "segment", "enterprise", "smb", "individual", "buyer", "account", "company"],
    "contacts":     ["contact", "ceo", "cto", "cfo", "person", "who", "director", "manager", "executive"],
    "employees":    ["employee", "staff", "headcount", "payroll", "salary", "department", "hr", "performance", "quota", "rep"],
    "transactions": ["transaction", "cash", "flow", "expense", "finance", "payment", "refund", "income", "budget"],
    "suppliers":    ["supplier", "vendor", "manufacturer", "lead time", "supply"],
}


def _entity_boost(entities: list) -> set[str]:
    boost: set[str] = set()
    for e in entities:
        etype = e.get("type", "").lower()
        if etype in ("contact", "person"):
            boost.update(["contacts", "orders", "customers"])
        elif etype == "customer":
            boost.update(["customers", "orders"])
        elif etype == "product":
            boost.update(["products", "orders"])
        elif etype == "employee":
            boost.add("employees")
        elif etype == "supplier":
            boost.update(["suppliers", "products"])
    return boost


async def select(intent: str, query_type: str, entities: list, db=None) -> list[str]:
    """Return 1-3 collection names relevant to the query, primary collection first."""
    if query_type == "smalltalk":
        return []

    boosted = _entity_boost(entities)

    # ── 1. pgvector semantic search ───────────────────────────────────────────
    try:
        from schema_registry import find_similar_collections
        cols = await find_similar_collections(intent, top_k=3)
        if cols:
            # Append entity-boosted collections not already in the list
            for ec in boosted:
                if ec not in cols:
                    cols.append(ec)
            return cols[:3]
    except Exception as exc:
        logger.debug("pgvector collection selector skipped: %s", exc)

    # ── 2. LLM with summaries ─────────────────────────────────────────────────
    try:
        from schema_registry import get_all_summaries
        summaries = await get_all_summaries()
        if summaries:
            selected = await _llm_select(intent, summaries, boosted)
            if selected:
                return selected[:3]
    except Exception as exc:
        logger.debug("LLM collection selector skipped: %s", exc)

    # ── 3. Keyword fallback ───────────────────────────────────────────────────
    return _keyword_select(intent, query_type, boosted)


async def _llm_select(intent: str, summaries: dict, entity_boost: set) -> list[str]:
    summary_lines = "\n".join(f"- {line}" for line in summaries.values())
    prompt = (
        "You are selecting MongoDB collections to answer a business question.\n\n"
        f"Available collections:\n{summary_lines}\n\n"
        f'Question intent: "{intent}"\n\n'
        "Return ONLY a JSON array of 1-3 collection names, ordered by relevance.\n"
        'Example: ["orders", "customers"]\nNo explanation, no markdown.'
    )
    try:
        resp = await _groq.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=60,
        )
        raw = (resp.choices[0].message.content or "").strip()
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            cols = json.loads(match.group())
            valid = list(summaries.keys())
            result = [c for c in cols if c in valid]
            for ec in entity_boost:
                if ec in valid and ec not in result:
                    result.append(ec)
            return result[:3]
    except Exception:
        pass
    return []


def _keyword_select(intent: str, query_type: str, entity_boost: set) -> list[str]:
    tokens = set(re.findall(r"[a-z]+", intent.lower()))
    scored = {col: 0 for col in _KEYWORD_MAP}
    for col, kws in _KEYWORD_MAP.items():
        for kw in kws:
            if kw in tokens:
                scored[col] += 1
    for ec in entity_boost:
        if ec in scored:
            scored[ec] += 3
    selected = [c for c, s in sorted(scored.items(), key=lambda x: -x[1]) if s > 0]
    if not selected:
        defaults = {
            "metric":     ["orders", "transactions"],
            "ranking":    ["orders", "products"],
            "comparison": ["orders", "customers"],
            "list":       ["orders"],
            "lookup":     ["customers", "products", "employees"],
        }
        selected = defaults.get(query_type, ["orders"])
    return selected[:3]
