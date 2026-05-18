"""Step 3: Collection Selection — LLM picks relevant collections from schema summaries."""

import json
import os
import re

from groq import AsyncGroq

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.1-8b-instant"

# Fallback keyword map (used when LLM fails or db is unavailable)
_KEYWORD_MAP = {
    "orders":       ["order", "sale", "revenue", "purchase", "invoice", "deal", "sold"],
    "products":     ["product", "item", "sku", "catalog", "inventory", "stock",
                     "category", "margin", "price"],
    "customers":    ["customer", "client", "segment", "enterprise", "smb", "individual",
                     "buyer", "account", "company"],
    "contacts":     ["contact", "ceo", "cto", "cfo", "person", "who", "director",
                     "manager", "executive"],
    "employees":    ["employee", "staff", "headcount", "payroll", "salary", "department",
                     "hr", "performance", "quota", "rep"],
    "transactions": ["transaction", "cash", "flow", "expense", "finance", "payment",
                     "refund", "income", "budget"],
    "suppliers":    ["supplier", "vendor", "manufacturer", "lead time", "supply"],
}


async def select(intent: str, query_type: str, entities: list, db=None) -> list:
    """Return a list of 1-3 collection names relevant to the query.

    Args:
        intent:     short description from the analyzer
        query_type: one of smalltalk|lookup|list|metric|ranking|comparison
        entities:   list of entity dicts from the analyzer
        db:         pymongo Database instance (used for dynamic schema summaries)

    Returns:
        Ordered list of collection names (primary first)
    """
    if query_type == "smalltalk":
        return []

    # Build entity-based boost set
    entity_collections: set[str] = set()
    for e in entities:
        etype = e.get("type", "").lower()
        if etype in ("contact", "person"):
            entity_collections.update(["contacts", "orders", "customers"])
        elif etype == "customer":
            entity_collections.update(["customers", "orders"])
        elif etype == "product":
            entity_collections.update(["products", "orders"])
        elif etype == "employee":
            entity_collections.add("employees")
        elif etype == "supplier":
            entity_collections.update(["suppliers", "products"])

    # Attempt LLM-based selection using live schema summaries
    summaries: dict = {}
    if db is not None:
        try:
            from db import get_extractor
            extractor = get_extractor()
            summaries = extractor.all_summaries()
        except Exception:
            pass

    if summaries:
        selected = await _llm_select(intent, summaries, entity_collections)
        if selected:
            return selected[:3]

    # Fallback: keyword matching
    return _keyword_select(intent, query_type, entity_collections)


async def _llm_select(intent: str, summaries: dict, entity_boost: set) -> list:
    summary_lines = "\n".join(f"- {line}" for line in summaries.values())
    prompt = (
        "You are selecting MongoDB collections to answer a business question.\n\n"
        f"Available collections:\n{summary_lines}\n\n"
        f'Question intent: "{intent}"\n\n'
        "Return ONLY a JSON array of 1-3 collection names, ordered by relevance.\n"
        'Example: ["orders", "customers"]\n'
        "No explanation, no markdown."
    )

    try:
        resp = await _groq.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=60,
        )
        raw = (resp.choices[0].message.content or "").strip()
        match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if match:
            cols  = json.loads(match.group())
            valid = list(summaries.keys())
            result = [c for c in cols if c in valid]
            # Add entity-boosted collections not already present
            for ec in entity_boost:
                if ec in valid and ec not in result:
                    result.append(ec)
            return result[:3]
    except Exception:
        pass
    return []


def _keyword_select(intent: str, query_type: str, entity_boost: set) -> list:
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
        # Fallback defaults by query type
        defaults = {
            "metric":     ["orders", "transactions"],
            "ranking":    ["orders", "products"],
            "comparison": ["orders", "customers"],
            "list":       ["orders"],
            "lookup":     ["customers", "products", "employees"],
        }
        selected = defaults.get(query_type, ["orders"])
    return selected[:3]
