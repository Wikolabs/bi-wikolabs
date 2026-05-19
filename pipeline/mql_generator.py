"""Step 4: MQL Generation — build a MongoDB query from the analysis.

Improvements over the original:
- instructor + Pydantic guarantees valid JSON output (auto-retries on parse failure)
- Schema injected from PostgreSQL schema_registry (dynamic, always current)
- Golden records prepended as few-shot context (if similar past questions exist)
- Full schema context is async (reads from PostgreSQL)
"""

from __future__ import annotations

import json
import logging
import os

import instructor
from groq import AsyncGroq

from pipeline.models import MongoQuery

logger = logging.getLogger(__name__)

_groq = instructor.from_groq(
    AsyncGroq(api_key=os.getenv("GROQ_API_KEY")),
    mode=instructor.Mode.JSON_SCHEMA,  # JSON_SCHEMA prevents multiple tool-call responses
)
MODEL = "llama-3.3-70b-versatile"

# ── Static few-shot examples (per query type) ─────────────────────────────────

_EXAMPLES: dict[str, str] = {
    "metric": """\
Example — metric:
Q: "What is the total revenue from completed orders?"
→ {"collection":"orders","operation":"aggregate","pipeline":[
  {"$match":{"status":"completed"}},
  {"$group":{"_id":null,"total_revenue":{"$sum":"$total_amount"}}},
  {"$project":{"_id":0,"total_revenue":{"$round":["$total_revenue",2]}}}
]}""",

    "ranking": """\
Example — ranking:
Q: "Top 5 products by revenue"
→ {"collection":"orders","operation":"aggregate","pipeline":[
  {"$match":{"status":"completed"}},
  {"$group":{"_id":"$product_id","revenue":{"$sum":"$total_amount"}}},
  {"$sort":{"revenue":-1}},{"$limit":5},
  {"$lookup":{"from":"products","localField":"_id","foreignField":"_id","as":"p"}},
  {"$unwind":"$p"},
  {"$project":{"_id":0,"product":"$p.name","category":"$p.category","revenue":{"$round":["$revenue",2]}}}
]}""",

    "comparison": """\
Example — comparison:
Q: "Compare revenue by customer segment"
→ {"collection":"orders","operation":"aggregate","pipeline":[
  {"$match":{"status":"completed"}},
  {"$lookup":{"from":"customers","localField":"customer_id","foreignField":"_id","as":"c"}},
  {"$unwind":"$c"},
  {"$group":{"_id":"$c.segment","revenue":{"$sum":"$total_amount"},"orders":{"$sum":1}}},
  {"$project":{"_id":0,"segment":"$_id","revenue":{"$round":["$revenue",2]},"orders":1}},
  {"$sort":{"revenue":-1}}
]}""",

    "list": """\
Example — list:
Q: "List all active products in Electronics"
→ {"collection":"products","operation":"find","filter":{"category":"Electronics","is_active":true},"limit":50}""",

    "lookup": """\
Example — lookup (resolved entity):
Q: "Orders from Acme Corp" (resolved: customer_id = ObjectId abc123)
→ {"collection":"orders","operation":"aggregate","pipeline":[
  {"$match":{"customer_id":{"$oid":"abc123"}}},
  {"$project":{"_id":0,"order_number":1,"total_amount":1,"status":1,"order_date":1}},
  {"$sort":{"order_date":-1}},{"$limit":20}
]}""",
}

# Inline static fallback schemas (used only when PostgreSQL is unavailable)
_FALLBACK_SCHEMAS: dict[str, str] = {
    "orders":       "order_number(str), customer_id(ObjectId→customers), product_id(ObjectId→products), account_manager_id(ObjectId→employees), contact_id(ObjectId→contacts), quantity(int), unit_price(float), discount_pct(float), total_amount(float), region(str), status(completed|pending|cancelled|refunded), payment_status(paid|invoiced|overdue), order_date(date), delivery_date(date)",
    "products":     "sku(str), name(str), category(Electronics|Software|Furniture|Office Supplies|Services), unit_price(float), cost_price(float), margin_pct(float), supplier_id(ObjectId→suppliers), stock_quantity(int), is_active(bool)",
    "customers":    "name(str), industry(str), segment(Enterprise|SMB|Individual), region(str), country(str), account_manager_id(ObjectId→employees), status(active|churned|prospect), annual_revenue(float), lifetime_value(float), registration_date(date), is_active(bool)",
    "contacts":     "name(str), title(str), email(str), phone(str), company_id(ObjectId→customers), company_name(str), is_primary(bool), last_contact_date(date)",
    "employees":    "name(str), department(Sales|Engineering|Finance|HR|Operations|Marketing), role(str), salary(float), hire_date(date), manager_id(ObjectId→employees), performance_score(float 1-5), quota(float), quota_attainment(float), is_active(bool)",
    "transactions": "type(revenue|expense|refund), category(str), amount(float), currency(str), date(date), status(completed|pending|failed), customer_id(ObjectId→customers), order_id(ObjectId→orders), employee_id(ObjectId→employees), description(str)",
    "suppliers":    "name(str), country(str), lead_time_days(int), contact_email(str), is_active(bool)",
}


