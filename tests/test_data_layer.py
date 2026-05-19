"""Tests for PostgresDataLayer — focused on list_threads robustness.

Key scenarios:
  1. TypedDict filters (Chainlit 1.x / plain dict) — bare .userId raises AttributeError
     without the fix; must return a valid response.
  2. Pydantic model filters (Chainlit 2.x) — normal attribute access.
  3. Filters is None — must not crash.
  4. DB pool failure — must return empty PaginatedResponse, never 500.
  5. Returns correct thread count and respects page_size.
  6. _step_dict helper — all required optional Chainlit 2.x fields present.

Chainlit is mocked at sys.modules level so the test suite runs without installing it.
"""

import json
import sys
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Chainlit stub — injected before data_layer is imported
# ---------------------------------------------------------------------------

def _make_chainlit_stub():
    """Build minimal chainlit modules that satisfy data_layer.py imports."""

    # chainlit.types: PaginatedResponse is a simple class that stores data + pageInfo
    class PaginatedResponse:
        def __init__(self, data, pageInfo):
            self.data     = data
            self.pageInfo = pageInfo

    # Minimal stubs for the other types used in data_layer
    class ThreadDict(dict):
        pass

    class ThreadFilter:
        pass

    class Pagination:
        pass

    class Feedback:
        pass

    types_mod = types.ModuleType("chainlit.types")
    types_mod.PaginatedResponse = PaginatedResponse
    types_mod.ThreadDict        = ThreadDict
    types_mod.ThreadFilter      = ThreadFilter
    types_mod.Pagination        = Pagination
    types_mod.Feedback          = Feedback

    # chainlit.data: BaseDataLayer is an ABC-like base
    class BaseDataLayer:
        pass

    data_mod            = types.ModuleType("chainlit.data")
    data_mod.BaseDataLayer = BaseDataLayer

    # chainlit.user
    class PersistedUser:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    class User:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)

    user_mod             = types.ModuleType("chainlit.user")
    user_mod.PersistedUser = PersistedUser
    user_mod.User          = User

    # top-level chainlit module
    cl_mod = types.ModuleType("chainlit")

    return cl_mod, data_mod, types_mod, user_mod, PaginatedResponse


_cl, _cl_data, _cl_types, _cl_user, PaginatedResponse = _make_chainlit_stub()

# Inject before data_layer import
sys.modules.setdefault("chainlit",       _cl)
sys.modules.setdefault("chainlit.data",  _cl_data)
sys.modules.setdefault("chainlit.types", _cl_types)
sys.modules.setdefault("chainlit.user",  _cl_user)


# ---------------------------------------------------------------------------
# Now safe to import data_layer
# ---------------------------------------------------------------------------

from data_layer import PostgresDataLayer, _step_dict  # noqa: E402


# ---------------------------------------------------------------------------
# Row / object helpers
# ---------------------------------------------------------------------------

def _thread_row(name="Test Thread", user_id=None, user_identifier="demo"):
    """Fake asyncpg Record-like dict."""
    now = datetime.now(timezone.utc)

    class FakeRow(dict):
        pass

    return FakeRow({
        "id":              str(uuid.uuid4()),
        "name":            name,
        "user_id":         user_id or str(uuid.uuid4()),
        "user_identifier": user_identifier,
        "metadata":        {},
        "tags":            [],
        "created_at":      now,
        "updated_at":      now,
        "deleted":         False,
    })


def _pagination_dict(first=20, cursor=None):
    """Chainlit 1.x — plain dict (TypedDict)."""
    return {"first": first, "cursor": cursor}


def _pagination_model(first=20, cursor=None):
    """Chainlit 2.x — Pydantic-like object with .first and .cursor."""
    m = MagicMock()
    m.first  = first
    m.cursor = cursor
    return m


def _filter_dict(user_id="demo-uuid"):
    """Chainlit 1.x — plain dict. Bare .userId would raise AttributeError."""
    return {"userId": user_id, "search": None, "feedback": None}


def _filter_model(user_id="demo-uuid"):
    """Chainlit 2.x — object with .userId attribute."""
    m = MagicMock()
    m.userId = user_id
    return m


def _mock_pool(rows, cursor_ts=None):
    """Return a mock asyncpg Pool that yields `rows` from conn.fetch.

    asyncpg uses `async with pool.acquire() as conn:` — pool.acquire() must be
    a synchronous call that returns an async context manager (not a coroutine).
    """
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=cursor_ts)
    conn.fetch    = AsyncMock(return_value=rows)

    # pool.acquire() is called synchronously; the result is the async ctx-manager
    acquire_ctx = MagicMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__  = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool


@pytest.fixture
def layer():
    return PostgresDataLayer()


