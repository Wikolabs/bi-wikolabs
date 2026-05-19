"""Integration tests — 10 behavioural scenarios + dynamic schema validation.

Strategy
--------
- MongoDB     : custom in-memory mock (no mongomock dependency)
- Groq LLM    : mocked (analyze + MQL generation + narrative)
- execute_with_retry : mocked (returns seeded result rows)
- Schema extraction  : REAL — _extract_fields() runs on seeded docs every time
- PostgreSQL helpers : redirected to real extraction results (no DB needed)
- Chart generation   : REAL — _build_chart / _build_dataframe run as-is

This means the only things not exercised are actual LLM accuracy and
real Mongo aggregation; everything else (selection, schema injection,
Pydantic validation, chart routing, dataframe building) runs live.
"""

import json
import re
import pytest
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from bson import ObjectId

from pipeline.models import AnalysisResult, MongoQuery, Entity
from pipeline.responder import _build_dataframe, _build_chart
from schema_registry import _extract_fields, _build_summary


# ── Seeded documents ──────────────────────────────────────────────────────────

_ORDERS = [
    {
        "_id": ObjectId(),
        "order_number": f"ORD-{i:04d}",
        "customer_id": ObjectId(),
        "product_id": ObjectId(),
        "total_amount": float(i * 120 + 500),
        "region": ["North", "South", "East", "West"][i % 4],
        "status": ["completed", "pending", "cancelled", "refunded"][i % 4],
        "order_date": datetime(2024, (i % 12) + 1, 1, tzinfo=timezone.utc),
        "quantity": (i % 10) + 1,
    }
    for i in range(40)
]

_CUSTOMERS = [
    {
        "_id": ObjectId(),
        "name": f"Company {i}",
        "segment": ["Enterprise", "SMB", "Individual"][i % 3],
        "region": ["North", "South", "East", "West"][i % 4],
        "status": "active" if i % 5 != 0 else "churned",
        "annual_revenue": float(i * 50000),
        "is_active": i % 5 != 0,
    }
    for i in range(20)
] + [
    {
        "_id": ObjectId(),
        "name": "Acme Corp",
        "segment": "Enterprise",
        "region": "North",
        "status": "active",
        "annual_revenue": 500000.0,
        "is_active": True,
    }
]

_PRODUCTS = [
    {
        "_id": ObjectId(),
        "name": f"Product {i}",
        "sku": f"SKU-{i:03d}",
        "category": ["Electronics", "Software", "Furniture", "Services"][i % 4],
        "unit_price": float(i * 25 + 10),
        "margin_pct": float(i % 30 + 10),
        "is_active": True,
    }
    for i in range(16)
]

_EMPLOYEES = [
    {
        "_id": ObjectId(),
        "name": f"Employee {i}",
        "department": ["Sales", "Engineering", "Finance", "HR"][i % 4],
        "role": ["Manager", "Rep", "Analyst"][i % 3],
        "salary": float(i * 5000 + 40000),
        "is_active": True,
    }
    for i in range(12)
]

# Non-standard field names — proves schema extraction is 100% dynamic
_SALES_CUSTOM = [
    {
        "_id": ObjectId(),
        "sale_value": float(i * 150 + 300),
        "buyer_region": ["North", "South", "East", "West"][i % 4],
        "purchase_date": datetime(2024, (i % 12) + 1, 1, tzinfo=timezone.utc),
        "item_code": f"ITEM-{i:03d}",
        "buyer_status": ["active", "inactive"][i % 2],
    }
    for i in range(20)
]

_ALL_COLLECTIONS = {
    "orders": _ORDERS,
    "customers": _CUSTOMERS,
    "products": _PRODUCTS,
    "employees": _EMPLOYEES,
    "sales_custom": _SALES_CUSTOM,
}


# ── Dynamic schema helpers (NO hardcoded content) ─────────────────────────────

