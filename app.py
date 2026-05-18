import json
import chainlit as cl
from agent import run_bi_agent, format_answer
from pipelines import match_pipeline

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

    async with cl.Step(name="🔍 Generating MongoDB query...") as step:
        try:
            pipeline_fn = match_pipeline(question)
            if pipeline_fn:
                results = pipeline_fn()
                spec = {"pipeline": "pre-built", "name": pipeline_fn.__name__}
                answer = format_answer(question, results)
            else:
                answer, spec = run_bi_agent(question)
            step.output = f"```json\n{json.dumps(spec, indent=2, default=str)}\n```"
        except Exception as e:
            step.output = f"Error: {e}"
            await cl.Message(content=f"❌ **Error:** {e}").send()
            return

    await cl.Message(content=answer).send()
