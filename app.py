import io
import tempfile
import os
from typing import Optional

import chainlit as cl
import chainlit.data as cl_data

from data_layer import SQLiteDataLayer
from pipeline.orchestrator import run as orchestrator_run

cl_data._data_layer = SQLiteDataLayer("/data/threads.db")


@cl.header_auth_callback
def header_auth_callback(headers: dict) -> Optional[cl.User]:
    # Auto-login every visitor as the shared demo user
    return cl.User(identifier="demo", metadata={"name": "Demo Analyst"})


WELCOME = """# 📊 BI Wikolabs — Enterprise Intelligence Agent

Ask me anything about your business data in plain English. I'll generate the MongoDB query and return actionable insights.

**Available analytics:**

| # | Analysis | Try asking |
|---|---|---|
| 1 | Revenue Trend | *"Show monthly revenue trend"* |
| 2 | Top Products | *"What are the top 10 products by revenue?"* |
| 3 | Customer Segments | *"Compare Enterprise vs SMB revenue"* |
| 4 | Regional Performance | *"Sales breakdown by region"* |
| 5 | HR Dashboard | *"Show headcount and payroll by department"* |
| 6 | Cash Flow | *"Monthly cash flow for the last year"* |
| 7 | Product Margins | *"Which product categories have the best margins?"* |

Or ask any custom business question — I'll figure out the query.
"""


@cl.on_chat_start
async def start():
    await cl.Message(content=WELCOME).send()


@cl.action_callback("export_xlsx")
async def on_export_xlsx(action: cl.Action):
    """Handle XLSX export button click."""
    df = cl.user_session.get("last_df")
    if df is None or df.empty:
        await cl.Message(content="No data available to export.").send()
        return

    from exporter import to_xlsx

    title = cl.user_session.get("last_question", "Export")
    xlsx_bytes = to_xlsx(df, title=title)

    # Write to a temp file so Chainlit can serve it
    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".xlsx", prefix="bi_export_"
    )
    try:
        tmp.write(xlsx_bytes)
        tmp.flush()
        tmp.close()
        file_el = cl.File(name="export.xlsx", path=tmp.name, display="inline")
        await cl.Message(content="📥 **XLSX export ready:**", elements=[file_el]).send()
    finally:
        # Clean up after send (Chainlit copies the file)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@cl.action_callback("export_pdf")
async def on_export_pdf(action: cl.Action):
    """Handle PDF export button click."""
    df = cl.user_session.get("last_df")
    if df is None or df.empty:
        await cl.Message(content="No data available to export.").send()
        return

    from exporter import to_pdf

    title = cl.user_session.get("last_question", "Export")
    pdf_bytes = to_pdf(df, title=title)

    tmp = tempfile.NamedTemporaryFile(
        delete=False, suffix=".pdf", prefix="bi_export_"
    )
    try:
        tmp.write(pdf_bytes)
        tmp.flush()
        tmp.close()
        file_el = cl.File(name="export.pdf", path=tmp.name, display="inline")
        await cl.Message(content="📥 **PDF export ready:**", elements=[file_el]).send()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


@cl.on_message
async def main(message: cl.Message):
    question = message.content.strip()

    # Store question for export filename
    cl.user_session.set("last_question", question[:60])

    async with cl.Step(name="🔍 Analysing & querying...") as step:
        try:
            narrative, figure, df = await orchestrator_run(question)
            if df is not None and not df.empty:
                step.output = f"Returned {len(df)} records."
            else:
                step.output = "Query complete."
        except Exception as exc:
            step.output = f"Error: {exc}"
            step.is_error = True
            await cl.Message(content=f"❌ **Error:** {exc}").send()
            return

    # Persist DataFrame for export actions
    cl.user_session.set("last_df", df)

    # Collect response elements
    elements = []

    if figure is not None:
        try:
            chart_el = cl.Plotly(figure=figure, display="inline", name="chart")
            elements.append(chart_el)
        except Exception:
            pass  # chart rendering optional

    # Send the narrative (with chart if any)
    await cl.Message(content=narrative, elements=elements).send()

    # Offer export buttons when tabular data is available
    if df is not None and not df.empty and len(df) > 0:
        actions = [
            cl.Action(
                name="export_xlsx",
                label="📊 Export XLSX",
                description="Download results as Excel spreadsheet",
                payload={"format": "xlsx"},
            ),
            cl.Action(
                name="export_pdf",
                label="📄 Export PDF",
                description="Download results as PDF table",
                payload={"format": "pdf"},
            ),
        ]
        await cl.Message(
            content=f"_Data available: {len(df)} rows × {len(df.columns)} columns_",
            actions=actions,
        ).send()
