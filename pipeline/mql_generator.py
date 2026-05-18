"""Step 4: MQL Generation — build a focused MongoDB query from the analysis."""

import json
import os
import re

from groq import AsyncGroq
from schemas import SCHEMAS

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

# Per-query-type few-shot examples to guide the model
_EXAMPLES = {
    "metric": """Example:
User: "What is the total revenue from completed sales?"
{"collection": "sales", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$group": {"_id": null, "total_revenue": {"$sum": "$total_amount"}}},
  {"$project": {"_id": 0, "total_revenue": {"$round": ["$total_revenue", 2]}}}
]}""",

    "ranking": """Example:
User: "Top 5 products by revenue"
{"collection": "sales", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$group": {"_id": "$product_id", "revenue": {"$sum": "$total_amount"}}},
  {"$sort": {"revenue": -1}},
  {"$limit": 5},
  {"$lookup": {"from": "products", "localField": "_id", "foreignField": "product_id", "as": "p"}},
  {"$unwind": "$p"},
  {"$project": {"_id": 0, "name": "$p.name", "revenue": {"$round": ["$revenue", 2]}}}
]}""",

    "comparison": """Example:
User: "Compare revenue by customer segment"
{"collection": "sales", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "customer_id", "as": "c"}},
  {"$unwind": "$c"},
  {"$group": {"_id": "$c.segment", "revenue": {"$sum": "$total_amount"}, "orders": {"$sum": 1}}},
  {"$project": {"_id": 0, "segment": "$_id", "revenue": {"$round": ["$revenue", 2]}, "orders": 1}},
  {"$sort": {"revenue": -1}}
]}""",

    "list": """Example:
User: "List all active products in the Electronics category"
{"collection": "products", "operation": "find", "filter": {"category": "Electronics", "is_active": true}, "limit": 50}""",

    "lookup": """Example:
User: "Show details for customer Acme Corp"
{"collection": "customers", "operation": "find", "filter": {"name": {"$regex": "Acme Corp", "$options": "i"}}, "limit": 1}""",
}


def _build_schema_context(collections: list) -> str:
    lines = ["RELEVANT COLLECTION SCHEMAS:"]
    for col in collections:
        schema = SCHEMAS.get(col, f"{col}: (schema unavailable)")
        lines.append(f"\n{col}:\n{schema}")
    return "\n".join(lines)


def _build_entity_context(entity_map: dict) -> str:
    if not entity_map:
        return ""
    lines = ["RESOLVED ENTITIES (use these exact IDs/names in filters):"]
    for original, info in entity_map.items():
        if info.get("resolved"):
            lines.append(
                f"  - '{original}' → name='{info['name']}', id='{info['id']}'"
            )
        else:
            lines.append(f"  - '{original}' → not found in DB, use as regex filter")
    return "\n".join(lines)


def _build_system_prompt(collections: list, entity_map: dict, query_type: str) -> str:
    schema_ctx = _build_schema_context(collections)
    entity_ctx = _build_entity_context(entity_map)
    example = _EXAMPLES.get(query_type, _EXAMPLES["metric"])

    parts = [
        "You are a MongoDB query generator for BI Wikolabs (database: bi_wikolabs).",
        "",
        schema_ctx,
    ]

    if entity_ctx:
        parts += ["", entity_ctx]

    parts += [
        "",
        "RULES:",
        "- Use aggregation pipelines for analytics (grouping, sums, averages, trends)",
        "- Use $lookup to join collections when needed",
        "- For date formatting use $dateToString with format '%Y-%m'",
        "- Do NOT use JavaScript ISODate() — use plain ISO strings for $match date filters",
        "- In $project, ALWAYS rename _id to a meaningful field (e.g. region, segment, category, month) — never leave _id unnamed or set to 0 without renaming it first",
        "- Return ONLY valid JSON, no explanation, no markdown fences",
        "",
        "OUTPUT FORMAT (aggregation):",
        '{"collection": "sales", "operation": "aggregate", "pipeline": [...]}',
        "",
        "OUTPUT FORMAT (simple find):",
        '{"collection": "customers", "operation": "find", "filter": {}, "limit": 20}',
        "",
        example,
    ]

    return "\n".join(parts)


async def generate(
    question: str,
    analysis: dict,
    collections: list,
    entity_map: dict,
    error_context: str | None = None,
) -> dict:
    """Generate a MongoDB query specification.

    Args:
        question: original user question
        analysis: output from analyzer.analyze()
        collections: selected collections from collection_selector.select()
        entity_map: resolved entities from entity_resolver.resolve()
        error_context: optional previous error to guide self-correction

    Returns:
        dict with keys: collection, operation, and either pipeline or filter+limit
    """
    system_prompt = _build_system_prompt(collections, entity_map, analysis.get("query_type", "metric"))

    user_content = f"Intent: {analysis.get('intent', question)}\nQuestion: {question}"
    if error_context:
        user_content += f"\n\nPrevious query failed with error:\n{error_context}\nPlease fix the query."

    resp = await _groq.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=1024,
    )
    raw = (resp.choices[0].message.content or "").strip()

    # Strip markdown fences
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group()

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"MQL generator returned unparseable response: {raw[:200]}") from exc
