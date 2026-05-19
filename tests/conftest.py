"""Shared fixtures and helpers for the test suite."""

import os

# Must be set before any pipeline module is imported (they instantiate AsyncGroq at load time)
os.environ.setdefault("GROQ_API_KEY", "test-dummy-key-for-unit-tests")

import pytest
from bson import ObjectId
from datetime import datetime, timezone

# ── MongoDB document shape fixtures ──────────────────────────────────────────

@pytest.fixture
def flat_docs():
    """Simple flat documents — the most common shape."""
    return [
        {"name": "Alice", "age": 30, "active": True,  "score": 4.5},
        {"name": "Bob",   "age": 25, "active": False, "score": 3.2},
        {"name": "Alice", "age": 31, "active": True,  "score": 4.8},
        {"name": "Carol", "age": 28, "active": True,  "score": None},
    ]


@pytest.fixture
def nested_docs():
    """Documents with 2-level nesting."""
    return [
        {"order_number": "ORD-001", "customer": {"name": "Acme", "region": "North"},
         "amount": 1500.0, "status": "completed"},
        {"order_number": "ORD-002", "customer": {"name": "Beta", "region": "South"},
         "amount": 800.0,  "status": "pending"},
        {"order_number": "ORD-003", "customer": {"name": "Acme", "region": "North"},
         "amount": 2200.0, "status": "completed"},
    ]


@pytest.fixture
def array_of_objects_docs():
    """Documents containing arrays of sub-documents."""
    return [
        {"order_id": "O1", "items": [{"sku": "A", "qty": 2}, {"sku": "B", "qty": 1}]},
        {"order_id": "O2", "items": [{"sku": "C", "qty": 5}]},
    ]


@pytest.fixture
def array_of_primitives_docs():
    """Documents containing arrays of primitive values (tags, categories)."""
    return [
        {"name": "Product A", "tags": ["electronics", "mobile"], "price": 299.99},
        {"name": "Product B", "tags": ["electronics", "laptop"], "price": 999.99},
        {"name": "Product C", "tags": ["furniture"],             "price": 199.99},
    ]


@pytest.fixture
def mixed_types_docs():
    """Same field appears as different types across documents."""
    return [
        {"ref": "X001",  "amount": 100,     "flag": True},
        {"ref": "X002",  "amount": 99.99,   "flag": False},
        {"ref": "X003",  "amount": None,    "flag": True},
        {"ref": None,    "amount": 50,      "flag": None},
    ]


@pytest.fixture
def objectid_docs():
    """Documents with ObjectId references."""
    cust_id = ObjectId()
    return [
        {"order_number": "ORD-1", "customer_id": cust_id,
         "order_date": datetime(2024, 1, 15, tzinfo=timezone.utc), "total": 500.0},
        {"order_number": "ORD-2", "customer_id": ObjectId(),
         "order_date": datetime(2024, 2, 20, tzinfo=timezone.utc), "total": 750.0},
    ]


@pytest.fixture
def deeply_nested_docs():
    """Documents with 3+ levels of nesting."""
    return [
        {"level1": {"level2": {"level3": {"value": "deep"}}}},
        {"level1": {"level2": {"level3": {"value": "also deep"}, "extra": 42}}},
    ]


@pytest.fixture
def enum_docs():
    """String field with low cardinality — should be detected as enum."""
    statuses = ["completed", "pending", "cancelled", "refunded"]
    return [
        {"order_id": f"O{i}", "status": statuses[i % 4], "amount": i * 10.0}
        for i in range(1, 21)
    ]


@pytest.fixture
def high_cardinality_docs():
    """String field with high cardinality — should NOT become enum."""
    return [
        {"email": f"user{i}@example.com", "score": i}
        for i in range(30)
    ]


@pytest.fixture
def empty_docs():
    """Empty collection — no documents sampled."""
    return []


@pytest.fixture
def unicode_docs():
    """Documents with unicode field values."""
    return [
        {"name": "François", "city": "Montréal", "country": "Canada"},
        {"name": "José",     "city": "São Paulo", "country": "Brazil"},
        {"name": "李明",      "city": "上海",       "country": "China"},
    ]


@pytest.fixture
def many_fields_docs():
    """Documents with 30+ fields — tests summary truncation."""
    return [
        {f"field_{i}": f"value_{i}" for i in range(35)}
        for _ in range(5)
    ]
