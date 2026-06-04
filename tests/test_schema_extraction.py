"""Unit tests for schema extraction logic (no DB, no network required).

Tests _flatten, _type_name, _extract_fields, _build_summary with every
MongoDB document shape a client could have.
"""

import pytest
from bson import ObjectId
from datetime import datetime, timezone

# Import the private functions directly
from schema_registry import _flatten, _type_name, _extract_fields, _build_summary


# ── _type_name ────────────────────────────────────────────────────────────────

class TestTypeName:
    def test_none(self):
        assert _type_name(None) == "null"

    def test_bool_true(self):
        assert _type_name(True) == "bool"

    def test_bool_false(self):
        assert _type_name(False) == "bool"

    def test_bool_not_confused_with_int(self):
        # In Python, isinstance(True, int) is True · must check bool first
        assert _type_name(True)  == "bool"
        assert _type_name(1)     == "int"
        assert _type_name(0)     == "int"

    def test_int(self):
        assert _type_name(42)   == "int"
        assert _type_name(-10)  == "int"
        assert _type_name(0)    == "int"

    def test_float(self):
        assert _type_name(3.14)  == "float"
        assert _type_name(-0.5)  == "float"

    def test_str(self):
        assert _type_name("hello") == "str"
        assert _type_name("")      == "str"

    def test_list(self):
        assert _type_name([])           == "array"
        assert _type_name([1, 2, 3])    == "array"
        assert _type_name(["a", "b"])   == "array"

    def test_dict(self):
        assert _type_name({})           == "object"
        assert _type_name({"a": 1})     == "object"

    def test_objectid(self):
        assert _type_name(ObjectId())   == "oid"

    def test_datetime(self):
        assert _type_name(datetime.now()) == "date"
        assert _type_name(datetime.now(timezone.utc)) == "date"

    def test_unicode_string(self):
        assert _type_name("François") == "str"
        assert _type_name("上海")      == "str"


# ── _flatten ──────────────────────────────────────────────────────────────────

class TestFlatten:
    def test_flat_document(self):
        doc = {"name": "Alice", "age": 30, "active": True}
        result = _flatten(doc)
        assert result == {"name": "Alice", "age": 30, "active": True}

    def test_skips_id(self):
        doc = {"_id": ObjectId(), "name": "Alice"}
        result = _flatten(doc)
        assert "_id" not in result
        assert result == {"name": "Alice"}

    def test_nested_one_level(self, nested_docs):
        doc = nested_docs[0]
        result = _flatten(doc)
        assert "customer.name"   in result
        assert "customer.region" in result
        assert "order_number"    in result
        assert "amount"          in result
        assert "customer"        not in result  # replaced by flat keys

    def test_nested_two_levels(self):
        doc = {"a": {"b": {"c": "deep_value"}}}
        result = _flatten(doc)
        assert result == {"a.b.c": "deep_value"}

    def test_array_of_objects_uses_first_element(self):
        doc = {"items": [{"sku": "X", "qty": 2}, {"sku": "Y", "qty": 3}]}
        result = _flatten(doc)
        assert "items[].sku" in result
        assert "items[].qty" in result

    def test_array_of_primitives_kept_as_array(self):
        doc = {"tags": ["electronics", "mobile"]}
        result = _flatten(doc)
        assert "tags" in result
        assert result["tags"] == ["electronics", "mobile"]

    def test_empty_array_kept_as_array(self):
        doc = {"tags": []}
        result = _flatten(doc)
        assert result["tags"] == []

    def test_null_value_preserved(self):
        doc = {"name": "Alice", "score": None}
        result = _flatten(doc)
        assert result["score"] is None

    def test_unicode_keys_and_values(self):
        doc = {"prénom": "François", "城市": "上海"}
        result = _flatten(doc)
        assert result["prénom"] == "François"
        assert result["城市"]   == "上海"

    def test_empty_document(self):
        result = _flatten({})
        assert result == {}

    def test_objectid_value(self):
        oid = ObjectId()
        doc = {"customer_id": oid}
        result = _flatten(doc)
        assert result["customer_id"] == oid