async def _schema_context(collections: list[str]) -> str:
    """Fetch schema from PostgreSQL; fall back to static strings."""
    try:
        from schema_registry import get_full_schema
        blocks = ["RELEVANT COLLECTION SCHEMAS:"]
        for col in collections:
            blocks.append(await get_full_schema(col))
        return "\n".join(blocks)
    except Exception as exc:
        logger.debug("Schema registry unavailable, using static fallback: %s", exc)
        lines = ["RELEVANT COLLECTION SCHEMAS (static fallback):"]
        for col in collections:
            lines.append(f"{col}: " + _FALLBACK_SCHEMAS.get(col, "(unknown)"))
        return "\n".join(lines)


async def _golden_context(question: str) -> str:
    """Retrieve similar past (question → MQL) pairs as few-shot context."""
    try:
        from golden_records import search_similar
        records = await search_similar(question, top_k=2, min_similarity=0.82)
        if not records:
            return ""
        lines = ["SIMILAR VERIFIED QUERIES (use as guidance, adapt to current question):"]
        for r in records:
            lines.append(f'  Q: "{r["question"]}"')
            lines.append(f'  → {json.dumps(r["mql"])}')
        return "\n".join(lines)
    except Exception:
        return ""


def _entity_context(entity_map: dict) -> str:
    if not entity_map:
        return ""
    lines = ["RESOLVED ENTITIES (use these exact ObjectIds in filters):"]
    for original, info in entity_map.items():
        if not info.get("resolved"):
            lines.append(f"  '{original}' → NOT FOUND — use regex filter on name field")
            continue
        etype, name, oid = info.get("type"), info.get("name", original), info.get("_id")
        if etype == "customer":
            lines.append(f"  '{original}' → customer '{name}', _id={oid}")
            lines.append(f'    orders filter: {{"customer_id":{{"$oid":"{oid}"}}}}')
        elif etype == "contact":
            cid = info.get("company_id")
            lines.append(f"  '{original}' → contact '{name}' ({info.get('title','')}) at {info.get('company_name','')}")
            if cid:
                lines.append(f'    orders filter: {{"customer_id":{{"$oid":"{cid}"}}}}')
        elif etype == "employee":
            lines.append(f"  '{original}' → employee '{name}' ({info.get('role','')}, {info.get('department','')}), _id={oid}")
            lines.append(f'    orders filter: {{"account_manager_id":{{"$oid":"{oid}"}}}}')
            lines.append(f'    employees filter: {{"_id":{{"$oid":"{oid}"}}}}')
        elif etype == "product":
            lines.append(f"  '{original}' → product '{name}', _id={oid}")
            lines.append(f'    orders filter: {{"product_id":{{"$oid":"{oid}"}}}}')
        elif etype == "supplier":
            lines.append(f"  '{original}' → supplier '{name}', _id={oid}")
            lines.append(f'    products filter: {{"supplier_id":{{"$oid":"{oid}"}}}}')
        else:
            lines.append(f"  '{original}' → {etype} '{name}', _id={oid}")
    return "\n".join(lines)


async def _build_system_prompt(
    collections: list[str],
    entity_map: dict,
    query_type: str,
    question: str,
) -> str:
    schema_ctx = await _schema_context(collections)
    entity_ctx = _entity_context(entity_map)
    golden_ctx = await _golden_context(question)
    example    = _EXAMPLES.get(query_type, _EXAMPLES["metric"])

    parts = [
        "You are a MongoDB query generator for a business intelligence system.",
        "",
        schema_ctx,
    ]
    if entity_ctx:
        parts += ["", entity_ctx]
    if golden_ctx:
        parts += ["", golden_ctx]
    parts += [
        "",
        "RULES:",
        "- Generate EXACTLY ONE MongoDB query for the specific question asked. Do not generate multiple queries.",
        '- Use {"$oid":"..."} syntax for ObjectId fields — executor converts them automatically.',
        "- Use $lookup + $unwind for joins; always match on _id fields.",
        "- Use aggregation pipelines for analytics (sums, averages, trends, rankings).",
        "- For date formatting use $dateToString with format '%Y-%m'.",
        "- Do NOT use ISODate() — use plain ISO strings for $match date filters.",
        "- In $project, always rename _id to a meaningful label (region, segment, month…).",
        "",
        "OUTPUT FORMAT — aggregation:",
        '{"collection":"orders","operation":"aggregate","pipeline":[...]}',
        "",
        "OUTPUT FORMAT — simple find:",
        '{"collection":"customers","operation":"find","filter":{},"limit":20}',
        "",
        example,
    ]
    return "\n".join(parts)


async def generate(
    question: str,
    analysis: dict,
    collections: list[str],
    entity_map: dict,
    error_context: str | None = None,
) -> dict:
    """Generate a MongoDB query spec dict.

    Returns dict with keys: collection, operation, and either pipeline or filter+limit.
    instructor validates the output against MongoQuery and auto-retries on failure.
    """
    system_prompt = await _build_system_prompt(
        collections, entity_map, analysis.get("query_type", "metric"), question
    )

    user_content = f"Intent: {analysis.get('intent', question)}\nQuestion: {question}"
    if error_context:
        user_content += f"\n\nPrevious query FAILED:\n{error_context}\nPlease fix the query."

    try:
        result: MongoQuery = await _groq.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_content},
            ],
            response_model=MongoQuery,
            temperature=0,
            max_tokens=1024,
            max_retries=3,
        )
        return result.to_dict()
    except Exception as exc:
        raise ValueError(f"MQL generation failed after retries: {exc}") from exc
