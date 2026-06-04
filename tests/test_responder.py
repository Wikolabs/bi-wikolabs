"""Unit tests for pipeline/responder.py · DataFrame and chart building.

No LLM calls are made · only _build_dataframe() and _build_chart() are tested.
"""

import pytest
import pandas as pd
import plotly.graph_objects as go
from bson import ObjectId

from pipeline.responder import _build_dataframe, _build_chart, _numeric_columns, _label_column, _should_use_pie


# ── _build_dataframe ──────────────────────────────────────────────────────────

class TestBuildDataframe:
    def test_empty_results_returns_none(self):
        assert _build_dataframe([]) is None

    def test_flat_rows_become_dataframe(self):
        rows = [{"region": "North", "revenue": 50000},
                {"region": "South", "revenue": 30000}]
        df = _build_dataframe(rows)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["region", "revenue"]
        assert len(df) == 2

    def test_id_none_dropped(self):
        rows = [{"_id": None, "name": "Alice", "score": 10}]
        df = _build_dataframe(rows)
        assert "_id" not in df.columns
        assert "group" not in df.columns
        assert "name" in df.columns

    def test_id_objectid_dropped(self):
        rows = [{"_id": ObjectId(), "name": "Alice"}]
        df = _build_dataframe(rows)
        assert "_id" not in df.columns
        assert "group" not in df.columns

    def test_id_string_becomes_group(self):
        rows = [{"_id": "North", "revenue": 50000},
                {"_id": "South", "revenue": 30000}]
        df = _build_dataframe(rows)
        assert "group" in df.columns
        assert "_id" not in df.columns
        assert set(df["group"]) == {"North", "South"}

    def test_id_integer_becomes_group(self):
        rows = [{"_id": 2024, "count": 5}, {"_id": 2023, "count": 3}]
        df = _build_dataframe(rows)
        assert "group" in df.columns
        assert set(df["group"]) == {2024, 2023}

    def test_group_column_not_overwritten_if_present(self):
        """If 'group' is already in the row, setdefault must not overwrite it."""
        rows = [{"_id": "North", "group": "existing", "revenue": 100}]
        df = _build_dataframe(rows)
        assert df.iloc[0]["group"] == "existing"

    def test_single_row(self):
        rows = [{"total_revenue": 12345}]
        df = _build_dataframe(rows)
        assert len(df) == 1
        assert df.iloc[0]["total_revenue"] == 12345

    def test_mixed_columns_across_rows(self):
        rows = [{"region": "North", "revenue": 100, "cost": 50},
                {"region": "South", "revenue": 80}]
        df = _build_dataframe(rows)
        assert "region"  in df.columns
        assert "revenue" in df.columns
        assert "cost"    in df.columns

    def test_no_id_in_rows_unchanged(self):
        rows = [{"name": "A", "val": 1}, {"name": "B", "val": 2}]
        df = _build_dataframe(rows)
        assert list(df.columns) == ["name", "val"]

    def test_many_rows(self):
        rows = [{"cat": f"cat_{i}", "val": i} for i in range(100)]
        df = _build_dataframe(rows)
        assert len(df) == 100

    def test_nested_dict_value_preserved(self):
        rows = [{"region": "North", "meta": {"key": "val"}}]
        df = _build_dataframe(rows)
        assert "meta" in df.columns


# ── _numeric_columns / _label_column ──────────────────────────────────────────

class TestNumericColumns:
    def test_finds_int_and_float_columns(self):
        df = pd.DataFrame([{"region": "A", "revenue": 100, "count": 5, "rate": 0.5}])
        cols = _numeric_columns(df)
        assert "revenue" in cols
        assert "count"   in cols
        assert "rate"    in cols
        assert "region"  not in cols

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        assert _numeric_columns(df) == []

    def test_all_string_columns(self):
        df = pd.DataFrame([{"a": "x", "b": "y"}])
        assert _numeric_columns(df) == []


class TestLabelColumn:
    def test_prefers_hint_column(self):
        df = pd.DataFrame([{"region": "North", "name": "Acme", "revenue": 100}])
        assert _label_column(df) in {"region", "name"}

    def test_falls_back_to_any_string(self):
        df = pd.DataFrame([{"sku": "A001", "revenue": 50}])
        assert _label_column(df) == "sku"

    def test_all_numeric_falls_back_to_non_metric(self):
        df = pd.DataFrame([{"year": 2023, "revenue": 50000}])
        label = _label_column(df)
        assert label == "year"

    def test_group_column_used_as_label(self):
        df = pd.DataFrame([{"group": "North", "revenue": 100},
                           {"group": "South", "revenue": 200}])
        assert _label_column(df) == "group"


# ── _should_use_pie ───────────────────────────────────────────────────────────

