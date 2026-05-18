import json
from typing import Optional
import chainlit as cl
import chainlit.data as cl_data
from data_layer import SQLiteDataLayer
from agent import run_bi_agent, format_answer
from pipelines import match_pipeline

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


@cl.on_message
async def main(message: cl.Message):
    question = message.content.strip()

    pipeline_fn = match_pipeline(question)

    if pipeline_fn:
        async with cl.Step(name="🔍 Running pre-built pipeline...") as step:
            try:
                results = pipeline_fn()
                answer = format_answer(question, results)
                step.output = f"```\nPipeline: {pipeline_fn.__name__} — {len(results)} records\n```"
            except Exception as e:
                step.output = f"Error: {e}"
                await cl.Message(content=f"❌ **Error:** {e}").send()
                return
    else:
        async with cl.Step(name="🔍 Generating MongoDB query...") as step:
            try:
                answer, spec = run_bi_agent(question)
                if spec:
                    step.output = f"```json\n{json.dumps(spec, indent=2, default=str)}\n```"
                else:
                    step.is_error = False
                    step.output = ""
            except Exception as e:
                step.output = f"Error: {e}"
                await cl.Message(content=f"❌ **Error:** {e}").send()
                return

    await cl.Message(content=answer).send()