# ── _extract_fields ───────────────────────────────────────────────────────────

class TestExtractFields:
    def test_flat_docs(self, flat_docs):
        fields = _extract_fields(flat_docs)
        assert "name"   in fields
        assert "age"    in fields
        assert "active" in fields
        assert "score"  in fields

    def test_type_frequencies_sum_to_one(self, flat_docs):
        fields = _extract_fields(flat_docs)
        for field, info in fields.items():
            total = sum(info["types"].values())
            assert abs(total - 1.0) < 0.01, f"types for '{field}' don't sum to 1: {info['types']}"

    def test_enum_detected_for_low_cardinality(self, enum_docs):
        fields = _extract_fields(enum_docs)
        assert "status" in fields
        assert "enum" in fields["status"], "Low-cardinality string field should be detected as enum"
        assert set(fields["status"]["enum"]) == {"completed", "pending", "cancelled", "refunded"}

    def test_enum_not_detected_for_high_cardinality(self, high_cardinality_docs):
        fields = _extract_fields(high_cardinality_docs)
        assert "email" in fields
        assert "enum" not in fields["email"], "High-cardinality field should NOT be enum"

    def test_mixed_types_tracked(self, mixed_types_docs):
        fields = _extract_fields(mixed_types_docs)
        # "amount" has int, float, null
        assert "amount" in fields
        types = fields["amount"]["types"]
        assert len(types) >= 2, "Mixed-type field should have multiple types"

    def test_null_type_tracked(self, mixed_types_docs):
        fields = _extract_fields(mixed_types_docs)
        assert "score" in fields or "flag" in fields or "amount" in fields
        # "amount" has None in one doc
        types = fields["amount"]["types"]
        assert "null" in types, "None values must be counted as 'null', not 'NoneType'"

    def test_bool_tracked_correctly(self, flat_docs):
        fields = _extract_fields(flat_docs)
        assert "active" in fields
        types = fields["active"]["types"]
        assert "bool" in types, "Boolean values must be typed as 'bool', not 'int'"
        assert "int" not in types, "Boolean values must NOT be typed as 'int'"

    def test_objectid_typed_as_oid(self, objectid_docs):
        fields = _extract_fields(objectid_docs)
        assert "customer_id" in fields
        assert fields["customer_id"]["types"].get("oid", 0) > 0

    def test_datetime_typed_as_date(self, objectid_docs):
        fields = _extract_fields(objectid_docs)
        assert "order_date" in fields
        assert fields["order_date"]["types"].get("date", 0) > 0

    def test_nested_fields_extracted(self, nested_docs):
        fields = _extract_fields(nested_docs)
        assert "customer.name"   in fields
        assert "customer.region" in fields

    def test_array_of_objects_fields_extracted(self, array_of_objects_docs):
        fields = _extract_fields(array_of_objects_docs)
        assert "items[].sku" in fields
        assert "items[].qty" in fields

    def test_array_of_primitives_typed_as_array(self, array_of_primitives_docs):
        fields = _extract_fields(array_of_primitives_docs)
        assert "tags" in fields
        assert fields["tags"]["types"].get("array", 0) > 0

    def test_empty_docs_returns_empty(self, empty_docs):
        fields = _extract_fields(empty_docs)
        assert fields == {}

    def test_unicode_values_in_enum(self, unicode_docs):
        fields = _extract_fields(unicode_docs)
        assert "country" in fields
        assert "enum" in fields["country"]
        assert "Canada" in fields["country"]["enum"]

    def test_many_fields_doc(self, many_fields_docs):
        fields = _extract_fields(many_fields_docs)
        assert len(fields) >= 35, "All 35 fields should be extracted"

    def test_deeply_nested(self, deeply_nested_docs):
        fields = _extract_fields(deeply_nested_docs)
        assert "level1.level2.level3.value" in fields


