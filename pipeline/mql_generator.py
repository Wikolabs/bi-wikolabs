"""Step 4: MQL Generation — build a focused MongoDB query from the analysis."""

import json
import os
import re

from groq import AsyncGroq

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

# Per-query-type few-shot examples to guide the model
_EXAMPLES = {
    "metric": """Example:
User: "What is the total revenue from completed orders?"
{"collection": "orders", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$group": {"_id": null, "total_revenue": {"$sum": "$total_amount"}}},
  {"$project": {"_id": 0, "total_revenue": {"$round": ["$total_revenue", 2]}}}
]}""",

    "ranking": """Example:
User: "Top 5 products by revenue"
{"collection": "orders", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$group": {"_id": "$product_id", "revenue": {"$sum": "$total_amount"}}},
  {"$sort": {"revenue": -1}},
  {"$limit": 5},
  {"$lookup": {"from": "products", "localField": "_id", "foreignField": "_id", "as": "p"}},
  {"$unwind": "$p"},
  {"$project": {"_id": 0, "product": "$p.name", "category": "$p.category", "revenue": {"$round": ["$revenue", 2]}}}
]}""",

    "comparison": """Example:
User: "Compare revenue by customer segment"
{"collection": "orders", "operation": "aggregate", "pipeline": [
  {"$match": {"status": "completed"}},
  {"$lookup": {"from": "customers", "localField": "customer_id", "foreignField": "_id", "as": "c"}},
  {"$unwind": "$c"},
  {"$group": {"_id": "$c.segment", "revenue": {"$sum": "$total_amount"}, "orders": {"$sum": 1}}},
  {"$project": {"_id": 0, "segment": "$_id", "revenue": {"$round": ["$revenue", 2]}, "orders": 1}},
  {"$sort": {"revenue": -1}}
]}""",

    "list": """Example:
User: "List all active products in the Electronics category"
{"collection": "products", "operation": "find", "filter": {"category": "Electronics", "is_active": true}, "limit": 50}""",

    "lookup": """Example:
User: "Show all orders from Acme Global Corp" (already resolved to ObjectId abc123)
{"collection": "orders", "operation": "aggregate", "pipeline": [
  {"$match": {"customer_id": {"$oid": "abc123"}, "status": "completed"}},
  {"$project": {"_id": 0, "order_number": 1, "product_name": 1, "total_amount": 1, "order_date": 1, "status": 1}},
  {"$sort": {"order_date": -1}},
  {"$limit": 20}
]}""",
}


# Inline fallback schemas — used when the dynamic extractor is unavailable
_FALLBACK_SCHEMAS: dict[str, str] = {
    "orders": "Fields: order_number(str), customer_id(ObjectId→customers), contact_id(ObjectId→contacts), account_manager_id(ObjectId→employees), product_id(ObjectId→products), product_name(str), customer_name(str), category(str), quantity(int), unit_price(float), discount_pct(float), total_amount(float), region(str), status(str: completed|pending|cancelled|refunded), payment_status(str: paid|invoiced|overdue), order_date(date), delivery_date(date)",
    "products": "Fields: sku(str), name(str), category(str: Electronics|Software|Furniture|Office Supplies|Services), unit_price(float), cost_price(float), margin_pct(float), supplier_id(ObjectId→suppliers), stock_quantity(int), is_active(bool)",
    "customers": "Fields: name(str), industry(str), segment(str: Enterprise|SMB|Individual), region(str), country(str), account_manager_id(ObjectId→employees), status(str: active|churned|prospect), annual_revenue(float), lifetime_value(float), registration_date(date), is_active(bool)",
    "contacts": "Fields: name(str), title(str), email(str), phone(str), company_id(ObjectId→customers), company_name(str), is_primary(bool), last_contact_date(date)",
    "employees": "Fields: name(str), department(str: Sales|Engineering|Finance|HR|Operations|Marketing), role(str), salary(float), hire_date(date), manager_id(ObjectId→employees), performance_score(float 1-5), quota(float), quota_attainment(float), is_active(bool)",
    "transactions": "Fields: type(str: revenue|expense|refund), category(str: Sales Revenue|Payroll|Marketing|Operations|IT Infrastructure|Refund|Consulting), amount(float), currency(str), date(date), status(str: completed|pending|failed), customer_id(ObjectId→customers), order_id(ObjectId→orders), employee_id(ObjectId→employees), description(str)",
    "suppliers": "Fields: name(str), country(str), lead_time_days(int), contact_email(str), is_active(bool)",
}


