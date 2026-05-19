"""Unit tests for pipeline/collection_selector.py — 3-tier selection logic.

All external dependencies (schema_registry, Groq) are mocked.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock


# ── Helper ────────────────────────────────────────────────────────────────────

async def _select(intent, query_type="metric", entities=None,
                  pgvector_result=None,
                  summaries=None,
                  llm_result=None,
                  pgvector_raises=False,
                  summaries_raises=False,
                  llm_raises=False):
    """Call select() with controlled mocks for all three tiers."""
    from pipeline.collection_selector import select

    def _maybe_raise_pgvector(*a, **kw):
        if pgvector_raises:
            raise Exception("pgvector unavailable")
        return pgvector_result or []

    def _maybe_raise_summaries(*a, **kw):
        if summaries_raises:
            raise Exception("postgres unavailable")
        return summaries or {}

    async def _maybe_llm(*a, **kw):
        if llm_raises:
            raise Exception("LLM failed")
        return llm_result or []

    with patch("schema_registry.find_similar_collections",
               new=AsyncMock(side_effect=_maybe_raise_pgvector)), \
         patch("schema_registry.get_all_summaries",
               new=AsyncMock(side_effect=_maybe_raise_summaries)), \
         patch("pipeline.collection_selector._llm_select",
               new=AsyncMock(side_effect=_maybe_llm)):
        return await select(
            intent=intent,
            query_type=query_type,
            entities=entities or [],
        )


# ── Tier 1: pgvector ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestPgvectorTier:
    async def test_uses_pgvector_when_available(self):
        result = await _select(
            "total revenue by region",
            pgvector_result=["orders", "customers"],
        )
        assert result[0] == "orders"
        assert "customers" in result

    async def test_pgvector_result_limited_to_3(self):
        result = await _select(
            "complex query",
            pgvector_result=["orders", "customers", "products", "employees"],
        )
        assert len(result) <= 3

    async def test_entity_boost_appended_to_pgvector_result(self):
        entities = [{"type": "employee", "value": "John"}]
        result = await _select(
            "how many orders",
            entities=entities,
            pgvector_result=["orders"],
        )
        # employee entity should add "employees" if not already present
        assert "employees" in result

    async def test_entity_boost_not_duplicated(self):
        entities = [{"type": "customer", "value": "Acme"}]
        result = await _select(
            "customer revenue",
            entities=entities,
            pgvector_result=["customers", "orders"],
        )
        assert result.count("customers") == 1

    async def test_pgvector_empty_falls_through_to_llm(self):
        result = await _select(
            "total payroll",
            pgvector_result=[],          # empty → fall through
            summaries={"employees": "employees(name, salary, department)"},
            llm_result=["employees"],
        )
        assert "employees" in result


# ── Tier 2: LLM with summaries ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestLlmTier:
    async def test_llm_used_when_pgvector_raises(self):
        result = await _select(
            "sales rep performance",
            pgvector_raises=True,
            summaries={"employees": "employees(name, quota)", "orders": "orders(amount)"},
            llm_result=["employees", "orders"],
        )
        assert "employees" in result

    async def test_llm_result_limited_to_3(self):
        result = await _select(
            "big query",
            pgvector_raises=True,
            summaries={f"col_{i}": f"col_{i}(field)" for i in range(6)},
            llm_result=["col_0", "col_1", "col_2", "col_3"],
        )
        assert len(result) <= 3

    async def test_llm_falls_through_to_keyword_when_empty(self):
        result = await _select(
            "order revenue",
            pgvector_raises=True,
            summaries={"orders": "orders(amount, status)"},
            llm_result=[],              # empty → fall through to keyword
        )
        # keyword fallback should catch "order" → "orders"
        assert "orders" in result


# ── Tier 3: keyword fallback ──────────────────────────────────────────────────

@pytest.mark.asyncio
class TestKeywordFallback:
    async def test_order_keyword_selects_orders(self):
        result = await _select(
            "how many orders were placed",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "orders" in result

    async def test_salary_keyword_selects_employees(self):
        result = await _select(
            "average salary by department",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "employees" in result

    async def test_product_keyword_selects_products(self):
        result = await _select(
            "top products by margin",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "products" in result

    async def test_supplier_keyword_selects_suppliers(self):
        result = await _select(
            "supplier lead time",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "suppliers" in result

    async def test_transaction_keyword_selects_transactions(self):
        result = await _select(
            "payment refund expenses",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "transactions" in result

    async def test_contact_keyword_selects_contacts(self):
        result = await _select(
            "who is the CEO of Acme",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert "contacts" in result

    async def test_no_keyword_uses_query_type_default(self):
        result = await _select(
            "xyzzy gibberish words",    # no keywords match
            query_type="ranking",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert len(result) > 0
        assert "orders" in result or "products" in result

    async def test_metric_default_is_orders_transactions(self):
        result = await _select(
            "zxqv no match",
            query_type="metric",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert any(c in result for c in ["orders", "transactions"])

    async def test_lookup_default_includes_customers(self):
        result = await _select(
            "zxqv no match",
            query_type="lookup",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert any(c in result for c in ["customers", "products", "employees"])

    async def test_result_limited_to_3(self):
        result = await _select(
            "order revenue customer product supplier",
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert len(result) <= 3


# ── smalltalk short-circuit ───────────────────────────────────────────────────

@pytest.mark.asyncio
class TestSmalltalk:
    async def test_smalltalk_returns_empty_list(self):
        result = await _select(
            "hello how are you",
            query_type="smalltalk",
        )
        assert result == []

    async def test_smalltalk_skips_all_tiers(self):
        """smalltalk must short-circuit before any tier is reached."""
        with patch("schema_registry.find_similar_collections",
                   new=AsyncMock(side_effect=Exception("should not be called"))) as m:
            from pipeline.collection_selector import select
            result = await select("hi", "smalltalk", [])
            m.assert_not_called()
        assert result == []


# ── entity boost ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestEntityBoost:
    async def test_contact_boosts_contacts_orders_customers(self):
        entities = [{"type": "contact", "value": "John"}]
        result = await _select(
            "contact info",
            entities=entities,
            pgvector_raises=True,
            summaries_raises=True,
        )
        # At least one of the boosted collections should appear
        assert any(c in result for c in ["contacts", "orders", "customers"])

    async def test_product_entity_boosts_products_orders(self):
        entities = [{"type": "product", "value": "Widget Pro"}]
        result = await _select(
            "product details",
            entities=entities,
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert any(c in result for c in ["products", "orders"])

    async def test_supplier_entity_boosts_suppliers_products(self):
        entities = [{"type": "supplier", "value": "Acme Supplies"}]
        result = await _select(
            "supplier info",
            entities=entities,
            pgvector_raises=True,
            summaries_raises=True,
        )
        assert any(c in result for c in ["suppliers", "products"])