# ── _build_summary ────────────────────────────────────────────────────────────

class TestBuildSummary:
    def test_format_starts_with_collection_name(self, flat_docs):
        fields = _extract_fields(flat_docs)
        summary = _build_summary("users", fields)
        assert summary.startswith("users(")
        assert summary.endswith(")")

    def test_enum_fields_shown_with_values(self, enum_docs):
        fields = _extract_fields(enum_docs)
        summary = _build_summary("orders", fields)
        assert "status[" in summary
        assert "completed" in summary or "pending" in summary

    def test_oid_fields_labeled(self, objectid_docs):
        fields = _extract_fields(objectid_docs)
        summary = _build_summary("orders", fields)
        assert "ObjectId" in summary

    def test_date_fields_labeled(self, objectid_docs):
        fields = _extract_fields(objectid_docs)
        summary = _build_summary("orders", fields)
        assert "date" in summary.lower()

    def test_empty_collection_returns_valid_summary(self, empty_docs):
        fields = _extract_fields(empty_docs)
        summary = _build_summary("empty_coll", fields)
        assert summary == "empty_coll()"

    def test_summary_is_single_line(self, flat_docs):
        fields = _extract_fields(flat_docs)
        summary = _build_summary("test", fields)
        assert "\n" not in summary


# ── Edge cases combining all shapes ───────────────────────────────────────────

class TestEdgeCases:
    def test_boolean_distinguished_from_zero(self):
        """Python: isinstance(False, int) is True · we must not tag False as int."""
        docs = [{"flag": False}, {"flag": True}, {"flag": True}]
        fields = _extract_fields(docs)
        types = fields["flag"]["types"]
        assert "bool" in types
        assert "int"  not in types

    def test_large_int_not_float(self):
        docs = [{"revenue": 1_000_000}, {"revenue": 2_500_000}]
        fields = _extract_fields(docs)
        assert fields["revenue"]["types"].get("int", 0) > 0
        assert fields["revenue"]["types"].get("float", 0) == 0

    def test_none_and_value_mixed(self):
        docs = [{"x": 1}, {"x": None}, {"x": 2}, {"x": None}]
        fields = _extract_fields(docs)
        types = fields["x"]["types"]
        assert "int"  in types
        assert "null" in types
        assert abs(types["null"] - 0.5) < 0.01

    def test_field_present_in_some_docs(self):
        """Field that only exists in 2 of 4 documents."""
        docs = [
            {"a": 1, "b": "yes"},
            {"a": 2},
            {"a": 3, "b": "no"},
            {"a": 4},
        ]
        fields = _extract_fields(docs)
        assert "a" in fields
        assert "b" in fields
        assert fields["b"]["types"].get("str", 0) > 0

    def test_all_null_field(self):
        docs = [{"score": None}, {"score": None}, {"score": None}]
        fields = _extract_fields(docs)
        assert fields["score"]["types"].get("null", 0) == 1.0

    def test_single_document(self):
        doc = {"name": "Solo", "value": 42}
        fields = _extract_fields([doc])
        assert "name"  in fields
        assert "value" in fields

    def test_fifteen_distinct_values_is_still_enum(self):
        """Exactly 15 distinct values should still become an enum."""
        docs = [{"status": f"state_{i}"} for i in range(15)] * 2
        fields = _extract_fields(docs)
        assert "enum" in fields["status"]
        assert len(fields["status"]["enum"]) == 15

    def test_sixteen_distinct_values_is_not_enum(self):
        """16 distinct values must NOT become an enum."""
        docs = [{"code": f"code_{i}"} for i in range(16)] * 2
        fields = _extract_fields(docs)
        assert "enum" not in fields["code"]

    def test_objectid_not_confused_with_str(self):
        oid = ObjectId()
        docs = [{"ref_id": oid}, {"ref_id": ObjectId()}, {"ref_id": ObjectId()}]
        fields = _extract_fields(docs)
        types = fields["ref_id"]["types"]
        assert "oid" in types
        assert "str" not in types