# ---------------------------------------------------------------------------
# list_threads — filter type compatibility
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dict_filters_no_attribute_error(layer):
    """Dict-style filters must not raise AttributeError on .userId (Chainlit 1.x path)."""
    pool = _mock_pool([_thread_row("T1"), _thread_row("T2")])
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(), _filter_dict())
    assert isinstance(result, PaginatedResponse)
    assert len(result.data) == 2


@pytest.mark.asyncio
async def test_model_filters_attribute_access_works(layer):
    """Pydantic-model filters must work via .userId attribute (Chainlit 2.x path)."""
    pool = _mock_pool([_thread_row("T1")])
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_model(), _filter_model())
    assert isinstance(result, PaginatedResponse)
    assert len(result.data) == 1
    assert result.data[0]["name"] == "T1"


@pytest.mark.asyncio
async def test_none_filters_no_crash(layer):
    """filters=None must not crash — user_id_filter defaults to None."""
    pool = _mock_pool([_thread_row()])
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(), None)
    assert isinstance(result, PaginatedResponse)


# ---------------------------------------------------------------------------
# list_threads — no 500 on failure (the critical guarantee)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_db_pool_failure_returns_empty_not_500(layer):
    """If the pool itself raises, list_threads must return empty — never propagate."""
    broken = AsyncMock(side_effect=Exception("connection refused"))
    with patch_pool(layer, broken, broken_pool=True):
        result = await layer.list_threads(_pagination_dict(), _filter_dict())

    assert isinstance(result, PaginatedResponse)
    assert result.data == []
    assert result.pageInfo["hasNextPage"] is False


@pytest.mark.asyncio
async def test_sql_error_returns_empty_not_500(layer):
    """If conn.fetch raises, still return empty response — never 500."""
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetch    = AsyncMock(side_effect=Exception("relation does not exist"))

    pool = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__  = AsyncMock(return_value=False)

    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(), _filter_dict())

    assert isinstance(result, PaginatedResponse)
    assert result.data == []


# ---------------------------------------------------------------------------
# list_threads — pagination correctness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_page_size_respected_and_has_next_page(layer):
    """page_size+1 rows returned from DB → hasNextPage=True, only page_size in data."""
    rows = [_thread_row(f"T{i}") for i in range(6)]  # 6 rows, page_size=5
    pool = _mock_pool(rows)
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(first=5), _filter_dict())
    assert len(result.data) == 5
    assert result.pageInfo["hasNextPage"] is True


@pytest.mark.asyncio
async def test_no_next_page_when_fewer_results(layer):
    rows = [_thread_row(f"T{i}") for i in range(3)]
    pool = _mock_pool(rows)
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(first=10), _filter_dict())
    assert len(result.data) == 3
    assert result.pageInfo["hasNextPage"] is False


@pytest.mark.asyncio
async def test_empty_db_returns_empty_cursors(layer):
    pool = _mock_pool([])
    with patch_pool(layer, pool):
        result = await layer.list_threads(_pagination_dict(), _filter_dict())
    assert result.data == []
    assert result.pageInfo["startCursor"] is None
    assert result.pageInfo["endCursor"]   is None


# ---------------------------------------------------------------------------
# _step_dict helper
# ---------------------------------------------------------------------------

def test_step_dict_has_all_chainlit2_optional_fields():
    """_step_dict must include the optional Chainlit 2.x fields that prevent crashes."""
    now = datetime.now(timezone.utc)
    row = {
        "id":         str(uuid.uuid4()),
        "thread_id":  str(uuid.uuid4()),
        "parent_id":  None,
        "type":       "user_message",
        "name":       "User",
        "input_":     "hello",
        "output":     "hi there",
        "metadata":   {},
        "created_at": now,
        "is_error":   False,
    }

    result = _step_dict(row)

    # These optional fields must exist or Chainlit 2.x frontend crashes
    for field in ("indent", "waitForAnswer", "start", "end", "language", "showInput", "streaming", "tags"):
        assert field in result, f"Missing required field: {field}"

    assert result["streaming"]     is False
    assert result["indent"]        == 0
    assert result["showInput"]     is False
    assert result["waitForAnswer"] is None
    assert result["tags"]          == []


# ---------------------------------------------------------------------------
# Context-manager helper (defined after fixtures to avoid forward refs)
# ---------------------------------------------------------------------------

from contextlib import contextmanager
from unittest.mock import patch as _patch


@contextmanager
def patch_pool(layer_obj, pool_or_side_effect, broken_pool=False):
    """Patch layer._pool to return the given pool (or raise if broken_pool=True)."""
    if broken_pool:
        with _patch.object(layer_obj, "_pool", pool_or_side_effect):
            yield
    else:
        async def _get_pool():
            return pool_or_side_effect
        with _patch.object(layer_obj, "_pool", _get_pool):
            yield