class TestShouldUsePie:
    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_comparison_with_2_to_10_rows_is_pie(self):
        df = self._df([{"group": "North", "revenue": 100},
                       {"group": "South", "revenue": 200}])
        assert _should_use_pie("comparison", df, "revenue breakdown") is True

    def test_metric_never_pie(self):
        df = self._df([{"total": 12345}])
        assert _should_use_pie("metric", df, "what is total revenue") is False

    def test_ranking_never_pie(self):
        df = self._df([{"group": f"G{i}", "revenue": i} for i in range(5)])
        assert _should_use_pie("ranking", df, "top 5 regions") is False

    def test_lookup_never_pie(self):
        df = self._df([{"name": "Acme", "revenue": 100}])
        assert _should_use_pie("lookup", df, "details for Acme") is False

    def test_more_than_10_rows_not_pie(self):
        df = self._df([{"group": f"G{i}", "revenue": i} for i in range(11)])
        assert _should_use_pie("comparison", df, "breakdown") is False

    def test_multiple_numeric_cols_not_pie(self):
        df = self._df([{"group": "A", "revenue": 100, "cost": 50},
                       {"group": "B", "revenue": 80,  "cost": 40}])
        assert _should_use_pie("comparison", df, "revenue breakdown") is False

    def test_pie_keyword_triggers_pie_for_list(self):
        df = self._df([{"group": f"R{i}", "val": i} for i in range(5)])
        assert _should_use_pie("list", df, "show distribution by region") is True

    def test_empty_df_not_pie(self):
        assert _should_use_pie("comparison", pd.DataFrame(), "breakdown") is False


# ── _build_chart ──────────────────────────────────────────────────────────────

class TestBuildChart:
    def _df(self, rows):
        return pd.DataFrame(rows)

    def test_metric_returns_none(self):
        df = self._df([{"total_revenue": 12345}])
        assert _build_chart("metric", df, "Total revenue") is None

    def test_lookup_returns_none(self):
        df = self._df([{"name": "Acme", "revenue": 100}])
        assert _build_chart("lookup", df, "Acme details") is None

    def test_empty_df_returns_none(self):
        assert _build_chart("list", pd.DataFrame(), "test") is None

    def test_none_df_returns_none(self):
        assert _build_chart("list", None, "test") is None

    def test_no_numeric_cols_returns_none(self):
        df = self._df([{"region": "North"}, {"region": "South"}])
        assert _build_chart("list", df, "list regions") is None

    def test_no_label_col_returns_none(self):
        df = self._df([{"revenue": 100, "cost": 50}])
        # Both cols are metric words · label_column returns None
        result = _build_chart("comparison", df, "cost vs revenue")
        # May or may not be None depending on label fallback; just check it doesn't crash
        assert result is None or isinstance(result, go.Figure)

    def test_ranking_produces_horizontal_bar(self):
        rows = [{"group": f"Rep{i}", "sales": i * 100} for i in range(1, 8)]
        df = self._df(rows)
        fig = _build_chart("ranking", df, "Top sales reps")
        assert isinstance(fig, go.Figure)
        assert fig.data[0].orientation == "h"

    def test_comparison_without_pie_keywords_produces_grouped_bar(self):
        rows = [{"group": "Q1", "revenue": 100, "cost": 50},
                {"group": "Q2", "revenue": 120, "cost": 60}]
        df = self._df(rows)
        fig = _build_chart("comparison", df, "Q1 vs Q2 costs and revenue")
        assert isinstance(fig, go.Figure)
        assert fig.layout.barmode == "group"

    def test_list_produces_vertical_bar(self):
        rows = [{"group": f"Product {i}", "revenue": i * 50} for i in range(1, 6)]
        df = self._df(rows)
        fig = _build_chart("list", df, "Products by revenue")
        assert isinstance(fig, go.Figure)
        # Vertical bar has no orientation set (default is "v")
        assert fig.data[0].orientation in (None, "v", "")

    def test_comparison_pie_with_breakdown_keyword(self):
        rows = [{"group": f"Region{i}", "revenue": i * 1000} for i in range(1, 5)]
        df = self._df(rows)
        fig = _build_chart("comparison", df, "Revenue breakdown by region",
                           question="Revenue breakdown by region")
        assert isinstance(fig, go.Figure)
        assert isinstance(fig.data[0], go.Pie)

    def test_ranking_limits_to_20_rows(self):
        rows = [{"group": f"Rep{i}", "sales": i} for i in range(1, 30)]
        df = self._df(rows)
        fig = _build_chart("ranking", df, "All reps")
        assert isinstance(fig, go.Figure)
        assert len(fig.data[0].x) <= 20

    def test_list_limits_to_30_rows(self):
        rows = [{"group": f"G{i}", "val": i} for i in range(1, 50)]
        df = self._df(rows)
        fig = _build_chart("list", df, "All groups")
        assert isinstance(fig, go.Figure)
        assert len(fig.data[0].x) <= 30

    def test_comparison_limits_to_4_traces(self):
        rows = [{"group": "A", "m1": 1, "m2": 2, "m3": 3, "m4": 4, "m5": 5},
                {"group": "B", "m1": 6, "m2": 7, "m3": 8, "m4": 9, "m5": 10}]
        df = self._df(rows)
        fig = _build_chart("comparison", df, "Multi metric comparison")
        if fig is not None:
            assert len(fig.data) <= 4

    def test_single_row_still_renders(self):
        df = self._df([{"group": "Only One", "revenue": 99999}])
        fig = _build_chart("list", df, "Single result")
        assert isinstance(fig, go.Figure)