def _build_schema_context(collections: list) -> str:
    """Build schema context using the dynamic extractor; fall back to inline schemas."""
    try:
        from db import get_extractor
        extractor = get_extractor()
        lines = ["RELEVANT COLLECTION SCHEMAS:"]
        for col in collections:
            lines.append(extractor.to_prompt(col))
        return "\n".join(lines)
    except Exception:
        lines = ["RELEVANT COLLECTION SCHEMAS (static fallback):"]
        for col in collections:
            schema = _FALLBACK_SCHEMAS.get(col, f"{col}: (schema unavailable)")
            lines.append(f"{col}: {schema}")
        return "\n".join(lines)


def _build_entity_context(entity_map: dict) -> str:
    if not entity_map:
        return ""
    lines = ["RESOLVED ENTITIES (use these exact ObjectIds in filters):"]
    for original, info in entity_map.items():
        if not info.get("resolved"):
            lines.append(f"  '{original}' → NOT FOUND — use regex filter on name field")
            continue
        etype   = info.get("type")
        name    = info.get("name", original)
        oid     = info.get("_id")

        if etype == "customer":
            lines.append(f"  '{original}' → customer '{name}', _id={oid}")
            lines.append(f'    Filter on orders: {{"customer_id": {{"$oid": "{oid}"}}}}')
        elif etype == "contact":
            company_id = info.get("company_id")
            lines.append(
                f"  '{original}' → contact '{name}' ({info.get('title', '')}) "
                f"at {info.get('company_name', '')}"
            )
            if company_id:
                lines.append(
                    f'    To find their orders: {{"customer_id": {{"$oid": "{company_id}"}}}}'
                )
        elif etype == "employee":
            lines.append(
                f"  '{original}' → employee '{name}' "
                f"({info.get('role', '')}, {info.get('department', '')}), _id={oid}"
            )
            lines.append(f'    Filter on orders:    {{"account_manager_id": {{"$oid": "{oid}"}}}}')
            lines.append(f'    Filter on employees: {{"_id": {{"$oid": "{oid}"}}}}')
        elif etype == "product":
            lines.append(f"  '{original}' → product '{name}', _id={oid}")
            lines.append(f'    Filter on orders: {{"product_id": {{"$oid": "{oid}"}}}}')
        elif etype == "supplier":
            lines.append(f"  '{original}' → supplier '{name}', _id={oid}")
            lines.append(f'    Filter on products: {{"supplier_id": {{"$oid": "{oid}"}}}}')
        else:
            lines.append(f"  '{original}' → {etype} '{name}', _id={oid}")
    return "\n".join(lines)


def _build_system_prompt(collections: list, entity_map: dict, query_type: str) -> str:
    schema_ctx = _build_schema_context(collections)
    entity_ctx = _build_entity_context(entity_map)
    example    = _EXAMPLES.get(query_type, _EXAMPLES["metric"])

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
        '- Use {"$oid": "..."} syntax for ObjectId fields — the executor converts them automatically.',
        "- For $lookup joins, match on _id fields (not string IDs): localField/_id → foreignField/_id",
        "- Use aggregation pipelines for analytics (grouping, sums, averages, trends)",
        "- For A→B→C join chains: $lookup A→B, $unwind, then $lookup B→C, $unwind",
        "- For date formatting use $dateToString with format '%Y-%m'",
        "- Do NOT use JavaScript ISODate() — use plain ISO strings for $match date filters",
        "- In $project, ALWAYS rename _id to a meaningful field (e.g. region, segment, category, month)",
        "- Return ONLY valid JSON, no explanation, no markdown fences",
        "",
        "OUTPUT FORMAT (aggregation):",
        '{"collection": "orders", "operation": "aggregate", "pipeline": [...]}',
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
        question:      original user question
        analysis:      output from analyzer.analyze()
        collections:   selected collections from collection_selector.select()
        entity_map:    resolved entities from entity_resolver.resolve()
        error_context: optional previous error to guide self-correction

    Returns:
        dict with keys: collection, operation, and either pipeline or filter+limit
    """
    system_prompt = _build_system_prompt(
        collections, entity_map, analysis.get("query_type", "metric")
    )

    user_content = f"Intent: {analysis.get('intent', question)}\nQuestion: {question}"
    if error_context:
        user_content += f"\n\nPrevious query failed with error:\n{error_context}\nPlease fix the query."

    resp = await _groq.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
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
        raise ValueError(
            f"MQL generator returned unparseable response: {raw[:200]}"
        ) from exc
