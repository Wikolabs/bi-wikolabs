"""Step 6: Response Generation · narrative, optional Plotly figure, optional DataFrame."""

import json
import os
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from groq import AsyncGroq

_groq = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"

_SYSTEM = """You are a senior business intelligence analyst at Wikolabs.
Present query results as clear executive insights using markdown.
Use tables for structured data, bullet points for key takeaways,
and bold the most important numbers. Be concise and business-focused.
Do NOT include raw JSON in your response."""


async def respond(
    question: str,
    analysis: dict,
    results: list,
) -> tuple[str, Optional[go.Figure], Optional[pd.DataFrame]]:
    """Generate narrative, chart, and dataframe from query results.

    Returns:
        (narrative: str, figure: Optional[go.Figure], df: Optional[pd.DataFrame])
    """
    query_type = analysis.get("query_type", "metric")

    # Build narrative
    sample = json.dumps(results[:25], default=str, indent=2)
    resp = await _groq.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": (
                    f"Question: {question}\n\n"
                    f"Data ({len(results)} records total, showing up to 25):\n{sample}\n\n"
                    "Provide a business analysis with key insights."
                ),
            },
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    narrative = (resp.choices[0].message.content or "*(No narrative generated)*").strip()

    if not results:
        return narrative, None, None

    # Build DataFrame
    df = _build_dataframe(results)

    # Build chart based on query type and question keywords
    figure = _build_chart(query_type, df, question, question)

    return narrative, figure, df


_METRIC_WORDS = {"amount", "revenue", "profit", "salary", "payroll", "total",
                 "sum", "avg", "average", "count", "value", "cost", "price", "margin"}
_PIE_WORDS    = {"breakdown", "share", "proportion", "distribution", "split",
                 "pie", "portion", "percentage", "by segment", "by region",
                 "by category", "by department", "by status"}


def _build_dataframe(results: list) -> Optional[pd.DataFrame]:
    """Flatten MongoDB results into a DataFrame.

    When the pipeline groups by a field (e.g. $group: {_id: '$region'}),
    the grouped value lives in _id. We rename it to 'group' so it is
    visible as a label column instead of being silently dropped.
    """
    if not results:
        return None
    clean = []
    for row in results:
        r = dict(row)
        if "_id" in r:
            id_val = r.pop("_id")
            # Keep non-None, non-ObjectId _id values as a label column
            if id_val is not None and not hasattr(id_val, "generation_time"):
                r.setdefault("group", id_val)
        clean.append(r)
    try:
        return pd.DataFrame(clean)
    except Exception:
        return None


def _numeric_columns(df: pd.DataFrame) -> list:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _label_column(df: pd.DataFrame) -> Optional[str]:
    """Return the best column to use as axis/slice labels.

    Priority:
    1. String column whose name hints at a label (region, name, segment …)
    2. Any string column
    3. A non-metric numeric column (e.g. year)
    Never returns a revenue/amount column as a label.
    """
    LABEL_HINTS = {"name", "region", "segment", "category", "department", "month",
                   "date", "period", "type", "status", "product", "employee",
                   "customer", "group", "label", "country", "city", "quarter", "year"}

    # 1. String column with a label-like name
    for col in df.columns:
        if (df[col].dtype == object or str(df[col].dtype) == "string") and col.lower() in LABEL_HINTS:
            return col
    # 2. Any string column
    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) == "string":
            return col
    # 3. Numeric but not a metric (e.g. year, month number)
    for col in df.columns:
        if not any(w in col.lower() for w in _METRIC_WORDS):
            return col
    return None


def _should_use_pie(query_type: str, df: pd.DataFrame, question: str) -> bool:
    """Return True when a pie chart fits better than a bar chart."""
    if query_type in ("metric", "lookup", "ranking"):
        return False
    if df is None or df.empty or len(df) < 2 or len(df) > 10:
        return False
    numeric_cols = _numeric_columns(df)
    if len(numeric_cols) != 1:
        return False  # multiple metrics → grouped bar is clearer
    q = question.lower()
    return query_type == "comparison" or any(w in q for w in _PIE_WORDS)


def _build_chart(
    query_type: str, df: Optional[pd.DataFrame], title: str, question: str = ""
) -> Optional[go.Figure]:
    if df is None or df.empty:
        return None

    numeric_cols = _numeric_columns(df)
    label_col = _label_column(df)

    if query_type in ("metric", "lookup"):
        return None

    if label_col is None or not numeric_cols:
        return None

    # ── Pie chart ─────────────────────────────────────────────────────────────
    if _should_use_pie(query_type, df, question):
        metric_col = numeric_cols[0]
        fig = go.Figure(
            go.Pie(
                labels=df[label_col].astype(str),
                values=df[metric_col],
                hole=0.35,
                textinfo="label+percent",
                hovertemplate="%{label}<br>%{value:,.2f}<br>%{percent}<extra></extra>",
            )
        )
        fig.update_layout(
            title=title,
            height=420,
            template="plotly_white",
            showlegend=True,
        )
        return fig

    # ── Horizontal bar · ranking ───────────────────────────────────────────────
    if query_type == "ranking":
        metric_col = numeric_cols[0]
        df_sorted = df.sort_values(metric_col, ascending=True).tail(20)
        fig = go.Figure(
            go.Bar(
                x=df_sorted[metric_col],
                y=df_sorted[label_col].astype(str),
                orientation="h",
                marker_color="#636EFA",
            )
        )
        fig.update_layout(
            title=title,
            xaxis_title=metric_col.replace("_", " ").title(),
            yaxis_title=label_col.replace("_", " ").title(),
            height=max(300, len(df_sorted) * 32 + 120),
            template="plotly_white",
        )
        return fig

    # ── Grouped bar · comparison ───────────────────────────────────────────────
    if query_type == "comparison":
        fig = go.Figure()
        for col in numeric_cols[:4]:
            fig.add_trace(
                go.Bar(
                    name=col.replace("_", " ").title(),
                    x=df[label_col].astype(str),
                    y=df[col],
                )
            )
        fig.update_layout(
            title=title,
            barmode="group",
            template="plotly_white",
            height=420,
        )
        return fig

    # ── Vertical bar · list / trend ────────────────────────────────────────────
    metric_col = numeric_cols[0]
    df_plot = df.head(30)
    fig = go.Figure(
        go.Bar(
            x=df_plot[label_col].astype(str),
            y=df_plot[metric_col],
            marker_color="#00CC96",
        )
    )
    fig.update_layout(
        title=title,
        xaxis_title=label_col.replace("_", " ").title(),
        yaxis_title=metric_col.replace("_", " ").title(),
        template="plotly_white",
        height=420,
    )
    return fig