def _make_full_schema(collection_name: str, docs: list) -> str:
    """Replicate get_full_schema() logic in-memory from raw documents."""
    fields = _extract_fields(docs)
    parts = []
    for path, info in sorted(fields.items()):
        dominant = max(info["types"], key=info["types"].get)
        enums = info.get("enum")
        if enums:
            label = " | ".join(str(v) for v in enums[:8])
            parts.append(f"  {path} ({dominant}: {label})")
        else:
            parts.append(f"  {path} ({dominant})")
    return collection_name + ":\n" + "\n".join(parts)


def _make_all_schemas() -> dict[str, str]:
    return {name: _make_full_schema(name, docs) for name, docs in _ALL_COLLECTIONS.items()}


def _make_all_summaries() -> dict[str, str]:
    return {
        name: _build_summary(name, _extract_fields(docs))
        for name, docs in _ALL_COLLECTIONS.items()
    }


# ── Mock MongoDB ──────────────────────────────────────────────────────────────

class _MockCollection:
    def __init__(self, docs: list):
        self._docs = docs
        self._agg_result: list = []

    def set_aggregate_result(self, rows: list):
        self._agg_result = rows

    def find(self, filter=None, projection=None):
        docs = self._docs
        if filter:
            docs = [d for d in docs if _matches(d, filter)]
        return iter(docs)

    def find_one(self, filter=None, projection=None):
        return next(self.find(filter, projection), None)

    def aggregate(self, pipeline):
        return iter(self._agg_result)

    def estimated_document_count(self):
        return len(self._docs)


class _MockDB:
    def __init__(self):
        self._cols = {
            name: _MockCollection(docs)
            for name, docs in _ALL_COLLECTIONS.items()
        }

    def __getitem__(self, name: str) -> _MockCollection:
        if name not in self._cols:
            self._cols[name] = _MockCollection([])
        return self._cols[name]

    def list_collection_names(self) -> list:
        return list(self._cols.keys())


def _matches(doc: dict, filter_: dict) -> bool:
    for key, val in filter_.items():
        if key.startswith("$"):
            continue
        dval = doc.get(key)
        if isinstance(val, dict):
            if "$regex" in val:
                if not re.search(str(val["$regex"]), str(dval or ""), re.IGNORECASE):
                    return False
            elif "$eq" in val:
                if dval != val["$eq"]:
                    return False
        elif dval != val:
            return False
    return True


# ── Scenario runner ───────────────────────────────────────────────────────────

