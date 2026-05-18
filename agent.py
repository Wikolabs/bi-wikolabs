import json
import re
import os
from groq import Groq
from db import get_db
from prompts import SYSTEM_PROMPT

_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
MODEL = "llama-3.3-70b-versatile"


def _llm(messages: list, temperature: float = 0, max_tokens: int = 1024) -> str:
    resp = _groq.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content


def _extract_json(text: str) -> dict:
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1))
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text.strip())


def generate_query(question: str) -> dict:
    content = _llm([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ])
    return _extract_json(content)


def execute_query(spec: dict) -> list:
    db = get_db()
    col = db[spec["collection"]]
    if spec.get("operation") == "aggregate":
        return list(col.aggregate(spec["pipeline"]))
    cursor = col.find(spec.get("filter", {}), spec.get("projection", {}))
    if spec.get("limit"):
        cursor = cursor.limit(int(spec["limit"]))
    return list(cursor)


def format_answer(question: str, results: list) -> str:
    sample = json.dumps(results[:25], default=str, indent=2)
    return _llm([
        {
            "role": "system",
            "content": (
                "You are a senior business intelligence analyst at Wikolabs. "
                "Present query results as clear executive insights using markdown. "
                "Use tables for structured data, bullet points for key takeaways, "
                "and bold the most important numbers. Be concise and business-focused."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Data ({len(results)} records total, showing up to 25):\n{sample}\n\n"
                "Provide a business analysis with key insights."
            ),
        },
    ], temperature=0.3, max_tokens=1500)


def run_bi_agent(question: str) -> tuple:
    spec = generate_query(question)
    results = execute_query(spec)
    answer = format_answer(question, results)
    return answer, spec
