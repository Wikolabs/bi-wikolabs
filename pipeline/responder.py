"""Step 6: Response Generation — narrative, optional Plotly figure, optional DataFrame."""

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

    # Build chart based on query type
    figure = _build_chart(query_type, df, question)

    return narrative, figure, df


def _build_dataframe(results: list) -> Optional[pd.DataFrame]:
    """Flatten results into a pandas DataFrame, removing Mongo _id if present."""
    if not results:
        return None
    clean = [{k: v for k, v in row.items() if k != "_id"} for row in results]
    try:
        return pd.DataFrame(clean)
    except Exception:
        return None


def _numeric_columns(df: pd.DataFrame) -> list:
    return [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]


def _label_column(df: pd.DataFrame) -> Optional[str]:
    """Pick the first string-like column to use as axis labels."""
    for col in df.columns:
        if df[col].dtype == object or str(df[col].dtype) == "string":
            return col
    return df.columns[0] if len(df.columns) > 0 else None


def _build_chart(
    query_type: str, df: Optional[pd.DataFrame], title: str
) -> Optional[go.Figure]:
    if df is None or df.empty:
        return None

    numeric_cols = _numeric_columns(df)
    label_col = _label_column(df)

    if query_type == "metric":
        # Single KPI — no chart needed
        return None

    if query_type == "lookup":
        # Detail view — no chart
        return None

    if query_type == "ranking":
        # Horizontal bar chart (top-N items)
        if not numeric_cols or label_col is None:
            return None
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
            height=max(300, len(df_sorted) * 30 + 100),
            template="plotly_white",
        )
        return fig

    if query_type == "comparison":
        # Grouped bar chart
        if not numeric_cols or label_col is None:
            return None
        fig = go.Figure()
        for col in numeric_cols[:4]:  # max 4 metrics
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
            height=400,
        )
        return fig

    if query_type in ("list", "metric"):
        # Vertical bar if there are numeric columns
        if not numeric_cols or label_col is None:
            return None
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
            height=400,
        )
        return fig

    return None