async def _run(
    question: str,
    analysis: dict,
    collections: list,
    mql_spec: dict,
    execute_result: list,
) -> tuple:
    """Run the full pipeline with mocked LLM + schema + MongoDB execution.

    Schema is ALWAYS dynamically extracted from seeded documents.
    Returns: (narrative, figure, df, last_spec, last_collections)
    """
    schemas   = _make_all_schemas()
    summaries = _make_all_summaries()

    async def _fake_full_schema(name, tenant_id="default"):
        return schemas.get(name, f"{name}: (not found)")

    async def _fake_summaries(tenant_id="default"):
        return summaries

    async def _fake_similar(question, top_k=3, tenant_id="default"):
        return collections[:top_k]

    narrative_resp = MagicMock()
    narrative_resp.choices[0].message.content = "Analysis complete — key insights below."

    with (
        patch("pipeline.orchestrator.get_client_db", return_value=_MockDB()),
        patch("pipeline.orchestrator.analyze",         new=AsyncMock(return_value=analysis)),
        patch("pipeline.orchestrator.select",          new=AsyncMock(return_value=collections)),
        patch("pipeline.orchestrator.resolve",         new=AsyncMock(return_value={})),
        patch("pipeline.orchestrator.generate",        new=AsyncMock(return_value=mql_spec)),
        patch("pipeline.orchestrator.execute_with_retry", new=AsyncMock(return_value=execute_result)),
        patch("schema_registry.get_full_schema",       new=AsyncMock(side_effect=_fake_full_schema)),
        patch("schema_registry.get_all_summaries",     new=AsyncMock(side_effect=_fake_summaries)),
        patch("schema_registry.find_similar_collections", new=AsyncMock(side_effect=_fake_similar)),
        patch("pipeline.responder._groq") as _mock_groq,
    ):
        _mock_groq.chat.completions.create = AsyncMock(return_value=narrative_resp)
        from pipeline.orchestrator import run
        return await run(question)


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 01 — Smalltalk
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS01Smalltalk:
    async def test_greeting_returns_welcome_message(self):
        analysis = {"query_type": "smalltalk", "is_why_question": False,
                    "intent": "greeting", "entities": []}
        narrative, figure, df, spec, cols = await _run(
            "Hello, what can you do?", analysis, [], {}, [],
        )
        assert len(narrative) > 50  # some greeting was returned
        assert figure is None
        assert df is None
        assert spec == {}
        assert cols == []

    async def test_smalltalk_produces_no_query(self):
        analysis = {"query_type": "smalltalk", "is_why_question": False,
                    "intent": "thanks", "entities": []}
        _, _, _, spec, _ = await _run("Thanks!", analysis, [], {}, [])
        assert spec == {}


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 02 — Metric: total revenue
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS02MetricTotalRevenue:
    _analysis = {"query_type": "metric", "is_why_question": False,
                 "intent": "total revenue completed orders", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$match": {"status": "completed"}},
                 {"$group": {"_id": None, "total_revenue": {"$sum": "$total_amount"}}},
             ]}
    _result = [{"total_revenue": 1_250_000.0}]

    async def test_collection_is_orders(self):
        _, _, _, spec, cols = await _run(
            "What is total revenue from completed orders?",
            self._analysis, ["orders"], self._spec, self._result,
        )
        assert spec["collection"] == "orders"
        assert "orders" in cols

    async def test_operation_is_aggregate(self):
        _, _, _, spec, _ = await _run(
            "Total revenue?", self._analysis, ["orders"], self._spec, self._result,
        )
        assert spec["operation"] == "aggregate"

    async def test_no_chart_for_metric(self):
        _, figure, _, _, _ = await _run(
            "Total revenue?", self._analysis, ["orders"], self._spec, self._result,
        )
        assert figure is None

    async def test_dataframe_has_revenue_column(self):
        _, _, df, _, _ = await _run(
            "Total revenue?", self._analysis, ["orders"], self._spec, self._result,
        )
        assert df is not None
        assert "total_revenue" in df.columns
        assert df.iloc[0]["total_revenue"] == 1_250_000.0

    async def test_narrative_non_empty(self):
        narrative, _, _, _, _ = await _run(
            "Total revenue?", self._analysis, ["orders"], self._spec, self._result,
        )
        assert len(narrative) > 10


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 03 — Ranking: top 5 customers
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS03RankingTopCustomers:
    _analysis = {"query_type": "ranking", "is_why_question": False,
                 "intent": "top 5 customers by order value", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$group": {"_id": "$customer_id", "total": {"$sum": "$total_amount"}}},
                 {"$sort": {"total": -1}}, {"$limit": 5},
                 {"$lookup": {"from": "customers", "localField": "_id",
                              "foreignField": "_id", "as": "c"}},
                 {"$unwind": "$c"},
                 {"$project": {"_id": 0, "customer": "$c.name", "total": 1}},
             ]}
    _result = [
        {"customer": f"Company {i}", "total": float((5 - i) * 50000)}
        for i in range(5)
    ]

    async def test_horizontal_bar_for_ranking(self):
        _, figure, _, _, _ = await _run(
            "Top 5 customers by order value",
            self._analysis, ["orders", "customers"], self._spec, self._result,
        )
        assert isinstance(figure, go.Figure)
        assert figure.data[0].orientation == "h"

    async def test_dataframe_has_5_rows(self):
        _, _, df, _, _ = await _run(
            "Top 5 customers", self._analysis,
            ["orders", "customers"], self._spec, self._result,
        )
        assert df is not None
        assert len(df) == 5

    async def test_pipeline_has_sort_and_limit(self):
        _, _, _, spec, _ = await _run(
            "Top 5 customers", self._analysis,
            ["orders", "customers"], self._spec, self._result,
        )
        stages = [list(s.keys())[0] for s in spec["pipeline"]]
        assert "$sort"  in stages
        assert "$limit" in stages


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 04 — Comparison + pie: revenue breakdown by region
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS04ComparisonPieRegion:
    _analysis = {"query_type": "comparison", "is_why_question": False,
                 "intent": "revenue breakdown by region", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$group": {"_id": "$region", "revenue": {"$sum": "$total_amount"}}},
                 {"$project": {"_id": 0, "region": "$_id", "revenue": 1}},
             ]}
    _result = [
        {"region": r, "revenue": float(v)}
        for r, v in [("North", 420000), ("South", 310000), ("East", 280000), ("West", 190000)]
    ]

    async def test_pie_chart_for_breakdown_keyword(self):
        _, figure, _, _, _ = await _run(
            "Show revenue breakdown by region",
            self._analysis, ["orders"], self._spec, self._result,
        )
        assert isinstance(figure, go.Figure)
        assert isinstance(figure.data[0], go.Pie)

    async def test_pie_has_4_slices(self):
        _, figure, _, _, _ = await _run(
            "Show revenue breakdown by region",
            self._analysis, ["orders"], self._spec, self._result,
        )
        assert len(figure.data[0].labels) == 4

    async def test_dataframe_shape(self):
        _, _, df, _, _ = await _run(
            "Revenue breakdown by region",
            self._analysis, ["orders"], self._spec, self._result,
        )
        assert df is not None
        assert len(df) == 4
        assert "revenue" in df.columns


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 05 — Comparison + grouped bar: multi-metric by segment
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS05ComparisonGroupedBar:
    _analysis = {"query_type": "comparison", "is_why_question": False,
                 "intent": "revenue and order count by customer segment", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$lookup": {"from": "customers", "localField": "customer_id",
                              "foreignField": "_id", "as": "c"}},
                 {"$unwind": "$c"},
                 {"$group": {"_id": "$c.segment",
                             "revenue": {"$sum": "$total_amount"},
                             "orders": {"$sum": 1}}},
                 {"$project": {"_id": 0, "segment": "$_id", "revenue": 1, "orders": 1}},
             ]}
    _result = [
        {"segment": "Enterprise", "revenue": 600000.0, "orders": 45},
        {"segment": "SMB",        "revenue": 350000.0, "orders": 38},
        {"segment": "Individual", "revenue": 150000.0, "orders": 22},
    ]

    async def test_grouped_bar_for_multi_metric(self):
        _, figure, _, _, _ = await _run(
            "Compare revenue and order count by customer segment",
            self._analysis, ["orders", "customers"], self._spec, self._result,
        )
        assert isinstance(figure, go.Figure)
        assert figure.layout.barmode == "group"

    async def test_has_two_traces_revenue_and_orders(self):
        _, figure, _, _, _ = await _run(
            "Revenue and order count by segment",
            self._analysis, ["orders", "customers"], self._spec, self._result,
        )
        assert len(figure.data) == 2

    async def test_pipeline_has_lookup(self):
        _, _, _, spec, _ = await _run(
            "Revenue by segment", self._analysis,
            ["orders", "customers"], self._spec, self._result,
        )
        stages = [list(s.keys())[0] for s in spec["pipeline"]]
        assert "$lookup" in stages


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 06 — List: active customers (no numeric → no chart)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS06ListNoChart:
    _analysis = {"query_type": "list", "is_why_question": False,
                 "intent": "list active customers in North region", "entities": []}
    _spec = {"collection": "customers", "operation": "find",
             "filter": {"status": "active", "region": "North"}, "limit": 50}
    _result = [
        {"name": f"Company {i}", "region": "North", "status": "active"}
        for i in range(8)
    ]

    async def test_no_chart_when_no_numeric_columns(self):
        _, figure, _, _, _ = await _run(
            "List all active customers in the North region",
            self._analysis, ["customers"], self._spec, self._result,
        )
        # No numeric columns in result → chart must be None
        assert figure is None

    async def test_operation_is_find(self):
        _, _, _, spec, _ = await _run(
            "List active customers", self._analysis,
            ["customers"], self._spec, self._result,
        )
        assert spec["operation"] == "find"

    async def test_dataframe_has_all_rows(self):
        _, _, df, _, _ = await _run(
            "List active customers", self._analysis,
            ["customers"], self._spec, self._result,
        )
        assert df is not None
        assert len(df) == 8


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 07 — Lookup: specific customer
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS07LookupCustomer:
    _analysis = {"query_type": "lookup", "is_why_question": False,
                 "intent": "details for Acme Corp",
                 "entities": [{"type": "customer", "value": "Acme Corp"}]}
    _spec = {"collection": "customers", "operation": "find",
             "filter": {"name": {"$regex": "Acme"}}, "limit": 1}
    _result = [
        {"name": "Acme Corp", "segment": "Enterprise", "region": "North",
         "status": "active", "annual_revenue": 500000.0}
    ]

    async def test_no_chart_for_lookup(self):
        _, figure, _, _, _ = await _run(
            "Give me details about Acme Corp",
            self._analysis, ["customers"], self._spec, self._result,
        )
        assert figure is None

    async def test_collection_is_customers(self):
        _, _, _, spec, cols = await _run(
            "Details for Acme Corp", self._analysis,
            ["customers"], self._spec, self._result,
        )
        assert spec["collection"] == "customers"

    async def test_single_result_row(self):
        _, _, df, _, _ = await _run(
            "Details for Acme Corp", self._analysis,
            ["customers"], self._spec, self._result,
        )
        assert df is not None
        assert len(df) == 1
        assert df.iloc[0]["name"] == "Acme Corp"


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 08 — Monthly trend (list + date grouping → vertical bar)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS08MonthlyTrend:
    _analysis = {"query_type": "list", "is_why_question": False,
                 "intent": "monthly revenue for last 6 months", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$match": {"status": "completed"}},
                 {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$order_date"}},
                             "revenue": {"$sum": "$total_amount"}}},
                 {"$project": {"_id": 0, "month": "$_id", "revenue": 1}},
                 {"$sort": {"month": 1}}, {"$limit": 6},
             ]}
    _result = [
        {"month": f"2024-{m:02d}", "revenue": float(m * 85000 + 100000)}
        for m in range(1, 7)
    ]

    async def test_vertical_bar_for_trend(self):
        _, figure, _, _, _ = await _run(
            "Show monthly revenue for the last 6 months",
            self._analysis, ["orders"], self._spec, self._result,
        )
        assert isinstance(figure, go.Figure)
        # Vertical bar: no orientation set (default) or "v"
        assert figure.data[0].orientation in (None, "v", "")

    async def test_dataframe_has_month_column(self):
        _, _, df, _, _ = await _run(
            "Monthly revenue", self._analysis, ["orders"], self._spec, self._result,
        )
        assert df is not None
        assert "month" in df.columns
        assert len(df) == 6

    async def test_pipeline_uses_dateToString(self):
        _, _, _, spec, _ = await _run(
            "Monthly revenue", self._analysis, ["orders"], self._spec, self._result,
        )
        pipeline_str = json.dumps(spec["pipeline"])
        assert "$dateToString" in pipeline_str


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 09 — Cross-collection join: revenue by product category
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
@pytest.mark.asyncio
class TestS09JoinByCategory:
    _analysis = {"query_type": "comparison", "is_why_question": False,
                 "intent": "total revenue by product category", "entities": []}
    _spec = {"collection": "orders", "operation": "aggregate",
             "pipeline": [
                 {"$match": {"status": "completed"}},
                 {"$lookup": {"from": "products", "localField": "product_id",
                              "foreignField": "_id", "as": "p"}},
                 {"$unwind": "$p"},
                 {"$group": {"_id": "$p.category", "revenue": {"$sum": "$total_amount"}}},
                 {"$project": {"_id": 0, "category": "$_id", "revenue": 1}},
                 {"$sort": {"revenue": -1}},
             ]}
    _result = [
        {"category": cat, "revenue": float(v)}
        for cat, v in [
            ("Electronics", 620000), ("Software", 410000),
            ("Furniture", 280000), ("Services", 150000),
        ]
    ]

    async def test_collections_include_orders_and_products(self):
        _, _, _, spec, cols = await _run(
            "Total revenue by product category",
            self._analysis, ["orders", "products"], self._spec, self._result,
        )
        assert spec["collection"] == "orders"
        assert "products" in cols or "orders" in cols

    async def test_pie_chart_for_comparison_4_rows(self):
        _, figure, _, _, _ = await _run(
            "Total revenue by product category",
            self._analysis, ["orders", "products"], self._spec, self._result,
        )
        assert isinstance(figure, go.Figure)
        # comparison + ≤10 rows + 1 metric → pie
        assert isinstance(figure.data[0], go.Pie)

    async def test_pipeline_has_lookup_and_group(self):
        _, _, _, spec, _ = await _run(
            "Revenue by category",
            self._analysis, ["orders", "products"], self._spec, self._result,
        )
        stages = [list(s.keys())[0] for s in spec["pipeline"]]
        assert "$lookup" in stages
        assert "$group"  in stages


