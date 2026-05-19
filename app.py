"""Chainlit entry point — chat handlers, export actions, golden record validation."""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import chainlit as cl
import chainlit.data as cl_data

from data_layer import PostgresDataLayer
from pipeline.orchestrator import run as orchestrator_run

logger = logging.getLogger(__name__)

cl_data._data_layer = PostgresDataLayer()

EXPORT_DIR = "/data/exports"
BASE_URL   = os.getenv("BASE_URL", "https://bi.wikolabs.com/exports")
os.makedirs(EXPORT_DIR, exist_ok=True)


# ── App startup ───────────────────────────────────────────────────────────────

@cl.on_chat_start
async def start():
    # Run DB migrations and initial schema scan on first chat (idempotent)
    try:
        from migrations import run_migrations
        await run_migrations()
    except Exception as exc:
        logger.error("Migration error: %s", exc)

    try:
        from schema_registry import needs_refresh, refresh_all
        if await needs_refresh():
            async with cl.Step(name="🔎 Scanning database schema…") as step:
                await refresh_all()
                step.output = "Schema extracted and stored."
    except Exception as exc:
        logger.warning("Schema refresh skipped: %s", exc)

    # Start nightly cron (idempotent — safe to call multiple times)
    try:
        from scheduler import start as start_scheduler
        start_scheduler()
    except Exception as exc:
        logger.warning("Scheduler start skipped: %s", exc)

    await cl.Message(content=await _build_welcome()).send()


# ── Auth ──────────────────────────────────────────────────────────────────────

@cl.header_auth_callback
def header_auth_callback(headers: dict) -> Optional[cl.User]:
    return cl.User(identifier="demo", metadata={"name": "Demo Analyst"})


# ── Main message handler ──────────────────────────────────────────────────────

@cl.on_message
async def main(message: cl.Message):
    question = message.content.strip()
    cl.user_session.set("last_question", question[:60])

    async with cl.Step(name="🔍 Analysing & querying…") as step:
        try:
            narrative, figure, df, spec, collections = await orchestrator_run(question)
            step.output = f"Returned {len(df)} records." if df is not None and not df.empty else "Query complete."
        except Exception as exc:
            step.output = f"Error: {exc}"
            step.is_error = True
            await cl.Message(content=f"❌ **Error:** {exc}").send()
            return

    # Persist for export and golden record actions
    cl.user_session.set("last_df",          df)
    cl.user_session.set("last_spec",        spec)
    cl.user_session.set("last_collections", collections)

    elements = []
    if figure is not None:
        try:
            elements.append(cl.Plotly(figure=figure, display="inline", name="chart"))
        except Exception:
            pass

    await cl.Message(content=narrative, elements=elements).send()

    if df is not None and not df.empty:
        actions = [
            cl.Action(name="export_xlsx",     label="📊 Export XLSX",
                      description="Download as Excel", payload={"format": "xlsx"}),
            cl.Action(name="export_pdf",      label="📄 Export PDF",
                      description="Download as PDF",   payload={"format": "pdf"}),
            cl.Action(name="validate_result", label="✅ Result is correct",
                      description="Save as verified example for future queries",
                      payload={"valid": True}),
            cl.Action(name="validate_result", label="❌ Result is wrong",
                      description="Mark result as incorrect",
                      payload={"valid": False}),
        ]
        await cl.Message(
            content=f"_Data: {len(df)} rows × {len(df.columns)} columns_",
            actions=actions,
        ).send()


# ── Export actions ────────────────────────────────────────────────────────────

@cl.action_callback("export_xlsx")
async def on_export_xlsx(action: cl.Action):
    df = cl.user_session.get("last_df")
    if df is None or df.empty:
        await cl.Message(content="No data available to export.").send()
        return
    from exporter import to_xlsx
    title    = cl.user_session.get("last_question", "Export")
    filename = f"export_{int(time.time())}.xlsx"
    path     = os.path.join(EXPORT_DIR, filename)
    with open(path, "wb") as f:
        f.write(to_xlsx(df, title=title))
    await cl.Message(content=f"📥 **[Download XLSX]({BASE_URL}/{filename})**").send()


@cl.action_callback("export_pdf")
async def on_export_pdf(action: cl.Action):
    df = cl.user_session.get("last_df")
    if df is None or df.empty:
        await cl.Message(content="No data available to export.").send()
        return
    from exporter import to_pdf
    title    = cl.user_session.get("last_question", "Export")
    filename = f"export_{int(time.time())}.pdf"
    path     = os.path.join(EXPORT_DIR, filename)
    with open(path, "wb") as f:
        f.write(to_pdf(df, title=title))
    await cl.Message(content=f"📥 **[Download PDF]({BASE_URL}/{filename})**").send()


# ── Golden record validation ──────────────────────────────────────────────────

@cl.action_callback("validate_result")
async def on_validate_result(action: cl.Action):
    valid = action.payload.get("valid", False)
    if not valid:
        await cl.Message(content="Thanks for the feedback — this won't be used as a reference.").send()
        return

    spec        = cl.user_session.get("last_spec")
    collections = cl.user_session.get("last_collections")
    question    = cl.user_session.get("last_question", "")

    if not spec or not question:
        await cl.Message(content="Nothing to save.").send()
        return

    try:
        from golden_records import save as save_golden
        await save_golden(question=question, mql_query=spec, collection_names=collections or [])
        await cl.Message(
            content="✅ **Saved as a verified example.** Future similar questions will use this query as reference."
        ).send()
    except Exception as exc:
        logger.error("Failed to save golden record: %s", exc)
        await cl.Message(content="Could not save — please try again.").send()


# ── Welcome message (dynamically built from live schema) ─────────────────────

_WELCOME_FALLBACK = """# BI Intelligence Agent

Ask me anything about your business data in plain English.

I'll automatically detect your data structure and answer questions about trends, rankings, comparisons, and specific records.

**Try asking:**
- *"What are the top items by value?"*
- *"Show me a monthly trend"*
- *"Compare performance across categories"*
- *"How many records match this condition?"*
"""

async def _build_welcome() -> str:
    try:
        from schema_registry import get_all_summaries
        summaries = await get_all_summaries()
        if not summaries:
            return _WELCOME_FALLBACK
        rows = "\n".join(
            f"| `{name}` | {summary[:70]}{'…' if len(summary) > 70 else ''} |"
            for name, summary in sorted(summaries.items())
        )
        return f"""# BI Intelligence Agent

Ask me anything about your business data in plain English.

**Available collections:**

| Collection | Description |
|---|---|
{rows}

**Example questions:**
- *"What are the top 10 entries by value?"*
- *"Show trends over the last 12 months"*
- *"Compare totals by category"*
- *"List all records matching a condition"*

Or describe any analysis — I'll build the query automatically.
"""
    except Exception:
        return _WELCOME_FALLBACK
