"""Unit tests for pipeline/analyzer.py — question classification.

All LLM calls are mocked via unittest.mock; no network required.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from pipeline.models import AnalysisResult, Entity


def _make_result(**kwargs) -> AnalysisResult:
    defaults = dict(
        query_type="metric",
        is_why_question=False,
        intent="default intent",
        entities=[],
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


# ── analyze() — happy path per query type ─────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyzeQueryTypes:
    async def _call(self, question: str, result: AnalysisResult) -> dict:
        with patch("pipeline.analyzer._groq") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(return_value=result)
            from pipeline.analyzer import analyze
            return await analyze(question)

    async def test_metric_query(self):
        result = _make_result(
            query_type="metric",
            intent="total revenue for the year",
        )
        out = await self._call("What is total revenue this year?", result)
        assert out["query_type"] == "metric"
        assert out["is_why_question"] is False
        assert "intent" in out

    async def test_ranking_query(self):
        result = _make_result(
            query_type="ranking",
            intent="top 5 sales reps by revenue",
        )
        out = await self._call("Who are the top 5 sales reps?", result)
        assert out["query_type"] == "ranking"

    async def test_comparison_query(self):
        result = _make_result(
            query_type="comparison",
            intent="compare Q1 and Q2 revenue",
        )
        out = await self._call("Compare Q1 vs Q2 revenue", result)
        assert out["query_type"] == "comparison"

    async def test_list_query(self):
        result = _make_result(
            query_type="list",
            intent="list all active customers",
        )
        out = await self._call("Show all active customers", result)
        assert out["query_type"] == "list"

    async def test_lookup_query(self):
        result = _make_result(
            query_type="lookup",
            intent="details for customer Acme Corp",
            entities=[Entity(type="customer", value="Acme Corp")],
        )
        out = await self._call("What do you know about Acme Corp?", result)
        assert out["query_type"] == "lookup"
        assert len(out["entities"]) == 1
        assert out["entities"][0]["value"] == "Acme Corp"

    async def test_smalltalk_query(self):
        result = _make_result(
            query_type="smalltalk",
            intent="greeting",
        )
        out = await self._call("Hello, how are you?", result)
        assert out["query_type"] == "smalltalk"

    async def test_why_question_flagged(self):
        result = _make_result(
            query_type="metric",
            is_why_question=True,
            intent="why did revenue drop last month",
        )
        out = await self._call("Why did revenue drop last month?", result)
        assert out["is_why_question"] is True


# ── analyze() — entity extraction ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyzeEntities:
    async def _call(self, question: str, result: AnalysisResult) -> dict:
        with patch("pipeline.analyzer._groq") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(return_value=result)
            from pipeline.analyzer import analyze
            return await analyze(question)

    async def test_customer_entity_extracted(self):
        result = _make_result(
            query_type="lookup",
            intent="details for Wikolabs",
            entities=[Entity(type="customer", value="Wikolabs")],
        )
        out = await self._call("Tell me about Wikolabs", result)
        assert any(e["type"] == "customer" for e in out["entities"])

    async def test_multiple_entities(self):
        result = _make_result(
            query_type="comparison",
            intent="compare Wikolabs and Acme",
            entities=[
                Entity(type="customer", value="Wikolabs"),
                Entity(type="customer", value="Acme"),
            ],
        )
        out = await self._call("Compare Wikolabs and Acme", result)
        assert len(out["entities"]) == 2

    async def test_employee_entity_extracted(self):
        result = _make_result(
            query_type="lookup",
            intent="details for employee John Smith",
            entities=[Entity(type="employee", value="John Smith")],
        )
        out = await self._call("What are John Smith's sales?", result)
        assert any(e["type"] == "employee" for e in out["entities"])

    async def test_region_entity_extracted(self):
        result = _make_result(
            query_type="metric",
            intent="total revenue for North region",
            entities=[Entity(type="region", value="North")],
        )
        out = await self._call("Revenue in the North region?", result)
        assert any(e["type"] == "region" for e in out["entities"])

    async def test_no_entities_when_generic_question(self):
        result = _make_result(
            query_type="metric",
            intent="total revenue",
            entities=[],
        )
        out = await self._call("What is total revenue?", result)
        assert out["entities"] == []


# ── analyze() — fallback behavior ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestAnalyzeFallback:
    async def test_llm_exception_returns_defaults(self):
        with patch("pipeline.analyzer._groq") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(
                side_effect=Exception("API timeout")
            )
            from pipeline.analyzer import analyze
            out = await analyze("Total revenue?")
        assert out["query_type"] == "metric"
        assert out["is_why_question"] is False
        assert out["entities"] == []
        assert "Total revenue?" in out["intent"]

    async def test_fallback_preserves_question_as_intent(self):
        with patch("pipeline.analyzer._groq") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(
                side_effect=RuntimeError("validation failed")
            )
            from pipeline.analyzer import analyze
            question = "How many orders were placed last week?"
            out = await analyze(question)
        assert out["intent"] == question

    async def test_fallback_has_all_required_keys(self):
        with patch("pipeline.analyzer._groq") as mock_groq:
            mock_groq.chat.completions.create = AsyncMock(
                side_effect=Exception("any error")
            )
            from pipeline.analyzer import analyze
            out = await analyze("?")
        required_keys = {"query_type", "is_why_question", "intent", "entities"}
        assert required_keys.issubset(out.keys())


# ── AnalysisResult model ───────────────────────────────────────────────────────

class TestAnalysisResultModel:
    def test_to_dict_contains_all_fields(self):
        result = _make_result(
            query_type="comparison",
            is_why_question=False,
            intent="compare regions",
            entities=[Entity(type="region", value="North")],
        )
        d = result.to_dict()
        assert d["query_type"] == "comparison"
        assert d["entities"][0]["value"] == "North"

    def test_invalid_query_type_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            AnalysisResult(
                query_type="invalid_type",
                is_why_question=False,
                intent="test",
            )

    def test_invalid_entity_type_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Entity(type="alien", value="test")

    def test_entities_default_empty(self):
        r = AnalysisResult(query_type="metric", is_why_question=False, intent="test")
        assert r.entities == []

    def test_all_valid_query_types_accepted(self):
        for qt in ("smalltalk", "lookup", "list", "metric", "ranking", "comparison"):
            r = AnalysisResult(query_type=qt, is_why_question=False, intent="x")
            assert r.query_type == qt

    def test_all_valid_entity_types_accepted(self):
        for et in ("customer", "contact", "employee", "product", "supplier", "region", "person"):
            e = Entity(type=et, value="test")
            assert e.type == et