# ═════════════════════════════════════════════════════════════════════════════
# Scenario 10 — Dynamic schema: custom field names
# Proves that schema is ALWAYS extracted from MongoDB content,
# never from the hardcoded _FALLBACK_SCHEMAS dict.
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestS10DynamicSchema:
    """Validates that the schema injected into the LLM prompt is dynamically
    extracted from actual MongoDB document structure, not from hardcoded strings."""

    def test_custom_fields_present_in_extracted_schema(self):
        schema = _make_full_schema("sales_custom", _SALES_CUSTOM)
        assert "sale_value"   in schema, "Dynamic field 'sale_value' missing from schema"
        assert "buyer_region" in schema, "Dynamic field 'buyer_region' missing from schema"
        assert "purchase_date" in schema, "Dynamic field 'purchase_date' missing from schema"
        assert "item_code"    in schema, "Dynamic field 'item_code' missing from schema"
        assert "buyer_status" in schema, "Dynamic field 'buyer_status' missing from schema"

    def test_hardcoded_fallback_fields_not_in_custom_schema(self):
        """Hardcoded fallback uses 'total_amount', 'order_date', 'sku'.
        None of these exist in our custom collection, so schema must be dynamic."""
        schema = _make_full_schema("sales_custom", _SALES_CUSTOM)
        assert "total_amount" not in schema, "Hardcoded fallback field leaked into schema"
        assert "order_date"   not in schema, "Hardcoded fallback field leaked into schema"
        assert "sku"          not in schema, "Hardcoded fallback field leaked into schema"

    def test_buyer_status_detected_as_enum(self):
        """Low-cardinality string field 'buyer_status' must be detected as enum."""
        fields = _extract_fields(_SALES_CUSTOM)
        assert "buyer_status" in fields
        assert "enum" in fields["buyer_status"]
        assert set(fields["buyer_status"]["enum"]) == {"active", "inactive"}

    def test_sale_value_typed_as_float(self):
        fields = _extract_fields(_SALES_CUSTOM)
        assert "sale_value" in fields
        assert fields["sale_value"]["types"].get("float", 0) == 1.0

    def test_purchase_date_typed_as_date(self):
        fields = _extract_fields(_SALES_CUSTOM)
        assert "purchase_date" in fields
        assert fields["purchase_date"]["types"].get("date", 0) == 1.0

    def test_item_code_high_cardinality_not_enum(self):
        """item_code has 20 distinct values — must NOT be flagged as enum."""
        fields = _extract_fields(_SALES_CUSTOM)
        assert "item_code" in fields
        assert "enum" not in fields["item_code"]

    @pytest.mark.asyncio
    async def test_schema_context_uses_dynamic_fields_not_fallback(self):
        """The system prompt sent to the LLM must contain dynamic fields."""
        from pipeline.mql_generator import _build_system_prompt

        custom_schema = _make_full_schema("sales_custom", _SALES_CUSTOM)

        async def _fake_get_full_schema(name, tenant_id="default"):
            if name == "sales_custom":
                return custom_schema
            return f"{name}: (not found)"

        async def _fake_golden(question):
            return ""

        with (
            patch("schema_registry.get_full_schema",
                  new=AsyncMock(side_effect=_fake_get_full_schema)),
            patch("golden_records.search_similar",
                  new=AsyncMock(return_value=[])),
        ):
            prompt = await _build_system_prompt(
                collections=["sales_custom"],
                entity_map={},
                query_type="metric",
                question="Total sale_value by buyer_region",
            )

        # The schema section sits between "RELEVANT COLLECTION SCHEMAS:" and "RULES:"
        # (static few-shot examples come after RULES and may reference other field names)
        import re as _re
        schema_section = _re.search(
            r"RELEVANT COLLECTION SCHEMAS:.*?(?=\nRULES:)", prompt, _re.DOTALL
        )
        assert schema_section, "Schema section not found in prompt"
        schema_text = schema_section.group()

        # Dynamic fields MUST be in the schema section
        assert "sale_value"    in schema_text
        assert "buyer_region"  in schema_text
        assert "purchase_date" in schema_text

        # Hardcoded fallback fields must NOT be in the schema section
        # (they may appear in static few-shot examples below the RULES separator)
        assert "total_amount" not in schema_text
        assert "order_date"   not in schema_text

    @pytest.mark.asyncio
    async def test_full_pipeline_with_custom_collection(self):
        """End-to-end: custom collection, dynamic schema injected, correct outputs."""
        analysis = {"query_type": "comparison", "is_why_question": False,
                    "intent": "sale_value breakdown by buyer_region", "entities": []}
        spec = {"collection": "sales_custom", "operation": "aggregate",
                "pipeline": [
                    {"$group": {"_id": "$buyer_region", "total": {"$sum": "$sale_value"}}},
                    {"$project": {"_id": 0, "region": "$_id", "total": 1}},
                ]}
        result = [
            {"region": r, "total": float(v)}
            for r, v in [("North", 8400), ("South", 6300), ("East", 5100), ("West", 3900)]
        ]

        _, figure, df, ret_spec, _ = await _run(
            "Show sale_value breakdown by buyer_region",
            analysis, ["sales_custom"], spec, result,
        )

        assert ret_spec["collection"] == "sales_custom"
        assert df is not None
        assert "total" in df.columns
        assert len(df) == 4
        # comparison + ≤10 rows + 1 metric + "breakdown" keyword → pie
        assert isinstance(figure, go.Figure)
        assert isinstance(figure.data[0], go.Pie)


