"""Unit tests for pipeline/executor.py — MongoDB query execution and retry.

Uses a mock pymongo Database so no real MongoDB connection is needed.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId

from pipeline.executor import _convert_oids, _execute


# ── _convert_oids ─────────────────────────────────────────────────────────────

class TestConvertOids:
    def test_plain_dict_unchanged(self):
        obj = {"status": "completed", "amount": 100}
        assert _convert_oids(obj) == obj

    def test_oid_dict_converted(self):
        oid_str = "507f1f77bcf86cd799439011"
        result = _convert_oids({"$oid": oid_str})
        assert isinstance(result, ObjectId)
        assert str(result) == oid_str

    def test_nested_oid_converted(self):
        oid_str = "507f1f77bcf86cd799439011"
        obj = {"customer_id": {"$oid": oid_str}, "amount": 500}
        result = _convert_oids(obj)
        assert isinstance(result["customer_id"], ObjectId)
        assert result["amount"] == 500

    def test_list_of_oids(self):
        oid_str = "507f1f77bcf86cd799439011"
        result = _convert_oids([{"$oid": oid_str}, {"$oid": oid_str}])
        assert all(isinstance(r, ObjectId) for r in result)

    def test_pipeline_with_oid_match(self):
        oid_str = "507f1f77bcf86cd799439011"
        pipeline = [
            {"$match": {"customer_id": {"$oid": oid_str}}},
            {"$group": {"_id": "$region", "total": {"$sum": "$amount"}}},
        ]
        result = _convert_oids(pipeline)
        assert isinstance(result[0]["$match"]["customer_id"], ObjectId)
        # Non-oid dict preserved
        assert result[1]["$group"]["_id"] == "$region"

    def test_dict_with_dollar_key_not_oid_preserved(self):
        obj = {"$match": {"status": "completed"}}
        result = _convert_oids(obj)
        # {"$match": ...} has more than one key would be wrong — but it IS one key
        # however "$match" != "$oid" so it should NOT be converted
        assert result == {"$match": {"status": "completed"}}

    def test_primitive_unchanged(self):
        assert _convert_oids("hello") == "hello"
        assert _convert_oids(42)      == 42
        assert _convert_oids(None)    is None
        assert _convert_oids(True)    is True


# ── _execute ──────────────────────────────────────────────────────────────────

def _make_db(find_result=None, aggregate_result=None):
    """Build a mock pymongo Database."""
    db = MagicMock()
    col = MagicMock()
    db.__getitem__ = MagicMock(return_value=col)

    # find mock
    cursor = MagicMock()
    cursor.limit.return_value = cursor
    cursor.__iter__ = MagicMock(return_value=iter(find_result or []))
    list_mock = MagicMock(return_value=find_result or [])
    col.find.return_value = cursor

    # aggregate mock
    col.aggregate.return_value = iter(aggregate_result or [])

    return db, col


class TestExecute:
    def test_aggregate_returns_results(self):
        rows = [{"region": "North", "revenue": 50000}]
        db, col = _make_db(aggregate_result=rows)
        spec = {"collection": "orders", "operation": "aggregate",
                "pipeline": [{"$group": {"_id": "$region", "revenue": {"$sum": "$total_amount"}}}]}
        result = _execute(spec, db)
        col.aggregate.assert_called_once()
        assert result == rows

    def test_find_returns_results(self):
        rows = [{"name": "Alice"}, {"name": "Bob"}]
        db, col = _make_db(find_result=rows)
        spec = {"collection": "customers", "operation": "find",
                "filter": {"active": True}, "limit": 10}
        result = _execute(spec, db)
        col.find.assert_called_once()

    def test_missing_collection_raises(self):
        db, _ = _make_db()
        with pytest.raises(ValueError, match="missing 'collection'"):
            _execute({}, db)

    def test_unknown_operation_raises(self):
        db, _ = _make_db()
        with pytest.raises(ValueError, match="Unknown operation"):
            _execute({"collection": "orders", "operation": "insert"}, db)

    def test_aggregate_oid_converted_before_execution(self):
        oid_str = "507f1f77bcf86cd799439011"
        db, col = _make_db(aggregate_result=[])
        spec = {
            "collection": "orders",
            "operation": "aggregate",
            "pipeline": [{"$match": {"customer_id": {"$oid": oid_str}}}],
        }
        _execute(spec, db)
        call_args = col.aggregate.call_args[0][0]
        assert isinstance(call_args[0]["$match"]["customer_id"], ObjectId)

    def test_find_with_no_filter_uses_empty_dict(self):
        rows = [{"name": "Product A"}]
        db, col = _make_db(find_result=rows)
        spec = {"collection": "products", "operation": "find"}
        _execute(spec, db)
        col.find.assert_called_once_with({}, {})

    def test_aggregate_missing_pipeline_raises(self):
        db, _ = _make_db()
        spec = {"collection": "orders", "operation": "aggregate"}
        with pytest.raises(ValueError, match="missing valid 'pipeline'"):
            _execute(spec, db)


# ── execute_with_retry ────────────────────────────────────────────────────────

@pytest.mark.asyncio
class TestExecuteWithRetry:
    async def test_success_on_first_attempt(self):
        from pipeline.executor import execute_with_retry
        rows = [{"total": 12345}]
        db, col = _make_db(aggregate_result=rows)
        spec = {"collection": "orders", "operation": "aggregate",
                "pipeline": [{"$group": {"_id": None, "total": {"$sum": "$amount"}}}]}

        result = await execute_with_retry(
            spec=spec, question="What is total revenue?",
            analysis={"query_type": "metric", "intent": "total revenue",
                       "is_why_question": False, "entities": []},
            collections=["orders"], entity_map={}, db=db,
        )
        assert result == rows

    async def test_retries_on_failure_then_succeeds(self):
        from pipeline.executor import execute_with_retry

        db = MagicMock()
        col = MagicMock()
        db.__getitem__ = MagicMock(return_value=col)
        good_result = [{"region": "North", "revenue": 1000}]

        # First call raises, second call succeeds
        call_count = [0]
        def side_effect(pipeline):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("invalid operator $groop")
            return iter(good_result)

        col.aggregate.side_effect = side_effect

        fixed_spec = {"collection": "orders", "operation": "aggregate",
                      "pipeline": [{"$group": {"_id": "$region", "revenue": {"$sum": "$amount"}}}]}

        with patch("pipeline.executor.generate", new=AsyncMock(return_value=fixed_spec)):
            result = await execute_with_retry(
                spec=fixed_spec,
                question="Revenue by region",
                analysis={"query_type": "comparison", "intent": "revenue by region",
                           "is_why_question": False, "entities": []},
                collections=["orders"], entity_map={}, db=db,
            )
        assert result == good_result

    async def test_raises_after_max_retries(self):
        from pipeline.executor import execute_with_retry

        db = MagicMock()
        col = MagicMock()
        db.__getitem__ = MagicMock(return_value=col)
        col.aggregate.side_effect = Exception("syntax error")

        bad_spec = {"collection": "orders", "operation": "aggregate",
                    "pipeline": [{"$baad": {}}]}

        with patch("pipeline.executor.generate", new=AsyncMock(return_value=bad_spec)):
            with pytest.raises(RuntimeError, match="failed after"):
                await execute_with_retry(
                    spec=bad_spec,
                    question="test",
                    analysis={"query_type": "metric", "intent": "test",
                               "is_why_question": False, "entities": []},
                    collections=["orders"], entity_map={}, db=db,
                )