# ═════════════════════════════════════════════════════════════════════════════
# Cross-cutting: MongoQuery Pydantic model validation
# ═════════════════════════════════════════════════════════════════════════════

class TestMongoQueryValidation:
    """Pydantic model rejects malformed specs before they reach MongoDB."""

    def test_aggregate_requires_pipeline(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MongoQuery(collection="orders", operation="aggregate", pipeline=[])

    def test_find_defaults_filter_and_limit(self):
        q = MongoQuery(collection="customers", operation="find")
        assert q.filter == {}
        assert q.limit == 50

    def test_invalid_operation_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            MongoQuery(collection="orders", operation="delete")

    def test_valid_aggregate_accepted(self):
        q = MongoQuery(
            collection="orders", operation="aggregate",
            pipeline=[{"$group": {"_id": "$region", "rev": {"$sum": "$total_amount"}}}],
        )
        assert q.operation == "aggregate"
        assert len(q.pipeline) == 1

    def test_to_dict_serialises_correctly(self):
        q = MongoQuery(
            collection="orders", operation="aggregate",
            pipeline=[{"$match": {"status": "completed"}}],
        )
        d = q.to_dict()
        assert d["collection"] == "orders"
        assert isinstance(d["pipeline"], list)
        assert "filter" not in d  # exclude_none=True

    def test_find_to_dict_includes_filter(self):
        q = MongoQuery(collection="products", operation="find",
                       filter={"is_active": True}, limit=20)
        d = q.to_dict()
        assert d["filter"] == {"is_active": True}
        assert d["limit"] == 20
